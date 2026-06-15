import argparse
import sys
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import (
    RunRepository,
    FeatureRepository,
    TaskRepository,
    AttemptRepository,
    DecisionRepository,
    TestMigrationRepository,
    ProviderStateRepository
)
from agent_loop.views import render_plan_md, render_progress_md
from agent_loop.orchestrator import Orchestrator

def get_db(config: Config) -> sqlite3.Connection:
    conn = get_connection(config.db_path)
    migrate(conn)
    return conn

def get_target_run_id(run_repo: RunRepository, run_id_arg: Optional[int]) -> int:
    if run_id_arg is not None:
        return run_id_arg
    # Fall back to the most recent run
    runs = run_repo.list_all()
    if not runs:
        print("Error: No runs found in the database. Please start a run first.", file=sys.stderr)
        sys.exit(1)
    return runs[0]["id"]

def handle_start(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)

    if args.non_interactive:
        if not args.goal:
            print("Error: --goal is required for non-interactive mode.", file=sys.stderr)
            sys.exit(1)
        run_id = run_repo.create(
            goal=args.goal,
            intake_mode="non_interactive",
            config_snapshot=config.data
        )
        print(f"Started run {run_id} in non-interactive mode.")
    else:
        # Interactive Wizard
        print("=== Agent Loop Intake Wizard ===")
        goal = args.goal
        if not goal:
            goal = input("Enter your broad goal: ").strip()
            if not goal:
                print("Error: Goal cannot be empty.", file=sys.stderr)
                sys.exit(1)

        print("\nSelect Intake Mode:")
        print("1) Brainstorm (Default)")
        print("2) Brainstorm with UI Lab")
        print("3) Autonomous")
        choice = input("Choice [1-3]: ").strip()

        if choice == "2":
            intake_mode = "brainstorm_ui_lab"
        elif choice == "3":
            intake_mode = "autonomous"
        else:
            intake_mode = "brainstorm"

        run_id = run_repo.create(
            goal=goal,
            intake_mode=intake_mode,
            config_snapshot=config.data
        )
        print(f"\nStarted run {run_id} in {intake_mode} mode.")

    # Render initial Markdown views
    render_plan_md(conn, run_id, Path("plan.md"))
    render_progress_md(conn, run_id, Path("progress.md"))

    # Instantiate Orchestrator and execute planning
    orch = Orchestrator(conn, config)
    print("Planning run...")
    plan_success = orch.plan_run(run_id)
    if plan_success:
        run = run_repo.get(run_id)
        if run["status"] == "running":
            print("Executing tasks...")
            orch.run_loop(run_id)
    else:
        print("Planning failed.")

    conn.close()

def handle_resume(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)
    attempt_repo = AttemptRepository(conn)

    run_id = get_target_run_id(run_repo, args.run_id)
    run = run_repo.get(run_id)
    if not run:
        print(f"Error: Run {run_id} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Resuming run {run_id}...")

    # Recovery: Reconcile active attempts. Interrupted 'running' attempts are marked 'abandoned'
    attempts = attempt_repo.get_by_run(run_id)
    abandoned_count = 0
    for attempt in attempts:
        if attempt["outcome"] == "running":
            # For Milestone 1, we assume no live worker is verified and mark them abandoned
            attempt_repo.update_outcome(attempt["id"], "abandoned")
            abandoned_count += 1

    if abandoned_count > 0:
        print(f"Reconciled state: marked {abandoned_count} interrupted running attempt(s) as abandoned.")

    # If run status was waiting/quota/running, keep it running or reset it to running/planning
    if run["status"] in {"draft", "planning", "awaiting_plan_approval", "running", "waiting_for_quota", "blocked", "reviewing"}:
        # Keep or reset status to running or planning
        if run["status"] == "waiting_for_quota" or run["status"] == "blocked":
            run_repo.update_status(run_id, "running")

    # Regenerate markdown views
    render_plan_md(conn, run_id, Path("plan.md"))
    render_progress_md(conn, run_id, Path("progress.md"))
    print("Markdown views regenerated.")

    # Actually resume orchestration
    orch = Orchestrator(conn, config)
    updated_run = run_repo.get(run_id)
    if updated_run["status"] == "planning":
        print("Resuming planning...")
        orch.plan_run(run_id)
        updated_run = run_repo.get(run_id)
    if updated_run["status"] == "running":
        print("Resuming task execution...")
        orch.run_loop(run_id)

    conn.close()

def handle_status(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)
    run_id = get_target_run_id(run_repo, args.run_id)
    run = run_repo.get(run_id)
    if not run:
        print(f"Error: Run {run_id} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Run ID: {run['id']}")
    print(f"Goal: {run['goal']}")
    print(f"Intake Mode: {run['intake_mode']}")
    print(f"Status: {run['status']}")
    print(f"Created At: {run['created_at']}")
    print(f"Updated At: {run['updated_at']}")
    conn.close()

def handle_plan(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)
    feature_repo = FeatureRepository(conn)
    task_repo = TaskRepository(conn)
    attempt_repo = AttemptRepository(conn)
    decision_repo = DecisionRepository(conn)
    migration_repo = TestMigrationRepository(conn)

    run_id = get_target_run_id(run_repo, args.run_id)
    run = run_repo.get(run_id)
    if not run:
        print(f"Error: Run {run_id} not found.", file=sys.stderr)
        sys.exit(1)

    features = feature_repo.get_by_run(run_id)
    tasks = task_repo.get_by_run(run_id)

    print(f"Plan for Run {run_id}")
    print(f"Objective: {run['goal']}")
    print("-" * 40)

    if not features:
        print("No features defined.")
    else:
        for feat in features:
            print(f"Feature: {feat['name']} (Risk: {feat['risk'].upper()}, Status: {feat['review_status']})")
            if feat["acceptance_criteria"]:
                print(f"  Criteria: {feat['acceptance_criteria']}")
            
            feat_tasks = [t for t in tasks if t["feature_id"] == feat["id"]]
            for t in feat_tasks:
                checked = "[x]" if t["status"] == "complete" else "[ ]"
                print(f"  {checked} Task: {t['name']} (Role: {t['role']}, Status: {t['status']})")

    if args.details:
        print("\n" + "=" * 40)
        print("DETAILED METADATA")
        print("=" * 40)
        
        # Print decisions
        decisions = decision_repo.get_by_run(run_id)
        print("\nDecisions:")
        if not decisions:
            print("  None")
        else:
            for dec in decisions:
                auton = "Autonomous" if dec["is_autonomous"] else "User-Approved"
                print(f"  - [{auton}] {dec['decision_type'].upper()}: {dec['summary']}")
                if dec["details"]:
                    print(f"    Details: {dec['details']}")

        # Print attempts
        attempts = attempt_repo.get_by_run(run_id)
        print("\nTask Attempts:")
        if not attempts:
            print("  None")
        else:
            for att in attempts:
                task_name = next((t["name"] for t in tasks if t["id"] == att["task_id"]), f"Task ID {att['task_id']}")
                print(f"  - Task '{task_name}' Attempt {att['id']}:")
                print(f"    Route: {att['route']} | Provider: {att['provider']} | Model: {att['model']}")
                print(f"    Outcome: {att['outcome']} | Commit: {att['commit_sha']}")
                print(f"    Logs: {att['logs_path']} | Worktree: {att['worktree_path']}")

        # Print test migrations
        migrations = migration_repo.get_by_run(run_id)
        print("\nTest Baseline Migrations:")
        if not migrations:
            print("  None")
        else:
            for mig in migrations:
                print(f"  - Old: {mig['old_test_path']} -> New: {mig['replacement_test_path']}")
                print(f"    Rationale: {mig['rationale']}")
                print(f"    Approval: {mig['approval_status']}")

    conn.close()

def handle_notify(args: argparse.Namespace, config: Config) -> None:
    # MVP test notification helper
    if args.subcommand == "test":
        import os
        webhook_url = os.environ.get(config.webhook_env_var)
        if not webhook_url:
            print(f"Warning: Environment variable '{config.webhook_env_var}' is not set.", file=sys.stderr)
            print("If a webhook URL were configured, a test notification would be sent.", file=sys.stderr)
            return

        print(f"Sending test webhook to configured URL...")
        # Since we're not executing network requests directly in main loop without user approval or adapter stub,
        # we will print a placeholder or run it if needed. For Milestone 1, we can stub/verify.
        # In actual execution, we'd use urllib.request.
        import urllib.request
        import json
        payload = {
            "text": "agent-loop: Test notification from orchestrator."
        }
        try:
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req) as res:
                body = res.read().decode("utf-8")
                print(f"Webhook response: {res.status} {body}")
        except Exception as e:
            print(f"Error sending webhook: {e}", file=sys.stderr)

def main() -> None:
    parser = argparse.ArgumentParser(
        description="agent-loop orchestrator CLI tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--config", type=Path, help="Path to config file")
    
    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    start_parser = subparsers.add_parser("start", help="Start a new run")
    start_parser.add_argument("--non-interactive", action="store_true", help="Run without wizard prompts")
    start_parser.add_argument("--goal", type=str, help="Broad goal for the run")
    start_parser.add_argument("--intake", choices=["brainstorm", "ui_lab", "autonomous"], help="Intake mode")

    # resume
    resume_parser = subparsers.add_parser("resume", help="Resume an existing run")
    resume_parser.add_argument("run_id", type=int, nargs="?", help="ID of the run to resume (defaults to last)")

    # status
    status_parser = subparsers.add_parser("status", help="Get run status")
    status_parser.add_argument("run_id", type=int, nargs="?", help="ID of the run (defaults to last)")

    # plan
    plan_parser = subparsers.add_parser("plan", help="Inspect run plan")
    plan_parser.add_argument("run_id", type=int, nargs="?", help="ID of the run (defaults to last)")
    plan_parser.add_argument("--details", action="store_true", help="Print detailed task and execution metadata")

    # notify
    notify_parser = subparsers.add_parser("notify", help="Notify helpers")
    notify_subparsers = notify_parser.add_subparsers(dest="subcommand", required=True)
    notify_subparsers.add_parser("test", help="Send a test notification")

    args = parser.parse_args()
    config = Config.load(args.config)

    if config.execution_mode == "trusted-host":
        print("Trusted-host execution: commands can access anything available to the current user.")

    if args.command == "start":
        handle_start(args, config)
    elif args.command == "resume":
        handle_resume(args, config)
    elif args.command == "status":
        handle_status(args, config)
    elif args.command == "plan":
        handle_plan(args, config)
    elif args.command == "notify":
        handle_notify(args, config)

if __name__ == "__main__":
    main()
