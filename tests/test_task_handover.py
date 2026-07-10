from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_loop.adapters import AttemptResult
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.orchestrator import Orchestrator
from agent_loop.repositories import (
    AttemptRepository,
    FeatureRepository,
    HandoverRepository,
    ReviewRepository,
    RunRepository,
    TaskRepository,
)
from agent_loop.views import render_task_handover_md


@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()


def test_task_handover_markdown_renders_executor_and_reviewer_entries(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)
    handover_repo = HandoverRepository(db_conn)

    run_id = run_repo.create("Build a robust dashboard", "none")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(
        run_id,
        feat_id,
        "Scaffold full-stack dashboard application",
        "implementation",
        "medium",
        scope={"writes": ["package.json", "src/app/page.tsx"]},
        required_verification="npm test",
    )
    attempt_id = attempt_repo.create(run_id, task_id, route="executor")
    attempt_repo.update_route_metadata(attempt_id, "executor", "agy", "Gemini 3.1 Pro", "high")
    attempt_repo.update_outcome(attempt_id, "completed", commit_sha="abc123")

    handover_repo.create(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        phase="executor",
        actor_route="executor:agy:Gemini 3.1 Pro",
        decision="completed",
        summary="Created the Next.js scaffold and added a SQLite-backed data layer.",
        followups="Consider adding end-to-end tests after the API settles.",
        commit_sha="abc123",
        verification_status="passed",
        evidence_paths=["/tmp/logs/stdout.log"],
    )
    handover_repo.create(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        phase="reviewer",
        actor_route="reviewer:codex:gpt-5.5",
        decision="rejected",
        severity="blocking",
        summary="Reviewer found one task-scoped correctness issue.",
        blocking_findings="The route accepts unauthenticated requests that trigger local CLI execution.",
        followups="Styling refinements can be handled later.",
        evidence_paths=["/tmp/logs/review.log"],
    )

    path = render_task_handover_md(db_conn, run_id, task_id, tmp_path / "handoffs")

    assert path.name == "goal-1-task-1-scaffold-full-stack.md"
    content = path.read_text()
    assert "# Goal 1 / Task 1: Scaffold full-stack dashboard application" in content
    assert "## Attempt 1" in content
    assert "### Executor" in content
    assert "Created the Next.js scaffold" in content
    assert "### Reviewer" in content
    assert "The route accepts unauthenticated requests" in content
    assert "Reviewer should reject only issues that block this task now" in content


def test_task_handover_markdown_renders_attempt_retry_strategy(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)
    handover_repo = HandoverRepository(db_conn)

    run_id = run_repo.create("Build a robust dashboard", "none")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(run_id, feat_id, "Scaffold dashboard", "implementation", "medium")
    attempt_id = attempt_repo.create(
        run_id,
        task_id,
        route="executor",
        retry_strategy="restart_from_main",
        retry_strategy_reason="Reviewer found the branch was solving the wrong task.",
    )
    handover_repo.create(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        phase="reviewer",
        decision="rejected",
        summary="Restart from a clean base.",
    )

    path = render_task_handover_md(db_conn, run_id, task_id, tmp_path / "handoffs")
    content = path.read_text()

    assert "- **Retry strategy:** restart_from_main" in content
    assert "- **Retry strategy reason:** Reviewer found the branch was solving the wrong task." in content


def test_task_execution_writes_handover_and_refreshes_progress_after_verification(db_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_loop.orchestrator.create_worktree",
        lambda repo, worktree, branch: Path(worktree).mkdir(parents=True, exist_ok=True),
    )
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda worktree, message: "mock_sha")
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo, branch, target: (True, []))
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo, worktree: None)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Build dashboard", "none")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(
        run_id,
        feat_id,
        "Scaffold dashboard",
        "implementation",
        "medium",
        scope={"writes": ["package.json"]},
        required_verification="pytest -q",
    )
    task_repo.update_status(task_id, "ready")

    config = Config(
        {
            "db_path": ":memory:",
            "logs_dir": str(tmp_path / ".agent-loop" / "logs"),
            "handoffs_dir": str(tmp_path / ".agent-loop" / "handoffs"),
        }
    )
    progress_path = tmp_path / ".agent-loop" / "progress.md"
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=progress_path)

    def fake_verification(run_id, task_id, attempt_id, command, worktree_dir, logs_dir):
        orch.test_run_repo.create(
            run_id,
            task_id,
            attempt_id,
            command,
            "task",
            0,
            0.12,
            str(Path(logs_dir) / "verification.log"),
        )
        return True

    executor_result = MagicMock()
    executor_result.provider = "agy"
    executor_result.model = "Gemini 3.1 Pro"
    executor_result.reasoning_level = "high"
    executor_result.result = AttemptResult(True, 0, "Implemented scaffold.", "")

    review_result = MagicMock()
    review_result.provider = "codex"
    review_result.model = "gpt-5.5"
    review_result.reasoning_level = "high"
    review_result.result = AttemptResult(
        True,
        0,
        '{"decision": "approved", "findings": "Task meets scope; non-blocking polish can wait."}',
        "",
    )

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.run.side_effect = [executor_result, review_result]
        mock_router_cls.return_value = mock_router

        assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is True

    progress = progress_path.read_text()
    assert "`pytest -q` -> PASSED" in progress
    assert "### Recent Lifecycle Events" in progress
    assert "`task_started`" in progress
    assert "`attempt_started`" in progress
    assert "`executor_completed`" in progress
    assert "`review_completed`" in progress

    handover_files = list((tmp_path / ".agent-loop" / "handoffs").glob("goal-1-task-1-*.md"))
    assert len(handover_files) == 1
    handover = handover_files[0].read_text()
    assert "### Executor" in handover
    assert "Implemented scaffold." in handover
    assert "### Reviewer" in handover
    assert "Task meets scope" in handover


def test_timed_out_execution_records_synthesized_handover_and_timeout_review(db_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_loop.orchestrator.create_worktree",
        lambda repo, worktree, branch: Path(worktree).mkdir(parents=True, exist_ok=True),
    )
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo, worktree: None)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    handover_repo = HandoverRepository(db_conn)

    run_id = run_repo.create("Build dashboard", "none")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(
        run_id,
        feat_id,
        "Scaffold dashboard",
        "implementation",
        "medium",
        scope={"writes": ["package.json", "src/server/store.ts"]},
    )
    task_repo.update_status(task_id, "ready")

    config = Config(
        {
            "db_path": ":memory:",
            "logs_dir": str(tmp_path / ".agent-loop" / "logs"),
            "handoffs_dir": str(tmp_path / ".agent-loop" / "handoffs"),
            "worktrees_dir": str(tmp_path / ".agent-loop" / "worktrees"),
        }
    )
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / ".agent-loop" / "progress.md")

    def fake_router_run(profile, prompt, workspace_path, logs_root):
        stdout_dir = Path(logs_root) / "01-executor_escalated-codex-gpt-5-5"
        stdout_dir.mkdir(parents=True, exist_ok=True)
        (stdout_dir / "stdout.log").write_text(
            "\n".join(
                [
                    '{"type":"item.completed","item":{"type":"agent_message","text":"Created package config and started the server data layer."}}',
                    '{"type":"item.completed","item":{"type":"file_change","changes":[{"path":"package.json"},{"path":"src/server/store.ts"}]}}',
                    '{"type":"item.completed","item":{"type":"command_execution","command":"npm run build","exit_code":1,"aggregated_output":"Could not resolve entry module src/client/main.tsx"}}',
                ]
            )
            + "\n"
        )
        routed = MagicMock()
        routed.provider = "codex"
        routed.model = "gpt-5.5"
        routed.reasoning_level = "high"
        routed.result = AttemptResult(
            False,
            -1,
            "",
            "Timeout expired after 600.0 seconds.",
            timed_out=True,
        )
        return routed

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls, patch.object(
        orch, "_preserve_uncommitted_changes", return_value=str(tmp_path / "patch.diff")), patch.object(
        orch, "run_timeout_review", return_value="retry_with_handoff"
    ) as mock_timeout_review:
        mock_router = MagicMock()
        mock_router.run.side_effect = fake_router_run
        mock_router_cls.return_value = mock_router

        assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is False

    handovers = handover_repo.get_by_task(run_id, task_id)
    executor_entries = [entry for entry in handovers if entry["phase"] == "executor"]
    assert len(executor_entries) == 1
    summary = executor_entries[0]["summary"]
    assert "Timed out before the executor returned a final handover" in summary
    assert "Created package config and started the server data layer" in summary
    assert "package.json" in summary
    assert "src/server/store.ts" in summary
    assert "npm run build" in summary
    assert "Could not resolve entry module src/client/main.tsx" in summary
    mock_timeout_review.assert_called_once()


def test_retry_prompt_includes_previous_timeout_handover_context(db_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_loop.orchestrator.create_worktree",
        lambda repo, worktree, branch: Path(worktree).mkdir(parents=True, exist_ok=True),
    )
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo, worktree: None)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)
    handover_repo = HandoverRepository(db_conn)

    run_id = run_repo.create("Build dashboard", "none")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(
        run_id,
        feat_id,
        "Scaffold dashboard",
        "implementation",
        "medium",
        scope={"writes": ["package.json"]},
    )
    task_repo.update_status(task_id, "ready")
    prior_attempt_id = attempt_repo.create(run_id, task_id, route="executor_escalated")
    attempt_repo.update_route_metadata(prior_attempt_id, "executor_escalated", "codex", "gpt-5.5", "high")
    attempt_repo.update_outcome(prior_attempt_id, "failed")
    handover_repo.create(
        run_id=run_id,
        task_id=task_id,
        attempt_id=prior_attempt_id,
        phase="executor",
        actor_route="executor_escalated:codex:gpt-5.5",
        decision="failed",
        summary="Timed out before the executor returned a final handover. It created package config and server files before failing on the missing client entry.",
        blocking_findings="Timeout expired after 600.0 seconds.",
        evidence_paths=["/tmp/stdout.log"],
    )

    config = Config(
        {
            "db_path": ":memory:",
            "logs_dir": str(tmp_path / ".agent-loop" / "logs"),
            "handoffs_dir": str(tmp_path / ".agent-loop" / "handoffs"),
            "worktrees_dir": str(tmp_path / ".agent-loop" / "worktrees"),
        }
    )
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / ".agent-loop" / "progress.md")
    captured_prompts = []

    def fake_router_run(profile, prompt, workspace_path, logs_root):
        captured_prompts.append(prompt)
        routed = MagicMock()
        routed.provider = "agy"
        routed.model = "Gemini 3.1 Pro"
        routed.reasoning_level = "high"
        routed.result = AttemptResult(False, 1, "", "deliberate test failure")
        return routed

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls, patch.object(
        orch, "_preserve_uncommitted_changes", return_value="CLEAN"):
        mock_router = MagicMock()
        mock_router.run.side_effect = fake_router_run
        mock_router_cls.return_value = mock_router

        assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is False

    assert captured_prompts
    prompt = captured_prompts[0]
    assert "Previous timed-out attempt handovers" in prompt
    assert "created package config and server files" in prompt
    assert "Timeout expired after 600.0 seconds" in prompt


def test_timeout_review_records_review_and_reviewer_handover(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)
    review_repo = ReviewRepository(db_conn)
    handover_repo = HandoverRepository(db_conn)

    run_id = run_repo.create("Build dashboard", "none")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(
        run_id,
        feat_id,
        "Scaffold dashboard",
        "implementation",
        "medium",
    )
    attempt_id = attempt_repo.create(run_id, task_id, route="executor_escalated")

    config = Config(
        {
            "db_path": ":memory:",
            "logs_dir": str(tmp_path / ".agent-loop" / "logs"),
            "handoffs_dir": str(tmp_path / ".agent-loop" / "handoffs"),
        }
    )
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / ".agent-loop" / "progress.md")

    routed = MagicMock()
    routed.provider = "codex"
    routed.model = "gpt-5.5"
    routed.result = AttemptResult(
        True,
        0,
        '{"decision":"retry_with_handoff","findings":"Preserve the package/server scaffold and create the missing client entry next."}',
        "",
    )

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.run.return_value = routed
        mock_router_cls.return_value = mock_router

        decision = orch.run_timeout_review(
            run_id,
            task_id,
            attempt_id=attempt_id,
            timeout_handover="Timed out after creating package config and server files.",
        )

    assert decision == "retry_with_handoff"
    reviews = review_repo.get_by_run(run_id)
    assert reviews[0]["subject_type"] == "timeout"
    assert reviews[0]["subject_id"] == attempt_id
    assert reviews[0]["decision"] == "retry_with_handoff"
    assert "Preserve the package/server scaffold" in reviews[0]["findings"]

    handovers = handover_repo.get_by_task(run_id, task_id)
    reviewer_entries = [entry for entry in handovers if entry["phase"] == "reviewer"]
    assert len(reviewer_entries) == 1
    assert reviewer_entries[0]["decision"] == "retry_with_handoff"
    assert "Preserve the package/server scaffold" in reviewer_entries[0]["summary"]
