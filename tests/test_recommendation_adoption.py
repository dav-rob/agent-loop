from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_loop.cli import choose_recommendation_ids, parse_recommendation_ids
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.orchestrator import Orchestrator
from agent_loop.repositories import (
    GoalDeliveryRepository,
    RecommendationRepository,
    RunRepository,
)
from agent_loop.views import render_plan_md


def _recommendation(repo, run_id, title):
    return repo.create(
        run_id,
        "maintenance",
        "medium",
        title,
        f"Rationale for {title}.",
        f"Evidence for {title}.",
    )


def test_parse_and_choose_recommendation_ids_support_none_and_multiple():
    available = [
        {"id": 4, "priority": "high", "category": "security", "title": "Add auth"},
        {"id": 7, "priority": "low", "category": "maintenance", "title": "Upgrade charts"},
    ]

    assert parse_recommendation_ids("4,7") == [4, 7]
    assert choose_recommendation_ids(available, input_func=lambda _prompt: "none") == []
    assert choose_recommendation_ids(available, input_func=lambda _prompt: "4, 7") == [4, 7]


def test_choose_recommendations_rejects_ids_outside_latest_goal():
    available = [{"id": 4, "priority": "high", "category": "security", "title": "Add auth"}]

    try:
        choose_recommendation_ids(available, input_func=lambda _prompt: "4,9")
    except ValueError as exc:
        assert "latest completed goal" in str(exc)
    else:
        raise AssertionError("Expected invalid recommendation selection to fail")


def test_selected_recommendations_are_added_to_planning_context_and_plan_view(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    runs = RunRepository(conn)
    recommendations = RecommendationRepository(conn)
    source_id = runs.create("Prototype", "none")
    runs.update_status(source_id, "complete", force=True)
    recommendation_id = _recommendation(recommendations, source_id, "Add browser coverage")
    adopting_id = runs.create(
        "Improve confidence",
        "none",
        goal_type="harden",
        goal_type_rationale="The user selected testing work.",
    )
    recommendations.select([recommendation_id], adopting_id)
    orchestrator = Orchestrator(
        conn,
        Config({"logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )
    routed = MagicMock(success=False, output="", error="Planner unavailable")

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router_cls.return_value.run.return_value = routed
        assert orchestrator.plan_run(adopting_id) is False

    prompt = router_cls.return_value.run.call_args.kwargs["prompt"]
    assert f"Recommendation {recommendation_id}" in prompt
    assert "Add browser coverage" in prompt
    assert "Evidence for Add browser coverage" in prompt

    render_plan_md(conn, adopting_id, tmp_path / "selected-plan.md")
    assert "Selected Recommendations" in (tmp_path / "selected-plan.md").read_text()
    conn.close()


def test_successful_goal_completion_resolves_selected_recommendations(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    runs = RunRepository(conn)
    recommendations = RecommendationRepository(conn)
    source_id = runs.create("Prototype", "none")
    runs.update_status(source_id, "complete", force=True)
    recommendation_id = _recommendation(recommendations, source_id, "Improve reliability")
    adopting_id = runs.create(
        "Harden app",
        "none",
        goal_type="harden",
        goal_type_rationale="The user selected reliability work.",
    )
    recommendations.select([recommendation_id], adopting_id)
    runs.update_status(adopting_id, "reviewing", force=True)
    GoalDeliveryRepository(conn).upsert(
        adopting_id,
        "Improved reliability.",
        "python app.py",
        None,
        ["pytest passed"],
        [],
    )
    orchestrator = Orchestrator(
        conn,
        Config(
            {
                "state_dir": str(tmp_path / ".agent-loop"),
                "logs_dir": str(tmp_path / ".agent-loop" / "logs"),
            }
        ),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )

    assert orchestrator.complete_goal(adopting_id) is True

    assert runs.get(adopting_id)["status"] == "complete"
    assert recommendations.get(recommendation_id)["status"] == "resolved"
    assert (tmp_path / ".agent-loop" / "delivery-report.md").exists()
    conn.close()


def test_blocked_goal_leaves_adopted_recommendation_selected():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    runs = RunRepository(conn)
    recommendations = RecommendationRepository(conn)
    source_id = runs.create("Prototype", "none")
    recommendation_id = _recommendation(recommendations, source_id, "Add validation")
    adopting_id = runs.create("Repair validation", "none", goal_type="repair")
    recommendations.select([recommendation_id], adopting_id)

    runs.update_status(adopting_id, "blocked", force=True)

    assert recommendations.get(recommendation_id)["status"] == "selected"
    conn.close()
