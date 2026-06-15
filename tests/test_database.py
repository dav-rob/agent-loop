import sqlite3
import pytest
from pathlib import Path
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import (
    RunRepository,
    FeatureRepository,
    TaskRepository,
    AttemptRepository,
    ReviewRepository,
    ProviderStateRepository,
    NotificationRepository,
    DecisionRepository,
    TestMigrationRepository,
)

@pytest.fixture
def db_conn():
    # In-memory SQLite database
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()

def test_migration_tables(db_conn):
    # Verify that all tables were created
    cursor = db_conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    
    expected_tables = {
        "schema_migrations",
        "runs",
        "features",
        "tasks",
        "attempts",
        "test_runs",
        "reviews",
        "provider_state",
        "notifications",
        "decisions",
        "test_migrations"
    }
    assert expected_tables.issubset(tables)

def test_run_repository(db_conn):
    repo = RunRepository(db_conn)
    run_id = repo.create(
        goal="Test orchestrator loop implementation",
        intake_mode="autonomous",
        config_snapshot={"max_workers": 2}
    )
    assert run_id > 0
    
    run = repo.get(run_id)
    assert run is not None
    assert run["goal"] == "Test orchestrator loop implementation"
    assert run["intake_mode"] == "autonomous"
    assert run["status"] == "draft"
    assert run["config_snapshot"] == {"max_workers": 2}
    
    all_runs = repo.list_all()
    assert len(all_runs) == 1
    assert all_runs[0]["id"] == run_id
    
    # Valid transition: draft -> planning
    repo.update_status(run_id, "planning")
    assert repo.get(run_id)["status"] == "planning"
    
    # Invalid transition: planning -> complete (not direct)
    with pytest.raises(ValueError, match="Invalid run status transition"):
        repo.update_status(run_id, "complete")

def test_feature_and_task_repository(db_conn):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    
    run_id = run_repo.create("E2E build task", "non_interactive")
    
    # Create feature
    feat_id = feature_repo.create(
        run_id=run_id,
        name="Foundation",
        risk="low",
        acceptance_criteria="Package installed, test passes",
        dependencies=[]
    )
    assert feat_id > 0
    
    feature = feature_repo.get(feat_id)
    assert feature["name"] == "Foundation"
    assert feature["review_status"] == "pending"
    
    # Update feature review status
    feature_repo.update_review_status(feat_id, "approved")
    assert feature_repo.get(feat_id)["review_status"] == "approved"
    
    # Create task
    task_id = task_repo.create(
        run_id=run_id,
        feature_id=feat_id,
        name="Install Package",
        role="implementation",
        risk="low",
        scope={"files": ["pyproject.toml"]},
        dependencies=[],
        required_verification="pip list"
    )
    assert task_id > 0
    
    task = task_repo.get(task_id)
    assert task["name"] == "Install Package"
    assert task["status"] == "pending"
    
    # Valid transition: pending -> ready -> running -> reviewing -> complete
    task_repo.update_status(task_id, "ready")
    task_repo.update_status(task_id, "running")
    task_repo.update_status(task_id, "reviewing")
    task_repo.update_status(task_id, "complete")
    assert task_repo.get(task_id)["status"] == "complete"
    
    # Invalid transition: complete -> pending (no transition from complete)
    with pytest.raises(ValueError, match="Invalid task status transition"):
        task_repo.update_status(task_id, "pending")

def test_attempt_repository(db_conn):
    run_repo = RunRepository(db_conn)
    feature_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)
    
    run_id = run_repo.create("Test run", "autonomous")
    feat_id = feature_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    
    attempt_id = attempt_repo.create(
        run_id=run_id,
        task_id=task_id,
        route="planning",
        provider="codex",
        model="gpt-5.5",
        reasoning_level="high",
        worktree_path="/tmp/worktree",
        commit_sha=None,
        logs_path="/tmp/logs"
    )
    assert attempt_id > 0
    
    attempt = attempt_repo.get(attempt_id)
    assert attempt["outcome"] == "running"
    assert attempt["provider"] == "codex"
    
    attempt_repo.update_outcome(attempt_id, "completed", commit_sha="abc123sha")
    attempt = attempt_repo.get(attempt_id)
    assert attempt["outcome"] == "completed"
    assert attempt["commit_sha"] == "abc123sha"

def test_decision_repository(db_conn):
    run_repo = RunRepository(db_conn)
    decision_repo = DecisionRepository(db_conn)
    
    run_id = run_repo.create("Test run", "autonomous")
    dec_id = decision_repo.create(
        run_id=run_id,
        decision_type="product",
        is_autonomous=True,
        summary="Use in-memory sqlite",
        details="For unit test speed"
    )
    assert dec_id > 0
    
    decs = decision_repo.get_by_run(run_id)
    assert len(decs) == 1
    assert decs[0]["summary"] == "Use in-memory sqlite"
    assert decs[0]["is_autonomous"] is True
