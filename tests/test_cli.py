import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from agent_loop.cli import main, describe_goal
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import RunRepository, FeatureRepository, TaskRepository, AttemptRepository

@pytest.fixture
def clean_workspace(tmp_path, monkeypatch):
    # Change working directory to a temp directory
    monkeypatch.chdir(tmp_path)
    return tmp_path

def default_state_dir() -> Path:
    return Path(".agent-loop")

def default_db_path() -> Path:
    return default_state_dir() / "agent-loop.db"

def test_cli_start_non_interactive(clean_workspace):
    # Test starting a run in non-interactive mode
    test_args = [
        "agent-loop",
        "start",
        "--non-interactive",
        "--goal",
        "Build a compiler"
    ]
    
    # Mock planning to update database state successfully without running adapters
    def mock_plan_run(run_id):
        db_path = default_db_path()
        conn = get_connection(db_path)
        RunRepository(conn).update_status(run_id, "planning")
        RunRepository(conn).update_status(run_id, "running")
        conn.close()
        return True

    with patch("agent_loop.cli.Orchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_orch.plan_run.side_effect = mock_plan_run
        mock_orch_cls.return_value = mock_orch

        with patch.object(sys, "argv", test_args):
            main()

        mock_orch.plan_run.assert_called_once()
        mock_orch.run_loop.assert_called_once()

    # Verify database exists and run is created
    state_dir = default_state_dir()
    db_path = default_db_path()
    assert state_dir.is_dir()
    assert db_path.exists()
    assert (state_dir / "logs").is_dir()
    assert (state_dir / "worktrees").is_dir()
    assert (state_dir / "goals").is_dir()
    assert (state_dir / "plans").is_dir()
    assert (state_dir / "specs").is_dir()
    assert (state_dir / "learning.md").exists()

    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    assert len(runs) == 1
    assert runs[0]["goal"] == "Build a compiler"
    assert runs[0]["intake_mode"] == "non_interactive"
    assert runs[0]["status"] == "running"
    conn.close()

    # Verify views rendered
    assert (state_dir / "plan.md").exists()
    assert (state_dir / "progress.md").exists()
    assert not Path(".agent-loop.db").exists()
    assert not Path("plan.md").exists()
    assert not Path("progress.md").exists()

def test_cli_plan_details(clean_workspace, capsys):
    # Setup test run in db
    db_path = default_db_path()
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


def test_cli_status_uses_goal_language(clean_workspace, capsys):
    db_path = default_db_path()
    conn = get_connection(db_path)
    migrate(conn)

    run_repo = RunRepository(conn)
    run_id = run_repo.create(
        "Add CSV export to reports and update the relevant tests for the CLI workflow",
        "autonomous"
    )
    conn.close()

    test_args = ["agent-loop", "status", str(run_id)]
    with patch.object(sys, "argv", test_args):
        main()

    captured = capsys.readouterr()
    assert f"Goal ID: {run_id}" in captured.out
    assert "Goal Description: Add CSV export to reports and update the relevant tests for the CLI..." in captured.out
    assert "Goal: Add CSV export" not in captured.out
    assert "Intake Mode: autonomous" in captured.out
    assert "Status: draft" in captured.out


def test_goal_description_truncates_cleanly():
    assert describe_goal("Short goal") == "Short goal"
    assert describe_goal("A" * 80) == ("A" * 67) + "..."

def test_cli_resume(clean_workspace):
    db_path = default_db_path()
    conn = get_connection(db_path)
    migrate(conn)

    run_repo = RunRepository(conn)
    feat_repo = FeatureRepository(conn)
    task_repo = TaskRepository(conn)
    attempt_repo = AttemptRepository(conn)

    run_id = run_repo.create("Resume test", "non_interactive")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    attempt_id = attempt_repo.create(run_id, task_id, "implementation", "codex", "gpt-5.4-mini")

    assert attempt_repo.get(attempt_id)["outcome"] == "running"
    conn.close()

    # Test 'agent-loop resume'
    test_args = ["agent-loop", "resume", str(run_id)]
    with patch("agent_loop.cli.Orchestrator.run_loop") as mock_run_loop:
        with patch.object(sys, "argv", test_args):
            main()
        mock_run_loop.assert_called_once()

    # Verify attempt is now marked abandoned
    conn = get_connection(db_path)
    attempt_repo = AttemptRepository(conn)
    assert attempt_repo.get(attempt_id)["outcome"] == "abandoned"
    conn.close()


def test_cli_resume_replans_blocked_goal_without_features(clean_workspace):
    db_path = default_db_path()
    conn = get_connection(db_path)
    migrate(conn)

    run_repo = RunRepository(conn)
    run_id = run_repo.create("Resume failed planning", "brainstorm")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "blocked")
    conn.close()

    def mock_plan_run(run_id_arg):
        conn_inner = get_connection(db_path)
        repo_inner = RunRepository(conn_inner)
        repo_inner.update_status(run_id_arg, "planning")
        repo_inner.update_status(run_id_arg, "awaiting_plan_approval")
        conn_inner.close()
        return True

    test_args = ["agent-loop", "resume", str(run_id)]
    with patch("agent_loop.cli.Orchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_orch.reconcile_interrupted_run.return_value = 0
        mock_orch.plan_run.side_effect = mock_plan_run
        mock_orch_cls.return_value = mock_orch

        with patch.object(sys, "argv", test_args):
            main()

        mock_orch.plan_run.assert_called_once_with(run_id)
        mock_orch.run_loop.assert_not_called()

    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    assert run_repo.get(run_id)["status"] == "awaiting_plan_approval"
    conn.close()


def test_cli_intake_and_approval(clean_workspace):
    # Test interactive wizard with brainstorm_ui_lab intake and plan approval
    test_args = ["agent-loop", "start"]

    # Simulating interactive inputs:
    # 1. Broad goal: "Design a nice portal"
    # 2. Choice of intake mode: "2" (Brainstorm with UI Lab)
    # 3. Refinement: "Must look sleek"
    # 4. UI styling: "Dark neon"
    # 5. UI pages: "Dashboard, settings"
    # 6. Plan approval: "y"
    user_inputs = [
        "Design a nice portal",
        "2",
        "Must look sleek",
        "fast",
        "tool",
        "immediately",
        "slow",
        "Dark neon",
        "disliked_app",
        "y"
    ]
    input_generator = (val for val in user_inputs)

    def mock_plan_run(run_id):
        db_path = default_db_path()
        conn = get_connection(db_path)
        RunRepository(conn).update_status(run_id, "planning")
        RunRepository(conn).update_status(run_id, "awaiting_plan_approval")
        conn.close()
        return True

    with patch("builtins.input", side_effect=lambda *args, **kwargs: next(input_generator)):
        with patch("agent_loop.cli.Orchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.plan_run.side_effect = mock_plan_run
            mock_orch_cls.return_value = mock_orch

            with patch("agent_loop.adapters.AgyAdapter") as mock_agy_adapter_cls:
                mock_adapter_instance = MagicMock()
                mock_agy_adapter_cls.return_value = mock_adapter_instance

                from agent_loop.adapters import AttemptResult
                mock_adapter_instance.run_attempt.return_value = AttemptResult(
                    success=True,
                    exit_code=0,
                    output=(
                        "UI Questions\n"
                        "- Should this feel fast or thoughtful?\n"
                        "- Should this feel like a tool or a guide?\n"
                        "- Should people see everything immediately or should detail appear gradually?\n"
                        "- What would make this feel wrong?\n"
                        "- What apps do you like?\n"
                        "- What apps do you dislike?\n"
                    ),
                    error=""
                )

                with patch.object(sys, "argv", test_args):
                    main()

                # Assert that AgyAdapter.run_attempt is called once and prompt starts with /brief
                mock_adapter_instance.run_attempt.assert_called_once()
                run_attempt_kwargs = mock_adapter_instance.run_attempt.call_args[1]
                assert run_attempt_kwargs["prompt"].startswith("/brief")

            mock_orch.plan_run.assert_called_once()
            mock_orch.run_loop.assert_called_once()

    # Verify db status and goal refinement
    db_path = default_db_path()
    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    assert len(runs) == 1
    assert "Must look sleek" in runs[0]["goal"]
    assert "Dark neon" in runs[0]["goal"]
    assert runs[0]["intake_mode"] == "brainstorm_ui_lab"
    assert runs[0]["status"] == "running" # Transitioned by 'y' approval
    conn.close()


def test_cli_start_captures_pasted_multiline_goal(clean_workspace):
    class ScriptedTTY:
        def __init__(self, first_line, pasted_lines, later_lines):
            self.first_line = first_line
            self.pasted_lines = list(pasted_lines)
            self.later_lines = list(later_lines)

        def readline(self):
            if self.first_line is not None:
                line = self.first_line
                self.first_line = None
                return line
            if self.pasted_lines:
                return self.pasted_lines.pop(0)
            if self.later_lines:
                return self.later_lines.pop(0)
            return ""

        def isatty(self):
            return True

        def fileno(self):
            return 0

    pasted_goal_lines = [
        "\n",
        "1) Headlines like this: https://www.google.com/search?q=news+headlines+today\n",
        "2) The coming weather by the hour for the next 24 hours\n",
        "3) Subscribe to Youtube sites, starting with\n",
        "https://www.youtube.com/@NatureVideoChannel, https://www.youtube.com/@TLDRnewsGLOBAL\n",
        "\n",
        "- if there is a new video, summarize it with agy CLI\n",
    ]
    fake_stdin = ScriptedTTY(
        "Create a website that runs in the background and checks hourly:\n",
        pasted_goal_lines,
        [
            "1\n",
            "\n",
        ],
    )

    def fake_select(readable, _writable, _exceptional, _timeout=0):
        if fake_stdin.pasted_lines:
            return readable, [], []
        return [], [], []

    def mock_plan_run(run_id):
        db_path = default_db_path()
        conn = get_connection(db_path)
        RunRepository(conn).update_status(run_id, "planning")
        RunRepository(conn).update_status(run_id, "running")
        conn.close()
        return True

    with patch.object(sys, "stdin", fake_stdin), \
         patch("select.select", side_effect=fake_select), \
         patch("agent_loop.cli.Orchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_orch.plan_run.side_effect = mock_plan_run
        mock_orch_cls.return_value = mock_orch

        with patch.object(sys, "argv", ["agent-loop", "start"]):
            main()

        mock_orch.plan_run.assert_called_once()
        mock_orch.run_loop.assert_called_once()

    db_path = default_db_path()
    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    assert len(runs) == 1
    assert "news+headlines+today" in runs[0]["goal"]
    assert "https://www.youtube.com/@TLDRnewsGLOBAL" in runs[0]["goal"]
    assert "summarize it with agy CLI" in runs[0]["goal"]
    assert runs[0]["intake_mode"] == "brainstorm"
    conn.close()


def test_cli_approve_command(clean_workspace):
    # Test the standalone approve subcommand
    db_path = default_db_path()
    conn = get_connection(db_path)
    migrate(conn)
    run_repo = RunRepository(conn)
    run_id = run_repo.create("Approve command test", "brainstorm")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "awaiting_plan_approval")
    conn.close()

    test_args = ["agent-loop", "approve", str(run_id)]
    with patch("agent_loop.cli.Orchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_orch_cls.return_value = mock_orch

        with patch.object(sys, "argv", test_args):
            main()

        mock_orch.run_loop.assert_called_once_with(run_id)

    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    assert run_repo.get(run_id)["status"] == "running"
    conn.close()
