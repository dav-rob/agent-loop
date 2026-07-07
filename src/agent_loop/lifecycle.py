import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_loop.repositories import LifecycleEventRepository
from agent_loop.views import render_progress_md


class TaskLifecycleRecorder:
    """Records task lifecycle events and refreshes generated progress views."""

    def __init__(self, conn: sqlite3.Connection, progress_path: Path):
        self.conn = conn
        self.progress_path = progress_path
        self.events = LifecycleEventRepository(conn)

    def render_progress(self, run_id: int) -> None:
        render_progress_md(self.conn, run_id, self.progress_path)

    def record(
        self,
        run_id: int,
        event_type: str,
        task_id: Optional[int] = None,
        attempt_id: Optional[int] = None,
        actor: Optional[str] = None,
        summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        evidence_paths: Optional[List[str]] = None,
        render: bool = True,
    ) -> int:
        event_id = self.events.create(
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            event_type=event_type,
            actor=actor,
            summary=summary,
            metadata=metadata,
            evidence_paths=evidence_paths,
        )
        if render:
            self.render_progress(run_id)
        return event_id

    def task_started(self, run_id: int, task: Dict[str, Any]) -> int:
        return self.record(
            run_id,
            "task_started",
            task_id=task["id"],
            summary=f"Started task {task['name']}.",
            metadata={"role": task.get("role"), "risk": task.get("risk")},
        )

    def attempt_started(
        self,
        run_id: int,
        task_id: int,
        attempt_id: int,
        route: str,
        worktree_path: Optional[str],
        logs_path: Optional[str],
    ) -> int:
        return self.record(
            run_id,
            "attempt_started",
            task_id=task_id,
            attempt_id=attempt_id,
            actor=route,
            summary=f"Attempt {attempt_id} started on route {route}.",
            metadata={"route": route, "worktree_path": worktree_path, "logs_path": logs_path},
            evidence_paths=[logs_path] if logs_path else None,
        )

    def executor_started(self, run_id: int, task_id: int, attempt_id: int, profile: str) -> int:
        return self.record(
            run_id,
            "executor_started",
            task_id=task_id,
            attempt_id=attempt_id,
            actor=profile,
            summary=f"Executor started using profile {profile}.",
            metadata={"profile": profile},
        )

    def executor_completed(
        self,
        run_id: int,
        task_id: int,
        attempt_id: int,
        actor: str,
        summary: str,
        commit_sha: Optional[str] = None,
        verification_status: Optional[str] = None,
        evidence_paths: Optional[List[str]] = None,
    ) -> int:
        return self.record(
            run_id,
            "executor_completed",
            task_id=task_id,
            attempt_id=attempt_id,
            actor=actor,
            summary=summary,
            metadata={"commit_sha": commit_sha, "verification_status": verification_status},
            evidence_paths=evidence_paths,
        )

    def executor_failed(
        self,
        run_id: int,
        task_id: int,
        attempt_id: int,
        actor: str,
        summary: str,
        reason: str,
        evidence_paths: Optional[List[str]] = None,
    ) -> int:
        return self.record(
            run_id,
            "executor_failed",
            task_id=task_id,
            attempt_id=attempt_id,
            actor=actor,
            summary=summary,
            metadata={"reason": reason},
            evidence_paths=evidence_paths,
        )

    def review_started(
        self,
        run_id: int,
        task_id: Optional[int],
        attempt_id: Optional[int],
        review_type: str,
    ) -> int:
        return self.record(
            run_id,
            "review_started",
            task_id=task_id,
            attempt_id=attempt_id,
            actor=review_type,
            summary=f"Started {review_type} review.",
            metadata={"review_type": review_type},
        )

    def review_completed(
        self,
        run_id: int,
        task_id: Optional[int],
        attempt_id: Optional[int],
        actor: Optional[str],
        decision: str,
        findings: str,
        review_type: str,
        evidence_paths: Optional[List[str]] = None,
    ) -> int:
        return self.record(
            run_id,
            "review_completed",
            task_id=task_id,
            attempt_id=attempt_id,
            actor=actor or review_type,
            summary=f"{review_type} review decided {decision}: {findings}",
            metadata={"decision": decision, "review_type": review_type},
            evidence_paths=evidence_paths,
        )

    def task_retrying(self, run_id: int, task_id: int, summary: str) -> int:
        return self.record(run_id, "task_retrying", task_id=task_id, summary=summary)

    def task_completed(self, run_id: int, task_id: int, summary: str) -> int:
        return self.record(run_id, "task_completed", task_id=task_id, summary=summary)

    def task_blocked(self, run_id: int, task_id: int, summary: str) -> int:
        return self.record(run_id, "task_blocked", task_id=task_id, summary=summary)
