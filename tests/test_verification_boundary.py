import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_loop.adapters import AttemptResult
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


def _routed_result(output: str) -> MagicMock:
    routed = MagicMock()
    routed.provider = "codex"
    routed.model = "test-model"
    routed.reasoning_level = "high"
    routed.result = AttemptResult(True, 0, output, "")
    routed.success = True
    routed.output = output
    routed.error = ""
    return routed


def test_planner_persists_declarative_verification_requirements(db_conn, tmp_path):
    run_id = RunRepository(db_conn).create("Build a dashboard", "none")
    plan = {
        "objective": "Build a dashboard",
        "decisions": [],
        "features": [
            {
                "name": "Dashboard",
                "risk": "low",
                "acceptance_criteria": "The dashboard starts locally",
                "dependencies": [],
            }
        ],
        "tasks": [
            {
                "name": "Build dashboard",
                "feature_name": "Dashboard",
                "role": "implementation",
                "risk": "low",
                "scope": {"files": [], "writes": [], "reads": []},
                "dependencies": [],
                "verification_requirements": [
                    "The dashboard starts locally",
                    "The focused dashboard tests pass",
                ],
            }
        ],
    }
    orch = Orchestrator(
        db_conn,
        Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router_cls.return_value.run.return_value = _routed_result(json.dumps(plan))
        assert orch.plan_run(run_id) is True

    task = TaskRepository(db_conn).get_by_run(run_id)[0]
    assert task["verification_requirements"] == [
        "The dashboard starts locally",
        "The focused dashboard tests pass",
    ]


def test_legacy_planner_command_is_inert_during_task_execution(db_conn, tmp_path, monkeypatch):
    sentinel = tmp_path / "legacy-command-executed"
    worktree = tmp_path / "worktree"
    logs = tmp_path / "logs"

    monkeypatch.setattr(
        "agent_loop.orchestrator.create_worktree",
        lambda repo, path, branch: Path(path).mkdir(parents=True, exist_ok=True),
    )
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda path, message: "abc123")
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo, branch, target: (True, []))
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo, path: None)

    runs = RunRepository(db_conn)
    features = FeatureRepository(db_conn)
    tasks = TaskRepository(db_conn)
    run_id = runs.create("Build dashboard", "none")
    runs.update_status(run_id, "planning")
    runs.update_status(run_id, "running")
    feature_id = features.create(run_id, "Dashboard", "low", "Starts locally")
    task_id = tasks.create(
        run_id,
        feature_id,
        "Build dashboard",
        "implementation",
        "low",
        scope={"writes": []},
        required_verification=f"touch {sentinel}",
    )
    tasks.update_status(task_id, "ready")

    orch = Orchestrator(
        db_conn,
        Config(
            {
                "db_path": ":memory:",
                "logs_dir": str(logs),
                "worktrees_dir": str(tmp_path / "worktrees"),
            }
        ),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    executor = _routed_result("Implemented and checked the dashboard.")
    reviewer = _routed_result(
        json.dumps(
            {
                "decision": "approved",
                "findings": "The dashboard meets the task contract.",
                "recommendations": [],
            }
        )
    )
    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router_cls.return_value.run.side_effect = [executor, reviewer]
        assert orch._execute_task_impl(run_id, tasks.get(task_id)) is True

    assert not sentinel.exists()
    assert tasks.get(task_id)["status"] == "complete"


def test_configured_regression_command_is_inert_after_final_review(db_conn, tmp_path):
    sentinel = tmp_path / "config-command-executed"
    runs = RunRepository(db_conn)
    features = FeatureRepository(db_conn)
    tasks = TaskRepository(db_conn)
    run_id = runs.create("Build dashboard", "autonomous")
    runs.update_status(run_id, "planning")
    runs.update_status(run_id, "running")
    feature_id = features.create(run_id, "Dashboard", "low")
    task_id = tasks.create(run_id, feature_id, "Build dashboard", "implementation", "low")
    tasks.update_status(task_id, "complete", force=True)
    features.update_review_status(feature_id, "approved")
    orch = Orchestrator(
        db_conn,
        Config(
            {
                "db_path": ":memory:",
                "logs_dir": str(tmp_path / "logs"),
                "commands": {"regression_test": f"touch {sentinel}"},
            }
        ),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    with patch.object(orch, "run_final_review", return_value=True):
        orch.run_loop(run_id)

    assert runs.get(run_id)["status"] == "complete"
    assert not sentinel.exists()
