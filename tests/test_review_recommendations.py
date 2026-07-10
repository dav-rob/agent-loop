from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_loop.adapters import AttemptResult
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.orchestrator import Orchestrator, parse_review_response
from agent_loop.repositories import (
    FeatureRepository,
    RecommendationRepository,
    RunRepository,
    TaskRepository,
)


@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()


def test_review_parser_accepts_valid_structured_recommendations():
    parsed = parse_review_response(
        """{
          "decision": "follow_up",
          "findings": "The prototype works; improve keyboard navigation later.",
          "recommendations": [{
            "category": "usability",
            "priority": "medium",
            "title": "Add keyboard navigation",
            "rationale": "Keyboard users cannot reach all controls.",
            "evidence": "The final task review found two mouse-only controls."
          }]
        }"""
    )

    assert parsed.decision == "follow_up"
    assert len(parsed.recommendations) == 1
    recommendation = parsed.recommendations[0]
    assert recommendation.category == "usability"
    assert recommendation.priority == "medium"
    assert recommendation.title == "Add keyboard navigation"


def test_review_parser_omits_invalid_recommendation_items_without_losing_decision():
    parsed = parse_review_response(
        '{"decision":"approved","findings":"Works.","recommendations":['
        '{"category":"polish","priority":"urgent","title":"Vague","rationale":"Later","evidence":"None"}'
        "]}"
    )

    assert parsed.decision == "approved"
    assert parsed.recommendations == ()


def test_agent_review_persists_recommendation_with_source_links(db_conn, tmp_path):
    run_id = RunRepository(db_conn).create("Build prototype", "none")
    feature_id = FeatureRepository(db_conn).create(run_id, "Dashboard", "medium")
    task_id = TaskRepository(db_conn).create(run_id, feature_id, "Build dashboard", "implementation", "medium")
    orchestrator = Orchestrator(
        db_conn,
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
            output=(
                '{"decision":"approved","findings":"The task works.","recommendations":['
                '{"category":"testing","priority":"low","title":"Add browser coverage",'
                '"rationale":"Only unit coverage exists.","evidence":"Review logs show no browser run."}]}'
            ),
            error="",
        ),
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router_cls.return_value.run.return_value = routed
        decision = orchestrator.run_agent_review(run_id, "task", task_id, "Review it.")

    assert decision == "approved"
    recommendation = RecommendationRepository(db_conn).get_by_run(run_id)[0]
    assert recommendation["feature_id"] == feature_id
    assert recommendation["task_id"] == task_id
    assert recommendation["source_review_id"] is not None


def test_feature_follow_up_completes_feature_without_creating_task(db_conn, tmp_path):
    run_id = RunRepository(db_conn).create(
        "Build prototype",
        "none",
        goal_type="prototype",
        goal_type_rationale="The user confirmed a prototype goal.",
    )
    feature_id = FeatureRepository(db_conn).create(run_id, "Dashboard", "medium")
    TaskRepository(db_conn).create(run_id, feature_id, "Build dashboard", "implementation", "medium")
    orchestrator = Orchestrator(
        db_conn,
        Config({"logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )
    before = len(TaskRepository(db_conn).get_by_run(run_id))

    orchestrator.apply_feature_review_decision(run_id, feature_id, "follow_up")

    assert FeatureRepository(db_conn).get(feature_id)["review_status"] == "approved"
    assert len(TaskRepository(db_conn).get_by_run(run_id)) == before


def test_prototype_final_follow_up_completes_with_recommendations(db_conn, tmp_path, monkeypatch):
    run_id = RunRepository(db_conn).create(
        "Build prototype", "none", goal_type="prototype", goal_type_rationale="Confirmed prototype."
    )
    harden_id = RunRepository(db_conn).create(
        "Harden prototype", "none", goal_type="harden", goal_type_rationale="Confirmed hardening."
    )
    orchestrator = Orchestrator(
        db_conn,
        Config({"logs_dir": str(tmp_path / "logs")}),
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
    )
    monkeypatch.setattr(orchestrator, "run_agent_review", lambda *args, **kwargs: "follow_up")

    assert orchestrator.run_final_review(run_id) is True
    assert orchestrator.run_final_review(harden_id) is False
