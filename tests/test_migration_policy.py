import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import RunRepository, FeatureRepository, TaskRepository
from agent_loop.orchestrator import Orchestrator
from agent_loop.views import render_progress_md

@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()

def test_accepted_test_migration(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Migration test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    # Mock git show --name-status to show modified test file and added test file
    mock_show = MagicMock(returncode=0, stdout="abcd123\nM\ttests/test_foo.py\nA\ttests/test_bar.py\n")
    # Mock git diff of modified file to show added skip marker
    mock_diff_file = MagicMock(returncode=0, stdout="+@pytest.mark.skip(reason='migration: MIG-101')\n")
    # Mock full diff to show new test function added
    mock_full_diff = MagicMock(returncode=0, stdout="+def test_bar():\n+    # covers: MIG-101\n")

    def run_side_effect(cmd, *args, **kwargs):
        if "show" in cmd:
            return mock_show
        if "--" in cmd:
            return mock_diff_file
        return mock_full_diff

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.detect_and_record_test_migrations(run_id, task_id, "abcd123")

    # Verify migration is created as pending because a replacement was found (test_bar.py added)
    migrations = orch.test_migration_repo.get_by_run(run_id)
    assert len(migrations) == 1
    assert migrations[0]["old_test_path"] == "tests/test_foo.py"
    assert migrations[0]["replacement_test_path"] == "tests/test_bar.py"
    assert migrations[0]["approval_status"] == "pending"

    # Verify progress view prepends TEST BASELINE CHANGES
    progress_file = tmp_path / "progress.md"
    render_progress_md(db_conn, run_id, progress_file)
    content = progress_file.read_text()
    assert "TEST BASELINE CHANGES" in content
    assert "tests/test_foo.py" in content

def test_rejected_test_migration(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Migration test", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "complete", force=True)
    feat_repo.update_review_status(feat_id, "approved")

    # Mock git show --name-status to show modified test file only (no replacement test files)
    mock_show = MagicMock(returncode=0, stdout="abcd123\nM\ttests/test_foo.py\n")
    # Mock git diff of modified file to show added skip marker
    mock_diff_file = MagicMock(returncode=0, stdout="+@pytest.mark.skip(reason='migration')\n")
    # Mock full diff to show no new test function added
    mock_full_diff = MagicMock(returncode=0, stdout="")

    def run_side_effect(cmd, *args, **kwargs):
        if "show" in cmd:
            return mock_show
        if "--" in cmd:
            return mock_diff_file
        return mock_full_diff

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.detect_and_record_test_migrations(run_id, task_id, "abcd123")

    # Verify migration is created as rejected because no replacement test was found
    migrations = orch.test_migration_repo.get_by_run(run_id)
    assert len(migrations) == 1
    assert migrations[0]["old_test_path"] == "tests/test_foo.py"
    assert migrations[0]["replacement_test_path"] == "none"
    assert migrations[0]["approval_status"] == "rejected"

    # Verify that run_loop blocks completion and transitions run to blocked status
    with patch.object(orch, "run_final_review", return_value=True):
        orch.run_loop(run_id)

    assert run_repo.get(run_id)["status"] == "blocked"
