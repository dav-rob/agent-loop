import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_loop.cli import handle_start
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.goal_intake import (
    GoalTypeInference,
    confirm_goal_type,
    parse_goal_type_inference,
)
from agent_loop.intake import infer_goal_type
from agent_loop.repositories import RunRepository
from agent_loop.views import render_plan_md, render_progress_md


def test_first_goal_inference_defaults_to_prototype_without_model_call(tmp_path):
    config = Config({"state_dir": str(tmp_path / ".agent-loop")})

    with patch("agent_loop.intake._call_intake_model") as model_call:
        inference = infer_goal_type("Build a weather dashboard", config, is_first_goal=True)

    assert inference == GoalTypeInference(
        goal_type="prototype",
        rationale="This is the first goal in the repository, so the team will produce an assessable first version quickly.",
    )
    model_call.assert_not_called()


def test_later_goal_inference_parses_structured_model_output(tmp_path):
    config = Config({"state_dir": str(tmp_path / ".agent-loop")})
    result = MagicMock(
        success=True,
        output='{"goal_type":"repair","rationale":"The user reported a concrete regression in saved searches."}',
    )

    with patch("agent_loop.intake._call_intake_model", return_value=result):
        inference = infer_goal_type("Saved searches disappeared after upgrade", config, is_first_goal=False)

    assert inference.goal_type == "repair"
    assert "concrete regression" in inference.rationale


def test_invalid_later_goal_inference_falls_back_to_extend():
    inference = parse_goal_type_inference(
        '{"goal_type":"optimize","rationale":"Make it better."}',
        is_first_goal=False,
    )

    assert inference.goal_type == "extend"
    assert "unsupported" in inference.rationale.lower()


def test_interactive_confirmation_accepts_or_changes_inferred_type():
    inferred = GoalTypeInference("prototype", "This is a new experimental feature.")

    approved = confirm_goal_type(inferred, input_func=lambda _prompt: "yes")
    answers = iter(["change", "harden"])
    changed = confirm_goal_type(inferred, input_func=lambda prompt: next(answers))

    assert approved == inferred
    assert changed.goal_type == "harden"
    assert changed.rationale == "The user corrected the inferred goal type from prototype to harden."


def test_non_interactive_start_persists_automatically_accepted_goal_type(tmp_path):
    config = Config(
        {
            "state_dir": str(tmp_path / ".agent-loop"),
            "logs_dir": str(tmp_path / ".agent-loop" / "logs"),
        }
    )
    args = argparse.Namespace(
        non_interactive=True,
        goal="Build a first runnable dashboard",
        intake="none",
        unattended_policy="reject",
        recommendations=None,
        ui=False,
        no_ui=False,
        no_spec_review=False,
    )

    with patch("agent_loop.cli.Orchestrator") as orchestrator_cls:
        orchestrator_cls.return_value.plan_run.return_value = False
        handle_start(args, config)

    conn = get_connection(config.db_path)
    migrate(conn)
    run = RunRepository(conn).list_all()[0]
    conn.close()
    assert run["goal_type"] == "prototype"
    assert "first goal" in run["goal_type_rationale"].lower()


def test_goal_type_and_rationale_render_in_plan_progress_and_status_data(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    run_id = RunRepository(conn).create(
        "Improve navigation",
        "none",
        goal_type="refine",
        goal_type_rationale="The user is focusing usability after feedback.",
    )
    plan_path = tmp_path / "plan.md"
    progress_path = tmp_path / "progress.md"

    render_plan_md(conn, run_id, plan_path)
    render_progress_md(conn, run_id, progress_path)

    assert "**Goal type:** refine" in plan_path.read_text()
    assert "The user is focusing usability after feedback." in plan_path.read_text()
    assert "Goal Type: refine" in progress_path.read_text()
    conn.close()
