import json
import sqlite3
from typing import Any, Dict, List, Optional

from agent_loop.goal_types import validate_goal_type

VALID_RUN_TRANSITIONS = {
    "draft": {"planning", "cancelled"},
    "planning": {"awaiting_plan_approval", "running", "cancelled", "blocked", "failed"},
    "awaiting_plan_approval": {"running", "planning", "cancelled"},
    "running": {"waiting_for_quota", "blocked", "reviewing", "cancelled", "failed"},
    "waiting_for_quota": {"running", "blocked", "cancelled"},
    "blocked": {"planning", "running", "cancelled"},
    "reviewing": {"complete_pending_test_review", "complete", "failed", "running", "cancelled", "blocked"},
    "complete_pending_test_review": {"complete", "failed", "cancelled", "blocked"},
    "complete": set(),
    "failed": set(),
    "cancelled": set()
}

VALID_TASK_TRANSITIONS = {
    "pending": {"ready", "blocked", "cancelled"},
    "ready": {"running", "blocked", "cancelled"},
    "running": {"reviewing", "failed", "cancelled", "blocked"},
    "reviewing": {"complete", "failed", "ready", "cancelled", "blocked"},
    "complete": set(),
    "failed": {"ready", "blocked", "cancelled"},
    "blocked": {"ready", "cancelled"},
    "cancelled": set()
}

class RunRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        goal: str,
        intake_mode: str,
        config_snapshot: Optional[Dict[str, Any]] = None,
        goal_type: str = "prototype",
        goal_type_rationale: Optional[str] = None,
    ) -> int:
        goal_type = validate_goal_type(goal_type)
        config_str = json.dumps(config_snapshot) if config_snapshot else None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO runs (
                goal, intake_mode, status, config_snapshot,
                goal_type, goal_type_rationale
            )
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (goal, intake_mode, "draft", config_str, goal_type, goal_type_rationale)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, run_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, goal, intake_mode, status, config_snapshot,
                   goal_type, goal_type_rationale, created_at, updated_at
            FROM runs WHERE id = ?;
            """,
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
            "goal_type": row[5],
            "goal_type_rationale": row[6],
            "created_at": row[7],
            "updated_at": row[8]
        }

    def list_all(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, goal, intake_mode, status, config_snapshot,
                   goal_type, goal_type_rationale, created_at, updated_at
            FROM runs ORDER BY id DESC;
            """
        )
        return [
            {
                "id": row[0],
                "goal": row[1],
                "intake_mode": row[2],
                "status": row[3],
                "config_snapshot": json.loads(row[4]) if row[4] else None,
                "goal_type": row[5],
                "goal_type_rationale": row[6],
                "created_at": row[7],
                "updated_at": row[8]
            }
            for row in cursor.fetchall()
        ]

    def update_status(self, run_id: int, new_status: str, force: bool = False) -> None:
        run = self.get(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found.")

        current_status = run["status"]
        if current_status == new_status:
            return

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

    def update_goal_type(self, run_id: int, goal_type: str, rationale: Optional[str]) -> None:
        if not self.get(run_id):
            raise ValueError(f"Run {run_id} not found.")
        normalized = validate_goal_type(goal_type)
        self.conn.execute(
            """
            UPDATE runs
            SET goal_type = ?, goal_type_rationale = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (normalized, rationale, run_id),
        )
        self.conn.commit()

    def get_latest_completed(self) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT id FROM runs WHERE status = 'complete' ORDER BY id DESC LIMIT 1;"
        ).fetchone()
        return self.get(row[0]) if row else None


RECOMMENDATION_CATEGORIES = {
    "usability",
    "security",
    "architecture",
    "reliability",
    "testing",
    "maintenance",
}
RECOMMENDATION_PRIORITIES = {"high", "medium", "low"}
RECOMMENDATION_STATUSES = {"open", "selected", "deferred", "declined", "resolved"}


class RecommendationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        run_id: int,
        category: str,
        priority: str,
        title: str,
        rationale: str,
        evidence: str,
        feature_id: Optional[int] = None,
        task_id: Optional[int] = None,
        source_review_id: Optional[int] = None,
    ) -> int:
        category = (category or "").strip().lower()
        priority = (priority or "").strip().lower()
        if category not in RECOMMENDATION_CATEGORIES:
            raise ValueError(f"Invalid recommendation category: {category}")
        if priority not in RECOMMENDATION_PRIORITIES:
            raise ValueError(f"Invalid recommendation priority: {priority}")
        if not all((title.strip(), rationale.strip(), evidence.strip())):
            raise ValueError("Recommendation title, rationale, and evidence are required.")
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO recommendations (
                run_id, feature_id, task_id, source_review_id,
                category, priority, title, rationale, evidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open');
            """,
            (
                run_id,
                feature_id,
                task_id,
                source_review_id,
                category,
                priority,
                title.strip(),
                rationale.strip(),
                evidence.strip(),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    @staticmethod
    def _row(row: sqlite3.Row | tuple) -> Dict[str, Any]:
        return {
            "id": row[0],
            "run_id": row[1],
            "feature_id": row[2],
            "task_id": row[3],
            "source_review_id": row[4],
            "category": row[5],
            "priority": row[6],
            "title": row[7],
            "rationale": row[8],
            "evidence": row[9],
            "status": row[10],
            "adopting_run_id": row[11],
            "created_at": row[12],
            "updated_at": row[13],
        }

    def get(self, recommendation_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT id, run_id, feature_id, task_id, source_review_id,
                   category, priority, title, rationale, evidence, status,
                   adopting_run_id, created_at, updated_at
            FROM recommendations WHERE id = ?;
            """,
            (recommendation_id,),
        ).fetchone()
        return self._row(row) if row else None

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, run_id, feature_id, task_id, source_review_id,
                   category, priority, title, rationale, evidence, status,
                   adopting_run_id, created_at, updated_at
            FROM recommendations WHERE run_id = ? ORDER BY id;
            """,
            (run_id,),
        ).fetchall()
        return [self._row(row) for row in rows]

    def get_open_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        return [item for item in self.get_by_run(run_id) if item["status"] == "open"]

    def get_selected_for_run(self, run_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, run_id, feature_id, task_id, source_review_id,
                   category, priority, title, rationale, evidence, status,
                   adopting_run_id, created_at, updated_at
            FROM recommendations
            WHERE adopting_run_id = ? AND status = 'selected'
            ORDER BY id;
            """,
            (run_id,),
        ).fetchall()
        return [self._row(row) for row in rows]

    def select(self, recommendation_ids: List[int], adopting_run_id: int) -> None:
        ids = list(dict.fromkeys(recommendation_ids))
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.conn:
            rows = self.conn.execute(
                f"SELECT id FROM recommendations WHERE id IN ({placeholders}) AND status = 'open';",
                ids,
            ).fetchall()
            if {row[0] for row in rows} != set(ids):
                raise ValueError("Only existing open recommendations can be selected.")
            self.conn.execute(
                f"""
                UPDATE recommendations
                SET status = 'selected', adopting_run_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders});
                """,
                [adopting_run_id, *ids],
            )

    def resolve_for_adopting_run(self, run_id: int) -> None:
        self.conn.execute(
            """
            UPDATE recommendations
            SET status = 'resolved', updated_at = CURRENT_TIMESTAMP
            WHERE adopting_run_id = ? AND status = 'selected';
            """,
            (run_id,),
        )
        self.conn.commit()


class GoalDeliveryRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(
        self,
        run_id: int,
        summary: str,
        launch_command: Optional[str],
        local_url: Optional[str],
        verification: List[str],
        known_limitations: List[str],
        launch_evidence: Optional[str] = None,
        investigation_conclusion: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO goal_deliveries (
                run_id, summary, launch_command, local_url, verification,
                known_limitations, launch_evidence, investigation_conclusion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                summary = excluded.summary,
                launch_command = excluded.launch_command,
                local_url = excluded.local_url,
                verification = excluded.verification,
                known_limitations = excluded.known_limitations,
                launch_evidence = excluded.launch_evidence,
                investigation_conclusion = excluded.investigation_conclusion,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (
                run_id,
                summary.strip(),
                launch_command,
                local_url,
                json.dumps(verification),
                json.dumps(known_limitations),
                launch_evidence,
                investigation_conclusion,
            ),
        )
        self.conn.commit()

    def get_by_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT id, run_id, summary, launch_command, local_url, verification,
                   known_limitations, launch_evidence, investigation_conclusion,
                   created_at, updated_at
            FROM goal_deliveries WHERE run_id = ?;
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "run_id": row[1],
            "summary": row[2],
            "launch_command": row[3],
            "local_url": row[4],
            "verification": json.loads(row[5]),
            "known_limitations": json.loads(row[6]),
            "launch_evidence": row[7],
            "investigation_conclusion": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }


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

    def create(
        self,
        run_id: int,
        feature_id: int,
        name: str,
        role: str,
        risk: str,
        scope: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[str]] = None,
        required_verification: Optional[str] = None,
        verification_requirements: Optional[List[str]] = None,
    ) -> int:
        deps_str = json.dumps(dependencies) if dependencies else None
        scope_str = json.dumps(scope) if scope else None
        requirements = [str(item).strip() for item in (verification_requirements or []) if str(item).strip()]
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO tasks (
                run_id, feature_id, name, role, risk, scope, dependencies,
                required_verification, verification_requirements, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                run_id, feature_id, name, role, risk, scope_str, deps_str,
                required_verification, json.dumps(requirements), "pending",
            )
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, run_id, feature_id, name, role, dependencies, scope, risk,
                   required_verification, verification_requirements, status
            FROM tasks WHERE id = ?;
            """,
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
            "legacy_required_verification": row[8],
            "verification_requirements": json.loads(row[9]) if row[9] else [],
            "status": row[10]
        }

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, run_id, feature_id, name, role, dependencies, scope, risk,
                   required_verification, verification_requirements, status
            FROM tasks WHERE run_id = ?;
            """,
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
                "legacy_required_verification": row[8],
                "verification_requirements": json.loads(row[9]) if row[9] else [],
                "status": row[10]
            }
            for row in cursor.fetchall()
        ]

    def update_status(self, task_id: int, new_status: str, force: bool = False) -> None:
        task = self.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found.")

        current_status = task["status"]
        if current_status == new_status:
            return

        if not force:
            allowed = VALID_TASK_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                raise ValueError(f"Invalid task status transition from '{current_status}' to '{new_status}'.")

        cursor = self.conn.cursor()
        cursor.execute("UPDATE tasks SET status = ? WHERE id = ?;", (new_status, task_id))
        self.conn.commit()

    def update_scope(self, task_id: int, new_scope: Dict[str, Any]) -> None:
        cursor = self.conn.cursor()
        cursor.execute("UPDATE tasks SET scope = ? WHERE id = ?;", (json.dumps(new_scope), task_id))
        self.conn.commit()


class AttemptRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        run_id: int,
        task_id: int,
        route: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_level: Optional[str] = None,
        worktree_path: Optional[str] = None,
        commit_sha: Optional[str] = None,
        logs_path: Optional[str] = None,
        patch_path: Optional[str] = None,
        start_sha: Optional[str] = None,
        base_sha: Optional[str] = None,
        retry_strategy: Optional[str] = None,
        retry_strategy_reason: Optional[str] = None,
    ) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO attempts (
                run_id, task_id, route, provider, model, reasoning_level,
                worktree_path, commit_sha, logs_path, patch_path,
                start_sha, base_sha, retry_strategy, retry_strategy_reason,
                outcome
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                run_id,
                task_id,
                route,
                provider,
                model,
                reasoning_level,
                worktree_path,
                commit_sha,
                logs_path,
                patch_path,
                start_sha,
                base_sha,
                retry_strategy,
                retry_strategy_reason,
                "running",
            )
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, attempt_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, run_id, task_id, route, provider, model, reasoning_level,
                   worktree_path, commit_sha, logs_path, outcome, created_at,
                   updated_at, patch_path, start_sha, base_sha, retry_strategy,
                   retry_strategy_reason
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
            "updated_at": row[12],
            "patch_path": row[13],
            "start_sha": row[14],
            "base_sha": row[15],
            "retry_strategy": row[16],
            "retry_strategy_reason": row[17],
        }

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, run_id, task_id, route, provider, model, reasoning_level,
                   worktree_path, commit_sha, logs_path, outcome, created_at,
                   updated_at, patch_path, start_sha, base_sha, retry_strategy,
                   retry_strategy_reason
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
                "updated_at": row[12],
                "patch_path": row[13],
                "start_sha": row[14],
                "base_sha": row[15],
                "retry_strategy": row[16],
                "retry_strategy_reason": row[17],
            }
            for row in cursor.fetchall()
        ]

    def update_outcome(self, attempt_id: int, outcome: str, commit_sha: Optional[str] = None, patch_path: Optional[str] = None) -> None:
        if outcome not in {"running", "completed", "failed", "abandoned"}:
            raise ValueError(f"Invalid attempt outcome: {outcome}")
        cursor = self.conn.cursor()
        if commit_sha is not None and patch_path is not None:
            cursor.execute(
                """
                UPDATE attempts 
                SET outcome = ?, commit_sha = ?, patch_path = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?;
                """,
                (outcome, commit_sha, patch_path, attempt_id)
            )
        elif commit_sha is not None:
            cursor.execute(
                """
                UPDATE attempts 
                SET outcome = ?, commit_sha = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?;
                """,
                (outcome, commit_sha, attempt_id)
            )
        elif patch_path is not None:
            cursor.execute(
                """
                UPDATE attempts 
                SET outcome = ?, patch_path = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?;
                """,
                (outcome, patch_path, attempt_id)
            )
        else:
            cursor.execute(
                """
                UPDATE attempts 
                SET outcome = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?;
                """,
                (outcome, attempt_id)
            )
        self.conn.commit()

    def update_route_metadata(
        self,
        attempt_id: int,
        route: Optional[str],
        provider: Optional[str],
        model: Optional[str],
        reasoning_level: Optional[str],
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE attempts
            SET route = ?, provider = ?, model = ?, reasoning_level = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (route, provider, model, reasoning_level, attempt_id),
        )
        self.conn.commit()

    def update_start_metadata(
        self,
        attempt_id: int,
        start_sha: Optional[str],
        base_sha: Optional[str],
        retry_strategy: Optional[str],
        retry_strategy_reason: Optional[str] = None,
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE attempts
            SET start_sha = ?, base_sha = ?, retry_strategy = ?,
                retry_strategy_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (start_sha, base_sha, retry_strategy, retry_strategy_reason, attempt_id),
        )
        self.conn.commit()

    def update_retry_strategy(
        self,
        attempt_id: int,
        retry_strategy: str,
        retry_strategy_reason: Optional[str] = None,
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE attempts
            SET retry_strategy = ?, retry_strategy_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (retry_strategy, retry_strategy_reason, attempt_id),
        )
        self.conn.commit()

    def escalate_failed_attempts(self, task_id: int) -> None:
        """Mark all previous failed or abandoned attempts for a task as 'escalated' so they don't count against retry limits."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE attempts 
            SET outcome = 'escalated', updated_at = CURRENT_TIMESTAMP 
            WHERE task_id = ? AND outcome IN ('failed', 'abandoned');
            """,
            (task_id,)
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


class VerificationEvidenceRepository:
    PHASES = {"executor", "task_review", "feature_review", "final_review"}
    STATUSES = {"passed", "failed", "blocked", "not_applicable"}

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        run_id: int,
        requirement: str,
        status: str,
        summary: str,
        phase: str,
        task_id: Optional[int] = None,
        attempt_id: Optional[int] = None,
        review_id: Optional[int] = None,
        actor_route: Optional[str] = None,
        command: Optional[str] = None,
        exit_status: Optional[int] = None,
        evidence_paths: Optional[List[str]] = None,
    ) -> int:
        if phase not in self.PHASES:
            raise ValueError(f"Invalid verification evidence phase: {phase}")
        if status not in self.STATUSES:
            raise ValueError(f"Invalid verification evidence status: {status}")
        if not requirement.strip() or not summary.strip():
            raise ValueError("Verification evidence requires a requirement and summary.")
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO verification_evidence (
                run_id, task_id, attempt_id, review_id, phase, actor_route,
                requirement, status, command, exit_status, summary, evidence_paths
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                run_id,
                task_id,
                attempt_id,
                review_id,
                phase,
                actor_route,
                requirement.strip(),
                status,
                command,
                exit_status,
                summary.strip(),
                json.dumps(evidence_paths or []),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, run_id, task_id, attempt_id, review_id, phase, actor_route,
                   requirement, status, command, exit_status, summary,
                   evidence_paths, created_at
            FROM verification_evidence
            WHERE run_id = ?
            ORDER BY id;
            """,
            (run_id,),
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "task_id": row[2],
                "attempt_id": row[3],
                "review_id": row[4],
                "phase": row[5],
                "actor_route": row[6],
                "requirement": row[7],
                "status": row[8],
                "command": row[9],
                "exit_status": row[10],
                "summary": row[11],
                "evidence_paths": json.loads(row[12]) if row[12] else [],
                "created_at": row[13],
            }
            for row in cursor.fetchall()
        ]


class ReviewRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, run_id: int, subject_type: str, subject_id: int, decision: str, reviewer_route: Optional[str] = None, findings: Optional[str] = None, evidence_paths: Optional[List[str]] = None) -> int:
        if decision not in {"approved", "rejected", "follow_up", "assessment", "block", "resume", "retry_with_handoff", "abandon"}:
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

    def get_latest_for(self, subject_type: str, subject_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, subject_type, subject_id, reviewer_route, findings, decision, evidence_paths, created_at "
            "FROM reviews "
            "WHERE subject_type = ? AND subject_id = ? "
            "ORDER BY id DESC LIMIT 1;",
            (subject_type, subject_id)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
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

    def get_latest_rejection(self, subject_type: str, subject_id: int) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT findings FROM reviews 
            WHERE subject_type = ? AND subject_id = ? AND decision = 'rejected' 
            ORDER BY id DESC LIMIT 1;
            """,
            (subject_type, subject_id)
        )
        row = cursor.fetchone()
        return row[0] if row else None


class HandoverRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        run_id: int,
        task_id: int,
        attempt_id: Optional[int],
        phase: str,
        actor_route: Optional[str] = None,
        decision: Optional[str] = None,
        severity: Optional[str] = None,
        summary: Optional[str] = None,
        blocking_findings: Optional[str] = None,
        followups: Optional[str] = None,
        commit_sha: Optional[str] = None,
        verification_status: Optional[str] = None,
        evidence_paths: Optional[List[str]] = None,
    ) -> int:
        if phase not in {"executor", "reviewer"}:
            raise ValueError(f"Invalid handover phase: {phase}")
        ev_str = json.dumps(evidence_paths) if evidence_paths else None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO task_handover_entries (
                run_id, task_id, attempt_id, phase, actor_route, decision, severity,
                summary, blocking_findings, followups, commit_sha, verification_status,
                evidence_paths
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                run_id,
                task_id,
                attempt_id,
                phase,
                actor_route,
                decision,
                severity,
                summary,
                blocking_findings,
                followups,
                commit_sha,
                verification_status,
                ev_str,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_by_task(self, run_id: int, task_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, run_id, task_id, attempt_id, phase, actor_route, decision,
                   severity, summary, blocking_findings, followups, commit_sha,
                   verification_status, evidence_paths, created_at
            FROM task_handover_entries
            WHERE run_id = ? AND task_id = ?
            ORDER BY COALESCE(attempt_id, 0), id;
            """,
            (run_id, task_id),
        )
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "task_id": row[2],
                "attempt_id": row[3],
                "phase": row[4],
                "actor_route": row[5],
                "decision": row[6],
                "severity": row[7],
                "summary": row[8],
                "blocking_findings": row[9],
                "followups": row[10],
                "commit_sha": row[11],
                "verification_status": row[12],
                "evidence_paths": json.loads(row[13]) if row[13] else [],
                "created_at": row[14],
            }
            for row in cursor.fetchall()
        ]


class LifecycleEventRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(
        self,
        run_id: int,
        event_type: str,
        task_id: Optional[int] = None,
        attempt_id: Optional[int] = None,
        actor: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        evidence_paths: Optional[List[str]] = None,
    ) -> int:
        metadata_str = json.dumps(metadata) if metadata else None
        evidence_str = json.dumps(evidence_paths) if evidence_paths else None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO lifecycle_events (
                run_id, task_id, attempt_id, event_type, actor, summary,
                metadata, evidence_paths
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                run_id,
                task_id,
                attempt_id,
                event_type,
                actor,
                summary,
                metadata_str,
                evidence_str,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_by_run(self, run_id: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        query = """
            SELECT id, run_id, task_id, attempt_id, event_type, actor, summary,
                   metadata, evidence_paths, created_at
            FROM lifecycle_events
            WHERE run_id = ?
            ORDER BY id ASC
        """
        params: List[Any] = [run_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        cursor.execute(query, params)
        return [
            {
                "id": row[0],
                "run_id": row[1],
                "task_id": row[2],
                "attempt_id": row[3],
                "event_type": row[4],
                "actor": row[5],
                "summary": row[6],
                "metadata": json.loads(row[7]) if row[7] else {},
                "evidence_paths": json.loads(row[8]) if row[8] else [],
                "created_at": row[9],
            }
            for row in cursor.fetchall()
        ]


class ProviderStateRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, provider: str, model: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT provider, model, capability_snapshot, availability, quota_state, quota_limit_reset, last_probe FROM provider_state WHERE provider = ? AND model = ?;",
            (provider, model)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "provider": row[0],
            "model": row[1],
            "capability_snapshot": json.loads(row[2]) if row[2] else {},
            "availability": bool(row[3]),
            "quota_state": row[4],
            "quota_limit_reset": row[5],
            "last_probe": row[6]
        }

    def list_all(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT provider, model, capability_snapshot, availability, quota_state, quota_limit_reset, last_probe FROM provider_state;")
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "provider": row[0],
                "model": row[1],
                "capability_snapshot": json.loads(row[2]) if row[2] else {},
                "availability": bool(row[3]),
                "quota_state": row[4],
                "quota_limit_reset": row[5],
                "last_probe": row[6]
            })
        return result

    def save(self, provider: str, model: str, capability_snapshot: Dict[str, Any], availability: bool, quota_state: str = "available", quota_limit_reset: Optional[str] = None, last_probe: Optional[str] = None) -> None:
        cap_str = json.dumps(capability_snapshot)
        avail_int = 1 if availability else 0
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO provider_state (provider, model, capability_snapshot, availability, quota_state, quota_limit_reset, last_probe)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, model) DO UPDATE SET
                capability_snapshot = excluded.capability_snapshot,
                availability = excluded.availability,
                quota_state = excluded.quota_state,
                quota_limit_reset = excluded.quota_limit_reset,
                last_probe = excluded.last_probe;
            """,
            (provider, model, cap_str, avail_int, quota_state, quota_limit_reset, last_probe)
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

    def create(self, run_id: int, task_id: Optional[int], old_test_path: str, replacement_test_path: str, rationale: str, evidence: Optional[str] = None, previous_behavior: Optional[str] = None, replacement_behavior: Optional[str] = None, commit_sha: Optional[str] = None) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO test_migrations (run_id, task_id, old_test_path, replacement_test_path, rationale, evidence, approval_status, previous_behavior, replacement_behavior, commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?);
            """,
            (run_id, task_id, old_test_path, replacement_test_path, rationale, evidence, previous_behavior, replacement_behavior, commit_sha)
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, migration_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, task_id, old_test_path, replacement_test_path, rationale, evidence, approval_status, created_at, previous_behavior, replacement_behavior, commit_sha FROM test_migrations WHERE id = ?;",
            (migration_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "run_id": row[1],
            "task_id": row[2],
            "old_test_path": row[3],
            "replacement_test_path": row[4],
            "rationale": row[5],
            "evidence": row[6],
            "approval_status": row[7],
            "created_at": row[8],
            "previous_behavior": row[9],
            "replacement_behavior": row[10],
            "commit_sha": row[11]
        }

    def get_by_run(self, run_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, run_id, task_id, old_test_path, replacement_test_path, rationale, evidence, approval_status, created_at, previous_behavior, replacement_behavior, commit_sha FROM test_migrations WHERE run_id = ?;",
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
                "created_at": row[8],
                "previous_behavior": row[9],
                "replacement_behavior": row[10],
                "commit_sha": row[11]
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
