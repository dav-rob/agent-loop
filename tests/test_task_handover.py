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

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls, patch.object(
        orch, "_ensure_workspace_deps"
    ), patch.object(orch, "run_verification", side_effect=fake_verification):
        mock_router = MagicMock()
        mock_router.run.side_effect = [executor_result, review_result]
        mock_router_cls.return_value = mock_router

        assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is True

    progress = progress_path.read_text()
    assert "`pytest -q` -> PASSED" in progress

    handover_files = list((tmp_path / ".agent-loop" / "handoffs").glob("goal-1-task-1-*.md"))
    assert len(handover_files) == 1
    handover = handover_files[0].read_text()
    assert "### Executor" in handover
    assert "Implemented scaffold." in handover
    assert "### Reviewer" in handover
    assert "Task meets scope" in handover
