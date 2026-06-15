import json
import sqlite3
from typing import Any, Dict, List, Optional

VALID_RUN_TRANSITIONS = {
    "draft": {"planning", "cancelled"},
    "planning": {"awaiting_plan_approval", "running", "cancelled"},
    "awaiting_plan_approval": {"running", "planning", "cancelled"},
    "running": {"waiting_for_quota", "blocked", "reviewing", "cancelled", "failed"},
    "waiting_for_quota": {"running", "blocked", "cancelled"},
    "blocked": {"running", "cancelled"},
    "reviewing": {"complete_pending_test_review", "complete", "failed", "running", "cancelled"},
    "complete_pending_test_review": {"complete", "failed", "cancelled"},
    "complete": set(),
    "failed": set(),
    "cancelled": set()
}

VALID_TASK_TRANSITIONS = {
    "pending": {"ready", "blocked", "cancelled"},
    "ready": {"running", "blocked", "cancelled"},
    "running": {"reviewing", "failed", "cancelled"},
    "reviewing": {"complete", "failed", "ready", "cancelled"},
    "complete": set(),
    "failed": {"ready", "cancelled"},
    "blocked": {"ready", "cancelled"},
    "cancelled": set()
}

class RunRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, goal: str, intake_mode: str, config_snapshot: Optional[Dict[str, Any]] = None) -> int:
        config_str = json.dumps(config_snapshot) if config_snapshot else None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO runs (goal, intake_mode, status, config_snapshot)
            VALUES (?, ?, ?, ?);
            """,
            (goal, intake_mode, "draft", config_str)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, run_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, goal, intake_mode, status, config_snapshot, created_at, updated_at FROM runs WHERE id = ?;",
            (run_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "goal": row[1],
            "intake_mode": row[2],
            "status": row[3],
            "config_snapshot": json.loads(row[4]) if row[4] else None,
            "created_at": row[5],
            "updated_at": row[6]
        }

    def list_all(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, goal, intake_mode, status, config_snapshot, created_at, updated_at FROM runs ORDER BY id DESC;"
        )
        return [
            {
                "id": row[0],
                "goal": row[1],
                "intake_mode": row[2],
                "status": row[3],
                "config_snapshot": json.loads(row[4]) if row[4] else None,
                "created_at": row[5],
                "updated_at": row[6]
            }
            for row in cursor.fetchall()
        ]

    def update_status(self, run_id: int, new_status: str, force: bool = False) -> None:
        run = self.get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found.")

        current_status = run["status"]
        if not force:
            allowed = VALID_RUN_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                raise ValueError(f"Invalid run status transition from '{current_status}' to '{new_status}'.")

        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE runs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (new_status, run_id)
        )
        self.conn.commit()


class FeatureRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, name: str, risk: str, acceptance_criteria: Optional[str] = None, dependencies: Optional[List[str]] = None) -> int:
        deps_str = json.dumps(dependencies) if dependencies else None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO features (run_id, name, risk, acceptance_criteria, dependencies, review_status)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (run_id, name, risk, acceptance_criteria, deps_str, "pending")
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, feature_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, name, outcome, acceptance_criteria, dependencies, risk, review_status FROM features WHERE id = ?;",
            (feature_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "run_id": row[1],
            "name": row[2],
            "outcome": row[3],
            "acceptance_criteria": row[4],
            "dependencies": json.loads(row[5]) if row[5] else [],
            "risk": row[6],
            "review_status": row[7]
        }

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, name, outcome, acceptance_criteria, dependencies, risk, review_status FROM features WHERE run_id = ?;",
            (run_id,)
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "name": row[2],
                "outcome": row[3],
                "acceptance_criteria": row[4],
                "dependencies": json.loads(row[5]) if row[5] else [],
                "risk": row[6],
                "review_status": row[7]
            }
            for row in cursor.fetchall()
        ]

    def update_outcome(self, feature_id: int, outcome: Optional[str]) -> None:
        cursor = self.conn.cursor()
        cursor.execute("UPDATE features SET outcome = ? WHERE id = ?;", (outcome, feature_id))
        self.conn.commit()

    def update_review_status(self, feature_id: int, review_status: str) -> None:
        if review_status not in {"pending", "approved", "rejected"}:
            raise ValueError(f"Invalid feature review status: {review_status}")
        cursor = self.conn.cursor()
        cursor.execute("UPDATE features SET review_status = ? WHERE id = ?;", (review_status, feature_id))
        self.conn.commit()


class TaskRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, feature_id: int, name: str, role: str, risk: str, scope: Optional[Dict[str, Any]] = None, dependencies: Optional[List[str]] = None, required_verification: Optional[str] = None) -> int:
        deps_str = json.dumps(dependencies) if dependencies else None
        scope_str = json.dumps(scope) if scope else None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO tasks (run_id, feature_id, name, role, risk, scope, dependencies, required_verification, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (run_id, feature_id, name, role, risk, scope_str, deps_str, required_verification, "pending")
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, feature_id, name, role, dependencies, scope, risk, required_verification, status FROM tasks WHERE id = ?;",
            (task_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "run_id": row[1],
            "feature_id": row[2],
            "name": row[3],
            "role": row[4],
            "dependencies": json.loads(row[5]) if row[5] else [],
            "scope": json.loads(row[6]) if row[6] else None,
            "risk": row[7],
            "required_verification": row[8],
            "status": row[9]
        }

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, feature_id, name, role, dependencies, scope, risk, required_verification, status FROM tasks WHERE run_id = ?;",
            (run_id,)
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "feature_id": row[2],
                "name": row[3],
                "role": row[4],
                "dependencies": json.loads(row[5]) if row[5] else [],
                "scope": json.loads(row[6]) if row[6] else None,
                "risk": row[7],
                "required_verification": row[8],
                "status": row[9]
            }
            for row in cursor.fetchall()
        ]

    def update_status(self, task_id: int, new_status: str, force: bool = False) -> None:
        task = self.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found.")

        current_status = task["status"]
        if not force:
            allowed = VALID_TASK_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                raise ValueError(f"Invalid task status transition from '{current_status}' to '{new_status}'.")

        cursor = self.conn.cursor()
        cursor.execute("UPDATE tasks SET status = ? WHERE id = ?;", (new_status, task_id))
        self.conn.commit()


class AttemptRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, task_id: int, route: Optional[str] = None, provider: Optional[str] = None, model: Optional[str] = None, reasoning_level: Optional[str] = None, worktree_path: Optional[str] = None, commit_sha: Optional[str] = None, logs_path: Optional[str] = None) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO attempts (run_id, task_id, route, provider, model, reasoning_level, worktree_path, commit_sha, logs_path, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (run_id, task_id, route, provider, model, reasoning_level, worktree_path, commit_sha, logs_path, "running")
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, attempt_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, run_id, task_id, route, provider, model, reasoning_level, worktree_path, commit_sha, logs_path, outcome, created_at, updated_at
            FROM attempts WHERE id = ?;
            """,
            (attempt_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "run_id": row[1],
            "task_id": row[2],
            "route": row[3],
            "provider": row[4],
            "model": row[5],
            "reasoning_level": row[6],
            "worktree_path": row[7],
            "commit_sha": row[8],
            "logs_path": row[9],
            "outcome": row[10],
            "created_at": row[11],
            "updated_at": row[12]
        }

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, run_id, task_id, route, provider, model, reasoning_level, worktree_path, commit_sha, logs_path, outcome, created_at, updated_at
            FROM attempts WHERE run_id = ?;
            """,
            (run_id,)
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "task_id": row[2],
                "route": row[3],
                "provider": row[4],
                "model": row[5],
                "reasoning_level": row[6],
                "worktree_path": row[7],
                "commit_sha": row[8],
                "logs_path": row[9],
                "outcome": row[10],
                "created_at": row[11],
                "updated_at": row[12]
            }
            for row in cursor.fetchall()
        ]

    def update_outcome(self, attempt_id: int, outcome: str, commit_sha: Optional[str] = None) -> None:
        if outcome not in {"running", "completed", "failed", "abandoned"}:
            raise ValueError(f"Invalid attempt outcome: {outcome}")
        cursor = self.conn.cursor()
        if commit_sha:
            cursor.execute(
                "UPDATE attempts SET outcome = ?, commit_sha = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (outcome, commit_sha, attempt_id)
            )
        else:
            cursor.execute(
                "UPDATE attempts SET outcome = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
                (outcome, attempt_id)
            )
        self.conn.commit()


class TestRunRepository:
    __test__ = False

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, task_id: Optional[int], attempt_id: Optional[int], command: str, scope: Optional[str], exit_status: Optional[int], duration_seconds: Optional[float], output_path: Optional[str]) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO test_runs (run_id, task_id, attempt_id, command, scope, exit_status, duration_seconds, output_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (run_id, task_id, attempt_id, command, scope, exit_status, duration_seconds, output_path)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, task_id, attempt_id, command, scope, exit_status, duration_seconds, output_path, created_at FROM test_runs WHERE run_id = ?;",
            (run_id,)
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "task_id": row[2],
                "attempt_id": row[3],
                "command": row[4],
                "scope": row[5],
                "exit_status": row[6],
                "duration_seconds": row[7],
                "output_path": row[8],
                "created_at": row[9]
            }
            for row in cursor.fetchall()
        ]


class ReviewRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, subject_type: str, subject_id: int, decision: str, reviewer_route: Optional[str] = None, findings: Optional[str] = None, evidence_paths: Optional[List[str]] = None) -> int:
        if decision not in {"approved", "rejected", "follow_up", "assessment", "block"}:
            raise ValueError(f"Invalid review decision: {decision}")
        ev_str = json.dumps(evidence_paths) if evidence_paths else None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO reviews (run_id, subject_type, subject_id, reviewer_route, findings, decision, evidence_paths)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (run_id, subject_type, subject_id, reviewer_route, findings, decision, ev_str)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, subject_type, subject_id, reviewer_route, findings, decision, evidence_paths, created_at FROM reviews WHERE run_id = ?;",
            (run_id,)
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "subject_type": row[2],
                "subject_id": row[3],
                "reviewer_route": row[4],
                "findings": row[5],
                "decision": row[6],
                "evidence_paths": json.loads(row[7]) if row[7] else [],
                "created_at": row[8]
            }
            for row in cursor.fetchall()
        ]


class ProviderStateRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, provider: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT provider, capability_snapshot, availability, quota_limit_reset, last_probe FROM provider_state WHERE provider = ?;",
            (provider,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "provider": row[0],
            "capability_snapshot": json.loads(row[1]) if row[1] else {},
            "availability": bool(row[2]),
            "quota_limit_reset": row[3],
            "last_probe": row[4]
        }

    def save(self, provider: str, capability_snapshot: Dict[str, Any], availability: bool, quota_limit_reset: Optional[str] = None, last_probe: Optional[str] = None) -> None:
        cap_str = json.dumps(capability_snapshot)
        avail_int = 1 if availability else 0
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO provider_state (provider, capability_snapshot, availability, quota_limit_reset, last_probe)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                capability_snapshot = excluded.capability_snapshot,
                availability = excluded.availability,
                quota_limit_reset = excluded.quota_limit_reset,
                last_probe = excluded.last_probe;
            """,
            (provider, cap_str, avail_int, quota_limit_reset, last_probe)
        )
        self.conn.commit()


class NotificationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, event: str, destination: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO notifications (run_id, event, destination, attempts, delivery_status)
            VALUES (?, ?, ?, 0, 'pending');
            """,
            (run_id, event, destination)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_pending(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, event, destination, attempts, delivery_status, created_at FROM notifications WHERE delivery_status = 'pending';"
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "event": row[2],
                "destination": row[3],
                "attempts": row[4],
                "delivery_status": row[5],
                "created_at": row[6]
            }
            for row in cursor.fetchall()
        ]

    def update_delivery(self, notification_id: int, status: str, attempts: int) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE notifications SET delivery_status = ?, attempts = ? WHERE id = ?;",
            (status, attempts, notification_id)
        )
        self.conn.commit()


class DecisionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, decision_type: str, is_autonomous: bool, summary: str, details: Optional[str] = None) -> int:
        auton_int = 1 if is_autonomous else 0
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO decisions (run_id, decision_type, is_autonomous, summary, details)
            VALUES (?, ?, ?, ?, ?);
            """,
            (run_id, decision_type, auton_int, summary, details)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, decision_type, is_autonomous, summary, details, created_at FROM decisions WHERE run_id = ?;",
            (run_id,)
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "decision_type": row[2],
                "is_autonomous": bool(row[3]),
                "summary": row[4],
                "details": row[5],
                "created_at": row[6]
            }
            for row in cursor.fetchall()
        ]


class TestMigrationRepository:
    __test__ = False

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, task_id: Optional[int], old_test_path: str, replacement_test_path: str, rationale: str, evidence: Optional[str] = None) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO test_migrations (run_id, task_id, old_test_path, replacement_test_path, rationale, evidence, approval_status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending');
            """,
            (run_id, task_id, old_test_path, replacement_test_path, rationale, evidence)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, task_id, old_test_path, replacement_test_path, rationale, evidence, approval_status, created_at FROM test_migrations WHERE run_id = ?;",
            (run_id,)
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "task_id": row[2],
                "old_test_path": row[3],
                "replacement_test_path": row[4],
                "rationale": row[5],
                "evidence": row[6],
                "approval_status": row[7],
                "created_at": row[8]
            }
            for row in cursor.fetchall()
        ]

    def update_approval(self, migration_id: int, approval_status: str) -> None:
        if approval_status not in {"pending", "approved", "rejected"}:
            raise ValueError(f"Invalid approval status: {approval_status}")
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE test_migrations SET approval_status = ? WHERE id = ?;",
            (approval_status, migration_id)
        )
        self.conn.commit()
