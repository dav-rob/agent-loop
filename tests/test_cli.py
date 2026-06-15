import sys
from pathlib import Path
from unittest.mock import patch
import pytest
from agent_loop.cli import main
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import RunRepository, FeatureRepository, TaskRepository, AttemptRepository

@pytest.fixture
def clean_workspace(tmp_path, monkeypatch):
    # Change working directory to a temp directory
    monkeypatch.chdir(tmp_path)
    return tmp_path

def test_cli_start_non_interactive(clean_workspace):
    # Test starting a run in non-interactive mode
    test_args = [
        "agent-loop",
        "start",
        "--non-interactive",
        "--goal",
        "Build a compiler"
    ]
    with patch.object(sys, "argv", test_args):
        main()

    # Verify database exists and run is created
    db_path = Path(".agent-loop.db")
    assert db_path.exists()

    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    assert len(runs) == 1
    assert runs[0]["goal"] == "Build a compiler"
    assert runs[0]["intake_mode"] == "non_interactive"
    assert runs[0]["status"] == "planning"
    conn.close()

    # Verify views rendered
    assert Path("plan.md").exists()
    assert Path("progress.md").exists()

def test_cli_plan_details(clean_workspace, capsys):
    # Setup test run in db
    db_path = Path(".agent-loop.db")
    conn = get_connection(db_path)
    migrate(conn)

    run_repo = RunRepository(conn)
    feat_repo = FeatureRepository(conn)
    task_repo = TaskRepository(conn)
    attempt_repo = AttemptRepository(conn)

    run_id = run_repo.create("Build a compiler", "non_interactive")
    feat_id = feat_repo.create(run_id, "Lexer", "low", "Parse tokens")
    task_id = task_repo.create(run_id, feat_id, "Define regex", "implementation", "low")
    attempt_repo.create(
        run_id=run_id,
        task_id=task_id,
        route="implementation",
        provider="codex",
        model="gpt-5.4-mini",
        reasoning_level="high",
        worktree_path="/tmp/worktree",
        commit_sha="abcd123",
        logs_path="/tmp/logs"
    )
    conn.close()

    # Test 'agent-loop plan'
    test_args = ["agent-loop", "plan", str(run_id)]
    with patch.object(sys, "argv", test_args):
        main()

    captured = capsys.readouterr()
    assert "Lexer" in captured.out
    assert "Define regex" in captured.out

    # Test 'agent-loop plan --details'
    test_args = ["agent-loop", "plan", str(run_id), "--details"]
    with patch.object(sys, "argv", test_args):
        main()

    captured_details = capsys.readouterr()
    assert "Lexer" in captured_details.out
    assert "DETAILED METADATA" in captured_details.out
    assert "gpt-5.4-mini" in captured_details.out
    assert "abcd123" in captured_details.out

def test_cli_resume(clean_workspace):
    db_path = Path(".agent-loop.db")
    conn = get_connection(db_path)
    migrate(conn)

    run_repo = RunRepository(conn)
    feat_repo = FeatureRepository(conn)
    task_repo = TaskRepository(conn)
    attempt_repo = AttemptRepository(conn)

    run_id = run_repo.create("Resume test", "non_interactive")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    attempt_id = attempt_repo.create(run_id, task_id, "implementation", "codex", "gpt-5.4-mini")

    assert attempt_repo.get(attempt_id)["outcome"] == "running"
    conn.close()

    # Test 'agent-loop resume'
    test_args = ["agent-loop", "resume", str(run_id)]
    with patch.object(sys, "argv", test_args):
        main()

    # Verify attempt is now marked abandoned
    conn = get_connection(db_path)
    attempt_repo = AttemptRepository(conn)
    assert attempt_repo.get(attempt_id)["outcome"] == "abandoned"
    conn.close()
