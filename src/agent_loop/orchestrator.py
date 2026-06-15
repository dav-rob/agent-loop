import json
import os
import re
import time
import urllib.request
import sqlite3
import subprocess
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
                attempt_logs_dir = (self.config.logs_dir / str(run_id) / "planning" / f"attempt_{provider}_{model}").resolve()
                attempt_logs_dir.mkdir(parents=True, exist_ok=True)

                result: AttemptResult = adapter.run_attempt(
                    model=model,
                    prompt=planning_prompt,
                    workspace_path=Path.cwd().resolve(),
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

    def run_verification(self, run_id: int, task_id: int, attempt_id: int, command: str, worktree_dir: Path, logs_dir: Path) -> bool:
        """Runs the verification command in the worktree directory.
        
        Note: Under trusted-host execution mode, commands executed via shell=True 
        will run with the full permissions and privileges of the current user.
        """
        start_time = time.time()
        test_out_file = logs_dir / "test_run_stdout.log"
        test_err_file = logs_dir / "test_run_stderr.log"
        
        try:
            with test_out_file.open("w") as out_f, test_err_file.open("w") as err_f:
                process = subprocess.run(
                    command,
                    shell=True,
                    cwd=worktree_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=out_f,
                    stderr=err_f,
                    timeout=300.0
                )
            duration = time.time() - start_time
            
            output_json = json.dumps({
                "stdout": str(test_out_file),
                "stderr": str(test_err_file)
            })
            
            self.test_run_repo.create(
                run_id=run_id,
                task_id=task_id,
                attempt_id=attempt_id,
                command=command,
                scope=None,
                exit_status=process.returncode,
                duration_seconds=duration,
                output_path=output_json
            )
            return process.returncode == 0
        except Exception as e:
            duration = time.time() - start_time
            output_json = json.dumps({
                "stdout": str(test_out_file),
                "stderr": str(test_err_file)
            })
            self.test_run_repo.create(
                run_id=run_id,
                task_id=task_id,
                attempt_id=attempt_id,
                command=command,
                scope=None,
                exit_status=-1,
                duration_seconds=duration,
                output_path=output_json
            )
            with test_err_file.open("a") as err_f:
                err_f.write(f"\nVerification failed with exception: {e}\n")
            return False

    def detect_and_record_test_migrations(self, run_id: int, task_id: int, commit_sha: str) -> None:
        try:
            res = subprocess.run(
                ["git", "show", "--name-status", "--oneline", commit_sha],
                cwd=Path.cwd(),
                capture_output=True,
                text=True,
                check=True
            )
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    status, file_path = parts[0], parts[1]
                    if "test" in file_path.lower() and status == "M":
                        self.test_migration_repo.create(
                            run_id=run_id,
                            task_id=task_id,
                            old_test_path=file_path,
                            replacement_test_path=file_path,
                            rationale="Audited test modified during task implementation.",
                            evidence=f"Commit: {commit_sha}"
                        )
        except Exception:
            pass

    def execute_task(self, run_id: int, task: Dict[str, Any]) -> bool:
        task_id = task["id"]
        self.task_repo.update_status(task_id, "running")
        render_progress_md(self.conn, run_id, self.progress_path)

        attempts = [a for a in self.attempt_repo.get_by_run(run_id) if a["task_id"] == task_id]
        is_high_reasoning = (task["risk"] == "high") or (len(attempts) >= self.config.retry_policy["escalation_threshold"])

        route_key = "planning" if is_high_reasoning else "implementation"
        routes = self.config.routes.get(route_key, [])

        selected_route = None
        for r in routes:
            p_state = self.provider_repo.get(r["provider"], r["model"])
            if not p_state or p_state["availability"]:
                selected_route = r
                break

        if not selected_route:
            self.task_repo.update_status(task_id, "failed")
            self.task_repo.update_status(task_id, "ready")
            return False

        provider = selected_route["provider"]
        model = selected_route["model"]
        reasoning_level = selected_route.get("reasoning_level")

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

        worktree_dir = (Path("worktrees") / f"run-{run_id}-task-{task_id}-attempt-{attempt_id}").resolve()
        logs_dir = (self.config.logs_dir / str(run_id) / str(task_id) / str(attempt_id)).resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)

        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE attempts SET worktree_path = ?, logs_path = ? WHERE id = ?;",
            (str(worktree_dir), str(logs_dir), attempt_id)
        )
        self.conn.commit()

        branch_name = f"agent-loop-run-{run_id}-task-{task_id}-att-{attempt_id}"
        try:
            create_worktree(Path.cwd(), worktree_dir, branch_name)
        except Exception:
            self.attempt_repo.update_outcome(attempt_id, "failed")
            self.task_repo.update_status(task_id, "failed")
            self.task_repo.update_status(task_id, "ready")
            return False

        previous_rejection = self.review_repo.get_latest_rejection("task", task_id)
        prompt = f"""
Goal: {self.run_repo.get(run_id)['goal']}
Task: {task['name']}
Verification Command: {task['required_verification']}
Scope: {json.dumps(task['scope'])}
"""
        if previous_rejection:
            prompt += f"\nPrevious attempt was rejected with the following findings:\n{previous_rejection}\n"
            
        prompt += "\nPlease implement this task in the workspace. Run verification to confirm success before exiting.\n"

        try:
            adapter = get_adapter(provider, self.config)
            result = adapter.run_attempt(
                model=model,
                prompt=prompt,
                workspace_path=worktree_dir,
                attempt_logs_dir=logs_dir,
                reasoning_level=reasoning_level
            )

            verification_success = True
            if task["required_verification"]:
                verification_success = self.run_verification(
                    run_id=run_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    command=task["required_verification"],
                    worktree_dir=worktree_dir,
                    logs_dir=logs_dir
                )

            if result.success and verification_success:
                sha = commit_changes(worktree_dir, f"agent-loop: complete {task['name']}")
                self.attempt_repo.update_outcome(attempt_id, "completed", commit_sha=sha)
                
                if sha:
                    self.detect_and_record_test_migrations(run_id, task_id, sha)

                self.task_repo.update_status(task_id, "reviewing")
                approved = self.run_task_review(run_id, task_id, sha)
                
                if approved:
                    merged = merge_branch(Path.cwd(), branch_name, "main")
                    if merged:
                        self.task_repo.update_status(task_id, "complete")
                    else:
                        self.create_integration_task(run_id, task, branch_name)
                        self.task_repo.update_status(task_id, "failed")
                        self.task_repo.update_status(task_id, "ready")
                else:
                    self.task_repo.update_status(task_id, "failed")
                    self.task_repo.update_status(task_id, "ready")

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

    def run_agent_review(self, run_id: int, subject_type: str, subject_id: int, review_prompt: str) -> bool:
        routes = self.config.routes.get("planning", [])
        selected_route = None
        for r in routes:
            p_state = self.provider_repo.get(r["provider"], r["model"])
            if not p_state or p_state["availability"]:
                selected_route = r
                break
        if not selected_route:
            selected_route = routes[0] if routes else {"provider": "codex", "model": "gpt-5.5"}

        provider = selected_route["provider"]
        model = selected_route["model"]
        
        review_logs_dir = self.config.logs_dir / str(run_id) / "reviews" / f"{subject_type}_{subject_id}"
        review_logs_dir.mkdir(parents=True, exist_ok=True)
        
        evidence_paths = [
            str(review_logs_dir / "stdout.log"),
            str(review_logs_dir / "stderr.log")
        ]
        
        try:
            adapter = get_adapter(provider, self.config)
            prompt = f"""
You are the Agent Loop Reviewer.
{review_prompt}

Analyze the changes skeptically. Check for correctness, safety, regressions, and complexity.
Output a JSON response in the following format:
{{
  "decision": "approved",
  "findings": "Detail findings here..."
}}
The "decision" must be one of: "approved", "rejected", "follow_up", "assessment", "block".
Only return the raw JSON object. Do not include markdown wrappers.
"""
            result = adapter.run_attempt(
                model=model,
                prompt=prompt,
                workspace_path=Path.cwd(),
                attempt_logs_dir=review_logs_dir
            )
            
            decision = "rejected"
            findings = "Diff checked"
            
            if result.success:
                # Clean and parse JSON
                cleaned_output = result.output.strip()
                if cleaned_output.startswith("```"):
                    first_newline = cleaned_output.find("\n")
                    if first_newline != -1:
                        cleaned_output = cleaned_output[first_newline:].strip()
                    if cleaned_output.endswith("```"):
                        cleaned_output = cleaned_output[:-3].strip()
                
                try:
                    data = json.loads(cleaned_output)
                    if isinstance(data, dict) and "decision" in data and "findings" in data:
                        dec_val = data["decision"]
                        find_val = data["findings"]
                        if dec_val in {"approved", "rejected", "follow_up", "assessment", "block"} and isinstance(find_val, str):
                            decision = dec_val
                            findings = find_val
                        else:
                            findings = f"Review output had invalid schema (decision={dec_val}, findings={type(find_val)}): {result.output}"
                    else:
                        findings = f"Review output not a dict or missing fields: {result.output}"
                except Exception as je:
                    findings = f"Failed to parse review JSON output: {je}. Raw output: {result.output}"
            else:
                findings = f"Review prompt failed: {result.error}"

            self.review_repo.create(
                run_id=run_id,
                subject_type=subject_type,
                subject_id=subject_id,
                decision=decision,
                reviewer_route=f"{provider}:{model}",
                findings=findings,
                evidence_paths=evidence_paths
            )
            return decision == "approved"
        except Exception as e:
            self.review_repo.create(
                run_id=run_id,
                subject_type=subject_type,
                subject_id=subject_id,
                decision="rejected",
                reviewer_route=f"{provider}:{model}",
                findings=f"Review crashed with exception: {e}",
                evidence_paths=evidence_paths
            )
            return False

    def run_task_review(self, run_id: int, task_id: int, commit_sha: Optional[str]) -> bool:
        diff = "No commit SHA provided."
        if commit_sha:
            try:
                res = subprocess.run(
                    ["git", "diff", f"{commit_sha}^..{commit_sha}"],
                    cwd=Path.cwd(),
                    capture_output=True,
                    text=True,
                    check=True
                )
                diff = res.stdout
            except Exception:
                diff = "Could not fetch git diff."
        
        prompt = f"Please review task '{self.task_repo.get(task_id)['name']}' diff:\n\n{diff}"
        return self.run_agent_review(run_id, "task", task_id, prompt)

    def run_feature_review(self, run_id: int, feature_id: int) -> bool:
        feat = self.feature_repo.get(feature_id)
        prompt = f"Please review completed feature '{feat['name']}' with criteria: {feat['acceptance_criteria']}"
        return self.run_agent_review(run_id, "feature", feature_id, prompt)

    def run_final_review(self, run_id: int) -> bool:
        run = self.run_repo.get(run_id)
        prompt = f"Please perform the final review for run goal: {run['goal']}. Verify all features are complete and correct."
        return self.run_agent_review(run_id, "final", run_id, prompt)

    def create_integration_task(self, run_id: int, task: Dict[str, Any], branch_name: str) -> None:
        self.decision_repo.create(
            run_id=run_id,
            decision_type="architecture",
            is_autonomous=True,
            summary=f"Merge conflict on task {task['name']}. Spawned integration task.",
            details=f"Conflict branch: {branch_name}"
        )
        self.task_repo.create(
            run_id=run_id,
            feature_id=task["feature_id"],
            name=f"Resolve merge conflict on {task['name']}",
            role="planning",
            risk="high",
            scope=task["scope"],
            dependencies=[],
            required_verification=task["required_verification"]
        )

    def check_and_recover_quotas(self, run_id: int, required_routes: List[Dict[str, Any]]) -> bool:
        all_limited = True
        earliest_reset = None
        
        for route in required_routes:
            p_state = self.provider_repo.get(route["provider"], route["model"])
            if not p_state or p_state["availability"]:
                all_limited = False
                break
            else:
                reset_str = p_state.get("quota_limit_reset")
                if reset_str:
                    try:
                        import datetime
                        reset_time = datetime.datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
                        now = datetime.datetime.now(datetime.timezone.utc)
                        if now < reset_time:
                            if earliest_reset is None or reset_time < earliest_reset:
                                earliest_reset = reset_time
                        else:
                            self.provider_repo.save(route["provider"], route["model"], {}, True)
                            all_limited = False
                            break
                    except Exception:
                        pass

        if not all_limited:
            return True

        self.run_repo.update_status(run_id, "waiting_for_quota")
        self.notify(run_id, "waiting_for_quota", "All configured model routes are quota limited. Sleeping...")
        render_progress_md(self.conn, run_id, self.progress_path)

        if earliest_reset:
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            sleep_secs = (earliest_reset - now).total_seconds()
            if sleep_secs > 0:
                time.sleep(min(sleep_secs, 10.0))
        else:
            time.sleep(2.0)

        for route in required_routes:
            try:
                adapter = get_adapter(route["provider"], self.config)
                caps = adapter.discover_capabilities()
                if caps.get("installed"):
                    self.provider_repo.save(route["provider"], route["model"], {}, True)
            except Exception:
                pass

        self.run_repo.update_status(run_id, "running")
        render_progress_md(self.conn, run_id, self.progress_path)
        return False

    def run_loop(self, run_id: int) -> None:
        while True:
            run = self.run_repo.get(run_id)
            if run["status"] not in {"running", "waiting_for_quota"}:
                break

            # Quota recovery check before executing tasks
            impl_routes = self.config.routes.get("implementation", [])
            self.check_and_recover_quotas(run_id, impl_routes)

            tasks = self.task_repo.get_by_run(run_id)
            complete_task_names = {t["name"] for t in tasks if t["status"] == "complete"}
            for t in tasks:
                if t["status"] == "pending":
                    deps = t["dependencies"]
                    if all(dep in complete_task_names for dep in deps):
                        self.task_repo.update_status(t["id"], "ready")

            tasks = self.task_repo.get_by_run(run_id)
            
            # Check feature completion reviews
            features = self.feature_repo.get_by_run(run_id)
            for feature in features:
                if feature["review_status"] == "pending":
                    feat_tasks = [t for t in tasks if t["feature_id"] == feature["id"]]
                    if feat_tasks and all(t["status"] == "complete" for t in feat_tasks):
                        approved = self.run_feature_review(run_id, feature["id"])
                        if approved:
                            self.feature_repo.update_review_status(feature["id"], "approved")
                        else:
                            self.feature_repo.update_review_status(feature["id"], "rejected")
                            self.run_repo.update_status(run_id, "blocked")
                            self.notify(run_id, "blocked", f"Feature '{feature['name']}' review was rejected. Run is blocked.")

            ready_tasks = [t for t in tasks if t["status"] == "ready"]
            running_tasks = [t for t in tasks if t["status"] == "running"]
            blocked_tasks = [t for t in tasks if t["status"] == "blocked"]

            if not ready_tasks and not running_tasks:
                if blocked_tasks:
                    self.run_repo.update_status(run_id, "blocked")
                elif all(t["status"] == "complete" for t in tasks):
                    features = self.feature_repo.get_by_run(run_id)
                    rejected_features = [f for f in features if f["review_status"] == "rejected"]
                    if rejected_features:
                        self.run_repo.update_status(run_id, "blocked")
                        self.notify(run_id, "blocked", f"Feature(s) {[f['name'] for f in rejected_features]} were rejected. Run is blocked.")
                        break

                    self.run_repo.update_status(run_id, "reviewing")
                    final_approved = self.run_final_review(run_id)
                    
                    if final_approved:
                        migrations = self.test_migration_repo.get_by_run(run_id)
                        has_pending = any(m["approval_status"] == "pending" for m in migrations)
                        if has_pending:
                            self.run_repo.update_status(run_id, "complete_pending_test_review")
                        else:
                            self.run_repo.update_status(run_id, "complete")
                    else:
                        self.run_repo.update_status(run_id, "blocked")
                        self.notify(run_id, "blocked", "Final review was rejected. Run is blocked.")
                break

            max_workers = self.config.max_workers
            available_slots = max_workers - len(running_tasks)
            
            # Parse running tasks' scopes to get currently active files
            active_files = set()
            for rt in running_tasks:
                if rt.get("scope"):
                    try:
                        scope_data = json.loads(rt["scope"]) if isinstance(rt["scope"], str) else rt["scope"]
                        if isinstance(scope_data, dict) and "files" in scope_data:
                            for f in scope_data["files"]:
                                active_files.add(f)
                    except Exception:
                        pass

            # Filter ready tasks that don't conflict with active files or other scheduled tasks in this batch
            scheduled_tasks = []
            scheduled_files = set()
            for rt in ready_tasks:
                rt_files = set()
                if rt.get("scope"):
                    try:
                        scope_data = json.loads(rt["scope"]) if isinstance(rt["scope"], str) else rt["scope"]
                        if isinstance(scope_data, dict) and "files" in scope_data:
                            for f in scope_data["files"]:
                                rt_files.add(f)
                    except Exception:
                        pass
                
                # Check for overlap
                if (rt_files & active_files) or (rt_files & scheduled_files):
                    # Conflict! Skip scheduling in this batch
                    continue
                
                scheduled_tasks.append(rt)
                scheduled_files.update(rt_files)
                if len(scheduled_tasks) >= available_slots:
                    break

            if available_slots > 0 and scheduled_tasks:
                # Concurrently schedule tasks up to available slots
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(self.execute_task, run_id, t): t
                        for t in scheduled_tasks
                    }
                    for future in as_completed(futures):
                        future.result()
            elif running_tasks:
                # Wait for running tasks to finish and release file locks
                time.sleep(1.0)
            else:
                break

            render_plan_md(self.conn, run_id, self.plan_path)
            render_progress_md(self.conn, run_id, self.progress_path)
            time.sleep(0.5)
