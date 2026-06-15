import json
import sqlite3
import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import ProviderStateRepository, RunRepository
from agent_loop.orchestrator import Orchestrator

@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()

def test_antigravity_usage_available(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "agy", "model": "Gemini 3.5 Flash (High)"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    quota_json = {
        "timestamp": "2026-06-15T15:02:24.687Z",
        "method": "google",
        "email": "user@example.com",
        "models": [
            {
                "label": "Gemini 3.5 Flash (High)",
                "modelId": "gemini-3-flash-agent",
                "remainingPercentage": 0.6106822,
                "isExhausted": False,
                "resetTime": "2026-06-15T17:56:15Z",
                "timeUntilResetMs": 10430325,
                "isAutocompleteOnly": False
            }
        ]
    }

    mock_run_ver = MagicMock(returncode=0, stdout="antigravity-usage 0.2.9\n")
    mock_run_quota = MagicMock(returncode=0, stdout=json.dumps(quota_json))

    def run_side_effect(cmd, *args, **kwargs):
        if "--version" in cmd:
            return mock_run_ver
        return mock_run_quota

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.refresh_provider_quotas("agy")

    p_state = orch.provider_repo.get("agy", "Gemini 3.5 Flash (High)")
    assert p_state is not None
    assert p_state["quota_state"] == "available"
    assert p_state["availability"] is True

def test_antigravity_usage_exhausted(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "agy", "model": "Gemini 3.5 Flash (High)"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    quota_json = {
        "timestamp": "2026-06-15T15:02:24.687Z",
        "method": "google",
        "email": "user@example.com",
        "models": [
            {
                "label": "Gemini 3.5 Flash (High)",
                "modelId": "gemini-3-flash-agent",
                "remainingPercentage": 0.0,
                "isExhausted": True,
                "resetTime": "2026-06-15T17:56:15Z",
                "timeUntilResetMs": 10430325,
                "isAutocompleteOnly": False
            }
        ]
    }

    mock_run_ver = MagicMock(returncode=0, stdout="antigravity-usage 0.2.9\n")
    mock_run_quota = MagicMock(returncode=0, stdout=json.dumps(quota_json))

    def run_side_effect(cmd, *args, **kwargs):
        if "--version" in cmd:
            return mock_run_ver
        return mock_run_quota

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.refresh_provider_quotas("agy")

    p_state = orch.provider_repo.get("agy", "Gemini 3.5 Flash (High)")
    assert p_state is not None
    assert p_state["quota_state"] == "limited_known_reset"
    assert p_state["quota_limit_reset"] == "2026-06-15T17:56:15Z"
    assert p_state["availability"] is False

def test_antigravity_usage_duplicate_labels(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "agy", "model": "Gemini 3.5 Flash (High)"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    # Duplicate labels: one is exhausted, one is not. Treated as not exhausted overall.
    quota_json = {
        "models": [
            {
                "label": "Gemini 3.5 Flash (High)",
                "modelId": "gemini-3-flash-agent-1",
                "isExhausted": True,
                "isAutocompleteOnly": False
            },
            {
                "label": "Gemini 3.5 Flash (High)",
                "modelId": "gemini-3-flash-agent-2",
                "isExhausted": False,
                "isAutocompleteOnly": False
            }
        ]
    }

    mock_run_ver = MagicMock(returncode=0, stdout="antigravity-usage 0.2.9\n")
    mock_run_quota = MagicMock(returncode=0, stdout=json.dumps(quota_json))

    def run_side_effect(cmd, *args, **kwargs):
        if "--version" in cmd:
            return mock_run_ver
        return mock_run_quota

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.refresh_provider_quotas("agy")

    p_state = orch.provider_repo.get("agy", "Gemini 3.5 Flash (High)")
    assert p_state is not None
    assert p_state["quota_state"] == "available"
    assert p_state["availability"] is True

def test_antigravity_usage_auth_required(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "agy", "model": "Gemini 3.5 Flash (High)"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    # Command fails with exit code 1 and auth required output
    mock_run_ver = MagicMock(returncode=0, stdout="antigravity-usage 0.2.9\n")
    mock_run_quota = MagicMock(returncode=1, stdout="Error: Authentication required. Please run antigravity-usage login first.\n", stderr="")

    def run_side_effect(cmd, *args, **kwargs):
        if "--version" in cmd:
            return mock_run_ver
        return mock_run_quota

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.refresh_provider_quotas("agy")

    p_state = orch.provider_repo.get("agy", "Gemini 3.5 Flash (High)")
    assert p_state is not None
    assert p_state["quota_state"] == "auth_required"
    assert p_state["availability"] is False

def test_antigravity_usage_malformed_json(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "agy", "model": "Gemini 3.5 Flash (High)"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    mock_run_ver = MagicMock(returncode=0, stdout="antigravity-usage 0.2.9\n")
    mock_run_quota = MagicMock(returncode=0, stdout="This is not valid JSON", stderr="")

    def run_side_effect(cmd, *args, **kwargs):
        if "--version" in cmd:
            return mock_run_ver
        return mock_run_quota

    with patch("subprocess.run", side_effect=run_side_effect):
        orch.refresh_provider_quotas("agy")

    # Should fall back conservatively and not crash
    p_state = orch.provider_repo.get("agy", "Gemini 3.5 Flash (High)")
    assert p_state is None

def test_antigravity_usage_absent_binary(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "agy", "model": "Gemini 3.5 Flash (High)"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    with patch("subprocess.run", side_effect=FileNotFoundError):
        orch.refresh_provider_quotas("agy")

    # Should fall back conservatively and not crash
    p_state = orch.provider_repo.get("agy", "Gemini 3.5 Flash (High)")
    assert p_state is None

def test_codex_rpc_rate_limits_read_success(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    # Setup mock process for codex app-server RPC
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    
    # Responses we expect to read
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 2, "result": {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 50, "resetsAt": 1781538912},
                "secondary": {"usedPercent": 10, "resetsAt": 1782125712},
                "rateLimitReachedType": None
            }
        }}
    ]
    
    def readline_side_effect():
        if responses:
            return json.dumps(responses.pop(0)) + "\n"
        return ""

    mock_proc.stdout.readline.side_effect = readline_side_effect

    with patch("subprocess.Popen", return_value=mock_proc), patch("select.select", return_value=([mock_proc.stdout], [], [])):
        orch.refresh_provider_quotas("codex")

    p_state = orch.provider_repo.get("codex", "gpt-5.4-mini")
    assert p_state is not None
    assert p_state["quota_state"] == "available"
    assert p_state["availability"] is True

def test_codex_rpc_rate_limits_read_exhausted(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    
    # primary is at 100%
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 2, "result": {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 100, "resetsAt": 1781538912},
                "secondary": {"usedPercent": 10, "resetsAt": 1782125712},
                "rateLimitReachedType": "primary"
            }
        }}
    ]
    
    def readline_side_effect():
        if responses:
            return json.dumps(responses.pop(0)) + "\n"
        return ""

    mock_proc.stdout.readline.side_effect = readline_side_effect

    with patch("subprocess.Popen", return_value=mock_proc), patch("select.select", return_value=([mock_proc.stdout], [], [])):
        orch.refresh_provider_quotas("codex")

    p_state = orch.provider_repo.get("codex", "gpt-5.4-mini")
    assert p_state is not None
    assert p_state["quota_state"] == "limited_known_reset"
    assert p_state["availability"] is False
    assert "2026-06-15" in p_state["quota_limit_reset"]

def test_fake_clock_known_reset_waiting(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    }
    config = Config(config_data)
    
    # Mark route as limited_known_reset in DB
    p_repo = ProviderStateRepository(db_conn)
    p_repo.save(
        provider="codex",
        model="gpt-5.4-mini",
        capability_snapshot={},
        availability=False,
        quota_state="limited_known_reset",
        quota_limit_reset="2026-06-15T16:30:00Z"
    )

    fake_now = datetime.datetime.fromisoformat("2026-06-15T16:00:00+00:00")
    
    # Setup Orchestrator with fake clock and mock sleeper
    sleep_calls = []
    def fake_sleep(secs):
        sleep_calls.append(secs)
        
    orch = Orchestrator(
        db_conn, 
        config, 
        plan_path=tmp_path / "plan.md", 
        progress_path=tmp_path / "progress.md",
        get_now=lambda: fake_now,
        sleep_func=fake_sleep
    )

    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Quota sleep test", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")

    # Mock refresh to make it available again
    def mock_refresh(provider, force_refresh=False):
        p_repo.save(provider, "gpt-5.4-mini", {}, True, "available")

    with patch.object(orch, "refresh_provider_quotas", side_effect=mock_refresh):
        orch.check_and_recover_quotas(run_id, config.routes["implementation"])

    # Should sleep for 30 minutes (1800 seconds)
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 1800.0
    
    # Should transition to running status after recovery
    assert run_repo.get(run_id)["status"] == "running"

def test_unknown_reset_exponential_backoff(db_conn, tmp_path):
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    }
    config = Config(config_data)
    
    # Mark route as limited_unknown_reset in DB with probe_count = 0
    p_repo = ProviderStateRepository(db_conn)
    p_repo.save(
        provider="codex",
        model="gpt-5.4-mini",
        capability_snapshot={"probe_count": 0},
        availability=False,
        quota_state="limited_unknown_reset"
    )

    fake_now = datetime.datetime.fromisoformat("2026-06-15T16:00:00+00:00")
    
    sleep_calls = []
    def fake_sleep(secs):
        sleep_calls.append(secs)
        
    orch = Orchestrator(
        db_conn, 
        config, 
        plan_path=tmp_path / "plan.md", 
        progress_path=tmp_path / "progress.md",
        get_now=lambda: fake_now,
        sleep_func=fake_sleep
    )

    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Quota sleep test", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")

    call_count = 0
    def mock_refresh(provider, force_refresh=False):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            p_repo.save(provider, "gpt-5.4-mini", {"probe_count": 1}, True, "available", last_probe="2026-06-15T16:00:00Z")

    with patch.object(orch, "refresh_provider_quotas", side_effect=mock_refresh):
        orch.check_and_recover_quotas(run_id, config.routes["implementation"])

    # Since last_probe is None and next probe is due now, it probes first.
    # Because probe still shows exhausted/limited, it updates probe_count to 1 and last_probe to now.
    # Then it enters wait/sleep state: next probe is due in 15 mins (900 seconds) since probe_count was updated to 1.
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 900.0

    p_state = p_repo.get("codex", "gpt-5.4-mini")
    assert p_state["capability_snapshot"]["probe_count"] == 1
    assert p_state["last_probe"] == "2026-06-15T16:00:00Z"
