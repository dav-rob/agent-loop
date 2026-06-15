import json
import sqlite3
import datetime
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import (
    RunRepository,
    FeatureRepository,
    TaskRepository,
    AttemptRepository,
    TestMigrationRepository,
    ProviderStateRepository,
    NotificationRepository,
    TestRunRepository
)
from agent_loop.orchestrator import Orchestrator
from agent_loop.adapters import resolve_binary, get_adapter, CodexAdapter, AgyAdapter, AttemptResult
from agent_loop.cli import handle_migration

@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()

# FIX-04: Gate completion on broader regression verification
def test_regression_gating(db_conn, tmp_path):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "commands": {
            "regression_test": "exit 0"
        }
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    
    run_id = run_repo.create("Test goal", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "complete", force=True)
    feat_repo.update_review_status(feat_id, "approved")
    
    # 1. Regression test succeeds
    with patch.object(orch, "run_final_review", return_value=True):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            orch.run_loop(run_id)
            mock_run.assert_called_once()
            assert run_repo.get(run_id)["status"] == "complete"
            
    # Check that test run was recorded
    tr_repo = TestRunRepository(db_conn)
    test_runs = tr_repo.get_by_run(run_id)
    assert len(test_runs) == 1
    assert test_runs[0]["command"] == "exit 0"
    assert test_runs[0]["exit_status"] == 0
    assert test_runs[0]["task_id"] is None
    assert test_runs[0]["attempt_id"] is None

    # 2. Regression test fails
    run_id2 = run_repo.create("Test goal 2", "autonomous")
    run_repo.update_status(run_id2, "planning")
    run_repo.update_status(run_id2, "running")
    feat_id2 = feat_repo.create(run_id2, "Feature 1", "low")
    task_id2 = task_repo.create(run_id2, feat_id2, "Task 1", "implementation", "low")
    task_repo.update_status(task_id2, "complete", force=True)
    feat_repo.update_review_status(feat_id2, "approved")

    config2 = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "commands": {
            "regression_test": "exit 1"
        }
    })
    orch2 = Orchestrator(db_conn, config2, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    
    with patch.object(orch2, "run_final_review", return_value=True):
        with patch("subprocess.run", return_value=MagicMock(returncode=1)) as mock_run:
            orch2.run_loop(run_id2)
            assert run_repo.get(run_id2)["status"] == "failed"
            test_runs2 = tr_repo.get_by_run(run_id2)
            assert len(test_runs2) == 1
            assert test_runs2[0]["exit_status"] == 1

# FIX-05: Complete the quota state machine across all active routes
def test_quota_state_machine_new_states(db_conn, tmp_path):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    })
    
    p_repo = ProviderStateRepository(db_conn)
    # Save a route as transient_failure
    p_repo.save("codex", "gpt-5.4-mini", {}, False, "transient_failure")
    
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Quota sleep test", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    
    # Ready tasks requiring implementation route
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "ready")
    
    fake_now = datetime.datetime.fromisoformat("2026-06-15T16:00:00+00:00")
    sleep_calls = []
    
    orch = Orchestrator(
        db_conn,
        config,
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
        get_now=lambda: fake_now,
        sleep_func=lambda secs: sleep_calls.append(secs)
    )
    
    # Verify get_required_routes resolves implementation route
    req = orch.get_required_routes(run_id)
    assert len(req) == 1
    assert req[0]["provider"] == "codex"
    assert req[0]["model"] == "gpt-5.4-mini"

    # Mock refresh_provider_quotas to make it available
    def mock_refresh(provider, force_refresh=False):
        p_repo.save("codex", "gpt-5.4-mini", {}, True, "available")
        
    with patch.object(orch, "refresh_provider_quotas", side_effect=mock_refresh):
        orch.check_and_recover_quotas(run_id, req)
        
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 60.0 # Default fallback sleep
    assert run_repo.get(run_id)["status"] == "running"

# FIX-06: Complete the test-migration approval workflow
def test_test_migration_strict_policy(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Migration test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    # 1. Success case: skip reason has migration: MIG-101 and covers: MIG-101 in added test
    mock_show = MagicMock(returncode=0, stdout="abcd123\nM\ttests/test_foo.py\nA\ttests/test_bar.py\n")
    mock_diff_file = MagicMock(returncode=0, stdout="+@pytest.mark.skip(reason='migration: MIG-101')\n+def test_foo():\n")
    mock_full_diff = MagicMock(returncode=0, stdout="+def test_bar():\n+    # covers: MIG-101\n")

    def run_side_effect(cmd, *args, **kwargs):
        if "show" in cmd: return mock_show
        if "--" in cmd: return mock_diff_file
        return mock_full_diff

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.detect_and_record_test_migrations(run_id, task_id, "abcd123")

    migs = orch.test_migration_repo.get_by_run(run_id)
    assert len(migs) == 1
    assert migs[0]["old_test_path"] == "tests/test_foo.py"
    assert migs[0]["replacement_test_path"] == "tests/test_bar.py"
    assert migs[0]["approval_status"] == "pending"
    assert migs[0]["previous_behavior"] == "def test_foo():"
    assert migs[0]["replacement_behavior"] == "def test_bar():"
    assert migs[0]["commit_sha"] == "abcd123"

    # 2. Reject case: skip reason has migration: MIG-102 but no covers tag in replacement
    mock_diff_file2 = MagicMock(returncode=0, stdout="+@pytest.mark.skip(reason='migration: MIG-102')\n")
    mock_full_diff2 = MagicMock(returncode=0, stdout="+def test_bar():\n")
    
    def run_side_effect2(cmd, *args, **kwargs):
        if "show" in cmd: return mock_show
        if "--" in cmd: return mock_diff_file2
        return mock_full_diff2
        
    with patch("subprocess.run", side_effect=run_side_effect2):
        orch.detect_and_record_test_migrations(run_id, task_id, "abcd124")
        
    migs = orch.test_migration_repo.get_by_run(run_id)
    assert len(migs) == 2
    assert migs[1]["approval_status"] == "rejected"
    assert "missing covering evidence" in migs[1]["rationale"]

def test_migration_cli_commands(db_conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs")
    })
    
    run_repo = RunRepository(db_conn)
    migration_repo = TestMigrationRepository(db_conn)
    
    run_id = run_repo.create("Test run", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    # Set run status to complete_pending_test_review
    run_repo.update_status(run_id, "reviewing")
    run_repo.update_status(run_id, "complete_pending_test_review")
    
    mig_id = migration_repo.create(
        run_id=run_id,
        task_id=None,
        old_test_path="tests/test_old.py",
        replacement_test_path="tests/test_new.py",
        rationale="skip reason",
        evidence="evidence",
        previous_behavior="def test_old():",
        replacement_behavior="def test_new():",
        commit_sha="sha123"
    )
    
    class MockConnectionWrapper:
        def __init__(self, conn):
            self.conn = conn
        def __getattr__(self, name):
            return getattr(self.conn, name)
        def close(self):
            pass

    wrapper = MockConnectionWrapper(db_conn)
    # Mock get_db and cli handle_migration for "approve"
    with patch("agent_loop.cli.get_db", return_value=wrapper):
        args = MagicMock(migration_id=mig_id, action="approve")
        handle_migration(args, config)
        
    assert migration_repo.get(mig_id)["approval_status"] == "approved"
    assert run_repo.get(run_id)["status"] == "complete"
    
    # Test reject command
    run_id2 = run_repo.create("Test run 2", "autonomous")
    run_repo.update_status(run_id2, "planning")
    run_repo.update_status(run_id2, "running")
    run_repo.update_status(run_id2, "reviewing")
    run_repo.update_status(run_id2, "complete_pending_test_review")
    
    mig_id2 = migration_repo.create(
        run_id=run_id2,
        task_id=None,
        old_test_path="tests/test_old.py",
        replacement_test_path="tests/test_new.py",
        rationale="skip reason",
        evidence="evidence"
    )
    
    wrapper2 = MockConnectionWrapper(db_conn)
    with patch("agent_loop.cli.get_db", return_value=wrapper2):
        args = MagicMock(migration_id=mig_id2, action="reject")
        handle_migration(args, config)
        
    assert migration_repo.get(mig_id2)["approval_status"] == "rejected"
    assert run_repo.get(run_id2)["status"] == "blocked"

# FIX-07: Provide operational quota and lifecycle notifications
def test_rich_quota_notifications(db_conn, tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_WEBHOOK_URL", "https://hooks.slack.com/services/SECRET_WEBHOOK_TOKEN")
    monkeypatch.setenv("TEST_OAUTH_TOKEN", "SECRET_OAUTH_TOKEN")
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "webhook_env_var": "TEST_WEBHOOK_URL"
    })
    
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Test goal", "autonomous")
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    
    # Set up mock urllib request to capture payload
    mock_urlopen = MagicMock()
    with patch("urllib.request.urlopen", mock_urlopen):
        # Trigger auth_required alert notification
        orch.notify(run_id, "quota_alert:codex:gpt-5.5:auth_required", "OAuth token: SECRET_OAUTH_TOKEN is required. See webhook: https://hooks.slack.com/services/SECRET_WEBHOOK_TOKEN")
        
    assert mock_urlopen.called
    req_arg = mock_urlopen.call_args[0][0]
    payload = json.loads(req_arg.data.decode("utf-8"))
    text = payload["text"]
    
    # Verify secrets are redacted from notification payload
    assert "SECRET_WEBHOOK_TOKEN" not in text
    assert "SECRET_OAUTH_TOKEN" not in text
    assert "[REDACTED]" in text

# FIX-09: Make provider execution portable and timeout-aware
def test_portable_binaries(tmp_path):
    config = Config({
        "codex_path": "/custom/path/to/codex",
        "agy_path": "/custom/path/to/agy"
    })
    
    # 1. Custom config paths
    assert resolve_binary("codex", config) == "/custom/path/to/codex"
    assert resolve_binary("agy", config) == "/custom/path/to/agy"
    
    # 2. PATH resolution fallback
    with patch("shutil.which", return_value="/usr/bin/codex") as mock_which:
        assert resolve_binary("codex") == "/usr/bin/codex"
        mock_which.assert_called_with("codex")
        
    # 3. Missing binary raises FileNotFoundError
    with patch("shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError):
            resolve_binary("codex")
        with pytest.raises(FileNotFoundError):
            resolve_binary("agy")

def test_agy_print_timeout_construction(tmp_path):
    # Verify that agy adapter passes --print-timeout
    adapter = AgyAdapter(binary_path="/custom/agy")
    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value="{}"):
                adapter.run_attempt("my-model", "hello", tmp_path, tmp_path, timeout_seconds=123.0)
                
    cmd_args = mock_run.call_args[0][0]
    assert "--print-timeout" in cmd_args
    assert "123" in cmd_args


def test_review_actions_comprehensive(db_conn, tmp_path):
    from agent_loop.repositories import DecisionRepository
    config = Config()
    config.data["retry_policy"] = {"max_attempts": 2, "escalation_threshold": 2}
    config.data["routes"] = {
        "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
        "planning": [{"provider": "codex", "model": "gpt-5.4-mini"}]
    }
    
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    decision_repo = DecisionRepository(db_conn)

    run_id = run_repo.create("Comprehensive review action test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")

    # Mocks for git operations
    mock_create_wt = MagicMock()
    mock_commit = MagicMock(return_value="mock_sha_abc")
    mock_merge = MagicMock(return_value=(True, []))
    mock_remove_wt = MagicMock()

    with patch("agent_loop.orchestrator.create_worktree", mock_create_wt), \
         patch("agent_loop.orchestrator.commit_changes", mock_commit), \
         patch("agent_loop.orchestrator.merge_branch", mock_merge), \
         patch("agent_loop.orchestrator.remove_worktree", mock_remove_wt), \
         patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter

        # 1. Test "block" decision records stop condition
        task_id_block = task_repo.create(run_id, feat_id, "Task Block Test", "implementation", "low")
        task_repo.update_status(task_id_block, "ready")
        
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "block", "findings": "Violation of constraints"}', error="")
        ]
        
        success = orch._execute_task_impl(run_id, task_repo.get(task_id_block))
        assert success is True
        assert task_repo.get(task_id_block)["status"] == "blocked"
        
        # Verify stop condition is created in decisions table
        decisions = decision_repo.get_by_run(run_id)
        assert len(decisions) >= 1
        stop_dec = [d for d in decisions if d["decision_type"] == "stop_condition"]
        assert len(stop_dec) == 1
        assert "Violation of constraints" in stop_dec[0]["details"]

        # 2. Test "assessment" decision creates architectural assessment task
        task_id_assess = task_repo.create(run_id, feat_id, "Task Assess Test", "implementation", "low")
        task_repo.update_status(task_id_assess, "ready")
        
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "assessment", "findings": "Needs system check"}', error="")
        ]
        
        success = orch._execute_task_impl(run_id, task_repo.get(task_id_assess))
        assert success is True
        assert task_repo.get(task_id_assess)["status"] == "blocked"
        
        # Verify architectural assessment task is created
        tasks = task_repo.get_by_run(run_id)
        assess_task = [t for t in tasks if t["name"] == "Architectural Assessment: Task Assess Test"]
        assert len(assess_task) == 1
        assert assess_task[0]["role"] == "planning"
        assert assess_task[0]["risk"] == "high"

        # 3. Test "follow_up" decision creates pending dependent follow-up task
        task_id_followup = task_repo.create(run_id, feat_id, "Task Followup Test", "implementation", "low")
        task_repo.update_status(task_id_followup, "ready")
        
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "follow_up", "findings": "Needs docs"}', error="")
        ]
        
        success = orch._execute_task_impl(run_id, task_repo.get(task_id_followup))
        assert success is True
        assert task_repo.get(task_id_followup)["status"] == "complete"
        
        tasks = task_repo.get_by_run(run_id)
        followup_task = [t for t in tasks if t["name"] == "Follow-up: Task Followup Test"]
        assert len(followup_task) == 1
        assert followup_task[0]["status"] == "pending"
        assert followup_task[0]["dependencies"] == ["Task Followup Test"]

        # 4. Test "rejected" limit bounds
        task_id_reject = task_repo.create(run_id, feat_id, "Task Reject Test", "implementation", "low")
        task_repo.update_status(task_id_reject, "ready")
        
        # First rejection: should be set back to ready
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done 1", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "rejected", "findings": "Bad code 1"}', error="")
        ]
        success = orch._execute_task_impl(run_id, task_repo.get(task_id_reject))
        assert success is True
        assert task_repo.get(task_id_reject)["status"] == "ready"
        
        # Second rejection (hits retry limit): should be set to blocked
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done 2", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "rejected", "findings": "Bad code 2"}', error="")
        ]
        success = orch._execute_task_impl(run_id, task_repo.get(task_id_reject))
        assert success is True
        assert task_repo.get(task_id_reject)["status"] == "blocked"


def test_quota_gating_capabilities(db_conn, tmp_path):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "planning": [{"provider": "agy", "model": "Gemini 3.5 Flash"}],
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    })
    
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    p_repo = ProviderStateRepository(db_conn)
    
    run_id = run_repo.create("Simultaneous planning/implementation gating", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    
    # Ready tasks requiring planning and implementation
    t1 = task_repo.create(run_id, feat_id, "Task Plan", "planning", "high")
    t2 = task_repo.create(run_id, feat_id, "Task Exec", "implementation", "low")
    task_repo.update_status(t1, "ready")
    task_repo.update_status(t2, "ready")
    
    sleep_calls = []
    orch = Orchestrator(
        db_conn,
        config,
        plan_path=tmp_path / "plan.md",
        progress_path=tmp_path / "progress.md",
        sleep_func=lambda secs: sleep_calls.append(secs)
    )
    
    # Verify both capabilities are tracked in get_required_routes
    req = orch.get_required_routes(run_id)
    assert len(req) == 2
    caps = {r["capability"] for r in req}
    assert caps == {"planning", "implementation"}
    
    # Scenario 1: One capability is available, other is limited.
    # agy (planning) is available, codex (implementation) is limited.
    p_repo.save("agy", "Gemini 3.5 Flash", {}, False, "available")
    p_repo.save("codex", "gpt-5.4-mini", {}, False, "limited_known_reset")
    
    # We mock refresh_provider_quotas to make it available on refresh.
    def mock_refresh(provider, force_refresh=False):
        p_repo.save("codex", "gpt-5.4-mini", {}, False, "available")
        
    with patch.object(orch, "refresh_provider_quotas", side_effect=mock_refresh):
        res = orch.check_and_recover_quotas(run_id, req)
        assert res is True
        assert len(sleep_calls) == 1
        
    # Scenario 2: Auth required for planning capability, implementation is available.
    # Should stop/block immediately and return False.
    p_repo.save("agy", "Gemini 3.5 Flash", {}, False, "auth_required")
    p_repo.save("codex", "gpt-5.4-mini", {}, False, "available")
    
    res = orch.check_and_recover_quotas(run_id, req)
    assert res is False
    assert run_repo.get(run_id)["status"] == "blocked"


def test_migration_fail_closed_negatives(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Migration negative test", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    # 1. Negative Case: Skip reason has generic "migration" text (no MIG-xxx stable identifier)
    mock_show = MagicMock(returncode=0, stdout="abcd123\nM\ttests/test_foo.py\nA\ttests/test_bar.py\n")
    mock_diff_generic = MagicMock(returncode=0, stdout="+@pytest.mark.skip(reason='migration')\n")
    mock_full_diff = MagicMock(returncode=0, stdout="+def test_bar():\n+    # covers: MIG-101\n")

    def run_side_effect_generic(cmd, *args, **kwargs):
        if "show" in cmd: return mock_show
        if "--" in cmd: return mock_diff_generic
        return mock_full_diff

    with patch("subprocess.run", side_effect=run_side_effect_generic):
        orch.detect_and_record_test_migrations(run_id, task_id, "abcd123")

    migs = orch.test_migration_repo.get_by_run(run_id)
    assert len(migs) == 1
    assert migs[0]["approval_status"] == "rejected"
    assert "missing a valid stable migration identifier" in migs[0]["rationale"]

    # 2. Negative Case: Skip reason has valid MIG identifier, but replacement test is unrelated (no covering evidence)
    mock_diff_valid = MagicMock(returncode=0, stdout="+@pytest.mark.skip(reason='migration: MIG-102')\n")
    mock_full_unrelated = MagicMock(returncode=0, stdout="+def test_bar():\n+    # covers: MIG-999\n") # mismatch!

    def run_side_effect_unrelated(cmd, *args, **kwargs):
        if "show" in cmd: return mock_show
        if "--" in cmd: return mock_diff_valid
        return mock_full_unrelated

    with patch("subprocess.run", side_effect=run_side_effect_unrelated):
        orch.detect_and_record_test_migrations(run_id, task_id, "abcd124")

    migs = orch.test_migration_repo.get_by_run(run_id)
    assert len(migs) == 2
    assert migs[1]["approval_status"] == "rejected"
    assert "missing covering evidence" in migs[1]["rationale"]

    # 3. Negative Case: Detector failure (raises exception). Must block run and raise the exception.
    def run_side_effect_fail(cmd, *args, **kwargs):
        if "cat-file" in cmd:
            return MagicMock(returncode=0)
        raise subprocess.SubprocessError("Git error")

    with patch("subprocess.run", side_effect=run_side_effect_fail):
        with pytest.raises(subprocess.SubprocessError):
            orch.detect_and_record_test_migrations(run_id, task_id, "abcd125")

    # Verify run is now blocked due to detector failure
    assert run_repo.get(run_id)["status"] == "blocked"


def test_recovery_idempotency(db_conn, tmp_path, monkeypatch):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Idempotent recovery test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    # Create a running attempt
    wt_dir = tmp_path / "wt_run"
    wt_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    attempt_id = attempt_repo.create(
        run_id=run_id,
        task_id=task_id,
        route="implementation",
        provider="codex",
        model="gpt-5.4-mini",
        worktree_path=str(wt_dir),
        logs_path=str(logs_dir)
    )

    # 1. First recovery run: should clean up worktree and save patch
    dummy_diff = "diff --git a/file.py b/file.py\n+new line"
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = dummy_diff

    def run_side_effect(cmd, *args, **kwargs):
        if "diff" in cmd: return mock_run
        return MagicMock(returncode=0, stdout="")

    mock_remove_wt = MagicMock()
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.reconcile_interrupted_run(run_id)

    mock_remove_wt.assert_called_once()
    attempt = attempt_repo.get(attempt_id)
    assert attempt["outcome"] == "abandoned"
    assert attempt["patch_path"] is not None
    assert attempt["worktree_path"] is None

    # Clean up directory as remove_worktree mock doesn't do it
    if wt_dir.exists():
        wt_dir.rmdir()

    # 2. Second recovery run: should be idempotent and not fail, not call remove_worktree again, not change path or outcome
    mock_remove_wt.reset_mock()
    with patch("subprocess.run", side_effect=run_side_effect):
        orch.reconcile_interrupted_run(run_id)

    mock_remove_wt.assert_not_called()
    attempt = attempt_repo.get(attempt_id)
    assert attempt["outcome"] == "abandoned"
    assert attempt["worktree_path"] is None


def test_ui_lab_brief_workflow_paths(tmp_path, monkeypatch):
    import sys
    from agent_loop.cli import main
    monkeypatch.chdir(tmp_path)
    
    # 1. UI Lab intake mode selected for a UI goal: should succeed and run AgyAdapter
    user_inputs = [
        "2",
        "Fast and clean",
        "fast",
        "tool",
        "immediately",
        "slow",
        "Dark neon",
        "disliked_app",
        "y"
    ]
    input_gen = (val for val in user_inputs)
    
    mock_orch = MagicMock()
    mock_orch.plan_run.return_value = True
    
    with patch("builtins.input", side_effect=lambda *args, **kwargs: next(input_gen)), \
         patch("agent_loop.cli.Orchestrator", return_value=mock_orch), \
         patch("agent_loop.adapters.AgyAdapter") as mock_agy_adapter_cls:
        
        mock_adapter_instance = MagicMock()
        mock_agy_adapter_cls.return_value = mock_adapter_instance
        from agent_loop.adapters import AttemptResult
        mock_adapter_instance.run_attempt.return_value = AttemptResult(
            success=True,
            exit_code=0,
            output="UI Questions\n- Q1?\n- Q2?\n- Q3?\n- Q4?\n- Q5?\n- Q6?",
            error=""
        )
        
        test_args = ["agent-loop", "start", "--goal", "Create a web login page"]
        with patch.object(sys, "argv", test_args):
            main()
            
        mock_adapter_instance.run_attempt.assert_called_once()
        
    db_path = Path(".agent-loop.db")
    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    assert len(runs) == 1
    assert runs[0]["intake_mode"] == "brainstorm_ui_lab"
    assert "Fast and clean" in runs[0]["goal"]
    conn.close()
    
    if db_path.exists():
        db_path.unlink()
        
    # 2. UI Lab intake mode requested via flag (--intake ui_lab) for a non-UI goal: should fail/exit
    with pytest.raises(SystemExit) as excinfo:
        test_args = ["agent-loop", "start", "--goal", "Implement a prime number generator", "--intake", "ui_lab"]
        with patch.object(sys, "argv", test_args):
            main()
    assert excinfo.value.code == 1
    
    # 3. Brainstorm intake mode (Option 1) for a non-UI goal
    user_inputs = [
        "1",
        "Only needs standard lexer",
        "y"
    ]
    input_gen = (val for val in user_inputs)
    with patch("builtins.input", side_effect=lambda *args, **kwargs: next(input_gen)), \
         patch("agent_loop.cli.Orchestrator", return_value=mock_orch):
        test_args = ["agent-loop", "start", "--goal", "Build a compiler"]
        with patch.object(sys, "argv", test_args):
            main()
            
    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    assert len(runs) == 1
    assert runs[0]["intake_mode"] == "brainstorm"
    assert "Only needs standard lexer" in runs[0]["goal"]
    conn.close()
    
    if db_path.exists():
        db_path.unlink()
        
    # 4. Autonomous intake mode (Option 3) for a UI goal
    user_inputs = ["3"]
    input_gen = (val for val in user_inputs)
    with patch("builtins.input", side_effect=lambda *args, **kwargs: next(input_gen)), \
         patch("agent_loop.cli.Orchestrator", return_value=mock_orch):
        test_args = ["agent-loop", "start", "--goal", "Create a page"]
        with patch.object(sys, "argv", test_args):
            main()
            
    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    assert len(runs) == 1
    assert runs[0]["intake_mode"] == "autonomous"
    conn.close()
    
    if db_path.exists():
        db_path.unlink()

    # 5. Non-interactive policy path
    with patch("agent_loop.cli.Orchestrator", return_value=mock_orch):
        test_args = ["agent-loop", "start", "--goal", "Build a compiler", "--non-interactive"]
        with patch.object(sys, "argv", test_args):
            main()
            
    conn = get_connection(db_path)
    run_repo = RunRepository(conn)
    runs = run_repo.list_all()
    assert len(runs) == 1
    assert runs[0]["intake_mode"] == "non_interactive"
    conn.close()


def test_safe_preservation_failures(db_conn, tmp_path, monkeypatch):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Safe preservation test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    # Create a running attempt with a dirty worktree directory
    wt_dir = tmp_path / "wt_run"
    wt_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    attempt_id = attempt_repo.create(
        run_id=run_id,
        task_id=task_id,
        route="implementation",
        provider="codex",
        model="gpt-5.4-mini",
        worktree_path=str(wt_dir),
        logs_path=str(logs_dir)
    )

    # Mock git status to fail (which means we cannot prove it is clean and preservation fails)
    def run_side_effect(cmd, *args, **kwargs):
        raise subprocess.SubprocessError("Git error")

    mock_remove_wt = MagicMock()
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.reconcile_interrupted_run(run_id)

    # Verify that remove_worktree was NOT called
    mock_remove_wt.assert_not_called()

    # Verify that the attempt is NOT marked abandoned in the database
    attempt = attempt_repo.get(attempt_id)
    assert attempt["outcome"] == "running"
    assert attempt["worktree_path"] == str(wt_dir)


def test_architectural_assessment_resolution(db_conn, tmp_path, monkeypatch):
    config = Config()
    config.data["retry_policy"] = {"max_attempts": 2, "escalation_threshold": 2}
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Assessment resolution test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")

    # Mocks for git operations
    mock_create_wt = MagicMock()
    mock_commit = MagicMock(return_value="mock_sha_abc")
    mock_merge = MagicMock(return_value=(True, []))
    mock_remove_wt = MagicMock()

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", mock_create_wt)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", mock_commit)
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", mock_merge)
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)
    monkeypatch.setattr(orch, "run_verification", lambda *args, **kwargs: True)

    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter

        # 1. Start with a task
        task_id = task_repo.create(
            run_id=run_id,
            feature_id=feat_id,
            name="Task Orig",
            role="implementation",
            risk="low",
            scope={"files": ["src/main.py"]},
            required_verification="pytest tests/test_main.py"
        )
        task_repo.update_status(task_id, "ready")
        task = task_repo.get(task_id)

        # First attempt of original task returns success but reviewer returns "assessment"
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "assessment", "findings": "Change interface design"}', error="")
        ]

        success = orch._execute_task_impl(run_id, task)
        assert success is True

        # Check original task is now blocked
        assert task_repo.get(task_id)["status"] == "blocked"

        # Check that Architectural Assessment task is created and has detailed scope
        tasks = task_repo.get_by_run(run_id)
        assess_task = [t for t in tasks if t["name"] == "Architectural Assessment: Task Orig"]
        assert len(assess_task) == 1
        
        # Verify role and risk
        assert assess_task[0]["role"] == "planning"
        assert assess_task[0]["risk"] == "high"
        assert assess_task[0]["status"] == "pending" # Newly created tasks are pending until loop schedules
        assert assess_task[0]["required_verification"] == "pytest tests/test_main.py"
        
        scope_data = json.loads(assess_task[0]["scope"]) if isinstance(assess_task[0]["scope"], str) else assess_task[0]["scope"]
        assert scope_data["original_task_id"] == task_id
        assert scope_data["original_task_name"] == "Task Orig"
        assert scope_data["reviewer_findings"] == "Change interface design"
        assert scope_data["files"] == ["src/main.py"]

        # Set the assessment task to ready
        task_repo.update_status(assess_task[0]["id"], "ready")

        # Execute the assessment task: should succeed and reviewer approves it
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="assessment done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "Approved assessment"}', error="")
        ]

        success2 = orch._execute_task_impl(run_id, task_repo.get(assess_task[0]["id"]))
        assert success2 is True

        # Check assessment task is now complete
        assert task_repo.get(assess_task[0]["id"])["status"] == "complete"

        # Check that the original blocked task status has transitioned back to "ready"!
        assert task_repo.get(task_id)["status"] == "ready"


