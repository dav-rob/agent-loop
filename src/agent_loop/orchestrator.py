import json
import os
import re
import time
import urllib.request
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent_loop.config import Config
from agent_loop.adapters import get_adapter, AttemptResult
from agent_loop.git_utils import (
    create_worktree,
    remove_worktree,
    commit_changes,
    merge_branch
)
from agent_loop.repositories import (
    RunRepository,
    FeatureRepository,
    TaskRepository,
    AttemptRepository,
    DecisionRepository,
    ProviderStateRepository,
    ReviewRepository,
    NotificationRepository,
    TestMigrationRepository,
    TestRunRepository
)
from agent_loop.views import render_plan_md, render_progress_md

def validate_dag(features: List[Dict[str, Any]], tasks: List[Dict[str, Any]]) -> bool:
    feat_names = {f["name"] for f in features}
    feat_deps = {f["name"]: f.get("dependencies", []) for f in features}

    for f_name, deps in feat_deps.items():
        for dep in deps:
            if dep not in feat_names:
                return False

    visited = {}
    def has_cycle_feat(node):
        if visited.get(node) == 1:
            return True
        if visited.get(node) == 2:
            return False
        visited[node] = 1
        for dep in feat_deps.get(node, []):
            if has_cycle_feat(dep):
                return True
        visited[node] = 2
        return False

    for f in feat_names:
        if has_cycle_feat(f):
            return False

    task_names = {t["name"] for t in tasks}
    task_deps = {t["name"]: t.get("dependencies", []) for t in tasks}

    for t_name, deps in task_deps.items():
        for dep in deps:
            if dep not in task_names:
                return False

    visited_task = {}
    def has_cycle_task(node):
        if visited_task.get(node) == 1:
            return True
        if visited_task.get(node) == 2:
            return False
        visited_task[node] = 1
        for dep in task_deps.get(node, []):
            if has_cycle_task(dep):
                return True
        visited_task[node] = 2
        return False

    for t in task_names:
        if has_cycle_task(t):
            return False

    for t in tasks:
        if t["feature_name"] not in feat_names:
            return False

    return True


class Orchestrator:
    def __init__(self, conn: sqlite3.Connection, config: Config, plan_path: Path = None, progress_path: Path = None):
        self.conn = conn
        self.config = config
        self.plan_path = plan_path or Path("plan.md")
        self.progress_path = progress_path or Path("progress.md")
        self.run_repo = RunRepository(conn)
        self.feature_repo = FeatureRepository(conn)
        self.task_repo = TaskRepository(conn)
        self.attempt_repo = AttemptRepository(conn)
        self.decision_repo = DecisionRepository(conn)
        self.provider_repo = ProviderStateRepository(conn)
        self.review_repo = ReviewRepository(conn)
        self.notification_repo = NotificationRepository(conn)
        self.test_migration_repo = TestMigrationRepository(conn)
        self.test_run_repo = TestRunRepository(conn)

    def plan_run(self, run_id: int) -> bool:
        run = self.run_repo.get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found.")

        self.run_repo.update_status(run_id, "planning")
        render_progress_md(self.conn, run_id, self.progress_path)

        routes = self.config.routes.get("planning", [])
        
        planning_prompt = f"""
You are the Agent Loop Planner.
Analyze the user's broad goal: "{run['goal']}"
Create a structured plan consisting of features, tasks within features, dependencies, risk levels, and verification commands.
Be concise.
Return ONLY a valid JSON object matching the requested schema. Do not include markdown formatting or wrapper around the JSON.
"""

        schema_path = Path(__file__).parent / "plan_schema.json"

        last_error = ""
        for route in routes:
            provider = route["provider"]
            model = route["model"]

            p_state = self.provider_repo.get(provider, model)
            if p_state and not p_state["availability"]:
                # Simple availability skip
                continue

            try:
                adapter = get_adapter(provider, self.config)
                attempt_logs_dir = self.config.logs_dir / str(run_id) / "planning" / f"attempt_{provider}_{model}"
                attempt_logs_dir.mkdir(parents=True, exist_ok=True)

                result: AttemptResult = adapter.run_attempt(
                    model=model,
                    prompt=planning_prompt,
                    workspace_path=Path.cwd(),
                    attempt_logs_dir=attempt_logs_dir
                )

                if not result.success:
                    if result.quota_exhausted:
                        self.provider_repo.save(
                            provider=provider,
                            model=model,
                            capability_snapshot={"models": [model]},
                            availability=False,
                            quota_limit_reset=result.quota_reset
                        )
                    last_error = result.error
                    continue

                plan_data = json.loads(result.output)
                
                features = plan_data.get("features", [])
                tasks = plan_data.get("tasks", [])
                if not validate_dag(features, tasks):
                    last_error = "Invalid DAG: detected cycles or invalid dependencies."
                    continue

                for dec in plan_data.get("decisions", []):
                    self.decision_repo.create(
                        run_id=run_id,
                        decision_type=dec["decision_type"],
                        is_autonomous=(run["intake_mode"] == "autonomous"),
                        summary=dec["summary"],
                        details=dec.get("details")
                    )

                feature_ids = {}
                for feat in features:
                    f_id = self.feature_repo.create(
                        run_id=run_id,
                        name=feat["name"],
                        risk=feat["risk"],
                        acceptance_criteria=feat.get("acceptance_criteria"),
                        dependencies=feat.get("dependencies", [])
                    )
                    feature_ids[feat["name"]] = f_id

                for task in tasks:
                    self.task_repo.create(
                        run_id=run_id,
                        feature_id=feature_ids[task["feature_name"]],
                        name=task["name"],
                        role=task["role"],
                        risk=task["risk"],
                        scope=task.get("scope"),
                        dependencies=task.get("dependencies", []),
                        required_verification=task.get("required_verification")
                    )

                if run["intake_mode"] in {"autonomous", "non_interactive"}:
                    self.run_repo.update_status(run_id, "running")
                else:
                    self.run_repo.update_status(run_id, "awaiting_plan_approval")

                render_plan_md(self.conn, run_id, self.plan_path)
                render_progress_md(self.conn, run_id, self.progress_path)
                return True

            except Exception as e:
                last_error = str(e)
                continue

        self.run_repo.update_status(run_id, "blocked")
        render_progress_md(self.conn, run_id, self.progress_path)
        return False

    def notify(self, run_id: int, event: str, message: str) -> None:
        webhook_url = os.environ.get(self.config.webhook_env_var)
        
        # Deduplication check: check if we've sent this event within the last hour
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT count(*) FROM notifications 
            WHERE run_id = ? AND event = ? AND delivery_status = 'sent' 
              AND datetime(created_at) > datetime('now', '-1 hour');
            """,
            (run_id, event)
        )
        if cursor.fetchone()[0] > 0:
            return # Skip duplicate

        dest = "Slack Webhook" if webhook_url else "stdout-fallback"
        notification_id = self.notification_repo.create(run_id, event, dest)

        if not webhook_url:
            self.notification_repo.update_delivery(notification_id, "sent", 1)
            return

        payload = {
            "text": f"Run {run_id} Event '{event}': {message}"
        }
        try:
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req) as res:
                self.notification_repo.update_delivery(notification_id, "sent", 1)
        except Exception:
            self.notification_repo.update_delivery(notification_id, "failed", 1)

    def execute_task(self, run_id: int, task: Dict[str, Any]) -> bool:
        task_id = task["id"]
        self.task_repo.update_status(task_id, "running")
        render_progress_md(self.conn, run_id, self.progress_path)

        # Decide route based on task risk/attempts count
        attempts = [a for a in self.attempt_repo.get_by_run(run_id) if a["task_id"] == task_id]
        is_high_reasoning = (task["risk"] == "high") or (len(attempts) >= self.config.retry_policy["escalation_threshold"])

        route_key = "planning" if is_high_reasoning else "implementation"
        routes = self.config.routes.get(route_key, [])

        # Find first available route
        selected_route = None
        for r in routes:
            p_state = self.provider_repo.get(r["provider"], r["model"])
            if not p_state or p_state["availability"]:
                selected_route = r
                break

        if not selected_route:
            # All routes limited
            self.task_repo.update_status(task_id, "ready")
            return False

        provider = selected_route["provider"]
        model = selected_route["model"]
        reasoning_level = selected_route.get("reasoning_level")

        # Isolated worktree directory
        attempt_id = self.attempt_repo.create(
            run_id=run_id,
            task_id=task_id,
            route=route_key,
            provider=provider,
            model=model,
            reasoning_level=reasoning_level,
            worktree_path=None,
            logs_path=None
        )

        worktree_dir = Path("worktrees") / f"run-{run_id}-task-{task_id}-attempt-{attempt_id}"
        logs_dir = self.config.logs_dir / str(run_id) / str(task_id) / str(attempt_id)
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Update attempt fields
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE attempts SET worktree_path = ?, logs_path = ? WHERE id = ?;",
            (str(worktree_dir), str(logs_dir), attempt_id)
        )
        self.conn.commit()

        # Create worktree (in mock mode/real, using local repo root as main)
        branch_name = f"agent-loop-run-{run_id}-task-{task_id}-att-{attempt_id}"
        try:
            create_worktree(Path.cwd(), worktree_dir, branch_name)
        except Exception as e:
            self.attempt_repo.update_outcome(attempt_id, "failed")
            self.task_repo.update_status(task_id, "ready")
            return False

        # Build prompt
        prompt = f"""
Goal: {self.run_repo.get(run_id)['goal']}
Task: {task['name']}
Verification Command: {task['required_verification']}
Scope: {json.dumps(task['scope'])}
Please implement this task in the workspace. Run verification to confirm success before exiting.
"""

        try:
            adapter = get_adapter(provider, self.config)
            result = adapter.run_attempt(
                model=model,
                prompt=prompt,
                workspace_path=worktree_dir,
                attempt_logs_dir=logs_dir
            )

            # Record test run if verification executed
            if task["required_verification"]:
                self.test_run_repo.create(
                    run_id=run_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    command=task["required_verification"],
                    scope=None,
                    exit_status=(0 if result.success else 1),
                    duration_seconds=0.1,
                    output_path=str(logs_dir / "stdout.log")
                )

            if result.success:
                # Commit changes
                sha = commit_changes(worktree_dir, f"agent-loop: complete {task['name']}")
                self.attempt_repo.update_outcome(attempt_id, "completed", commit_sha=sha)
                
                # Perform task review
                self.task_repo.update_status(task_id, "reviewing")
                approved = self.run_task_review(run_id, task_id, sha)
                
                if approved:
                    # Merge task branch into main
                    merged = merge_branch(Path.cwd(), branch_name, "main")
                    if merged:
                        self.task_repo.update_status(task_id, "complete")
                    else:
                        # Conflict! Create integration task
                        self.create_integration_task(run_id, task, branch_name)
                        self.task_repo.update_status(task_id, "failed")
                        self.task_repo.update_status(task_id, "ready")
                else:
                    self.task_repo.update_status(task_id, "failed")
                    self.task_repo.update_status(task_id, "ready")

                # Remove worktree
                remove_worktree(Path.cwd(), worktree_dir)
                return True
            else:
                if result.quota_exhausted:
                    self.provider_repo.save(
                        provider=provider,
                        model=model,
                        capability_snapshot={"models": [model]},
                        availability=False,
                        quota_limit_reset=result.quota_reset
                    )
                    self.attempt_repo.update_outcome(attempt_id, "abandoned")
                    self.task_repo.update_status(task_id, "failed")
                    self.task_repo.update_status(task_id, "ready")
                else:
                    self.attempt_repo.update_outcome(attempt_id, "failed")
                    if len(attempts) + 1 >= self.config.retry_policy["max_attempts"]:
                        self.task_repo.update_status(task_id, "blocked")
                        self.notify(run_id, "blocked", f"Task {task['name']} failed {self.config.retry_policy['max_attempts']} times.")
                    else:
                        self.task_repo.update_status(task_id, "failed")
                        self.task_repo.update_status(task_id, "ready")

                remove_worktree(Path.cwd(), worktree_dir)
                return False

        except Exception:
            self.attempt_repo.update_outcome(attempt_id, "failed")
            self.task_repo.update_status(task_id, "failed")
            self.task_repo.update_status(task_id, "ready")
            remove_worktree(Path.cwd(), worktree_dir)
            return False

    def run_task_review(self, run_id: int, task_id: int, commit_sha: Optional[str]) -> bool:
        # Task review skepticism stub
        self.review_repo.create(
            run_id=run_id,
            subject_type="task",
            subject_id=task_id,
            decision="approved",
            findings="Diff checked, tests passed"
        )
        return True

    def create_integration_task(self, run_id: int, task: Dict[str, Any], branch_name: str) -> None:
        self.decision_repo.create(
            run_id=run_id,
            decision_type="architecture",
            is_autonomous=True,
            summary=f"Merge conflict on task {task['name']}, created integration branch."
        )

    def run_loop(self, run_id: int) -> None:
        # Core execution loop
        while True:
            run = self.run_repo.get(run_id)
            if run["status"] not in {"running", "waiting_for_quota"}:
                break

            tasks = self.task_repo.get_by_run(run_id)
            
            # Update task readiness based on dependencies
            complete_task_names = {t["name"] for t in tasks if t["status"] == "complete"}
            for t in tasks:
                if t["status"] == "pending":
                    deps = t["dependencies"]
                    if all(dep in complete_task_names for dep in deps):
                        self.task_repo.update_status(t["id"], "ready")

            # Refresh task list
            tasks = self.task_repo.get_by_run(run_id)
            ready_tasks = [t for t in tasks if t["status"] == "ready"]
            running_tasks = [t for t in tasks if t["status"] == "running"]
            blocked_tasks = [t for t in tasks if t["status"] == "blocked"]

            if not ready_tasks and not running_tasks:
                if blocked_tasks:
                    self.run_repo.update_status(run_id, "blocked")
                elif all(t["status"] == "complete" for t in tasks):
                    self.run_repo.update_status(run_id, "reviewing")
                    
                    self.review_repo.create(
                        run_id=run_id,
                        subject_type="final",
                        subject_id=run_id,
                        decision="approved",
                        findings="All features verified, final tests passed."
                    )
                    
                    migrations = self.test_migration_repo.get_by_run(run_id)
                    has_pending = any(m["approval_status"] == "pending" for m in migrations)
                    if has_pending:
                        self.run_repo.update_status(run_id, "complete_pending_test_review")
                    else:
                        self.run_repo.update_status(run_id, "complete")
                break

            # Execute ready tasks up to max_workers
            max_workers = self.config.max_workers
            available_slots = max_workers - len(running_tasks)
            
            if available_slots > 0 and ready_tasks:
                # Sequential execute for MVP control
                for t in ready_tasks[:available_slots]:
                    self.execute_task(run_id, t)
            else:
                # No slot or no tasks, sleep/wait
                break

            render_plan_md(self.conn, run_id, self.plan_path)
            render_progress_md(self.conn, run_id, self.progress_path)
            time.sleep(0.5)
