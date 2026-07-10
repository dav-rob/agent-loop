from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_loop.adapters import AttemptResult
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.delivery import (
    DeliveryDetails,
    parse_delivery_details,
    render_delivery_report,
    validate_delivery_details,
)
from agent_loop.orchestrator import Orchestrator
from agent_loop.repositories import GoalDeliveryRepository, RecommendationRepository, RunRepository


def test_web_delivery_requires_launch_command_url_and_evidence():
    incomplete = DeliveryDetails(
        kind="web",
        summary="Built a dashboard.",
        launch_command=None,
        local_url=None,
        verification=("Unit tests passed",),
        known_limitations=("No auth",),
        launch_evidence=None,
        investigation_conclusion=None,
    )

    error = validate_delivery_details(incomplete, "prototype")

    assert "launch command" in error
    assert "local URL" in error
    assert "launch evidence" in error


def test_investigation_delivery_requires_a_conclusion():
    details = DeliveryDetails(
        kind="investigation",
        summary="Compared queue implementations.",
        launch_command=None,
        local_url=None,
        verification=("Benchmarks recorded",),
        known_limitations=(),
        launch_evidence=None,
        investigation_conclusion=None,
    )

    assert "conclusion" in validate_delivery_details(details, "investigate")


def test_parse_delivery_details_accepts_structured_final_review_payload():
    details = parse_delivery_details(
        {
            "kind": "web",
            "summary": "Built a runnable dashboard.",
            "launch_command": "npm run dev",
            "local_url": "http://localhost:5173",
            "verification": ["npm test passed", "GET / returned 200"],
            "known_limitations": ["No authentication"],
            "launch_evidence": "Final review launched the server and recorded HTTP 200.",
            "investigation_conclusion": None,
        }
    )

    assert details.kind == "web"
    assert details.verification == ("npm test passed", "GET / returned 200")


def test_confirmed_final_review_rejects_missing_delivery_metadata(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    run_id = RunRepository(conn).create(
        "Build dashboard",
        "none",
        goal_type="prototype",
        goal_type_rationale="Confirmed prototype.",
    )
    orchestrator = Orchestrator(
        conn,
        Config({"logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )
    routed = MagicMock(
        provider="codex",
        model="gpt-5.6-sol",
        reasoning_level="high",
        result=AttemptResult(
            success=True,
            exit_code=0,
            output='{"decision":"approved","findings":"Looks good.","recommendations":[]}',
            error="",
        ),
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router_cls.return_value.run.return_value = routed
        decision = orchestrator.run_agent_review(run_id, "final", run_id, "Review final delivery.")

    assert decision == "rejected"
    assert GoalDeliveryRepository(conn).get_by_run(run_id) is None
    conn.close()


def test_final_review_persists_delivery_and_renderer_groups_recommendations(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    run_repo = RunRepository(conn)
    run_id = run_repo.create(
        "Build dashboard",
        "none",
        goal_type="prototype",
        goal_type_rationale="Confirmed prototype.",
    )
    recommendations = RecommendationRepository(conn)
    recommendations.create(
        run_id,
        "security",
        "high",
        "Add authentication",
        "Protect personal data before deployment.",
        "Prototype routes are currently public.",
    )
    recommendations.create(
        run_id,
        "maintenance",
        "low",
        "Upgrade chart library",
        "Move to the maintained major version later.",
        "The current major version still works.",
    )
    GoalDeliveryRepository(conn).upsert(
        run_id,
        "Built a runnable dashboard.",
        "npm run dev",
        "http://localhost:5173",
        ["npm test passed", "Homepage returned HTTP 200"],
        ["No authentication"],
        "Final review launched the server and recorded HTTP 200.",
    )
    run_repo.update_status(run_id, "complete", force=True)
    report_path = tmp_path / "delivery-report.md"

    render_delivery_report(conn, run_id, report_path)

    report = report_path.read_text()
    assert "# Delivery Report" in report
    assert "## What Was Built" in report
    assert "`npm run dev`" in report
    assert "http://localhost:5173" in report
    assert "## High Recommendations" in report
    assert "Add authentication" in report
    assert "## Low Recommendations" in report
    assert "Upgrade chart library" in report
    assert "start the next goal" in report.lower()
    conn.close()


def test_config_exposes_delivery_report_under_state_directory(tmp_path):
    config = Config({"state_dir": str(tmp_path / ".agent-loop")})

    assert config.delivery_report_path == (tmp_path / ".agent-loop" / "delivery-report.md").resolve()
