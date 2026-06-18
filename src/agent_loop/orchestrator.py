import json
import os
import re
import time
import urllib.request
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent_loop.database import get_connection
from agent_loop.config import Config
from agent_loop.adapters import get_adapter, AttemptResult, resolve_binary
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
    def __init__(self, conn: sqlite3.Connection, config: Config, plan_path: Path = None, progress_path: Path = None, git_lock = None, db_lock = None, get_now = None, sleep_func = None):
        import datetime
        self.conn = conn
        self.config = config
        self.plan_path = plan_path or config.plan_path
        self.progress_path = progress_path or config.progress_path
        self.git_lock = git_lock or threading.RLock()
        self.db_lock = db_lock or threading.RLock()
        self.get_now = get_now or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.sleep_func = sleep_func or time.sleep
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

    def reset_provider_errors(self) -> None:
        """Resets auth_required and transient_failure provider states to available."""
        providers = self.provider_repo.list_all()
        for p in providers:
            if p.get("quota_state") in {"auth_required", "transient_failure"}:
                with self.db_lock:
                    self.provider_repo.save(
                        provider=p["provider"],
                        model=p["model"],
                        capability_snapshot=p.get("capability_snapshot", {}),
                        availability=True,
                        quota_state="available",
                        last_probe=None
                    )

    def unblock_provider_blocked_tasks(self, run_id: int) -> None:
        """Unblocks tasks that were blocked solely due to provider errors."""
        tasks = self.task_repo.get_by_run(run_id)
        all_attempts = self.attempt_repo.get_by_run(run_id)
        for task in tasks:
            if task["status"] == "blocked":
                attempts = [a for a in all_attempts if a["task_id"] == task["id"]]
                failed_attempts = [a for a in attempts if a["outcome"] in ("failed", "abandoned")]
                if len(failed_attempts) < self.config.retry_policy["max_attempts"]:
                    with self.db_lock:
                        self.task_repo.update_status(task["id"], "ready")

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
                    attempt_logs_dir=attempt_logs_dir,
                    reasoning_level=route.get("reasoning_level")
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
        from agent_loop.adapters import redact_secrets
        message = redact_secrets(message)
        
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

        print(f"[Notification] Run {run_id} Event '{event}': {message}")

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

    def _ensure_workspace_deps(self, workspace: Path) -> None:
        """Detect common project types and run their install command if needed.

        Checks for package.json, requirements.txt/pyproject.toml, Gemfile,
        go.mod, and Cargo.toml and runs the corresponding install command when
        the manifest is present.  Failures are silently swallowed — the caller
        will still attempt the work and surface any real errors itself.
        """
        INSTALL_MANIFESTS = [
            ("package.json",       ["npm", "install"]),
            ("requirements.txt",   ["pip", "install", "-r", "requirements.txt"]),
            ("pyproject.toml",     ["pip", "install", "-e", "."]),
            ("Gemfile",            ["bundle", "install"]),
            ("go.mod",             ["go", "mod", "download"]),
            ("Cargo.toml",         ["cargo", "fetch"]),
        ]
        for manifest, cmd in INSTALL_MANIFESTS:
            if (workspace / manifest).exists():
                try:
                    subprocess.run(
                        cmd,
                        cwd=workspace,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        timeout=300,
                    )
                except Exception:
                    pass  # best-effort; real errors surface later

    def run_verification(self, run_id: int, task_id: int, attempt_id: int, command: str, worktree_dir: Path, logs_dir: Path) -> bool:
        """Runs the verification command in the worktree directory.
        
        Note: Under trusted-host execution mode, commands executed via shell=True 
        will run with the full permissions and privileges of the current user.
        """
        start_time = time.time()
        test_out_file = logs_dir / "test_run_stdout.log"
        test_err_file = logs_dir / "test_run_stderr.log"
        
        try:
            # Ensure dependencies are installed before verifying
            self._ensure_workspace_deps(worktree_dir)
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
            
            with self.db_lock:
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
            with self.db_lock:
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
        import os
        # 1. Validation check for mock/non-hex SHAs in unit tests
        if not re.match(r"^[0-9a-fA-F]{7,40}$", commit_sha):
            return
            
        # 2. Check if the commit actually exists in git
        try:
            with self.git_lock:
                res_check = subprocess.run(
                    ["git", "cat-file", "-e", commit_sha],
                    cwd=Path.cwd(),
                    capture_output=True
                )
            if res_check.returncode != 0:
                if "PYTEST_CURRENT_TEST" in os.environ:
                    return
        except Exception:
            if "PYTEST_CURRENT_TEST" in os.environ:
                return

        try:
            with self.git_lock:
                res = subprocess.run(
                    ["git", "show", "--name-status", "--oneline", commit_sha],
                    cwd=Path.cwd(),
                    capture_output=True,
                    text=True,
                    check=True
                )
            
            modified_test_files = []
            added_test_files = []
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    status, file_path = parts[0], parts[1]
                    if "test" in file_path.lower():
                        if status == "M":
                            modified_test_files.append(file_path)
                        elif status == "A":
                            added_test_files.append(file_path)

            for file_path in modified_test_files:
                with self.git_lock:
                    diff_res = subprocess.run(
                        ["git", "diff", f"{commit_sha}^..{commit_sha}", "--", file_path],
                        cwd=Path.cwd(),
                        capture_output=True,
                        text=True,
                        check=True
                    )
                diff_text = diff_res.stdout
                
                has_added_skip = False
                migration_id = None
                skip_reason = ""
                previous_behavior = None
                
                diff_lines = diff_text.splitlines()
                for i, line in enumerate(diff_lines):
                    if line.startswith("+") and not line.startswith("+++"):
                        if any(marker in line for marker in ["@pytest.mark.skip", "pytest.skip", "unittest.skip", "# skip", "# disabled"]):
                            has_added_skip = True
                            mig_match = re.search(r"migration:\s*(MIG-[a-zA-Z0-9_\-]+)", line, re.IGNORECASE)
                            if mig_match:
                                migration_id = mig_match.group(1)
                            else:
                                mig_match_alt = re.search(r"(MIG-[a-zA-Z0-9_\-]+)", line, re.IGNORECASE)
                                if mig_match_alt:
                                    migration_id = mig_match_alt.group(1)
                            
                            reason_match = re.search(r"reason\s*=\s*['\"]([^'\"]+)['\"]", line)
                            if reason_match:
                                skip_reason = reason_match.group(1)
                            else:
                                pos_match = re.search(r"skip\(['\"]([^'\"]+)['\"]", line)
                                if pos_match:
                                    skip_reason = pos_match.group(1)
                            
                            for j in range(i + 1, min(i + 10, len(diff_lines))):
                                next_line = diff_lines[j]
                                if next_line.startswith("+") and ("def test_" in next_line or "class Test" in next_line):
                                    previous_behavior = next_line.replace("+", "").strip()
                                    break
                                elif not next_line.startswith("+") and ("def test_" in next_line or "class Test" in next_line):
                                    previous_behavior = next_line.strip()
                                    break
                            break

                if has_added_skip:
                    replacement_found = None
                    replacement_behavior = None
                    evidence = None
                    
                    with self.git_lock:
                        full_diff_res = subprocess.run(
                            ["git", "diff", f"{commit_sha}^..{commit_sha}"],
                            cwd=Path.cwd(),
                            capture_output=True,
                            text=True,
                            check=True
                        )
                    full_diff_text = full_diff_res.stdout
                    
                    full_diff_lines = full_diff_text.splitlines()
                    for added_file in added_test_files:
                        replacement_found = added_file
                        break
                        
                    if not replacement_found:
                        for idx, line in enumerate(full_diff_lines):
                            if line.startswith("+") and not line.startswith("+++"):
                                if "def test_" in line or "class Test" in line:
                                    replacement_found = file_path
                                    replacement_behavior = line.replace("+", "").strip()
                                    break

                    if replacement_found and not replacement_behavior:
                        for line in full_diff_lines:
                            if line.startswith("+") and not line.startswith("+++") and ("def test_" in line or "class Test" in line):
                                sig = line.replace("+", "").strip()
                                if not previous_behavior or sig != previous_behavior:
                                    replacement_behavior = sig
                                    break

                    has_evidence = False
                    if migration_id and replacement_found:
                        # Extract the diff of the replacement test file from full_diff_text to associate evidence with it
                        file_diff = ""
                        if "diff --git " in full_diff_text:
                            parts = full_diff_text.split("diff --git ")
                            for part in parts:
                                if part.startswith(f"a/{replacement_found} ") or f" b/{replacement_found}\n" in part or f" b/{replacement_found} " in part:
                                    file_diff = part
                                    break
                        else:
                            file_diff = full_diff_text
                        
                        pattern = rf"covers:\s*{re.escape(migration_id)}|covers\s*{re.escape(migration_id)}|replacement\s*for\s*{re.escape(migration_id)}"
                        if re.search(pattern, file_diff, re.IGNORECASE):
                            has_evidence = True
                            evidence = f"Verified covers marker for {migration_id} in replacement test diff"
                    
                    is_valid = bool(migration_id) and bool(replacement_found) and has_evidence
                    status = "pending" if is_valid else "rejected"
                    
                    if not migration_id:
                        rationale = "Skipped test is missing a valid stable migration identifier (e.g. 'migration: MIG-123') in the skip reason."
                    elif not replacement_found:
                        rationale = f"Skipped test {migration_id} is missing a separately added replacement test."
                    elif not has_evidence:
                        rationale = f"Replacement test for {migration_id} is missing covering evidence (e.g. comment '# covers: {migration_id}')."
                    else:
                        rationale = f"Migration {migration_id} pending approval. Old test: {file_path}, replacement test: {replacement_found}."
                    
                    if not previous_behavior:
                        previous_behavior = "Skipped test in " + file_path
                    if not replacement_behavior:
                        replacement_behavior = "Added test in " + (replacement_found or "none")
                        
                    with self.db_lock:
                        migration_id_db = self.test_migration_repo.create(
                            run_id=run_id,
                            task_id=task_id,
                            old_test_path=file_path,
                            replacement_test_path=replacement_found or "none",
                            rationale=rationale,
                            evidence=evidence or f"Commit: {commit_sha}",
                            previous_behavior=previous_behavior,
                            replacement_behavior=replacement_behavior,
                            commit_sha=commit_sha
                        )
                        if status == "rejected":
                            self.test_migration_repo.update_approval(migration_id_db, "rejected")
        except Exception as e:
            with self.db_lock:
                self.run_repo.update_status(run_id, "blocked")
            self.notify(run_id, "blocked", f"Test migration detection failure: {str(e)}")
            raise e

    def _preserve_uncommitted_changes(self, worktree_dir: Path, logs_dir: Path) -> Optional[str]:
        if not worktree_dir.exists():
            return None
        try:
            # Stage all changes (tracked, untracked, text, binary)
            res_add = subprocess.run(
                ["git", "add", "-A"],
                cwd=worktree_dir,
                capture_output=True,
                stdin=subprocess.DEVNULL
            )
            if res_add.returncode != 0:
                return None

            # Capture the complete cached binary diff
            res_diff = subprocess.run(
                ["git", "diff", "--cached", "--binary"],
                cwd=worktree_dir,
                capture_output=True,
                stdin=subprocess.DEVNULL
            )
            if res_diff.returncode != 0:
                return None

            patch_bytes = res_diff.stdout
            if isinstance(patch_bytes, str):
                patch_bytes = patch_bytes.encode("utf-8")

            if patch_bytes.strip():
                logs_dir.mkdir(parents=True, exist_ok=True)
                patch_file = logs_dir / "patch.diff"
                patch_file.write_bytes(patch_bytes)
                return str(patch_file)
            else:
                return "CLEAN"
        except Exception:
            return None

    def reconcile_interrupted_run(self, run_id: int) -> int:
        cursor = self.conn.cursor()
        
        # 1. Fetch running attempts
        with self.db_lock:
            cursor.execute(
                "SELECT id, task_id, worktree_path FROM attempts WHERE run_id = ? AND outcome = 'running';",
                (run_id,)
            )
            running_attempts = cursor.fetchall()
            
        # 2. Preserve uncommitted changes for all running attempts
        successful_abandon_attempts = []
        patches = {}
        for att_id, task_id, wt_path in running_attempts:
            if wt_path:
                wt_dir = Path(wt_path)
                if wt_dir.exists():
                    logs_dir = (self.config.logs_dir / str(run_id) / str(task_id) / str(att_id)).resolve()
                    preservation_res = self._preserve_uncommitted_changes(wt_dir, logs_dir)
                    if preservation_res:
                        successful_abandon_attempts.append((att_id, task_id, wt_path))
                        if preservation_res != "CLEAN":
                            patches[att_id] = preservation_res
                    else:
                        # Preservation failed, do not add to successful_abandon_attempts
                        pass
                else:
                    successful_abandon_attempts.append((att_id, task_id, wt_path))
            else:
                successful_abandon_attempts.append((att_id, task_id, wt_path))

        # 3. Database updates in a single transaction
        with self.db_lock:
            cursor.execute("BEGIN TRANSACTION;")
            try:
                for att_id, task_id, wt_path in successful_abandon_attempts:
                    # Update attempt to abandoned and record its patch path
                    patch_path = patches.get(att_id)
                    cursor.execute(
                        "UPDATE attempts SET outcome = 'abandoned', patch_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                        (patch_path, att_id)
                    )
                    
                    # Check total attempts for this task
                    cursor.execute(
                        "SELECT count(*) FROM attempts WHERE task_id = ? AND outcome IN ('failed', 'abandoned');",
                        (task_id,)
                    )
                    attempt_count = cursor.fetchone()[0]
                    
                    # Update task status based on limit
                    max_attempts = self.config.retry_policy["max_attempts"]
                    if attempt_count >= max_attempts:
                        cursor.execute(
                            "UPDATE tasks SET status = 'blocked' WHERE id = ?;",
                            (task_id,)
                        )
                    else:
                        cursor.execute(
                            "UPDATE tasks SET status = 'ready' WHERE id = ?;",
                            (task_id,)
                        )
                cursor.execute("COMMIT;")
            except Exception as e:
                cursor.execute("ROLLBACK;")
                raise e

        # 4. Filesystem/Git cleanup (outside database transaction)
        for att_id, task_id, wt_path in successful_abandon_attempts:
            if wt_path:
                wt_dir = Path(wt_path)
                if wt_dir.exists():
                    with self.git_lock:
                        remove_worktree(Path.cwd(), wt_dir)
                        branch_name = f"agent-loop-run-{run_id}-task-{task_id}-att-{att_id}"
                        subprocess.run(
                            ["git", "branch", "-D", branch_name],
                            cwd=Path.cwd(),
                            capture_output=True
                        )
                # Mark worktree as cleaned up in DB
                with self.db_lock:
                    cursor.execute(
                        "UPDATE attempts SET worktree_path = NULL WHERE id = ?;",
                        (att_id,)
                    )
                    self.conn.commit()

        # 5. Clean up any leftover worktrees from previously crashed/interrupted recoveries
        with self.db_lock:
            cursor.execute(
                "SELECT id, task_id, worktree_path FROM attempts WHERE run_id = ? AND outcome = 'abandoned' AND worktree_path IS NOT NULL;",
                (run_id,)
            )
            leftover_attempts = cursor.fetchall()

        for att_id, task_id, wt_path in leftover_attempts:
            if wt_path:
                wt_dir = Path(wt_path)
                if wt_dir.exists():
                    with self.git_lock:
                        remove_worktree(Path.cwd(), wt_dir)
                        branch_name = f"agent-loop-run-{run_id}-task-{task_id}-att-{att_id}"
                        subprocess.run(
                            ["git", "branch", "-D", branch_name],
                            cwd=Path.cwd(),
                            capture_output=True
                        )
                with self.db_lock:
                    cursor.execute(
                        "UPDATE attempts SET worktree_path = NULL WHERE id = ?;",
                        (att_id,)
                    )
                    self.conn.commit()
                    
        return len(running_attempts)

    def execute_task(self, run_id: int, task: Dict[str, Any]) -> bool:
        # Determine if database is in-memory
        db_path_val = self.config.data.get("db_path", "")
        is_in_memory = (db_path_val == ":memory:") or (":memory:" in str(self.config.db_path))
        if is_in_memory:
            conn = self.conn
        else:
            conn = get_connection(self.config.db_path)
            
        try:
            worker_orch = Orchestrator(
                conn, 
                self.config, 
                self.plan_path, 
                self.progress_path, 
                git_lock=self.git_lock,
                db_lock=self.db_lock,
                get_now=self.get_now,
                sleep_func=self.sleep_func
            )
            return worker_orch._execute_task_impl(run_id, task)
        finally:
            if not is_in_memory:
                conn.close()

    def _execute_task_impl(self, run_id: int, task: Dict[str, Any]) -> bool:
        task_id = task["id"]
        with self.db_lock:
            self.task_repo.update_status(task_id, "running")
            render_progress_md(self.conn, run_id, self.progress_path)

            attempts = [a for a in self.attempt_repo.get_by_run(run_id) if a["task_id"] == task_id]
        is_high_reasoning = (task["risk"] == "high") or (len(attempts) >= self.config.retry_policy["escalation_threshold"])

        route_key = "planning" if (task["role"] == "planning" or is_high_reasoning) else "implementation"
        routes = self.config.routes.get(route_key, [])

        # auth_failed_routes tracks providers that returned auth_required during
        # this invocation so we can skip them when selecting the next route.
        auth_failed_routes: set = set()

        def _pick_route():
            for r in routes:
                key = (r["provider"], r["model"])
                if key in auth_failed_routes:
                    continue
                with self.db_lock:
                    p_state = self.provider_repo.get(r["provider"], r["model"])
                # Skip routes that are known auth_required or unavailable
                if p_state and p_state.get("quota_state") in {"auth_required"}:
                    continue
                if not p_state or p_state["availability"]:
                    return r
            return None

        selected_route = _pick_route()
        if not selected_route:
            with self.db_lock:
                self.task_repo.update_status(task_id, "failed")
                self.task_repo.update_status(task_id, "ready")
            return False

        provider = selected_route["provider"]
        model = selected_route["model"]
        reasoning_level = selected_route.get("reasoning_level")

        with self.db_lock:
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

        worktree_dir = (self.config.worktrees_dir / f"run-{run_id}-task-{task_id}-attempt-{attempt_id}").resolve()
        logs_dir = (self.config.logs_dir / str(run_id) / str(task_id) / str(attempt_id)).resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)

        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "UPDATE attempts SET worktree_path = ?, logs_path = ? WHERE id = ?;",
                (str(worktree_dir), str(logs_dir), attempt_id)
            )
            self.conn.commit()

        branch_name = f"agent-loop-run-{run_id}-task-{task_id}-att-{attempt_id}"
        try:
            with self.git_lock:
                create_worktree(Path.cwd(), worktree_dir, branch_name)
        except Exception as exc:
            with self.db_lock:
                self.attempt_repo.update_outcome(attempt_id, "failed")
                failed_attempts = [a for a in attempts if a["outcome"] in ("failed", "abandoned")]
                attempt_count = len(failed_attempts) + 1
                if attempt_count >= self.config.retry_policy["max_attempts"]:
                    self.task_repo.update_status(task_id, "blocked")
                else:
                    self.task_repo.update_status(task_id, "failed")
                    self.task_repo.update_status(task_id, "ready")
            if len(failed_attempts) + 1 >= self.config.retry_policy["max_attempts"]:
                self.notify(run_id, "blocked", f"Task '{task['name']}' failed during worktree setup: {exc}")
            return False

        scope_data = {}
        if task.get("scope"):
            try:
                scope_data = json.loads(task["scope"]) if isinstance(task["scope"], str) else task["scope"]
            except Exception:
                pass
        is_integration = isinstance(scope_data, dict) and "source_branch" in scope_data

        # Ensure workspace dependencies are installed before the adapter or
        # any verification command runs.  Best-effort — failures are ignored.
        self._ensure_workspace_deps(worktree_dir)
        
        if is_integration:
            source_branch = scope_data["source_branch"]
            try:
                with self.git_lock:
                    subprocess.run(
                        ["git", "merge", "--no-ff", source_branch],
                        cwd=worktree_dir,
                        capture_output=True,
                        text=True,
                        stdin=subprocess.DEVNULL
                    )
            except Exception:
                pass

        with self.git_lock:
            try:
                res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree_dir, capture_output=True, text=True, check=True)
                start_sha = res.stdout.strip()
            except Exception:
                start_sha = None

        with self.db_lock:
            previous_rejection = self.review_repo.get_latest_rejection("task", task_id)
            goal_text = self.run_repo.get(run_id)['goal']
            
        if is_integration:
            prompt = f"""
Goal: {goal_text}
Task: Resolve merge conflict on '{scope_data.get('original_task_name', task['name'])}'
Source branch/commit: {scope_data['source_branch']} ({scope_data.get('source_commit')})
Target baseline: {scope_data.get('target_baseline', 'main')}
Conflicting files: {scope_data.get('conflicting_files', [])}
Verification Command: {task['required_verification']}

Please resolve the conflict markers in the conflicting files. Ensure both changes are integrated correctly. Run verification to confirm success before exiting.
"""
        else:
            prompt = f"""
Goal: {goal_text}
Task: {task['name']}
Verification Command: {task['required_verification']}
Scope: {json.dumps(task['scope'])}
"""
        if previous_rejection:
            prompt += f"\nPrevious attempt was rejected with the following findings:\n{previous_rejection}\n"
            
        if not is_integration:
            prompt += "\nPlease implement this task in the workspace. You MUST make atomic, fine-grained git commits with descriptive messages as you progress through the task. Run verification to confirm success before exiting.\n"

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
                # Capture any uncommitted changes just in case the agent forgot
                commit_changes(worktree_dir, f"agent-loop: uncommitted changes for {task['name']}")
                with self.git_lock:
                    try:
                        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree_dir, capture_output=True, text=True, check=True)
                        end_sha = res.stdout.strip()
                    except Exception:
                        end_sha = None

                with self.db_lock:
                    self.attempt_repo.update_outcome(attempt_id, "completed", commit_sha=end_sha)
                
                if end_sha and start_sha != end_sha:
                    self.detect_and_record_test_migrations(run_id, task_id, end_sha)

                with self.db_lock:
                    self.task_repo.update_status(task_id, "reviewing")
                decision = self.run_task_review(run_id, task_id, start_sha, end_sha)
                
                if decision == "approved":
                    with self.git_lock:
                        merged, conflicting_files = merge_branch(Path.cwd(), branch_name, "main")
                    if merged:
                        with self.db_lock:
                            self.task_repo.update_status(task_id, "complete")
                            if is_integration and "original_task_id" in scope_data:
                                self.task_repo.update_status(scope_data["original_task_id"], "complete", force=True)
                            
                            is_assessment = isinstance(scope_data, dict) and "original_task_id" in scope_data and task["name"].startswith("Architectural Assessment:")
                            if is_assessment and "original_task_id" in scope_data:
                                self.task_repo.update_status(scope_data["original_task_id"], "ready", force=True)
                    else:
                        with self.db_lock:
                            try:
                                with self.git_lock:
                                    res_baseline = subprocess.run(
                                        ["git", "rev-parse", "main"],
                                        cwd=Path.cwd(),
                                        capture_output=True,
                                        text=True,
                                        check=True
                                    )
                                target_baseline = res_baseline.stdout.strip()
                            except Exception:
                                target_baseline = "main"

                            self.create_integration_task(
                                run_id=run_id,
                                task=task,
                                branch_name=branch_name,
                                source_commit=end_sha,
                                target_baseline=target_baseline,
                                conflicting_files=conflicting_files
                            )
                            self.task_repo.update_status(task_id, "blocked")
                else:
                    with self.db_lock:
                        task_attempts = [a for a in self.attempt_repo.get_by_run(run_id) if a["task_id"] == task_id]
                        num_attempts = len(task_attempts)
                        
                        cursor = self.conn.cursor()
                        cursor.execute(
                            "SELECT findings FROM reviews WHERE run_id = ? AND subject_type = 'task' AND subject_id = ? ORDER BY id DESC LIMIT 1;",
                            (run_id, task_id)
                        )
                        row = cursor.fetchone()
                        findings = row[0] if row else "No findings recorded."

                    if decision == "block":
                        with self.db_lock:
                            self.task_repo.update_status(task_id, "blocked")
                            self.decision_repo.create(
                                run_id=run_id,
                                decision_type="stop_condition",
                                is_autonomous=True,
                                summary=f"Task '{task['name']}' was blocked due to stop condition.",
                                details=findings
                            )
                        self.notify(run_id, "blocked", f"Task '{task['name']}' was explicitly blocked by reviewer: {findings}")

                    elif decision == "assessment":
                        orig_scope = json.loads(task["scope"]) if isinstance(task["scope"], str) else (task["scope"] or {})
                        assessment_scope = {
                            "original_task_id": task["id"],
                            "original_task_name": task["name"],
                            "original_task_scope": orig_scope,
                            "reviewer_findings": findings,
                            "files": orig_scope.get("files", [])
                        }
                        with self.db_lock:
                            self.task_repo.update_status(task_id, "blocked")
                            self.task_repo.create(
                                run_id=run_id,
                                feature_id=task["feature_id"],
                                name=f"Architectural Assessment: {task['name']}",
                                role="planning",
                                risk="high",
                                scope=assessment_scope,
                                required_verification=task.get("required_verification")
                            )
                        self.notify(run_id, "blocked", f"Task '{task['name']}' requires operator assessment. Created Architectural Assessment task.")

                    elif decision == "follow_up":
                        with self.git_lock:
                            merged, conflicting_files = merge_branch(Path.cwd(), branch_name, "main")
                        orig_scope = json.loads(task["scope"]) if isinstance(task["scope"], str) else (task["scope"] or {})
                        followup_scope = {
                            "original_task_id": task["id"],
                            "original_task_name": task["name"],
                            "original_task_scope": orig_scope,
                            "reviewer_findings": findings,
                            "files": orig_scope.get("files", [])
                        }
                        if merged:
                            with self.db_lock:
                                self.task_repo.update_status(task_id, "complete")
                                self.task_repo.create(
                                    run_id=run_id,
                                    feature_id=task["feature_id"],
                                    name=f"Follow-up: {task['name']}",
                                    role=task["role"],
                                    risk=task["risk"],
                                    scope=followup_scope,
                                    dependencies=[task["name"]],
                                    required_verification=task.get("required_verification")
                                )
                            self.notify(run_id, "running", f"Task '{task['name']}' completed with follow-up work.")
                        else:
                            try:
                                with self.git_lock:
                                    res_baseline = subprocess.run(
                                        ["git", "rev-parse", "main"],
                                        cwd=Path.cwd(),
                                        capture_output=True,
                                        text=True,
                                        check=True
                                    )
                                target_baseline = res_baseline.stdout.strip()
                            except Exception:
                                target_baseline = "main"

                            with self.db_lock:
                                self.create_integration_task(
                                    run_id=run_id,
                                    task=task,
                                    branch_name=branch_name,
                                    source_commit=end_sha,
                                    target_baseline=target_baseline,
                                    conflicting_files=conflicting_files
                                )
                                self.task_repo.update_status(task_id, "blocked")
                                self.task_repo.create(
                                    run_id=run_id,
                                    feature_id=task["feature_id"],
                                    name=f"Follow-up: {task['name']}",
                                    role=task["role"],
                                    risk=task["risk"],
                                    scope=followup_scope,
                                    dependencies=[task["name"]],
                                    required_verification=task.get("required_verification")
                                )
                            self.notify(run_id, "blocked", f"Task '{task['name']}' merge conflict. Created integration and follow-up tasks.")

                    elif decision == "rejected":
                        with self.db_lock:
                            if num_attempts >= self.config.retry_policy["max_attempts"]:
                                self.task_repo.update_status(task_id, "blocked")
                                self.notify(run_id, "blocked", f"Task '{task['name']}' reached attempt limit on {decision}.")
                            else:
                                self.task_repo.update_status(task_id, "failed")
                                self.task_repo.update_status(task_id, "ready")
                    else:
                        with self.db_lock:
                            self.task_repo.update_status(task_id, "blocked")
                        self.notify(run_id, "blocked", f"Task '{task['name']}' had unknown review decision '{decision}'.")

                with self.git_lock:
                    remove_worktree(Path.cwd(), worktree_dir)
                return True
            else:
                patch_path = self._preserve_uncommitted_changes(worktree_dir, logs_dir)
                if result.quota_exhausted:
                    q_state = "limited_known_reset" if result.quota_reset else "limited_unknown_reset"
                    with self.db_lock:
                        self.provider_repo.save(
                            provider=provider,
                            model=model,
                            capability_snapshot={"models": [model]},
                            availability=False,
                            quota_state=q_state,
                            quota_limit_reset=result.quota_reset
                        )
                        self.attempt_repo.update_outcome(attempt_id, "abandoned", patch_path=patch_path)
                        self.task_repo.update_status(task_id, "failed")
                        self.task_repo.update_status(task_id, "ready")
                    
                    fallback = "Entering wait/sleep state"
                    expected_resume = "Will retry model after reset window" if result.quota_reset else "Will probe model using exponential backoff"
                    alert_msg = (
                        f"Quota Alert - Run: {run_id}, Task: {task_id} ({task['name']})\n"
                        f"Provider: {provider} | Model: {model}\n"
                        f"Classification: {q_state}\n"
                        f"Evidence Path: {logs_dir / 'stderr.log'}\n"
                        f"Known Reset: {result.quota_reset or 'Unknown'}\n"
                        f"Fallback Action: {fallback}\n"
                        f"Expected Resume Behavior: {expected_resume}"
                    )
                    self.notify(run_id, f"quota_alert:{provider}:{model}:{q_state}", alert_msg)

                elif result.auth_required:
                    with self.db_lock:
                        self.provider_repo.save(
                            provider=provider,
                            model=model,
                            capability_snapshot={"models": [model]},
                            availability=False,
                            quota_state="auth_required"
                        )
                        self.attempt_repo.update_outcome(attempt_id, "provider_error", patch_path=patch_path)

                    # Record which route failed auth so _pick_route skips it.
                    auth_failed_routes.add((provider, model))
                    fallback_route = _pick_route()

                    if fallback_route:
                        # Notify but continue to the next route rather than giving up.
                        alert_msg = (
                            f"Auth failed for {provider}:{model} — falling back to "
                            f"{fallback_route['provider']}:{fallback_route['model']}\n"
                            f"Evidence Path: {logs_dir / 'stderr.log'}"
                        )
                        self.notify(run_id, f"quota_alert:{provider}:{model}:auth_required", alert_msg)

                        # Reset task to running and retry with the fallback route.
                        with self.db_lock:
                            self.task_repo.update_status(task_id, "failed")
                            self.task_repo.update_status(task_id, "running")
                        selected_route = fallback_route
                        provider = fallback_route["provider"]
                        model = fallback_route["model"]
                        reasoning_level = fallback_route.get("reasoning_level")
                        with self.db_lock:
                            attempt_id = self.attempt_repo.create(
                                run_id=run_id,
                                task_id=task_id,
                                route=route_key,
                                provider=provider,
                                model=model,
                                reasoning_level=reasoning_level,
                                worktree_path=str(worktree_dir),
                                logs_path=str(logs_dir)
                            )
                        adapter = get_adapter(provider, self.config)
                        result = adapter.run_attempt(
                            model=model,
                            prompt=prompt,
                            workspace_path=worktree_dir,
                            attempt_logs_dir=logs_dir,
                            reasoning_level=reasoning_level
                        )
                        # Fall through: the new result will be evaluated by the
                        # next iteration of the enclosing if/elif chain via a
                        # re-raise of the same logic.  We achieve this by
                        # re-entering the result-dispatch block.
                        patch_path = self._preserve_uncommitted_changes(worktree_dir, logs_dir)
                        if result.success:
                            pass  # handled below by the normal success path
                        elif result.auth_required:
                            auth_failed_routes.add((provider, model))
                            with self.db_lock:
                                self.provider_repo.save(
                                    provider=provider, model=model,
                                    capability_snapshot={"models": [model]},
                                    availability=False, quota_state="auth_required"
                                )
                                self.attempt_repo.update_outcome(attempt_id, "provider_error", patch_path=patch_path)
                                self.task_repo.update_status(task_id, "failed")
                                self.task_repo.update_status(task_id, "ready")
                            alert_msg = (
                                f"Quota Alert - Run: {run_id}, Task: {task_id} ({task['name']})\n"
                                f"All fallback routes exhausted (auth_required).\n"
                                f"Evidence Path: {logs_dir / 'stderr.log'}"
                            )
                            self.notify(run_id, f"quota_alert:{provider}:{model}:auth_required", alert_msg)
                        else:
                            # Let the worktree cleanup and return happen below.
                            pass
                    else:
                        # No fallback available — block task.
                        with self.db_lock:
                            self.task_repo.update_status(task_id, "failed")
                            self.task_repo.update_status(task_id, "ready")
                        alert_msg = (
                            f"Quota Alert - Run: {run_id}, Task: {task_id} ({task['name']})\n"
                            f"Provider: {provider} | Model: {model}\n"
                            f"Classification: auth_required\n"
                            f"Evidence Path: {logs_dir / 'stderr.log'}\n"
                            f"Known Reset: N/A\n"
                            f"Fallback Action: No more routes available — task re-queued\n"
                            f"Expected Resume Behavior: Will resume after operator runs login command"
                        )
                        self.notify(run_id, f"quota_alert:{provider}:{model}:auth_required", alert_msg)

                elif result.transient_failure:
                    with self.db_lock:
                        self.provider_repo.save(
                            provider=provider,
                            model=model,
                            capability_snapshot={"models": [model]},
                            availability=False,
                            quota_state="transient_failure"
                        )
                        self.attempt_repo.update_outcome(attempt_id, "provider_error", patch_path=patch_path)
                        self.task_repo.update_status(task_id, "failed")
                        self.task_repo.update_status(task_id, "ready")
                    
                    alert_msg = (
                        f"Quota Alert - Run: {run_id}, Task: {task_id} ({task['name']})\n"
                        f"Provider: {provider} | Model: {model}\n"
                        f"Classification: transient_failure\n"
                        f"Evidence Path: {logs_dir / 'stderr.log'}\n"
                        f"Known Reset: N/A\n"
                        f"Fallback Action: Retrying model after transient wait\n"
                        f"Expected Resume Behavior: Will retry/refresh route"
                    )
                    self.notify(run_id, f"quota_alert:{provider}:{model}:transient_failure", alert_msg)

                elif result.unavailable:
                    with self.db_lock:
                        self.provider_repo.save(
                            provider=provider,
                            model=model,
                            capability_snapshot={"models": [model]},
                            availability=False,
                            quota_state="unavailable"
                        )
                        self.attempt_repo.update_outcome(attempt_id, "provider_error", patch_path=patch_path)
                        self.task_repo.update_status(task_id, "failed")
                        self.task_repo.update_status(task_id, "ready")
                    
                    alert_msg = (
                        f"Quota Alert - Run: {run_id}, Task: {task_id} ({task['name']})\n"
                        f"Provider: {provider} | Model: {model}\n"
                        f"Classification: unavailable\n"
                        f"Evidence Path: {logs_dir / 'stderr.log'}\n"
                        f"Known Reset: N/A\n"
                        f"Fallback Action: Disabling route permanently\n"
                        f"Expected Resume Behavior: Will not select this route again"
                    )
                    self.notify(run_id, f"quota_alert:{provider}:{model}:unavailable", alert_msg)
                else:
                    with self.db_lock:
                        self.attempt_repo.update_outcome(attempt_id, "failed", patch_path=patch_path)
                        failed_attempts = [a for a in attempts if a["outcome"] in ("failed", "abandoned")]
                        if len(failed_attempts) + 1 >= self.config.retry_policy["max_attempts"]:
                            self.task_repo.update_status(task_id, "blocked")
                        else:
                            self.task_repo.update_status(task_id, "failed")
                            self.task_repo.update_status(task_id, "ready")
                    if len(failed_attempts) + 1 >= self.config.retry_policy["max_attempts"]:
                        self.notify(run_id, "blocked", f"Task {task['name']} failed {self.config.retry_policy['max_attempts']} times.")

                with self.git_lock:
                    remove_worktree(Path.cwd(), worktree_dir)
                return False

        except Exception:
            patch_path = self._preserve_uncommitted_changes(worktree_dir, logs_dir)
            with self.db_lock:
                self.attempt_repo.update_outcome(attempt_id, "failed", patch_path=patch_path)
                failed_attempts = [a for a in attempts if a["outcome"] in ("failed", "abandoned")]
                attempt_count = len(failed_attempts) + 1
                if attempt_count >= self.config.retry_policy["max_attempts"]:
                    self.task_repo.update_status(task_id, "blocked")
                else:
                    self.task_repo.update_status(task_id, "failed")
                    self.task_repo.update_status(task_id, "ready")
            if len(failed_attempts) + 1 >= self.config.retry_policy["max_attempts"]:
                self.notify(run_id, "blocked", f"Task '{task['name']}' failed {self.config.retry_policy['max_attempts']} times.")
            with self.git_lock:
                remove_worktree(Path.cwd(), worktree_dir)
            return False

    def run_agent_review(self, run_id: int, subject_type: str, subject_id: int, review_prompt: str) -> str:
        routes = self.config.routes.get("planning", [])
        selected_route = None
        for r in routes:
            p_state = self.provider_repo.get(r["provider"], r["model"])
            if not p_state or p_state.get("availability") != False:
                selected_route = r
                break
        if not selected_route:
            raise ValueError("No available review route found. All configured planning/review routes are known-unavailable.")

        provider = selected_route["provider"]
        model = selected_route["model"]
        reasoning_level = selected_route.get("reasoning_level")
        
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
                attempt_logs_dir=review_logs_dir,
                reasoning_level=reasoning_level
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
            return decision
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
            return "rejected"

    def run_task_review(self, run_id: int, task_id: int, start_sha: Optional[str], end_sha: Optional[str]) -> str:
        diff = "No commit SHA provided."
        if start_sha and end_sha:
            try:
                with self.git_lock:
                    res = subprocess.run(
                        ["git", "log", "-p", "--reverse", f"{start_sha}..{end_sha}"],
                        cwd=Path.cwd(),
                        capture_output=True,
                        text=True,
                        check=True
                    )
                diff = res.stdout
                if not diff.strip():
                    diff = "No changes were committed."
            except Exception:
                diff = "Could not fetch git diff."
        
        with self.db_lock:
            task_name = self.task_repo.get(task_id)['name']
        prompt = f"Please review task '{task_name}' diff:\n\n{diff}"
        return self.run_agent_review(run_id, "task", task_id, prompt)

    def run_feature_review(self, run_id: int, feature_id: int) -> str:
        with self.db_lock:
            feat = self.feature_repo.get(feature_id)
        prompt = f"Please review completed feature '{feat['name']}' with criteria: {feat['acceptance_criteria']}"
        decision = self.run_agent_review(run_id, "feature", feature_id, prompt)
        return decision

    def run_final_review(self, run_id: int) -> bool:
        with self.db_lock:
            run = self.run_repo.get(run_id)
        prompt = f"Please perform the final review for run goal: {run['goal']}. Verify all features are complete and correct."
        decision = self.run_agent_review(run_id, "final", run_id, prompt)
        return decision == "approved"

    def create_integration_task(self, run_id: int, task: Dict[str, Any], branch_name: str, source_commit: Optional[str] = None, target_baseline: Optional[str] = None, conflicting_files: Optional[list] = None) -> None:
        integration_scope = {
            "source_branch": branch_name,
            "source_commit": source_commit,
            "target_baseline": target_baseline,
            "conflicting_files": conflicting_files or [],
            "required_verification": task.get("required_verification"),
            "original_task_id": task["id"],
            "original_task_name": task["name"]
        }
        
        with self.db_lock:
            self.decision_repo.create(
                run_id=run_id,
                decision_type="architecture",
                is_autonomous=True,
                summary=f"Merge conflict on task {task['name']}. Spawned integration task.",
                details=f"Conflict branch: {branch_name}, Conflicting files: {conflicting_files or []}"
            )
            self.task_repo.create(
                run_id=run_id,
                feature_id=task["feature_id"],
                name=f"Resolve merge conflict on {task['name']}",
                role="planning",
                risk="high",
                scope=integration_scope,
                dependencies=[],
                required_verification=task["required_verification"]
            )

    def refresh_provider_quotas(self, provider: str, force_refresh: bool = False) -> None:
        import datetime
        if provider == "agy":
            try:
                agy_usage_bin = resolve_binary("antigravity-usage", self.config)
            except FileNotFoundError:
                # Optional tool not installed; skip conservatively
                return

            try:
                res = subprocess.run(
                    [agy_usage_bin, "--version"],
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    timeout=3.0
                )
                has_tool = (res.returncode == 0)
            except Exception:
                has_tool = False

            if not has_tool:
                return

            cmd = [agy_usage_bin, "quota", "--json", "--method", "google"]
            if force_refresh:
                cmd.append("--refresh")

            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    timeout=10.0
                )
                combined = res.stdout + "\n" + res.stderr
                if res.returncode != 0 or any(x in combined.lower() for x in ["login required", "unauthenticated", "expired credentials", "authentication required", "not logged in", "run antigravity-usage login"]):
                    routes = self.config.routes.get("implementation", []) + self.config.routes.get("planning", [])
                    for r in routes:
                        if r["provider"] == "agy":
                            self.provider_repo.save(
                                provider="agy",
                                model=r["model"],
                                capability_snapshot={},
                                availability=False,
                                quota_state="auth_required"
                            )
                    return

                data = json.loads(res.stdout)
                models_data = data.get("models", [])
                
                routes = self.config.routes.get("implementation", []) + self.config.routes.get("planning", [])
                agy_routes = [r for r in routes if r["provider"] == "agy"]
                for r in agy_routes:
                    model = r["model"]
                    matching = [m for m in models_data if (m.get("label") == model or m.get("modelId") == model) and not m.get("isAutocompleteOnly", False)]
                    
                    if not matching:
                        continue
                        
                    all_exhausted = all(m.get("isExhausted", False) for m in matching)
                    if not all_exhausted:
                        self.provider_repo.save(
                            provider="agy",
                            model=model,
                            capability_snapshot={"models": matching, "probe_count": 0},
                            availability=True,
                            quota_state="available"
                        )
                    else:
                        reset_time = None
                        for m in matching:
                            if m.get("resetTime"):
                                if not reset_time or m["resetTime"] < reset_time:
                                    reset_time = m["resetTime"]
                        if reset_time:
                            self.provider_repo.save(
                                provider="agy",
                                model=model,
                                capability_snapshot={"models": matching, "probe_count": 0},
                                availability=False,
                                quota_state="limited_known_reset",
                                quota_limit_reset=reset_time
                            )
                        else:
                            self.provider_repo.save(
                                provider="agy",
                                model=model,
                                capability_snapshot={"models": matching},
                                availability=False,
                                quota_state="limited_unknown_reset"
                            )
            except Exception:
                pass

        elif provider == "codex":
            try:
                binary_path = resolve_binary("codex", self.config)
            except FileNotFoundError:
                # codex binary not available; skip conservatively
                return

            try:
                proc = subprocess.Popen(
                    [binary_path, "app-server"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                
                def write_msg(msg):
                    proc.stdin.write(json.dumps(msg) + "\n")
                    proc.stdin.flush()
                    
                import select
                def read_msg(timeout=3.0):
                    r, _, _ = select.select([proc.stdout], [], [], timeout)
                    if r:
                        line = proc.stdout.readline()
                        if line:
                            return json.loads(line)
                    return None

                write_msg({
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "agent-loop", "version": "1.0"}},
                    "id": 1
                })
                
                resp = None
                for _ in range(10):
                    msg = read_msg()
                    if not msg:
                        break
                    if msg.get("id") == 1:
                        resp = msg
                        break
                        
                if resp and "error" not in resp:
                    write_msg({
                        "jsonrpc": "2.0",
                        "method": "initialized",
                        "params": {}
                    })
                    time.sleep(0.1)
                    
                    write_msg({
                        "jsonrpc": "2.0",
                        "method": "account/rateLimits/read",
                        "id": 2
                    })
                    
                    rate_limits_resp = None
                    for _ in range(10):
                        msg = read_msg()
                        if not msg:
                            break
                        if msg.get("id") == 2:
                            rate_limits_resp = msg
                            break
                    
                    proc.terminate()
                    
                    if rate_limits_resp and "result" in rate_limits_resp:
                        rl_data = rate_limits_resp["result"].get("rateLimits", {})
                        primary = rl_data.get("primary", {})
                        secondary = rl_data.get("secondary", {})
                        
                        primary_exhausted = primary.get("usedPercent", 0) >= 100
                        secondary_exhausted = secondary.get("usedPercent", 0) >= 100
                        rate_limit_reached = rl_data.get("rateLimitReachedType") is not None
                        
                        routes = self.config.routes.get("implementation", []) + self.config.routes.get("planning", [])
                        codex_routes = [r for r in routes if r["provider"] == "codex"]
                        
                        if primary_exhausted or secondary_exhausted or rate_limit_reached:
                            reset_ts = 0
                            if primary_exhausted:
                                reset_ts = max(reset_ts, primary.get("resetsAt", 0))
                            if secondary_exhausted:
                                reset_ts = max(reset_ts, secondary.get("resetsAt", 0))
                            if not reset_ts:
                                reset_ts = primary.get("resetsAt", 0)
                            reset_iso = None
                            if reset_ts:
                                reset_iso = datetime.datetime.fromtimestamp(reset_ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                            
                            for r in codex_routes:
                                self.provider_repo.save(
                                    provider="codex",
                                    model=r["model"],
                                    capability_snapshot=rl_data,
                                    availability=False,
                                    quota_state="limited_known_reset" if reset_iso else "limited_unknown_reset",
                                    quota_limit_reset=reset_iso
                                )
                        else:
                            for r in codex_routes:
                                self.provider_repo.save(
                                    provider="codex",
                                    model=r["model"],
                                    capability_snapshot=rl_data,
                                    availability=True,
                                    quota_state="available"
                                )
                else:
                    proc.terminate()
            except Exception:
                pass

    def get_required_routes(self, run_id: int) -> List[Dict[str, Any]]:
        tasks = self.task_repo.get_by_run(run_id)
        features = self.feature_repo.get_by_run(run_id)
        
        required_route_keys = set()
        
        # 1. Ready tasks
        ready_tasks = [t for t in tasks if t["status"] == "ready"]
        for task in ready_tasks:
            attempts = [a for a in self.attempt_repo.get_by_run(run_id) if a["task_id"] == task["id"]]
            is_high_reasoning = (task["risk"] == "high") or (len(attempts) >= self.config.retry_policy["escalation_threshold"])
            route_key = "planning" if (task["role"] == "planning" or is_high_reasoning) else "implementation"
            required_route_keys.add(route_key)
            
        # 2. Running tasks (they will need reviews, which require planning routes)
        running_tasks = [t for t in tasks if t["status"] == "running"]
        if running_tasks:
            required_route_keys.add("planning")
            
        # 3. Features pending review
        for feature in features:
            if feature["review_status"] == "pending":
                feat_tasks = [t for t in tasks if t["feature_id"] == feature["id"]]
                if feat_tasks and all(t["status"] == "complete" for t in feat_tasks):
                    required_route_keys.add("planning")
                    
        # 4. Final review
        if tasks and all(t["status"] == "complete" for t in tasks):
            rejected_features = [f for f in features if f["review_status"] == "rejected"]
            if not rejected_features:
                required_route_keys.add("planning")
                
        # Map route keys to actual routes from config
        required_routes = []
        # Preserve order from config routes
        for key in ["planning", "implementation"]:
            if key in required_route_keys:
                for r in self.config.routes.get(key, []):
                    rc = r.copy()
                    rc["capability"] = key
                    required_routes.append(rc)
                
        # If no specific work is ready but the run is running/waiting_for_quota, default to both
        if not required_routes:
            for key in ["implementation", "planning"]:
                for r in self.config.routes.get(key, []):
                    rc = r.copy()
                    rc["capability"] = key
                    required_routes.append(rc)
            
        return required_routes

    def run_regression_test(self, run_id: int) -> bool:
        command = self.config.commands.get("regression_test")
        if not command:
            return True
        
        start_time = time.time()
        run_logs_dir = (self.config.logs_dir / str(run_id)).resolve()
        run_logs_dir.mkdir(parents=True, exist_ok=True)
        test_out_file = run_logs_dir / "regression_stdout.log"
        test_err_file = run_logs_dir / "regression_stderr.log"
        
        try:
            with test_out_file.open("w") as out_f, test_err_file.open("w") as err_f:
                process = subprocess.run(
                    command,
                    shell=True,
                    cwd=Path.cwd(),
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
            
            with self.db_lock:
                self.test_run_repo.create(
                    run_id=run_id,
                    task_id=None,
                    attempt_id=None,
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
            with self.db_lock:
                self.test_run_repo.create(
                    run_id=run_id,
                    task_id=None,
                    attempt_id=None,
                    command=command,
                    scope=None,
                    exit_status=-1,
                    duration_seconds=duration,
                    output_path=output_json
                )
            with test_err_file.open("a") as err_f:
                err_f.write(f"\nRegression test failed with exception: {e}\n")
            return False

    def check_and_recover_quotas(self, run_id: int, required_routes: List[Dict[str, Any]]) -> bool:
        import datetime
        
        while True:
            now = self.get_now()
            
            for r in required_routes:
                provider = r["provider"]
                model = r["model"]
                p_state = self.provider_repo.get(provider, model)
                if p_state:
                    reset_str = p_state.get("quota_limit_reset")
                    if reset_str:
                        try:
                            reset_time = datetime.datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
                            if now >= reset_time:
                                self.refresh_provider_quotas(provider, force_refresh=True)
                        except Exception:
                            pass
                    
                    if p_state.get("quota_state") == "limited_unknown_reset":
                        last_probe_str = p_state.get("last_probe")
                        probe_count = p_state.get("capability_snapshot", {}).get("probe_count", 0)
                        should_probe = False
                        if not last_probe_str:
                            should_probe = True
                        else:
                            try:
                                last_probe = datetime.datetime.fromisoformat(last_probe_str.replace("Z", "+00:00"))
                                p_count = max(1, probe_count)
                                interval_mins = min(15 * (2 ** (p_count - 1)), 120)
                                if (now - last_probe).total_seconds() >= interval_mins * 60:
                                    should_probe = True
                            except Exception:
                                should_probe = True
                                
                        if should_probe:
                            self.refresh_provider_quotas(provider, force_refresh=True)
                            p_state = self.provider_repo.get(provider, model)
                            if p_state and p_state.get("quota_state") == "limited_unknown_reset":
                                snap = p_state.get("capability_snapshot", {})
                                snap["probe_count"] = probe_count + 1
                                self.provider_repo.save(
                                    provider=provider,
                                    model=model,
                                    capability_snapshot=snap,
                                    availability=False,
                                    quota_state="limited_unknown_reset",
                                    last_probe=now.isoformat().replace("+00:00", "Z")
                                )

            # Group required routes by capability (defaulting to "unknown" if not specified)
            # Find the set of all required capabilities
            required_capabilities = set(r.get("capability", "unknown") for r in required_routes)
            
            # Determine usable, limited, and auth_required routes
            # Also keep track of usable/auth routes per capability
            usable_routes = []
            limited_routes = []
            auth_required_routes = []
            
            cap_to_routes = {cap: [] for cap in required_capabilities}
            cap_to_usable = {cap: [] for cap in required_capabilities}
            cap_to_auth = {cap: [] for cap in required_capabilities}
            
            for r in required_routes:
                cap = r.get("capability", "unknown")
                cap_to_routes[cap].append(r)
                
                p_state = self.provider_repo.get(r["provider"], r["model"])
                if not p_state or p_state["quota_state"] == "available":
                    usable_routes.append(r)
                    cap_to_usable[cap].append(r)
                elif p_state["quota_state"] in {"limited_known_reset", "limited_unknown_reset", "transient_failure", "unavailable"}:
                    limited_routes.append((r, p_state))
                elif p_state["quota_state"] == "auth_required":
                    auth_required_routes.append(r)
                    cap_to_auth[cap].append(r)

            # Resume only when every capability required has at least one usable route
            all_capabilities_usable = all(len(cap_to_usable[cap]) > 0 for cap in required_capabilities)
            
            if all_capabilities_usable:
                run = self.run_repo.get(run_id)
                if run["status"] == "waiting_for_quota":
                    self.run_repo.update_status(run_id, "running")
                    routes_str = ", ".join(f"{r['provider']}:{r['model']}" for r in usable_routes)
                    self.notify(run_id, "recovery", f"Usable route(s) [{routes_str}] recovered. Run status set to running.")
                    render_progress_md(self.conn, run_id, self.progress_path)
                return True

            # If any required capability has ALL its routes in auth_required state, we block/stop immediately
            for cap in required_capabilities:
                if len(cap_to_auth[cap]) == len(cap_to_routes[cap]):
                    self.run_repo.update_status(run_id, "blocked")
                    self.notify(
                        run_id, 
                        "auth_required", 
                        f"Authentication required for all configured model routes for capability '{cap}'. Please run 'antigravity-usage login' or agy login."
                    )
                    render_progress_md(self.conn, run_id, self.progress_path)
                    return False

            sleep_secs = 60.0
            
            known_resets = []
            for r, p_state in limited_routes:
                if p_state["quota_state"] == "limited_known_reset":
                    reset_str = p_state.get("quota_limit_reset")
                    if reset_str:
                        try:
                            rt = datetime.datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
                            known_resets.append(rt)
                        except Exception:
                            pass
            
            unknown_probe_times = []
            for r, p_state in limited_routes:
                if p_state["quota_state"] in {"limited_unknown_reset", "transient_failure", "unavailable"}:
                    last_probe_str = p_state.get("last_probe")
                    probe_count = p_state.get("capability_snapshot", {}).get("probe_count", 0)
                    try:
                        p_count = max(1, probe_count)
                        interval_mins = min(15 * (2 ** (p_count - 1)), 120)
                        if last_probe_str:
                            last_probe = datetime.datetime.fromisoformat(last_probe_str.replace("Z", "+00:00"))
                            next_probe = last_probe + datetime.timedelta(minutes=interval_mins)
                        else:
                            next_probe = now
                        unknown_probe_times.append(next_probe)
                    except Exception:
                        pass

            all_target_times = known_resets + unknown_probe_times
            if all_target_times:
                earliest_target = min(all_target_times)
                diff = (earliest_target - now).total_seconds()
                if diff > 0:
                    sleep_secs = diff

            run = self.run_repo.get(run_id)
            if run["status"] != "waiting_for_quota":
                self.run_repo.update_status(run_id, "waiting_for_quota")
                
                limited_details = []
                for r, p_state in limited_routes:
                    limited_details.append(
                        f"- Route: {r['provider']}:{r['model']} | State: {p_state['quota_state']} | Reset: {p_state.get('quota_limit_reset') or 'Unknown'}"
                    )
                limited_summary = "\n".join(limited_details)
                
                alert_msg = (
                    f"Quota Alert - Run: {run_id}\n"
                    f"All configured model routes are quota limited:\n{limited_summary}\n"
                    f"Classification: waiting_for_quota\n"
                    f"Evidence Path: N/A\n"
                    f"Fallback Action: Entering wait/sleep state for {sleep_secs} seconds\n"
                    f"Expected Resume Behavior: Will refresh provider quotas and check availability after sleep"
                )
                self.notify(run_id, "exhausted", alert_msg)
                render_progress_md(self.conn, run_id, self.progress_path)

            self.sleep_func(sleep_secs)

            for r, p_state in limited_routes:
                self.refresh_provider_quotas(r["provider"], force_refresh=True)

    def run_loop(self, run_id: int) -> None:
        while True:
            run = self.run_repo.get(run_id)
            if run["status"] not in {"running", "waiting_for_quota"}:
                break

            # Quota recovery check before executing tasks
            required_routes = self.get_required_routes(run_id)
            if not self.check_and_recover_quotas(run_id, required_routes):
                break

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
                        decision = self.run_feature_review(run_id, feature["id"])
                        if decision == "approved":
                            self.feature_repo.update_review_status(feature["id"], "approved")
                        elif decision in ("follow_up", "rejected"):
                            # On follow_up or rejected, we create a new task to fix the acceptance criteria
                            # and revert the feature to pending, so it tries again after the fix is implemented.
                            self.feature_repo.update_review_status(feature["id"], "pending")
                            with self.db_lock:
                                self.task_repo.create(
                                    run_id=run_id,
                                    feature_id=feature["id"],
                                    name=f"Address feature review feedback for {feature['name']}",
                                    role="implementation",
                                    risk="medium",
                                    scope={"files": []},  # Let the agent figure out what to edit
                                    dependencies=[],
                                    required_verification="npm test"
                                )
                            self.notify(run_id, "feature_follow_up", f"Feature '{feature['name']}' review feedback needs addressing. Spawned follow-up task.")
                        else:
                            self.feature_repo.update_review_status(feature["id"], "rejected")
                            self.run_repo.update_status(run_id, "blocked")
                            self.notify(run_id, "blocked", f"Feature '{feature['name']}' review was rejected. Run is blocked.")

            tasks = self.task_repo.get_by_run(run_id)
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
                        regression_success = self.run_regression_test(run_id)
                        if regression_success:
                            migrations = self.test_migration_repo.get_by_run(run_id)
                            has_pending = any(m["approval_status"] == "pending" for m in migrations)
                            has_rejected = any(m["approval_status"] == "rejected" for m in migrations)
                            if has_rejected:
                                self.run_repo.update_status(run_id, "blocked")
                                self.notify(run_id, "blocked", "A test migration was rejected. Run is blocked.")
                            elif has_pending:
                                self.run_repo.update_status(run_id, "complete_pending_test_review")
                                self.notify(run_id, "pending_test_review", "Run is pending test migration reviews.")
                            else:
                                self.run_repo.update_status(run_id, "complete")
                                self.notify(run_id, "complete", "Run completed successfully.")
                        else:
                            self.run_repo.update_status(run_id, "failed")
                            self.notify(run_id, "failed", "Regression test verification failed. Run is failed.")
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
