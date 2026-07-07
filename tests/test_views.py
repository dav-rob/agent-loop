import sqlite3
import pytest
from pathlib import Path
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import RunRepository, FeatureRepository, TaskRepository, AttemptRepository, LifecycleEventRepository
from agent_loop.views import render_plan_md, render_progress_md

@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()

def test_markdown_rendering(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    # Setup database state
    run_id = run_repo.create(
        goal="Establish baseline package structure",
        intake_mode="non_interactive"
    )
    run_repo.update_status(run_id, "planning")

    feat_id = feat_repo.create(
        run_id=run_id,
        name="Foundation",
        risk="low",
        acceptance_criteria="Package can be built",
        dependencies=[]
    )

    task_id = task_repo.create(
        run_id=run_id,
        feature_id=feat_id,
        name="Init repo",
        role="implementation",
        risk="low",
        scope=None,
        dependencies=[],
        required_verification="pytest tests"
    )

    plan_file = tmp_path / "plan.md"
    progress_file = tmp_path / "progress.md"

    # Render files
    render_plan_md(db_conn, run_id, plan_file)
    render_progress_md(db_conn, run_id, progress_file)

    # Verify plan.md
    plan_content = plan_file.read_text()
    assert "Establish baseline package structure" in plan_content
    assert "Foundation" in plan_content
    assert "Init repo" in plan_content
    assert "Risk: LOW" in plan_content
    assert "[ ] Init repo" in plan_content

    # Verify progress.md
    progress_content = progress_file.read_text()
    assert "Establish baseline package structure" in progress_content
    assert "Run Status: planning" in progress_content
    assert "LOOP_STATUS: continue" in progress_content

    # Move to complete status
    run_repo.update_status(run_id, "running")
    task_repo.update_status(task_id, "ready")
    task_repo.update_status(task_id, "running")
    task_repo.update_status(task_id, "reviewing")
    task_repo.update_status(task_id, "complete")
    run_repo.update_status(run_id, "reviewing")
    run_repo.update_status(run_id, "complete")

    render_plan_md(db_conn, run_id, plan_file)
    render_progress_md(db_conn, run_id, progress_file)

    # Verify completed checkmark in plan
    plan_content_new = plan_file.read_text()
    assert "[x] Init repo" in plan_content_new

    # Verify complete loop status in progress
    progress_content_new = progress_file.read_text()
    assert "Run Status: complete" in progress_content_new
    assert "LOOP_STATUS: complete" in progress_content_new


def test_progress_md_shows_running_task_before_attempt_exists(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Build dashboard", "none")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(
        run_id=run_id,
        feature_id=feat_id,
        name="Scaffold full-stack app",
        role="implementation",
        risk="medium",
        scope={"writes": ["package.json"]},
        dependencies=[],
        required_verification="npm run typecheck",
    )
    task_repo.update_status(task_id, "ready")
    task_repo.update_status(task_id, "running")

    progress_file = tmp_path / "progress.md"
    render_progress_md(db_conn, run_id, progress_file)

    progress_content = progress_file.read_text()
    assert "- Starting task: **Scaffold full-stack app**" in progress_content
    assert "No active task attempts." not in progress_content


def test_progress_md_shows_running_attempt_with_pending_model_metadata(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Build dashboard", "none")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(
        run_id=run_id,
        feature_id=feat_id,
        name="Scaffold full-stack app",
        role="implementation",
        risk="medium",
        scope={"writes": ["package.json"]},
        dependencies=[],
        required_verification="npm run typecheck",
    )
    task_repo.update_status(task_id, "ready")
    task_repo.update_status(task_id, "running")
    attempt_repo.create(
        run_id=run_id,
        task_id=task_id,
        route="executor",
        logs_path="/tmp/agent-loop/logs/1/1/1",
        worktree_path="/tmp/agent-loop/worktrees/run-1-task-1-attempt-1",
    )

    progress_file = tmp_path / "progress.md"
    render_progress_md(db_conn, run_id, progress_file)

    progress_content = progress_file.read_text()
    assert "- Task: **Scaffold full-stack app**" in progress_content
    assert "Route: executor" in progress_content
    assert "Model: pending" in progress_content
    assert "Logs: `/tmp/agent-loop/logs/1/1/1`" in progress_content


def test_progress_md_renders_recent_lifecycle_events(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)
    lifecycle_repo = LifecycleEventRepository(db_conn)

    run_id = run_repo.create("Build dashboard", "none")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Foundation", "medium")
    task_id = task_repo.create(run_id, feat_id, "Scaffold dashboard", "implementation", "medium")
    attempt_id = attempt_repo.create(run_id, task_id, route="executor_escalated")

    lifecycle_repo.create(
        run_id=run_id,
        event_type="task_started",
        task_id=task_id,
        summary="Started task Scaffold dashboard.",
    )
    lifecycle_repo.create(
        run_id=run_id,
        event_type="executor_failed",
        task_id=task_id,
        attempt_id=attempt_id,
        actor="executor_escalated:codex:gpt-5.5",
        summary="Timed out before returning final handover.",
        metadata={"reason": "timeout"},
    )

    progress_file = tmp_path / "progress.md"
    render_progress_md(db_conn, run_id, progress_file)

    progress_content = progress_file.read_text()
    assert "### Recent Lifecycle Events" in progress_content
    assert "`task_started`" in progress_content
    assert "Started task Scaffold dashboard." in progress_content
    assert "`executor_failed`" in progress_content
    assert "Attempt 1" in progress_content
    assert "Timed out before returning final handover." in progress_content
