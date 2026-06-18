import argparse
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

def describe_goal(goal: str, max_length: int = 70) -> str:
    one_line = " ".join((goal or "").split())
    if len(one_line) <= max_length:
        return one_line
    return one_line[: max_length - 3].rstrip() + "..."

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

def _collect_brainstorming_notes() -> str:
    questions: List[Tuple[str, str]] = [
        ("User / audience", "Who is the main user or audience? (Optional): "),
        ("Success criteria", "What should be true when this goal is complete? (Optional): "),
        ("Constraints / preferences", "Any constraints, preferences, integrations, or style choices? (Optional): "),
        ("Non-goals / risks", "What should be out of scope, risky, or easy to get wrong? (Optional): "),
        ("Verification", "How should the agent verify the result? (Optional): "),
    ]
    answers = []
    for label, prompt in questions:
        answer = input(prompt).strip()
        if answer:
            answers.append(f"- {label}: {answer}")

    if not answers:
        return ""
    return "Brainstorming Notes:\n" + "\n".join(answers)

def ensure_workspace(config: Config) -> None:
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

def handle_start(args: argparse.Namespace, config: Config) -> None:
    conn = get_db(config)
    run_repo = RunRepository(conn)

    if args.non_interactive:
        if not args.goal:
            print("Error: --goal is required for non-interactive mode.", file=sys.stderr)
            sys.exit(1)

        # Make --intake effective in non-interactive mode
        if args.intake == "ui_lab":
            is_ui_work = any(x in args.goal.lower() for x in ["ui", "interface", "ux", "web", "frontend", "front-end", "screen", "view", "page", "styling", "css", "html", "design", "layout"])
            if not is_ui_work:
                print("Error: UI Lab is only offered for UI goals.", file=sys.stderr)
                sys.exit(1)
            intake_mode = "brainstorm_ui_lab"
        elif args.intake == "brainstorm":
            intake_mode = "brainstorm"
        elif args.intake == "autonomous":
            intake_mode = "autonomous"
        else:
            intake_mode = "non_interactive"

        goal = args.goal
        if intake_mode == "brainstorm_ui_lab":
            brief_output = ""
            try:
                from agent_loop.adapters import AgyAdapter
                routes = config.routes.get("planning", []) + config.routes.get("implementation", [])
                selected_model = "Gemini 3.5 Flash (High)"
                for r in routes:
                    if r["provider"] == "agy":
                        selected_model = r["model"]
                        break
                
                adapter = AgyAdapter(config=config)
                import tempfile
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_path = Path(tmpdir)
                    res = adapter.run_attempt(
                        model=selected_model,
                        prompt=f"/brief {args.goal}",
                        workspace_path=tmp_path,
                        attempt_logs_dir=tmp_path / "logs"
                    )
                    if res.success:
                        brief_output = res.output
            except Exception:
                pass
            goal = f"{args.goal}\n\nUI Lab Brief:\n{brief_output or 'Default Brief'}"

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

        # Make --intake effective in interactive mode
        if args.intake:
            if args.intake == "ui_lab":
                is_ui_work = any(x in goal.lower() for x in ["ui", "interface", "ux", "web", "frontend", "front-end", "screen", "view", "page", "styling", "css", "html", "design", "layout"])
                if not is_ui_work:
                    print("Error: UI Lab is only offered for UI goals.", file=sys.stderr)
                    sys.exit(1)
                intake_mode = "brainstorm_ui_lab"
            elif args.intake == "autonomous":
                intake_mode = "autonomous"
            else:
                intake_mode = "brainstorm"
        else:
            is_ui_work = any(x in goal.lower() for x in ["ui", "interface", "ux", "web", "frontend", "front-end", "screen", "view", "page", "styling", "css", "html", "design", "layout"])
            print("\nSelect Intake Mode:")
            print("1) Brainstorm (Default)")
            if is_ui_work:
                print("2) Brainstorm with UI Lab")
                print("3) Autonomous")
                choice = input("Choice [1-3]: ").strip()
                if choice == "2":
                    intake_mode = "brainstorm_ui_lab"
                elif choice == "3":
                    intake_mode = "autonomous"
                else:
                    intake_mode = "brainstorm"
            else:
                print("2) Autonomous")
                choice = input("Choice [1-2]: ").strip()
                if choice == "2":
                    intake_mode = "autonomous"
                else:
                    intake_mode = "brainstorm"

        # Implement concise brainstorm interaction for brainstorm modes
        if intake_mode in {"brainstorm", "brainstorm_ui_lab"}:
            print("\n--- Brainstorming Questions ---")
            notes = _collect_brainstorming_notes()
            if notes:
                goal = f"{goal}\n{notes}"

            if intake_mode == "brainstorm_ui_lab":
                import re
                print("\nRunning UI Lab brief workflow...")
                brief_output = ""
                try:
                    from agent_loop.adapters import AgyAdapter
                    routes = config.routes.get("planning", []) + config.routes.get("implementation", [])
                    selected_model = "Gemini 3.5 Flash (High)"
                    for r in routes:
                        if r["provider"] == "agy":
                            selected_model = r["model"]
                            break
                    
                    adapter = AgyAdapter(config=config)
                    import tempfile
                    with tempfile.TemporaryDirectory() as tmpdir:
                        tmp_path = Path(tmpdir)
                        res = adapter.run_attempt(
                            model=selected_model,
                            prompt=f"/brief {goal}",
                            workspace_path=tmp_path,
                            attempt_logs_dir=tmp_path / "logs"
                        )
                        if res.success:
                            brief_output = res.output
                except Exception as e:
                    print(f"Warning: Could not invoke UI Lab brief workflow automatically: {e}")
                
                # Parse questions
                questions = []
                if brief_output:
                    in_q_section = False
                    for line in brief_output.splitlines():
                        line_strip = line.strip()
                        if "UI Questions" in line_strip:
                            in_q_section = True
                            continue
                        if in_q_section:
                            if line_strip.startswith("###") or (line_strip.startswith("##") and not "UI Questions" in line_strip):
                                in_q_section = False
                                continue
                            if line_strip.startswith(("-", "*")) or (line_strip and line_strip[0].isdigit()):
                                q_text = re.sub(r"^[\-\*\d\.\s]+", "", line_strip).strip()
                                if q_text.endswith("?"):
                                    questions.append(q_text)
                
                if not questions:
                    questions = [
                        "Should this feel fast or thoughtful?",
                        "Should this feel like a tool or a guide?",
                        "Should people see everything immediately or should detail appear gradually?",
                        "What would make this feel wrong?",
                        "What apps do you like?",
                        "What apps do you dislike?"
                    ]
                
                print("\n--- UI Lab Brief Questionnaire ---")
                answers = []
                for q in questions:
                    ans = input(f"{q} ").strip()
                    answers.append(f"- {q}: {ans}")
                
                goal_refinement = f"\n\nUI Lab Brief:\n{brief_output or 'Default Brief'}\n\nUser Answers:\n" + "\n".join(answers)
                goal = f"{goal}{goal_refinement}"

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
    
    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    start_parser = subparsers.add_parser("start", help="Start a new goal")
    start_parser.add_argument("--non-interactive", action="store_true", help="Run without wizard prompts")
    start_parser.add_argument("--goal", type=str, help="Broad goal to execute")
    start_parser.add_argument("--intake", choices=["brainstorm", "ui_lab", "autonomous"], help="Intake mode")
    start_parser.add_argument("--unattended-policy", choices=["approve", "reject"], default="approve", help="Unattended policy for plan approval")

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

    if config.execution_mode == "trusted-host" and args.command in {"start", "resume", "approve", "migration"}:
        print("Trusted-host execution: commands can access anything available to the current user.")

    if args.command == "start":
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
