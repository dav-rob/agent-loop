from pathlib import Path
import sqlite3
from typing import Optional
from agent_loop.repositories import (
    RunRepository,
    FeatureRepository,
    TaskRepository,
    AttemptRepository,
    TestRunRepository,
    ProviderStateRepository
)

def render_plan_md(conn: sqlite3.Connection, run_id: int, dest_path: Path) -> None:
    run = RunRepository(conn).get(run_id)
    if not run:
        return

    features = FeatureRepository(conn).get_by_run(run_id)
    tasks = TaskRepository(conn).get_by_run(run_id)

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
    lines.append("> [!NOTE]")
    lines.append("> This file is a human-readable summary. Full task metadata, dependencies, attempts, and evidence are stored in the agent-loop SQLite database. Run `agent-loop plan --details` to inspect them.")
    lines.append("")
    
    # Calculate overall risk
    risk_levels = [f["risk"] for f in features] + [t["risk"] for t in tasks]
    overall_risk = "low"
    if "high" in risk_levels:
        overall_risk = "high"
    elif "medium" in risk_levels:
        overall_risk = "medium"
    
    lines.append(f"## Risk Level: {overall_risk.upper()}")
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

    dest_path.write_text("\n".join(lines) + "\n")


def render_progress_md(conn: sqlite3.Connection, run_id: int, dest_path: Path) -> None:
    run = RunRepository(conn).get(run_id)
    if not run:
        return

    features = FeatureRepository(conn).get_by_run(run_id)
    tasks = TaskRepository(conn).get_by_run(run_id)
    attempts = AttemptRepository(conn).get_by_run(run_id)
    test_runs = TestRunRepository(conn).get_by_run(run_id)

    # Provider states
    cursor = conn.cursor()
    cursor.execute("SELECT provider, availability, quota_limit_reset FROM provider_state;")
    provider_rows = cursor.fetchall()

    lines = []
    lines.append("# progress.md")
    lines.append("")
    lines.append("Use this file to monitor progress as the agent loops through tasks to achieve its goal.")
    lines.append("")
    lines.append("## Goal")
    lines.append("")
    lines.append(run["goal"])
    lines.append("")
    lines.append("## Current status")
    lines.append("")
    lines.append(f"Run Status: {run['status']}")
    lines.append("")

    # Active attempts / current work
    active_attempts = [a for a in attempts if a["outcome"] == "running"]
    lines.append("### Active Work")
    if not active_attempts:
        lines.append("No active task attempts.")
    else:
        for attempt in active_attempts:
            # find task name
            task_name = "Unknown Task"
            for t in tasks:
                if t["id"] == attempt["task_id"]:
                    task_name = t["name"]
                    break
            lines.append(f"- Task: **{task_name}** (Route: {attempt['route']}, Model: {attempt['model']}, Logs: `{attempt['logs_path']}`)")
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

    dest_path.write_text("\n".join(lines) + "\n")
