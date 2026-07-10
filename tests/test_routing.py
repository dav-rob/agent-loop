from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_loop.adapters import AttemptResult
from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import ProviderStateRepository
from agent_loop.routing import ModelRouter


def test_router_runs_next_route_when_first_route_requires_auth(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    provider_repo = ProviderStateRepository(conn)
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "executor": [
                {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
                {"provider": "codex", "model": "gpt-5.4-mini", "reasoning_level": "high"},
            ],
        },
    })

    auth_failure = AttemptResult(
        success=False,
        exit_code=1,
        output="",
        error="login required",
        auth_required=True,
    )
    success = AttemptResult(success=True, exit_code=0, output="done", error="")

    agy_adapter = MagicMock()
    agy_adapter.run_attempt.return_value = auth_failure
    codex_adapter = MagicMock()
    codex_adapter.run_attempt.return_value = success

    with patch("agent_loop.routing.get_adapter", side_effect=[agy_adapter, codex_adapter]):
        router = ModelRouter(config=config, provider_repo=provider_repo)
        result = router.run(
            profile="executor",
            prompt="Do work",
            workspace_path=tmp_path,
            logs_root=tmp_path / "logs" / "attempt",
        )

    assert result.success is True
    assert result.provider == "codex"
    assert result.model == "gpt-5.4-mini"
    assert provider_repo.get("agy", "Gemini 3.1 Pro (High)")["quota_state"] == "auth_required"
    assert agy_adapter.run_attempt.call_count == 1
    assert codex_adapter.run_attempt.call_count == 1


def test_router_skips_known_unavailable_routes(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    provider_repo = ProviderStateRepository(conn)
    provider_repo.save(
        provider="agy",
        model="Gemini 3.1 Pro (High)",
        capability_snapshot={},
        availability=False,
        quota_state="unavailable",
    )
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "executor": [
                {"provider": "agy", "model": "Gemini 3.1 Pro (High)"},
                {"provider": "codex", "model": "gpt-5.4-mini"},
            ],
        },
    })

    success = AttemptResult(success=True, exit_code=0, output="done", error="")
    codex_adapter = MagicMock()
    codex_adapter.run_attempt.return_value = success

    with patch("agent_loop.routing.get_adapter", return_value=codex_adapter) as mock_get_adapter:
        router = ModelRouter(config=config, provider_repo=provider_repo)
        result = router.run(
            profile="executor",
            prompt="Do work",
            workspace_path=tmp_path,
            logs_root=tmp_path / "logs" / "attempt",
        )

    assert result.success is True
    assert result.provider == "codex"
    mock_get_adapter.assert_called_once_with("codex", config)


def test_router_escalated_executor_uses_strong_profile_first(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    provider_repo = ProviderStateRepository(conn)
    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})

    success = AttemptResult(success=True, exit_code=0, output="done", error="")
    adapter = MagicMock()
    adapter.run_attempt.return_value = success

    with patch("agent_loop.routing.get_adapter", return_value=adapter):
        router = ModelRouter(config=config, provider_repo=provider_repo)
        result = router.run(
            profile="executor_escalated",
            prompt="Do hard work",
            workspace_path=tmp_path,
            logs_root=tmp_path / "logs" / "attempt",
        )

    assert result.success is True
    assert result.provider == "codex"
    assert result.model == "gpt-5.6-sol"
    assert result.reasoning_level == "xhigh"


def test_router_does_not_fail_over_after_execution_timeout(tmp_path):
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    provider_repo = ProviderStateRepository(conn)
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "executor_escalated": [
                {"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
                {"provider": "agy", "model": "Claude Opus 4.6 (Thinking)", "reasoning_level": "high"},
            ],
        },
    })

    timeout = AttemptResult(
        success=False,
        exit_code=-1,
        output="",
        error="Timeout expired after 600 seconds.",
        timed_out=True,
    )
    codex_adapter = MagicMock()
    codex_adapter.run_attempt.return_value = timeout
    agy_adapter = MagicMock()

    with patch("agent_loop.routing.get_adapter", side_effect=[codex_adapter, agy_adapter]):
        router = ModelRouter(config=config, provider_repo=provider_repo)
        result = router.run(
            profile="executor_escalated",
            prompt="Do hard work",
            workspace_path=tmp_path,
            logs_root=tmp_path / "logs" / "attempt",
        )

    assert result.success is False
    assert result.provider == "codex"
    assert result.model == "gpt-5.5"
    assert codex_adapter.run_attempt.call_count == 1
    assert agy_adapter.run_attempt.call_count == 0
    assert provider_repo.get("codex", "gpt-5.5") is None
