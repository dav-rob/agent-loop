from typing import Final


GOAL_TYPES: Final[tuple[str, ...]] = (
    "prototype",
    "extend",
    "refine",
    "repair",
    "harden",
    "investigate",
)


def validate_goal_type(goal_type: str) -> str:
    normalized = (goal_type or "").strip().lower()
    if normalized not in GOAL_TYPES:
        raise ValueError(f"Invalid goal type: {goal_type}")
    return normalized
