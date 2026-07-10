from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_loop.adapters import AttemptResult
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.orchestrator import Orchestrator
from agent_loop.repositories import RunRepository
from agent_loop.review_policy import goal_review_policy


@pytest.mark.parametrize(
    ("goal_type", "expected"),
    [
        ("prototype", "cannot build or run"),
        ("extend", "requested capability is absent"),
        ("refine", "behavioral or usability outcome"),
        ("repair", "target defect remains"),
        ("harden", "stricter security, reliability, testing, performance, and architecture"),
        ("investigate", "quality of the evidence"),
    ],
)
def test_goal_review_policy_defines_each_operating_mode(goal_type, expected):
    policy = goal_review_policy(goal_type)

    assert f"Goal type: {goal_type}" in policy
    assert expected in policy


def test_prototype_review_policy_sends_non_blocking_quality_findings_to_recommendations():
    policy = goal_review_policy("prototype")

    assert "Security, architecture, portability, dependency upgrades, edge cases, and polish" in policy
    assert "recommendations" in policy
    assert "must not cause another retry" in policy


def test_agent_review_prompt_includes_confirmed_goal_type_policy(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    run_id = RunRepository(conn).create(
        "Audit session handling",
        "none",
        goal_type="harden",
        goal_type_rationale="The user deliberately requested security hardening.",
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
            output=(
                '{"decision":"approved","findings":"The hardening criteria are met.",'
                '"recommendations":[],"delivery":{"kind":"software",'
                '"summary":"Hardened session handling.","launch_command":"python app.py",'
                '"local_url":null,"verification":["Security tests passed"],'
                '"known_limitations":[],"launch_evidence":null,'
                '"investigation_conclusion":null}}'
            ),
            error="",
        ),
    )

    with patch("agent_loop.orchestrator.ModelRouter") as router_cls:
        router_cls.return_value.run.return_value = routed
        decision = orchestrator.run_agent_review(run_id, "final", run_id, "Review the completed goal.")

    assert decision == "approved"
    prompt = router_cls.return_value.run.call_args.kwargs["prompt"]
    assert "Goal type: harden" in prompt
    assert "deliberately requested security hardening" in prompt
    assert "stricter security, reliability, testing, performance, and architecture" in prompt
    conn.close()


def test_investigate_review_does_not_require_runnable_software():
    policy = goal_review_policy("investigate")

    assert "Runnable software is not required" in policy
    assert "stated question was answered" in policy
