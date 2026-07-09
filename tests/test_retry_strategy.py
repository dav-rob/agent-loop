from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_loop.adapters import AttemptResult
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.orchestrator import Orchestrator, parse_review_response
from agent_loop.repositories import AttemptRepository, FeatureRepository, RunRepository, TaskRepository


@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()


def test_attempt_repository_records_retry_strategy_and_start_base_shas(db_conn):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Build resilient retries", "none")
    feature_id = feature_repo.create(run_id, "Retry continuity", "medium")
    task_id = task_repo.create(run_id, feature_id, "Continue failed work", "implementation", "medium")

    attempt_id = attempt_repo.create(
        run_id=run_id,
        task_id=task_id,
        route="executor",
        worktree_path="/tmp/task-worktree",
        start_sha="start123",
        base_sha="base456",
        retry_strategy="continue_existing_branch",
    )

    attempt = attempt_repo.get(attempt_id)
    assert attempt["start_sha"] == "start123"
    assert attempt["base_sha"] == "base456"
    assert attempt["retry_strategy"] == "continue_existing_branch"

    attempt_repo.update_retry_strategy(
        attempt_id,
        "restart_from_main",
        retry_strategy_reason="Reviewer said the approach edits the wrong subsystem.",
    )

    attempt = attempt_repo.get(attempt_id)
    assert attempt["retry_strategy"] == "restart_from_main"
    assert attempt["retry_strategy_reason"] == "Reviewer said the approach edits the wrong subsystem."


def test_review_parser_accepts_optional_retry_strategy():
    parsed = parse_review_response(
        """
        {
          "decision": "rejected",
          "retry_strategy": "restart_from_main",
          "findings": "The implementation is in the wrong stack; restart from the clean base."
        }
        """
    )

    assert parsed.decision == "rejected"
    assert parsed.findings == "The implementation is in the wrong stack; restart from the clean base."
    assert parsed.retry_strategy == "restart_from_main"


def test_review_parser_defaults_missing_rejected_strategy_to_continue():
    parsed = parse_review_response('{"decision": "rejected", "findings": "Fix the validation bug."}')

    assert parsed.decision == "rejected"
    assert parsed.retry_strategy == "continue_existing_branch"


def test_task_review_records_retry_strategy_on_attempt(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Build resilient retries", "none")
    feature_id = feature_repo.create(run_id, "Retry continuity", "medium")
    task_id = task_repo.create(run_id, feature_id, "Continue failed work", "implementation", "medium")
    attempt_id = attempt_repo.create(run_id, task_id, route="executor")

    orch = Orchestrator(
        db_conn,
        Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    routed = MagicMock()
    routed.provider = "codex"
    routed.model = "gpt-5.5"
    routed.reasoning_level = "high"
    routed.result = AttemptResult(
        success=True,
        exit_code=0,
        output='{"decision": "rejected", "retry_strategy": "restart_from_main", "retry_strategy_reason": "Wrong stack.", "findings": "This is the wrong implementation stack."}',
        error="",
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router = MagicMock()
        router.run.return_value = routed
        router_cls.return_value = router

        decision = orch.run_task_review(run_id, task_id, "start123", "end456", attempt_id=attempt_id)

    assert decision == "rejected"
    attempt = attempt_repo.get(attempt_id)
    assert attempt["retry_strategy"] == "restart_from_main"
    assert attempt["retry_strategy_reason"] == "Wrong stack."


def test_task_review_runs_in_task_worktree_and_prompts_with_review_metadata(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Build resilient retries", "none")
    feature_id = feature_repo.create(run_id, "Retry continuity", "medium")
    task_id = task_repo.create(run_id, feature_id, "Continue failed work", "implementation", "medium")
    attempt_id = attempt_repo.create(run_id, task_id, route="executor")

    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "worktrees_dir": str(tmp_path / "worktrees"),
    })
    orch = Orchestrator(
        db_conn,
        config,
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    task_worktree = orch._task_worktree_dir(run_id, task_id)
    task_worktree.mkdir(parents=True)

    routed = MagicMock()
    routed.provider = "codex"
    routed.model = "gpt-5.5"
    routed.reasoning_level = "high"
    routed.result = AttemptResult(
        success=True,
        exit_code=0,
        output='{"decision": "approved", "findings": "The task branch is correct."}',
        error="",
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router = MagicMock()
        router.run.return_value = routed
        router_cls.return_value = router

        decision = orch.run_task_review(run_id, task_id, "start123", "end456", attempt_id=attempt_id)

    assert decision == "approved"
    call = router.run.call_args.kwargs
    assert call["workspace_path"] == task_worktree
    prompt = call["prompt"]
    assert f"Task worktree: {task_worktree}" in prompt
    assert f"Task branch: agent-loop-run-{run_id}-task-{task_id}" in prompt
    assert f"Attempt ID: {attempt_id}" in prompt
    assert "Start SHA: start123" in prompt
    assert "End SHA: end456" in prompt
    assert "authoritative checkout for file inspection and verification commands" in prompt


def test_timeout_review_records_retry_strategy_on_attempt(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Build resilient retries", "none")
    feature_id = feature_repo.create(run_id, "Retry continuity", "medium")
    task_id = task_repo.create(run_id, feature_id, "Continue failed work", "implementation", "medium")
    attempt_id = attempt_repo.create(run_id, task_id, route="executor")

    orch = Orchestrator(
        db_conn,
        Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    routed = MagicMock()
    routed.provider = "codex"
    routed.model = "gpt-5.5"
    routed.reasoning_level = "high"
    routed.result = AttemptResult(
        success=True,
        exit_code=0,
        output='{"decision": "abandon", "retry_strategy": "apply_patch_to_clean_branch", "retry_strategy_reason": "Branch is dirty but patch is usable.", "findings": "Preserve the patch but restart clean."}',
        error="",
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router = MagicMock()
        router.run.return_value = routed
        router_cls.return_value = router

        decision = orch.run_timeout_review(run_id, task_id, attempt_id, "Timed out after useful edits.")

    assert decision == "abandon"
    attempt = attempt_repo.get(attempt_id)
    assert attempt["retry_strategy"] == "apply_patch_to_clean_branch"
    assert attempt["retry_strategy_reason"] == "Branch is dirty but patch is usable."


def test_reconcile_interrupted_run_preserves_useful_worktree_and_records_strategy(db_conn, tmp_path, monkeypatch):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Recover useful interrupted work", "none")
    feature_id = feature_repo.create(run_id, "Recovery", "medium")
    task_id = task_repo.create(run_id, feature_id, "Keep partial edits", "implementation", "medium")
    task_repo.update_status(task_id, "ready")
    task_repo.update_status(task_id, "running")

    worktree_dir = tmp_path / "worktrees" / "run-1-task-1-attempt-99"
    worktree_dir.mkdir(parents=True)
    logs_dir = tmp_path / "logs" / "1" / "1" / "1"
    logs_dir.mkdir(parents=True)
    patch_path = logs_dir / "patch.diff"
    patch_path.write_text("diff --git a/app.js b/app.js\n+console.log('partial');\n")

    attempt_id = attempt_repo.create(
        run_id=run_id,
        task_id=task_id,
        route="executor",
        worktree_path=str(worktree_dir),
        logs_path=str(logs_dir),
    )

    config = Config(
        {
            "db_path": ":memory:",
            "logs_dir": str(tmp_path / "logs"),
            "worktrees_dir": str(tmp_path / "worktrees"),
        }
    )
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    monkeypatch.setattr(orch, "_preserve_uncommitted_changes", MagicMock(return_value=str(patch_path)))
    remove_mock = MagicMock()
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", remove_mock)

    assert orch.reconcile_interrupted_run(run_id) == 1

    attempt = attempt_repo.get(attempt_id)
    assert attempt["outcome"] == "abandoned"
    assert attempt["patch_path"] == str(patch_path)
    assert attempt["worktree_path"] == str(worktree_dir)
    assert attempt["retry_strategy"] == "apply_patch_to_clean_branch"
    assert "Preserved interrupted work" in attempt["retry_strategy_reason"]
    assert task_repo.get(task_id)["status"] == "ready"
    remove_mock.assert_not_called()


def test_prepare_task_worktree_restart_from_main_recreates_branch(db_conn, tmp_path, monkeypatch):
    config = Config({"db_path": ":memory:", "worktrees_dir": str(tmp_path / "worktrees")})
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    worktree_dir = tmp_path / "worktrees" / "run-1-task-2"
    worktree_dir.mkdir(parents=True)

    remove_mock = MagicMock()
    create_mock = MagicMock()
    git_commands = []

    def fake_run(cmd, cwd=None, **kwargs):
        git_commands.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", remove_mock)
    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", create_mock)
    monkeypatch.setattr("subprocess.run", fake_run)

    orch._prepare_task_worktree(
        repo_path=repo_path,
        worktree_dir=worktree_dir,
        branch_name="agent-loop-run-1-task-2",
        strategy="restart_from_main",
        patch_path=None,
    )

    remove_mock.assert_called_once_with(repo_path, worktree_dir)
    assert ["git", "branch", "-D", "agent-loop-run-1-task-2"] in git_commands
    create_mock.assert_called_once_with(repo_path, worktree_dir, "agent-loop-run-1-task-2", base_commit="main")


def test_prepare_task_worktree_apply_patch_to_clean_branch(db_conn, tmp_path, monkeypatch):
    config = Config({"db_path": ":memory:", "worktrees_dir": str(tmp_path / "worktrees")})
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    worktree_dir = tmp_path / "worktrees" / "run-1-task-2"
    patch_path = tmp_path / "patch.diff"
    patch_path.write_text("diff --git a/app.js b/app.js\n+partial\n")

    remove_mock = MagicMock()
    create_mock = MagicMock()
    git_commands = []

    def fake_run(cmd, cwd=None, **kwargs):
        git_commands.append((cmd, cwd))
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", remove_mock)
    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", create_mock)
    monkeypatch.setattr("subprocess.run", fake_run)

    orch._prepare_task_worktree(
        repo_path=repo_path,
        worktree_dir=worktree_dir,
        branch_name="agent-loop-run-1-task-2",
        strategy="apply_patch_to_clean_branch",
        patch_path=str(patch_path),
    )

    remove_mock.assert_called_once_with(repo_path, worktree_dir)
    create_mock.assert_called_once_with(repo_path, worktree_dir, "agent-loop-run-1-task-2", base_commit="main")
    assert (["git", "apply", str(patch_path)], worktree_dir) in git_commands


def test_latest_retry_strategy_uses_commit_for_restart_from_last_good(db_conn, tmp_path):
    orch = Orchestrator(
        db_conn,
        Config({"db_path": ":memory:"}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    strategy, reason, patch_path, base_ref = orch._latest_retry_strategy(
        [
            {
                "id": 3,
                "retry_strategy": "restart_from_last_good_commit",
                "retry_strategy_reason": "Current branch went bad.",
                "patch_path": None,
                "base_sha": "base123",
                "commit_sha": "good456",
            }
        ]
    )

    assert strategy == "restart_from_last_good_commit"
    assert reason == "Current branch went bad."
    assert patch_path is None
    assert base_ref == "good456"


def test_execute_task_uses_previous_retry_strategy_for_worktree_preparation(db_conn, tmp_path, monkeypatch):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Use explicit retry strategy", "none")
    feature_id = feature_repo.create(run_id, "Retry continuity", "medium")
    task_id = task_repo.create(run_id, feature_id, "Retry from clean main", "implementation", "medium")
    task_repo.update_status(task_id, "ready")
    previous_attempt_id = attempt_repo.create(run_id, task_id, route="executor")
    attempt_repo.update_outcome(previous_attempt_id, "failed", patch_path=str(tmp_path / "patch.diff"))
    attempt_repo.update_retry_strategy(previous_attempt_id, "restart_from_main", "Reviewer requested a clean restart.")

    config = Config(
        {
            "db_path": ":memory:",
            "logs_dir": str(tmp_path / "logs"),
            "worktrees_dir": str(tmp_path / "worktrees"),
        }
    )
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    monkeypatch.setattr(orch, "_ensure_workspace_deps", MagicMock())
    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", MagicMock(side_effect=RuntimeError("old path reached")))
    prepare_mock = MagicMock(side_effect=RuntimeError("stop after strategy check"))
    monkeypatch.setattr(orch, "_prepare_task_worktree", prepare_mock)

    assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is False

    prepare_mock.assert_called_once()
    assert prepare_mock.call_args.kwargs["strategy"] == "restart_from_main"


def test_execute_task_blocks_immediately_for_block_for_human_strategy(db_conn, tmp_path, monkeypatch):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Use explicit retry strategy", "none")
    feature_id = feature_repo.create(run_id, "Retry continuity", "medium")
    task_id = task_repo.create(run_id, feature_id, "Needs human", "implementation", "medium")
    task_repo.update_status(task_id, "ready")
    previous_attempt_id = attempt_repo.create(run_id, task_id, route="executor")
    attempt_repo.update_outcome(previous_attempt_id, "failed")
    attempt_repo.update_retry_strategy(previous_attempt_id, "block_for_human", "Recovery is ambiguous.")

    config = Config(
        {
            "db_path": ":memory:",
            "logs_dir": str(tmp_path / "logs"),
            "worktrees_dir": str(tmp_path / "worktrees"),
        }
    )
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    prepare_mock = MagicMock()
    monkeypatch.setattr(orch, "_prepare_task_worktree", prepare_mock)

    assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is False

    assert task_repo.get(task_id)["status"] == "blocked"
    attempts = attempt_repo.get_by_run(run_id)
    current_attempt = attempts[-1]
    assert current_attempt["outcome"] == "abandoned"
    assert current_attempt["retry_strategy"] == "block_for_human"
    prepare_mock.assert_not_called()
