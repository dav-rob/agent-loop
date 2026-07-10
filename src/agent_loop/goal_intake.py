import json
from dataclasses import dataclass
from typing import Callable

from agent_loop.goal_types import GOAL_TYPES, validate_goal_type


@dataclass(frozen=True)
class GoalTypeInference:
    goal_type: str
    rationale: str


def fallback_goal_type(is_first_goal: bool, reason: str = "") -> GoalTypeInference:
    goal_type = "prototype" if is_first_goal else "extend"
    if is_first_goal:
        rationale = "This is the first goal in the repository, so the team will produce an assessable first version quickly."
    else:
        rationale = "The inferred goal type was unsupported, so the team will extend the existing project while preserving what works."
    if reason:
        rationale = f"{rationale} ({reason})"
    return GoalTypeInference(goal_type, rationale)


def parse_goal_type_inference(output: str, is_first_goal: bool) -> GoalTypeInference:
    try:
        data = json.loads((output or "").strip())
        goal_type = validate_goal_type(data.get("goal_type", ""))
        rationale = data.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("Missing goal type rationale")
        return GoalTypeInference(goal_type, rationale.strip())
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return fallback_goal_type(is_first_goal, f"Inference output was unsupported: {exc}")


def goal_type_inference_prompt(goal: str) -> str:
    descriptions = {
        "prototype": "produce a runnable first version of an app, feature, or risky idea quickly",
        "extend": "add useful capability while preserving existing behavior",
        "refine": "improve behavior, usability, or product focus after feedback",
        "repair": "correct a defect or regression without unnecessary scope growth",
        "harden": "raise security, reliability, testing, performance, or architecture standards deliberately",
        "investigate": "answer a question through evidence, diagnosis, or research",
    }
    choices = "\n".join(f"- {name}: {descriptions[name]}" for name in GOAL_TYPES)
    return f"""You are inferring the operating mode for a software development goal.

Goal:
{goal}

Choose exactly one goal_type:
{choices}

These are peer operating modes, not maturity levels. A mature project can use prototype for a new experimental feature.

Return ONLY JSON:
{{"goal_type":"prototype","rationale":"One short user-facing sentence explaining why."}}"""


def confirm_goal_type(
    inference: GoalTypeInference,
    input_func: Callable[[str], str] | None = None,
) -> GoalTypeInference:
    input_func = input_func or input
    print(f"\nI inferred this as a '{inference.goal_type}' goal: {inference.rationale}")
    try:
        answer = input_func("Proceed with this goal type? [Y/change]: ")
    except (EOFError, OSError, StopIteration):
        print("Input ended; accepting the inferred goal type.")
        return inference
    normalized = answer.strip().lower() if isinstance(answer, str) else ""
    if normalized not in {"change", "c", "no", "n"}:
        return inference

    replacement = input_func(f"Goal type ({', '.join(GOAL_TYPES)}): ")
    replacement = validate_goal_type(replacement)
    return GoalTypeInference(
        replacement,
        f"The user corrected the inferred goal type from {inference.goal_type} to {replacement}.",
    )
