import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_loop.adapters import AttemptResult
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.orchestrator import Orchestrator, parse_review_response
from agent_loop.repositories import (
    FeatureRepository,
    RunRepository,
    TaskRepository,
    VerificationEvidenceRepository,
    ReviewRepository,
)


@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()


def test_verification_evidence_repository_stores_commands_as_inert_text(db_conn):
    runs = RunRepository(db_conn)
    features = FeatureRepository(db_conn)
    tasks = TaskRepository(db_conn)
    run_id = runs.create("Build dashboard", "none")
    feature_id = features.create(run_id, "Dashboard", "low")
    task_id = tasks.create(run_id, feature_id, "Build dashboard", "implementation", "low")

    evidence = VerificationEvidenceRepository(db_conn)
    evidence.create(
        run_id=run_id,
        task_id=task_id,
        phase="task_review",
        requirement="The dashboard tests pass",
        status="passed",
        command="touch /tmp/this-must-remain-inert",
        exit_status=0,
        summary="Reviewer reported a passing check.",
        evidence_paths=["/tmp/reviewer.log"],
    )

    row = evidence.get_by_run(run_id)[0]
    assert row["command"] == "touch /tmp/this-must-remain-inert"
    assert row["status"] == "passed"
    assert row["evidence_paths"] == ["/tmp/reviewer.log"]


def test_review_parser_accepts_verification_evidence_and_blocker_kind():
    parsed = parse_review_response(
        json.dumps(
            {
                "decision": "approved",
                "findings": "The focused checks pass.",
                "blocker_kind": None,
                "recommendations": [],
                "verification_evidence": [
                    {
                        "requirement": "The dashboard tests pass",
                        "status": "passed",
                        "command": "venv/bin/python -m pytest tests/test_dashboard.py",
                        "exit_status": 0,
                        "summary": "Seven tests passed.",
                        "evidence_paths": ["/tmp/reviewer.log"],
                    }
                ],
            }
        )
    )

    assert parsed.blocker_kind is None
    assert len(parsed.verification_evidence) == 1
    assert parsed.verification_evidence[0].status == "passed"


def test_task_reviewer_owns_environment_and_persists_independent_evidence(db_conn, tmp_path):
    runs = RunRepository(db_conn)
    features = FeatureRepository(db_conn)
    tasks = TaskRepository(db_conn)
    run_id = runs.create(
        "Build dashboard",
        "none",
        goal_type="prototype",
        goal_type_rationale="The user approved a prototype.",
    )
    feature_id = features.create(run_id, "Dashboard", "low")
    task_id = tasks.create(
        run_id,
        feature_id,
        "Build dashboard",
        "implementation",
        "low",
        verification_requirements=["The focused dashboard tests pass"],
    )
    workspace = tmp_path / "worktree"
    workspace.mkdir()

    routed = MagicMock()
    routed.provider = "codex"
    routed.model = "review-model"
    routed.result = AttemptResult(
        True,
        0,
        json.dumps(
            {
                "decision": "approved",
                "findings": "The reviewer created a local environment and the checks pass.",
                "blocker_kind": None,
                "recommendations": [],
                "verification_evidence": [
                    {
                        "requirement": "The focused dashboard tests pass",
                        "status": "passed",
                        "command": "venv/bin/python -m pytest -q",
                        "exit_status": 0,
                        "summary": "Seven tests passed.",
                        "evidence_paths": ["review.log"],
                    }
                ],
            }
        ),
        "",
    )
    orch = Orchestrator(
        db_conn,
        Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router_cls.return_value.run.return_value = routed
        decision = orch.run_agent_review(
            run_id,
            "task",
            task_id,
            "Review the dashboard task.",
            workspace_path=workspace,
        )
        prompt = router_cls.return_value.run.call_args.kwargs["prompt"]

    assert decision == "approved"
    assert "The focused dashboard tests pass" in prompt
    assert "create or reuse an ignored worktree-local environment" in prompt.lower()
    rows = VerificationEvidenceRepository(db_conn).get_by_run(run_id)
    assert len(rows) == 1
    assert rows[0]["phase"] == "task_review"
    assert rows[0]["status"] == "passed"


@pytest.mark.parametrize(
    ("result", "expected_finding"),
    [
        (AttemptResult(False, 1, "", "review provider unavailable"), "Review prompt failed"),
        (AttemptResult(True, 0, "not valid review output", ""), "Failed to parse review JSON"),
    ],
)
def test_reviewer_runtime_failure_blocks_instead_of_rejecting_implementation(
    db_conn, tmp_path, result, expected_finding
):
    run_id = RunRepository(db_conn).create("Build dashboard", "none")
    feature_id = FeatureRepository(db_conn).create(run_id, "Dashboard", "low")
    task_id = TaskRepository(db_conn).create(
        run_id,
        feature_id,
        "Build dashboard",
        "implementation",
        "low",
        verification_requirements=["The dashboard tests pass"],
    )
    routed = MagicMock(provider="codex", model="review-model", result=result)
    orch = Orchestrator(
        db_conn,
        Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router_cls.return_value.run.return_value = routed
        decision = orch.run_agent_review(run_id, "task", task_id, "Review task", workspace_path=tmp_path)

    assert decision == "block"
    review = ReviewRepository(db_conn).get_by_run(run_id)[0]
    assert review["decision"] == "block"
    assert expected_finding in review["findings"]
