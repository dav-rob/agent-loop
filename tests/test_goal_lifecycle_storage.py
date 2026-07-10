from pathlib import Path

import pytest

from agent_loop.database import get_connection, migrate
from agent_loop.goal_types import GOAL_TYPES
from agent_loop.repositories import (
    GoalDeliveryRepository,
    RecommendationRepository,
    RunRepository,
)


@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()


def test_run_repository_persists_validated_goal_type_and_rationale(db_conn):
    repo = RunRepository(db_conn)

    run_id = repo.create(
        "Try a new search interface",
        "spec",
        goal_type="prototype",
        goal_type_rationale="This is a first runnable version of a new feature.",
    )

    run = repo.get(run_id)
    assert run["goal_type"] == "prototype"
    assert run["goal_type_rationale"] == "This is a first runnable version of a new feature."
    assert set(GOAL_TYPES) == {"prototype", "extend", "refine", "repair", "harden", "investigate"}

    repo.update_goal_type(run_id, "repair", "The user is reporting a concrete regression.")
    assert repo.get(run_id)["goal_type"] == "repair"

    with pytest.raises(ValueError, match="Invalid goal type"):
        repo.update_goal_type(run_id, "mature", "Not an operating mode.")


def test_existing_callers_default_new_goals_to_prototype(db_conn):
    run_id = RunRepository(db_conn).create("Build something", "none")

    run = RunRepository(db_conn).get(run_id)
    assert run["goal_type"] == "prototype"
    assert run["goal_type_rationale"] is None


def test_latest_completed_goal_ignores_active_and_failed_goals(db_conn):
    repo = RunRepository(db_conn)
    first = repo.create("First", "none")
    repo.update_status(first, "complete", force=True)
    second = repo.create("Second", "none")
    repo.update_status(second, "failed", force=True)
    repo.create("Third", "none")

    assert repo.get_latest_completed()["id"] == first


def test_recommendations_validate_and_follow_selection_lifecycle(db_conn):
    run_repo = RunRepository(db_conn)
    recommendations = RecommendationRepository(db_conn)
    source_run = run_repo.create("Build prototype", "none")
    run_repo.update_status(source_run, "complete", force=True)
    adopting_run = run_repo.create("Improve prototype", "none", goal_type="refine")

    recommendation_id = recommendations.create(
        run_id=source_run,
        category="usability",
        priority="high",
        title="Clarify the primary action",
        rationale="The prototype works but the next action is ambiguous.",
        evidence="Final review observed two equally prominent buttons.",
    )

    recommendation = recommendations.get(recommendation_id)
    assert recommendation["status"] == "open"
    assert recommendations.get_open_by_run(source_run)[0]["id"] == recommendation_id

    recommendations.select([recommendation_id], adopting_run)
    selected = recommendations.get(recommendation_id)
    assert selected["status"] == "selected"
    assert selected["adopting_run_id"] == adopting_run

    recommendations.resolve_for_adopting_run(adopting_run)
    assert recommendations.get(recommendation_id)["status"] == "resolved"

    with pytest.raises(ValueError, match="Invalid recommendation category"):
        recommendations.create(
            run_id=source_run,
            category="polish",
            priority="low",
            title="Invalid category",
            rationale="Not allowed.",
            evidence="None.",
        )

    with pytest.raises(ValueError, match="Invalid recommendation priority"):
        recommendations.create(
            run_id=source_run,
            category="maintenance",
            priority="urgent",
            title="Invalid priority",
            rationale="Not allowed.",
            evidence="None.",
        )


def test_recommendation_selection_is_atomic(db_conn):
    run_repo = RunRepository(db_conn)
    recommendations = RecommendationRepository(db_conn)
    source_run = run_repo.create("Source", "none")
    adopting_run = run_repo.create("Adopter", "none", goal_type="extend")
    recommendation_id = recommendations.create(
        run_id=source_run,
        category="testing",
        priority="medium",
        title="Add browser coverage",
        rationale="The happy path only has unit coverage.",
        evidence="No browser test was recorded.",
    )

    with pytest.raises(ValueError, match="open recommendations"):
        recommendations.select([recommendation_id, 99999], adopting_run)

    assert recommendations.get(recommendation_id)["status"] == "open"


def test_goal_delivery_repository_upserts_one_structured_record(db_conn):
    run_id = RunRepository(db_conn).create("Deliver prototype", "none")
    deliveries = GoalDeliveryRepository(db_conn)

    deliveries.upsert(
        run_id=run_id,
        summary="Built a runnable dashboard.",
        launch_command="npm run dev",
        local_url="http://localhost:5173",
        verification=["Unit tests passed", "Homepage returned HTTP 200"],
        known_limitations=["No authentication yet"],
        launch_evidence="HTTP 200 recorded in final review logs.",
    )
    deliveries.upsert(
        run_id=run_id,
        summary="Built and verified a runnable dashboard.",
        launch_command="npm run dev",
        local_url="http://localhost:5173",
        verification=["Full test suite passed", "Homepage returned HTTP 200"],
        known_limitations=[],
        launch_evidence="HTTP 200 recorded in final review logs.",
    )

    delivery = deliveries.get_by_run(run_id)
    assert delivery["summary"] == "Built and verified a runnable dashboard."
    assert delivery["verification"] == ["Full test suite passed", "Homepage returned HTTP 200"]
    assert delivery["known_limitations"] == []
    count = db_conn.execute("SELECT COUNT(*) FROM goal_deliveries WHERE run_id = ?", (run_id,)).fetchone()[0]
    assert count == 1
