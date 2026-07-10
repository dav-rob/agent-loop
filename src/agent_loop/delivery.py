from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any, Optional

from agent_loop.repositories import GoalDeliveryRepository, RecommendationRepository, RunRepository


DELIVERY_KINDS = {"web", "software", "investigation"}


@dataclass(frozen=True)
class DeliveryDetails:
    kind: str
    summary: str
    launch_command: Optional[str]
    local_url: Optional[str]
    verification: tuple[str, ...]
    known_limitations: tuple[str, ...]
    launch_evidence: Optional[str]
    investigation_conclusion: Optional[str]


def _optional_text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _text_items(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def parse_delivery_details(data: Any) -> Optional[DeliveryDetails]:
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind", "")).strip().lower()
    summary = _optional_text(data.get("summary"))
    if kind not in DELIVERY_KINDS or not summary:
        return None
    return DeliveryDetails(
        kind=kind,
        summary=summary,
        launch_command=_optional_text(data.get("launch_command")),
        local_url=_optional_text(data.get("local_url")),
        verification=_text_items(data.get("verification")),
        known_limitations=_text_items(data.get("known_limitations")),
        launch_evidence=_optional_text(data.get("launch_evidence")),
        investigation_conclusion=_optional_text(data.get("investigation_conclusion")),
    )


def validate_delivery_details(details: Optional[DeliveryDetails], goal_type: str) -> Optional[str]:
    if details is None:
        return "Final review did not provide structured delivery metadata."
    missing = []
    if not details.verification:
        missing.append("verification evidence")
    if details.kind == "web":
        if not details.launch_command:
            missing.append("launch command")
        if not details.local_url:
            missing.append("local URL")
        if not details.launch_evidence:
            missing.append("launch evidence")
    if goal_type == "investigate" and not details.investigation_conclusion:
        missing.append("investigation conclusion")
    if missing:
        return "Final delivery is missing required " + ", ".join(missing) + "."
    return None


def render_delivery_report(conn: sqlite3.Connection, run_id: int, dest_path: Path) -> Path:
    run = RunRepository(conn).get(run_id)
    delivery = GoalDeliveryRepository(conn).get_by_run(run_id)
    if not run or not delivery:
        raise ValueError(f"No delivery record exists for goal {run_id}.")
    recommendations = RecommendationRepository(conn).get_by_run(run_id)

    lines = [
        "# Delivery Report",
        "",
        f"**Goal type:** {run['goal_type']}",
        "",
    ]
    if run["goal_type"] == "investigate":
        lines.extend(["## What Was Learned", "", delivery["summary"], ""])
        if delivery.get("investigation_conclusion"):
            lines.extend(["## Conclusion", "", delivery["investigation_conclusion"], ""])
    else:
        lines.extend(["## What Was Built", "", delivery["summary"], ""])

    if delivery.get("launch_command") or delivery.get("local_url"):
        lines.extend(["## Run Or Open", ""])
        if delivery.get("launch_command"):
            lines.append(f"- Run: `{delivery['launch_command']}`")
        if delivery.get("local_url"):
            lines.append(f"- Open: {delivery['local_url']}")
        if delivery.get("launch_evidence"):
            lines.append(f"- Launch evidence: {delivery['launch_evidence']}")
        lines.append("")

    lines.extend(["## Verified", ""])
    lines.extend(f"- {item}" for item in delivery["verification"])
    lines.append("")
    lines.extend(["## Known Limitations", ""])
    if delivery["known_limitations"]:
        lines.extend(f"- {item}" for item in delivery["known_limitations"])
    else:
        lines.append("- None recorded.")
    lines.append("")

    for priority in ("high", "medium", "low"):
        items = [item for item in recommendations if item["priority"] == priority and item["status"] != "declined"]
        lines.extend([f"## {priority.title()} Recommendations", ""])
        if items:
            for item in items:
                lines.append(f"- **{item['title']}** ({item['category']}): {item['rationale']}")
        else:
            lines.append("- None.")
        lines.append("")

    lines.extend(
        [
            "## What Next",
            "",
            "Start the next goal from selected recommendations or describe fresh feedback from using this delivery.",
            "",
        ]
    )
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text("\n".join(lines), encoding="utf-8")
    return dest_path
