from pathlib import Path
import re
import sqlite3
from typing import Optional
from agent_loop.repositories import (
    RunRepository,
    FeatureRepository,
    TaskRepository,
    AttemptRepository,
    TestRunRepository,
    ProviderStateRepository,
    TestMigrationRepository,
    HandoverRepository,
    LifecycleEventRepository,
    RecommendationRepository,
)


def _slugify(value: str, max_chars: int = 20) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_chars].strip("-") or "task"


def _append_field(lines: list[str], label: str, value: Optional[str]) -> None:
    if value:
        lines.append(f"- **{label}:** {value}")


def _compact_text(value: Optional[str], max_chars: int = 180) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def render_task_handover_md(conn: sqlite3.Connection, run_id: int, task_id: int, dest_dir: Path) -> Path:
    run = RunRepository(conn).get(run_id)
    task = TaskRepository(conn).get(task_id)
    if not run or not task:
        raise ValueError(f"Cannot render handover for run {run_id}, task {task_id}.")

    attempts = [a for a in AttemptRepository(conn).get_by_run(run_id) if a["task_id"] == task_id]
    entries = HandoverRepository(conn).get_by_task(run_id, task_id)
    attempt_order = {attempt["id"]: idx + 1 for idx, attempt in enumerate(attempts)}
    entries_by_attempt = {}
    for entry in entries:
        entries_by_attempt.setdefault(entry["attempt_id"], []).append(entry)

    lines = [
        f"# Goal {run_id} / Task {task_id}: {task['name']}",
        "",
        "## Task Contract",
        "",
        f"- **Goal:** {run['goal']}",
        f"- **Role:** {task['role']}",
        f"- **Risk:** {task['risk']}",
    ]
    if task.get("scope"):
        lines.append(f"- **Scope:** `{task['scope']}`")
    if task.get("required_verification"):
        lines.append(f"- **Required verification:** `{task['required_verification']}`")
    lines.extend(
        [
            "",
            "## Enough To Proceed",
            "",
            "- Required verification has passed or a concrete reason is recorded.",
            "- Blocking correctness, security, data-loss, or regression issues in this task scope are addressed.",
            "- Non-blocking polish and future improvements are captured as follow-ups instead of retrying the same task.",
            "- Generated, dependency, runtime, and local state files have been reviewed for `.gitignore` coverage.",
            "- Reviewer should reject only issues that block this task now; otherwise use follow-up.",
            "",
        ]
    )

    if not entries:
        lines.append("No handover entries recorded yet.")
    else:
        ordered_attempt_ids = sorted(
            entries_by_attempt.keys(),
            key=lambda val: attempt_order.get(val, 10**9 if val is None else val),
        )
        for attempt_id in ordered_attempt_ids:
            attempt_number = attempt_order.get(attempt_id, attempt_id or "unknown")
            lines.append(f"## Attempt {attempt_number}")
            lines.append("")
            attempt_meta = next((attempt for attempt in attempts if attempt["id"] == attempt_id), None)
            if attempt_meta:
                _append_field(lines, "Retry strategy", attempt_meta.get("retry_strategy"))
                _append_field(lines, "Retry strategy reason", attempt_meta.get("retry_strategy_reason"))
                _append_field(lines, "Start SHA", attempt_meta.get("start_sha"))
                _append_field(lines, "Base SHA", attempt_meta.get("base_sha"))
                if any(
                    attempt_meta.get(key)
                    for key in ("retry_strategy", "retry_strategy_reason", "start_sha", "base_sha")
                ):
                    lines.append("")
            for phase in ("executor", "reviewer"):
                phase_entries = [entry for entry in entries_by_attempt[attempt_id] if entry["phase"] == phase]
                if not phase_entries:
                    continue
                lines.append(f"### {phase.title()}")
                lines.append("")
                for entry in phase_entries:
                    _append_field(lines, "Route", entry.get("actor_route"))
                    _append_field(lines, "Decision", entry.get("decision"))
                    _append_field(lines, "Severity", entry.get("severity"))
                    _append_field(lines, "Commit", entry.get("commit_sha"))
                    _append_field(lines, "Verification", entry.get("verification_status"))
                    _append_field(lines, "Summary", entry.get("summary"))
                    _append_field(lines, "Blocking findings", entry.get("blocking_findings"))
                    _append_field(lines, "Follow-ups", entry.get("followups"))
                    if entry.get("evidence_paths"):
                        lines.append("- **Evidence:**")
                        for evidence_path in entry["evidence_paths"]:
                            lines.append(f"  - `{evidence_path}`")
                    if phase == "executor":
                        lines.append("- **Handover checklist:** `.gitignore`, generated files, runtime files, verification, and known risks reviewed.")
                    else:
                        lines.append("- **Scope threshold:** Reviewer should reject only issues that block this task now; otherwise use follow-up.")
                    lines.append("")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"goal-{run_id}-task-{task_id}-{_slugify(task['name'])}.md"
    dest_path.write_text("\n".join(lines).rstrip() + "\n")
    return dest_path

def render_plan_md(conn: sqlite3.Connection, run_id: int, dest_path: Path) -> None:
    run = RunRepository(conn).get(run_id)
    if not run:
        return

    features = FeatureRepository(conn).get_by_run(run_id)
    tasks = TaskRepository(conn).get_by_run(run_id)
    selected_recommendations = RecommendationRepository(conn).get_selected_for_run(run_id)

    # Group tasks by feature
    tasks_by_feature = {}
    for task in tasks:
        tasks_by_feature.setdefault(task["feature_id"], []).append(task)

    lines = []
    lines.append("# Agent Loop Implementation Plan")
    lines.append("")
    lines.append("## Objective")
    lines.append("")
    lines.append(run["goal"])
    lines.append("")
    lines.append(f"- **Goal type:** {run.get('goal_type', 'prototype')}")
    if run.get("goal_type_rationale"):
        lines.append(f"- **Why:** {run['goal_type_rationale']}")
    lines.append("")
    lines.append("> [!NOTE]")
    lines.append("> This file is a human-readable summary. Full task metadata, dependencies, attempts, and evidence are stored in the agent-loop SQLite database. Run `agent-loop plan --details` to inspect them.")
    lines.append("")
    if selected_recommendations:
        lines.append("## Selected Recommendations")
        lines.append("")
        for item in selected_recommendations:
            lines.append(f"- **#{item['id']} {item['title']}** ({item['priority']}/{item['category']}): {item['rationale']}")
        lines.append("")
    

    
    lines.append("## Features")
    lines.append("")
    
    if not features:
        lines.append("No features defined yet.")
    else:
        for feature in features:
            status_str = f"Status: {feature['review_status']}"
            if feature["outcome"]:
                status_str += f" ({feature['outcome']})"
            
            lines.append(f"### {feature['name']} (Risk: {feature['risk'].upper()}, {status_str})")
            if feature["acceptance_criteria"]:
                lines.append(f"**Acceptance Criteria:** {feature['acceptance_criteria']}")
                lines.append("")
            if feature["dependencies"]:
                deps = ", ".join(feature["dependencies"])
                lines.append(f"*Depends on:* {deps}")
                lines.append("")
            
            feat_tasks = tasks_by_feature.get(feature["id"], [])
            if not feat_tasks:
                lines.append("No tasks defined for this feature.")
            else:
                for task in feat_tasks:
                    checked = "x" if task["status"] == "complete" else " "
                    dep_str = f" (depends on {', '.join(task['dependencies'])})" if task["dependencies"] else ""
                    lines.append(f"- [{checked}] {task['name']}{dep_str}")
                    lines.append(f"  - **Role:** {task['role']} | **Status:** {task['status']} | **Risk:** {task['risk']}")
                    if task["required_verification"]:
                        lines.append(f"  - **Verification:** `{task['required_verification']}`")
            lines.append("")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text("\n".join(lines) + "\n")


def render_progress_md(conn: sqlite3.Connection, run_id: int, dest_path: Path) -> None:
    run = RunRepository(conn).get(run_id)
    if not run:
        return

    features = FeatureRepository(conn).get_by_run(run_id)
    tasks = TaskRepository(conn).get_by_run(run_id)
    attempts = AttemptRepository(conn).get_by_run(run_id)
    test_runs = TestRunRepository(conn).get_by_run(run_id)
    lifecycle_events = LifecycleEventRepository(conn).get_by_run(run_id)

    # Provider states
    cursor = conn.cursor()
    cursor.execute("SELECT provider, availability, quota_limit_reset FROM provider_state;")
    provider_rows = cursor.fetchall()

    lines = []
    
    migrations = TestMigrationRepository(conn).get_by_run(run_id)
    if migrations:
        lines.append("## TEST BASELINE CHANGES")
        lines.append("")
        for m in migrations:
            lines.append(f"- **Old Test:** `{m['old_test_path']}`")
            lines.append(f"  - **Replacement:** `{m['replacement_test_path']}`")
            lines.append(f"  - **Rationale:** {m['rationale']}")
            lines.append(f"  - **Status:** {m['approval_status']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("# progress.md")
    lines.append("")
    lines.append("Use this file to monitor progress as the agent loops through tasks to achieve its goal.")
    lines.append("")
    lines.append("## Goal")
    lines.append("")
    lines.append(run["goal"])
    lines.append("")
    lines.append(f"Goal Type: {run.get('goal_type', 'prototype')}")
    if run.get("goal_type_rationale"):
        lines.append(f"Goal Type Rationale: {run['goal_type_rationale']}")
    lines.append("")
    lines.append("## Current status")
    lines.append("")
    lines.append(f"Run Status: {run['status']}")
    lines.append("")

    # Active attempts / current work
    active_attempts = [a for a in attempts if a["outcome"] == "running"]
    lines.append("### Active Work")
    if not active_attempts:
        running_tasks = [t for t in tasks if t["status"] == "running"]
        if not running_tasks:
            lines.append("No active task attempts.")
        else:
            for task in running_tasks:
                lines.append(f"- Starting task: **{task['name']}** ({task['role']})")
    else:
        for attempt in active_attempts:
            # find task name
            task_name = "Unknown Task"
            for t in tasks:
                if t["id"] == attempt["task_id"]:
                    task_name = t["name"]
                    break
            route = attempt["route"] or "pending"
            provider = attempt["provider"] or "pending"
            model = attempt["model"] or "pending"
            logs_path = attempt["logs_path"] or "pending"
            details = f"Route: {route}, Provider: {provider}, Model: {model}"
            if attempt.get("retry_strategy"):
                details += f", Strategy: {attempt['retry_strategy']}"
                if attempt.get("retry_strategy_reason"):
                    details += f" ({_compact_text(attempt['retry_strategy_reason'], max_chars=90)})"
            details += f", Logs: `{logs_path}`"
            lines.append(f"- Task: **{task_name}** ({details})")
    lines.append("")

    lines.append("### Recent Lifecycle Events")
    if not lifecycle_events:
        lines.append("No lifecycle events recorded yet.")
    else:
        task_names = {task["id"]: task["name"] for task in tasks}
        attempt_order = {attempt["id"]: idx + 1 for idx, attempt in enumerate(attempts)}
        for event in lifecycle_events[-8:]:
            parts = [f"- `{event['event_type']}`"]
            if event.get("task_id"):
                task_label = task_names.get(event["task_id"], f"Task {event['task_id']}")
                parts.append(f"Task: **{task_label}**")
            if event.get("attempt_id"):
                parts.append(f"Attempt {attempt_order.get(event['attempt_id'], event['attempt_id'])}")
            if event.get("actor"):
                parts.append(f"Actor: {event['actor']}")
            summary = _compact_text(event.get("summary"))
            if summary:
                parts.append(summary)
            lines.append(" - ".join(parts))
    lines.append("")

    # Completed outcomes
    completed_tasks = [t for t in tasks if t["status"] == "complete"]
    lines.append("### Completed Outcomes")
    if not completed_tasks:
        lines.append("No tasks completed yet.")
    else:
        for t in completed_tasks:
            lines.append(f"- [x] {t['name']} ({t['role']})")
    lines.append("")

    # Active blockers
    blocked_tasks = [t for t in tasks if t["status"] == "blocked"]
    lines.append("### Active Blockers")
    if not blocked_tasks:
        lines.append("None.")
    else:
        for t in blocked_tasks:
            lines.append(f"- {t['name']} (Blocked)")
    lines.append("")

    # Test runs
    lines.append("### Test Results")
    if not test_runs:
        lines.append("No test runs recorded.")
    else:
        # Show last 5 test runs
        for tr in test_runs[-5:]:
            status_str = "PASSED" if tr["exit_status"] == 0 else f"FAILED (exit: {tr['exit_status']})"
            lines.append(f"- `{tr['command']}` -> {status_str} ({tr['duration_seconds'] or 0.0:.2f}s)")
    lines.append("")

    # Provider states
    lines.append("### Provider State")
    if not provider_rows:
        lines.append("No provider state recorded.")
    else:
        for p_row in provider_rows:
            avail_str = "Available" if p_row[1] else "Unavailable / Quota Limited"
            reset_str = f" | Reset at: {p_row[2]}" if p_row[2] else ""
            lines.append(f"- **{p_row[0]}**: {avail_str}{reset_str}")
    lines.append("")

    # Next step
    lines.append("## Next step")
    lines.append("")
    
    # Determine the next step based on run status and tasks
    if run["status"] == "draft":
        next_action = "Initiating run planning."
    elif run["status"] == "planning":
        next_action = "Generating feature and task DAG."
    elif run["status"] == "awaiting_plan_approval":
        next_action = "Awaiting user approval of the plan."
    elif run["status"] == "running":
        ready_tasks = [t for t in tasks if t["status"] == "ready"]
        running_tasks = [t for t in tasks if t["status"] == "running"]
        pending_tasks = [t for t in tasks if t["status"] == "pending"]
        if running_tasks:
            next_action = f"Executing task(s): {', '.join(t['name'] for t in running_tasks)}."
        elif ready_tasks:
            next_action = f"Scheduling ready task(s): {', '.join(t['name'] for t in ready_tasks)}."
        elif pending_tasks:
            next_action = "Awaiting dependencies of pending tasks."
        else:
            next_action = "Transitioning to reviewing."
    elif run["status"] == "waiting_for_quota":
        next_action = "Waiting for provider quota resets."
    elif run["status"] == "blocked":
        next_action = "Blocked. Requires user intervention."
    elif run["status"] == "reviewing":
        next_action = "Performing feature/final review."
    elif run["status"] == "complete_pending_test_review":
        next_action = "Awaiting approval for test migrations."
    elif run["status"] == "complete":
        next_action = "Goal achieved. Complete."
    elif run["status"] == "failed":
        next_action = "Run failed."
    elif run["status"] == "cancelled":
        next_action = "Run cancelled."
    else:
        next_action = "Determining next steps."
        
    lines.append(next_action)
    lines.append("")

    loop_status = "continue"
    if run["status"] == "complete":
        loop_status = "complete"
    elif run["status"] in {"blocked", "failed", "cancelled"}:
        loop_status = "blocked"
        
    lines.append(f"LOOP_STATUS: {loop_status}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text("\n".join(lines) + "\n")
