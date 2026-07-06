import argparse
import json
import select
import shutil
import sys
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

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
from agent_loop.handoffs import validate_handoff
from agent_loop.git_utils import ensure_git_repository, ensure_initial_commit

def describe_goal(goal: str, max_length: int = 70) -> str:
    one_line = " ".join((goal or "").split())
    if len(one_line) <= max_length:
        return one_line
    return one_line[: max_length - 3].rstrip() + "..."

TERMINAL_RUN_STATUSES = {"complete", "failed", "cancelled"}

def _read_ready_tty_lines(stdin: Any, pause_seconds: float = 0.05) -> List[str]:
    try:
        if not stdin.isatty():
            return []
        stdin.fileno()
    except (AttributeError, OSError):
        return []

    lines = []
    while True:
        try:
            readable, _, _ = select.select([stdin], [], [], pause_seconds)
        except (OSError, TypeError, ValueError):
            return lines
        if not readable:
            return lines

        line = stdin.readline()
        if line == "":
            return lines
        lines.append(line.rstrip("\r\n"))

def _read_goal_input(prompt: str) -> str:
    first_line = input(prompt)
    pasted_lines = _read_ready_tty_lines(sys.stdin)
    return "\n".join([first_line, *pasted_lines]).strip()

def _bundled_skills_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "skills"

def _sync_workspace_skills(config: Config) -> None:
    source_dir = _bundled_skills_dir()
    if not source_dir.exists():
        return

    target_dir = config.state_dir / "skills"
    target_dir.mkdir(parents=True, exist_ok=True)
    for source in source_dir.iterdir():
        if source.name.startswith("."):
            continue
        target = target_dir / source.name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        elif source.is_file():
            shutil.copy2(source, target)

def _fallback_brainstorm_questions() -> List[Tuple[str, str]]:
    return [
        ("User / audience", "Who is the main user or audience? (Optional): "),
        ("Success criteria", "What should be true when this goal is complete? (Optional): "),
        ("Constraints / preferences", "Any constraints, preferences, integrations, or style choices? (Optional): "),
        ("Non-goals / risks", "What should be out of scope, risky, or easy to get wrong? (Optional): "),
        ("Verification", "How should the agent verify the result? (Optional): "),
    ]

def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise

# _collect_brainstorming_notes has been replaced by agent_loop.intake

def ensure_workspace(config: Config) -> None:
    try:
        ensure_git_repository(Path.cwd())
        ensure_initial_commit(Path.cwd())
    except Exception:
        pass

    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.logs_dir.mkdir(parents=True, exist_ok=True)
    config.worktrees_dir.mkdir(parents=True, exist_ok=True)
    (config.state_dir / "goals").mkdir(parents=True, exist_ok=True)
    (config.state_dir / "plans").mkdir(parents=True, exist_ok=True)
    (config.state_dir / "specs").mkdir(parents=True, exist_ok=True)
    if not config.learning_path.exists():
        config.learning_path.write_text(
            "# learning.md\n\n"
            "Use this file to record durable facts for this repository's agent-loop goals.\n",
            encoding="utf-8"
        )
    config_path = Path("agent-loop.toml")
    if not config_path.exists():
        Config.write_default_toml(config_path)
    _sync_workspace_skills(config)

def get_db(config: Config) -> sqlite3.Connection:
    ensure_workspace(config)
    conn = get_connection(config.db_path)
    migrate(conn)
    return conn

def get_target_run_id(run_repo: RunRepository, run_id_arg: Optional[int]) -> int:
    if run_id_arg is not None:
        return run_id_arg
    # Fall back to the most recent run
    runs = run_repo.list_all()
    if not runs:
        print("Error: No goals found in the database. Please start a goal first.", file=sys.stderr)
        sys.exit(1)
    return runs[0]["id"]

def _start_args() -> argparse.Namespace:
    return argparse.Namespace(
        non_interactive=False,
        goal=None,
        intake=None,
        unattended_policy="approve",
    )

def handle_default(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    active_runs = [run for run in runs if run["status"] not in TERMINAL_RUN_STATUSES]

    if not active_runs:
        conn.close()
        if not sys.stdin.isatty():
            print("No goals in progress.")
            print("Start a new goal: agent-loop start")
            return
        print("No goals in progress.")
        choice = input("Start a new goal? (yes/no): ").strip().lower()
        if choice in {"yes", "y"}:
            handle_start(_start_args(), config)
        return

    run = active_runs[0]
    run_id = run["id"]
    run_count = len(active_runs)

    render_plan_md(conn, run_id, config.plan_path)
    render_progress_md(conn, run_id, config.progress_path)
    conn.close()

    goal_label = "goal" if run_count == 1 else "goals"
    print(f"You have {run_count} {goal_label} in progress.")
    print()
    print(f"Status: {run['status']}")
    print(f"Goal: {describe_goal(run['goal'], 200)}")
    print()

    if run["status"] == "awaiting_plan_approval":
        print("There is a plan waiting for your approval.")
        print(f"Plan file: {config.plan_path}")
        print("[v] View Plan")
        print("[a] Approve and Start")
        print("[n] Start New Goal")
        print("[q] Quit")
        if not sys.stdin.isatty():
            return

        print()
        choice = input("Choice: ").strip().lower()
        if choice == "v":
            handle_plan(argparse.Namespace(run_id=run_id, details=False), config)
        elif choice == "a":
            handle_approve(argparse.Namespace(run_id=run_id), config)
        elif choice == "n":
            handle_start(_start_args(), config)
        return

    print(f"Resume this goal: agent-loop resume {run_id}")
    print(f"Show status: agent-loop status {run_id}")
    print("Start a new goal: agent-loop start")
    if not sys.stdin.isatty():
        return

    print()
    print("[r] Resume  [s] Status  [n] Start new goal  [q] Quit")
    choice = input("Choice: ").strip().lower()
    if choice == "r":
        handle_resume(argparse.Namespace(run_id=run_id), config)
    elif choice == "s":
        handle_status(argparse.Namespace(run_id=run_id), config)
    elif choice == "n":
        handle_start(_start_args(), config)

def handle_start(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)
    from agent_loop.intake import run_spec_intake

    if args.non_interactive:
        if not args.goal:
            print("Error: --goal is required for non-interactive mode.", file=sys.stderr)
            sys.exit(1)

        # Mapping legacy aliases
        intake_mode = args.intake
        force_ui = None
        if intake_mode == "ui_lab":
            print("Warning: 'ui_lab' intake mode is deprecated. Use 'spec' instead. Defaulting to 'spec' with UI enabled.")
            intake_mode = "spec"
            force_ui = True
        elif intake_mode == "brainstorm":
            print("Warning: 'brainstorm' intake mode is deprecated. Use 'spec' instead.")
            intake_mode = "spec"
        elif intake_mode == "autonomous" or intake_mode == "non_interactive":
            print("Warning: 'autonomous' intake mode is deprecated. Use 'none' instead.")
            intake_mode = "none"

        if intake_mode not in {"none", "spec"}:
            intake_mode = "none"

        goal = args.goal
        if intake_mode == "spec":
            # Non-interactive spec mode
            from agent_loop.intake import run_brainstorm_discussion, run_spec_review
            # Empty transcript to skip discussion and go straight to drafting
            print("Drafting non-interactive spec...")
            from agent_loop.intake import draft_compact_spec
            spec = draft_compact_spec(goal, "", config)
            if not getattr(args, "no_spec_review", False):
                status, spec = run_spec_review(spec, config)
                if status == "needs-user-answer":
                    print("Error: Spec review needs a user answer, but running in non-interactive mode. Cannot proceed.", file=sys.stderr)
                    sys.exit(1)
            goal = spec

        cfg_snap = config.data.copy()
        cfg_snap["unattended_policy"] = args.unattended_policy

        run_id = run_repo.create(
            goal=goal,
            intake_mode=intake_mode,
            config_snapshot=cfg_snap
        )
        print(f"Started goal {run_id} in {intake_mode} mode (unattended policy: {args.unattended_policy}).")
    else:
        # Interactive Wizard
        print("=== Agent Loop Intake Wizard ===")
        goal = args.goal
        if not goal:
            goal = _read_goal_input("Enter your broad goal: ")
            if not goal:
                print("Error: Goal cannot be empty.", file=sys.stderr)
                sys.exit(1)

        intake_mode = args.intake
        force_ui = None
        if intake_mode == "ui_lab":
            print("Warning: 'ui_lab' intake mode is deprecated. Use 'spec' instead. Defaulting to 'spec' with UI enabled.")
            intake_mode = "spec"
            force_ui = True
        elif intake_mode == "brainstorm":
            print("Warning: 'brainstorm' intake mode is deprecated. Use 'spec' instead.")
            intake_mode = "spec"
        elif intake_mode == "autonomous":
            print("Warning: 'autonomous' intake mode is deprecated. Use 'none' instead.")
            intake_mode = "none"

        if not intake_mode:
            print("\nSelect Intake Mode:")
            print("1) Spec (Discuss and define requirements first)")
            print("2) None (Start planning immediately)")
            choice = input("Choice [1-2]: ").strip()
            if choice == "2":
                intake_mode = "none"
            else:
                intake_mode = "spec"
                
        if args.ui:
            force_ui = True
        elif args.no_ui:
            force_ui = False

        if intake_mode == "spec":
            spec_review = not getattr(args, "no_spec_review", False)
            approved_spec = run_spec_intake(goal, config, force_ui=force_ui, spec_review=spec_review)
            if not approved_spec:
                sys.exit(1)
            goal = approved_spec

        cfg_snap = config.data.copy()
        cfg_snap["unattended_policy"] = "ask"

        run_id = run_repo.create(
            goal=goal,
            intake_mode=intake_mode,
            config_snapshot=cfg_snap
        )
        print(f"\nStarted goal {run_id} in {intake_mode} mode.")

    # Render initial Markdown views
    render_plan_md(conn, run_id, config.plan_path)
    render_progress_md(conn, run_id, config.progress_path)

    # Instantiate Orchestrator and execute planning
    orch = Orchestrator(conn, config)
    print("Planning run...")
    plan_success = orch.plan_run(run_id)
    if plan_success:
        run = run_repo.get(run_id)
        if run["status"] == "running":
            print("Executing tasks...")
            orch.run_loop(run_id)
        elif run["status"] == "awaiting_plan_approval":
            if not args.non_interactive:
                print(f"\nPlan generated for goal {run_id} (see {config.plan_path}).")
                approve = input("Do you approve this plan? (yes/no): ").strip().lower()
                if approve in {"yes", "y"}:
                    run_repo.update_status(run_id, "running")
                    # Regenerate markdown views
                    render_plan_md(conn, run_id, config.plan_path)
                    render_progress_md(conn, run_id, config.progress_path)
                    print("Plan approved! Executing tasks...")
                    orch.run_loop(run_id)
                else:
                    print("Plan not approved. Run is left in 'awaiting_plan_approval' state.")
            else:
                policy = args.unattended_policy
                print(f"Unattended policy is '{policy}'.")
                if policy == "approve":
                    run_repo.update_status(run_id, "running")
                    render_plan_md(conn, run_id, config.plan_path)
                    render_progress_md(conn, run_id, config.progress_path)
                    print("Plan automatically approved via unattended policy. Executing tasks...")
                    orch.run_loop(run_id)
                else:
                    print(f"Plan not approved. Run is left in 'awaiting_plan_approval' state under '{policy}' policy.")
    else:
        print("Planning failed.")

    conn.close()

def handle_resume(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)
    feature_repo = FeatureRepository(conn)
    attempt_repo = AttemptRepository(conn)

    run_id = get_target_run_id(run_repo, args.run_id)
    run = run_repo.get(run_id)
    if not run:
        print(f"Error: Goal {run_id} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Resuming goal {run_id}...")

    # Recovery: Reconcile active attempts. Interrupted 'running' attempts are marked 'abandoned'
    orch = Orchestrator(conn, config)
    abandoned_count = orch.reconcile_interrupted_run(run_id)
    orch.reset_provider_errors()
    orch.unblock_provider_blocked_tasks(run_id)

    if abandoned_count > 0:
        print(f"Reconciled state: marked {abandoned_count} interrupted running attempt(s) as abandoned.")

    # If run status was waiting/quota/running, keep it running or reset it to running/planning
    if run["status"] in {"draft", "planning", "awaiting_plan_approval", "running", "waiting_for_quota", "blocked", "reviewing"}:
        # Keep or reset status to running or planning
        if run["status"] == "blocked" and not feature_repo.get_by_run(run_id):
            run_repo.update_status(run_id, "planning")
        elif run["status"] == "waiting_for_quota" or run["status"] == "blocked":
            run_repo.update_status(run_id, "running")

    # Regenerate markdown views
    render_plan_md(conn, run_id, config.plan_path)
    render_progress_md(conn, run_id, config.progress_path)
    print("Markdown views regenerated.")

    # Actually resume orchestration
    updated_run = run_repo.get(run_id)
    if updated_run["status"] == "planning":
        print("Resuming planning...")
        orch.plan_run(run_id)
        updated_run = run_repo.get(run_id)
    if updated_run["status"] == "awaiting_plan_approval":
        print(f"Goal {run_id} is awaiting plan approval.")
        print(f"Review the plan: agent-loop plan {run_id}")
        print(f"Full markdown plan: {config.plan_path}")
        print(f"Approve and start execution: agent-loop approve {run_id}")
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
        print(f"Error: Goal {run_id} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Goal ID: {run['id']}")
    print(f"Goal Description: {describe_goal(run['goal'])}")
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
        print(f"Error: Goal {run_id} not found.", file=sys.stderr)
        sys.exit(1)

    features = feature_repo.get_by_run(run_id)
    tasks = task_repo.get_by_run(run_id)

    print(f"Plan for Goal {run_id}")
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
                if att.get("patch_path"):
                    print(f"    Patch: {att['patch_path']}")

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

def handle_approve(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)
    run_id = get_target_run_id(run_repo, args.run_id)
    run = run_repo.get(run_id)
    if not run:
        print(f"Error: Goal {run_id} not found.", file=sys.stderr)
        sys.exit(1)

    if run["status"] != "awaiting_plan_approval":
        print(f"Error: Goal {run_id} is in status '{run['status']}', not 'awaiting_plan_approval'. Cannot approve.", file=sys.stderr)
        sys.exit(1)

    run_repo.update_status(run_id, "running")
    print(f"Plan approved for goal {run_id}. Starting execution...")

    # Regenerate markdown views
    render_plan_md(conn, run_id, config.plan_path)
    render_progress_md(conn, run_id, config.progress_path)

    orch = Orchestrator(conn, config)
    orch.run_loop(run_id)
    conn.close()


def handle_handoff(args: argparse.Namespace) -> None:
    if args.handoff_command != "validate":
        return

    result = validate_handoff(args.request_path, args.response_path)
    if not result.valid:
        print("Handoff validation failed:", file=sys.stderr)
        for error in result.errors:
            print(f"- {error}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Handoff validation passed: {len(result.requirement_statuses)} requirements accounted for."
    )

def handle_migration(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    migration_repo = TestMigrationRepository(conn)
    run_repo = RunRepository(conn)
    
    mig = migration_repo.get(args.migration_id)
    if not mig:
        print(f"Error: Test migration {args.migration_id} not found.", file=sys.stderr)
        sys.exit(1)
        
    run_id = mig["run_id"]
    
    if args.action == "approve":
        migration_repo.update_approval(args.migration_id, "approved")
        print(f"Test migration {args.migration_id} approved.")
        
        # Check other migrations for this run
        all_migs = migration_repo.get_by_run(run_id)
        has_pending = any(m["approval_status"] == "pending" for m in all_migs)
        has_rejected = any(m["approval_status"] == "rejected" for m in all_migs)
        
        run = run_repo.get(run_id)
        if run["status"] == "complete_pending_test_review":
            if has_rejected:
                run_repo.update_status(run_id, "blocked")
                print(f"Goal {run_id} is now blocked due to rejected migration(s).")
            elif not has_pending:
                run_repo.update_status(run_id, "complete")
                print(f"Goal {run_id} completed successfully (all migrations approved).")
                
    elif args.action == "reject":
        migration_repo.update_approval(args.migration_id, "rejected")
        print(f"Test migration {args.migration_id} rejected.")
        
        run = run_repo.get(run_id)
        if run["status"] in {"complete_pending_test_review", "running", "reviewing"}:
            run_repo.update_status(run_id, "blocked")
            print(f"Goal {run_id} is now blocked due to rejected migration.")
            
    # Regenerate markdown views
    render_plan_md(conn, run_id, config.plan_path)
    render_progress_md(conn, run_id, config.progress_path)
    conn.close()

def main() -> None:
    parser = argparse.ArgumentParser(
        description="agent-loop orchestrator CLI tool",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--config", type=Path, help="Path to config file")
    
    subparsers = parser.add_subparsers(dest="command")

    # start
    start_parser = subparsers.add_parser("start", help="Start a new goal")
    start_parser.add_argument("--non-interactive", action="store_true", help="Run without wizard prompts")
    start_parser.add_argument("--goal", type=str, help="Broad goal to execute")
    start_parser.add_argument("--intake", choices=["none", "spec", "brainstorm", "ui_lab", "autonomous"], help="Intake mode")
    start_parser.add_argument("--unattended-policy", choices=["approve", "reject"], default="approve", help="Unattended policy for plan approval")
    start_parser.add_argument("--ui", action="store_true", help="Force UI brainstorming branch")
    start_parser.add_argument("--no-ui", action="store_true", help="Skip UI brainstorming branch")
    start_parser.add_argument("--no-spec-review", action="store_true", help="Skip internal spec review")

    # resume
    resume_parser = subparsers.add_parser("resume", help="Resume an existing goal")
    resume_parser.add_argument("run_id", metavar="goal_id", type=int, nargs="?", help="Goal ID to resume; internally this is the run ID (defaults to latest)")

    # status
    status_parser = subparsers.add_parser("status", help="Get goal status")
    status_parser.add_argument("run_id", metavar="goal_id", type=int, nargs="?", help="Goal ID to inspect; internally this is the run ID (defaults to latest)")

    # plan
    plan_parser = subparsers.add_parser("plan", help="Inspect goal plan")
    plan_parser.add_argument("run_id", metavar="goal_id", type=int, nargs="?", help="Goal ID to inspect; internally this is the run ID (defaults to latest)")
    plan_parser.add_argument("--details", action="store_true", help="Print detailed task and execution metadata")

    # approve
    approve_parser = subparsers.add_parser("approve", help="Approve the generated plan to start execution")
    approve_parser.add_argument("run_id", metavar="goal_id", type=int, nargs="?", help="Goal ID to approve; internally this is the run ID (defaults to latest)")

    # notify
    notify_parser = subparsers.add_parser("notify", help="Notify helpers")
    notify_subparsers = notify_parser.add_subparsers(dest="subcommand", required=True)
    notify_subparsers.add_parser("test", help="Send a test notification")

    # handoff
    handoff_parser = subparsers.add_parser("handoff", help="Validate supervisor/executor handoffs")
    handoff_subparsers = handoff_parser.add_subparsers(dest="handoff_command", required=True)
    handoff_validate_parser = handoff_subparsers.add_parser(
        "validate", help="Validate an executor response against its request"
    )
    handoff_validate_parser.add_argument("request_path", type=Path)
    handoff_validate_parser.add_argument("response_path", type=Path)

    # migration
    migration_parser = subparsers.add_parser("migration", help="Manage test migrations")
    migration_subparsers = migration_parser.add_subparsers(dest="action", required=True)
    
    mig_approve = migration_subparsers.add_parser("approve", help="Approve a test migration")
    mig_approve.add_argument("migration_id", type=int, help="ID of migration to approve")
    
    mig_reject = migration_subparsers.add_parser("reject", help="Reject a test migration")
    mig_reject.add_argument("migration_id", type=int, help="ID of migration to reject")

    args = parser.parse_args()
    config = Config.load(args.config)

    if config.execution_mode == "trusted-host" and args.command in {None, "start", "resume", "approve", "migration"}:
        print("Trusted-host execution: commands can access anything available to the current user.")

    if args.command is None:
        handle_default(args, config)
    elif args.command == "start":
        handle_start(args, config)
    elif args.command == "resume":
        handle_resume(args, config)
    elif args.command == "status":
        handle_status(args, config)
    elif args.command == "plan":
        handle_plan(args, config)
    elif args.command == "approve":
        handle_approve(args, config)
    elif args.command == "notify":
        handle_notify(args, config)
    elif args.command == "handoff":
        handle_handoff(args)
    elif args.command == "migration":
        handle_migration(args, config)

if __name__ == "__main__":
    main()
