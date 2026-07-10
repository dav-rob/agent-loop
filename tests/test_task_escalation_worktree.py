from pathlib import Path

import pytest

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.orchestrator import Orchestrator
from agent_loop.repositories import FeatureRepository, RunRepository, TaskRepository


@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()


def test_task_escalation_review_runs_in_task_worktree(db_conn, tmp_path, monkeypatch):
    run_id = RunRepository(db_conn).create("Recover a difficult task", "none")
    feature_id = FeatureRepository(db_conn).create(run_id, "Recovery", "medium")
    task_id = TaskRepository(db_conn).create(
        run_id,
        feature_id,
        "Repair the implementation",
        "implementation",
        "medium",
    )
    orchestrator = Orchestrator(
        db_conn,
        Config(
            {
                "db_path": ":memory:",
                "logs_dir": str(tmp_path / "logs"),
                "worktrees_dir": str(tmp_path / "worktrees"),
            }
        ),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )
    captured = {}

    def fake_run_agent_review(
        run_id_arg,
        subject_type,
        subject_id,
        prompt,
        attempt_id=None,
        workspace_path=None,
    ):
        captured["run_id"] = run_id_arg
        captured["subject_type"] = subject_type
        captured["subject_id"] = subject_id
        captured["workspace_path"] = workspace_path
        return "follow_up"

    monkeypatch.setattr(orchestrator, "run_agent_review", fake_run_agent_review)

    decision = orchestrator.run_task_escalation(
        run_id,
        task_id,
        attempt_count=3,
        reason="Verification is still failing.",
    )

    assert decision == "follow_up"
    assert captured == {
        "run_id": run_id,
        "subject_type": "task_escalation",
        "subject_id": task_id,
        "workspace_path": orchestrator._task_worktree_dir(run_id, task_id),
    }
