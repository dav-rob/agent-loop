import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import subprocess
from agent_loop.adapters import CodexAdapter, AgyAdapter, redact_secrets, get_adapter

def test_secret_redaction():
    # Setup sensitive environment variables
    with patch.dict(os.environ, {
        "MY_API_KEY": "supersecret-12345",
        "OTHER_VAR": "public-content",
        "WEBHOOK_URL": "http://hooks.slack.com/services/abc/xyz"
    }):
        text = "Running command with key supersecret-12345 and url http://hooks.slack.com/services/abc/xyz"
        redacted = redact_secrets(text)
        assert "supersecret-12345" not in redacted
        assert "http://hooks.slack.com/services/abc/xyz" not in redacted
        assert "[REDACTED]" in redacted
        assert "public-content" not in redacted

def test_codex_discover_capabilities():
    adapter = CodexAdapter()
    
    # Successful version check
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = "codex-cli 0.139.0\n"
    
    with patch("subprocess.run", return_value=mock_run) as mock_subprocess:
        caps = adapter.discover_capabilities()
        assert caps["installed"] is True
        assert "0.139.0" in caps["version"]
        assert "gpt-5.5" in caps["models"]
        mock_subprocess.assert_called_once()

def test_agy_discover_capabilities():
    adapter = AgyAdapter()
    
    # Successful version and models check
    mock_run_ver = MagicMock()
    mock_run_ver.returncode = 0
    mock_run_ver.stdout = "1.0.8\n"
    
    mock_run_mod = MagicMock()
    mock_run_mod.returncode = 0
    mock_run_mod.stdout = "Gemini 3.5 Flash (High)\nClaude Opus 4.6 (Thinking)\n"
    
    def side_effect(cmd, *args, **kwargs):
        if "models" in cmd:
            return mock_run_mod
        return mock_run_ver
        
    with patch("subprocess.run", side_effect=side_effect) as mock_subprocess:
        caps = adapter.discover_capabilities()
        assert caps["installed"] is True
        assert caps["version"] == "1.0.8"
        assert "Gemini 3.5 Flash (High)" in caps["models"]
        assert "Claude Opus 4.6 (Thinking)" in caps["models"]
        assert mock_subprocess.call_count == 2

def test_codex_run_attempt_success(tmp_path):
    adapter = CodexAdapter()
    logs_dir = tmp_path / "logs"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    
    # Simulate stdout that would be written by subprocess to out_f
    def run_side_effect(cmd, stdin, stdout, stderr, timeout, **kwargs):
        # stdout is a file object
        stdout.write(
            '{"event": "token_usage", "input": 100, "output": 200}\n'
            '{"event": "message", "role": "assistant", "content": "Hello world"}\n'
        )
        return mock_proc

    with patch("subprocess.run", side_effect=run_side_effect) as mock_subprocess:
        res = adapter.run_attempt(
            model="gpt-5.4-mini",
            prompt="Say hello",
            workspace_path=workspace,
            attempt_logs_dir=logs_dir,
            timeout_seconds=30
        )
        assert res.success is True
        assert res.exit_code == 0
        assert res.output == "Hello world"
        assert res.token_usage == {"input": 100, "output": 200}
        assert res.quota_exhausted is False
        
        # Verify call args contains absolute workspace path for --cd
        cmd_args = mock_subprocess.call_args[0][0]
        cd_idx = cmd_args.index("--cd")
        assert Path(cmd_args[cd_idx + 1]).is_absolute()
        assert Path(cmd_args[cd_idx + 1]) == workspace.resolve()
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd_args
        assert "-a" not in cmd_args
        assert "-s" not in cmd_args

def test_codex_run_attempt_quota_exhausted(tmp_path):
    adapter = CodexAdapter()
    logs_dir = tmp_path / "logs"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    
    def run_side_effect(cmd, stdin, stdout, stderr, timeout, **kwargs):
        stdout.write(
            '{"event": "error", "error": {"code": "insufficient_quota", "reset": "2026-06-15T15:00:00Z"}}\n'
        )
        return mock_proc

    with patch("subprocess.run", side_effect=run_side_effect):
        res = adapter.run_attempt(
            model="gpt-5.4-mini",
            prompt="Say hello",
            workspace_path=workspace,
            attempt_logs_dir=logs_dir
        )
        assert res.success is False
        assert res.quota_exhausted is True
        assert res.quota_reset == "2026-06-15T15:00:00Z"

def test_agy_run_attempt_success(tmp_path):
    adapter = AgyAdapter()
    logs_dir = tmp_path / "logs"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    
    def run_side_effect(cmd, stdin, stdout, stderr, timeout, **kwargs):
        stdout.write("Hello from agy\n")
        return mock_proc

    with patch("subprocess.run", side_effect=run_side_effect) as mock_subprocess:
        res = adapter.run_attempt(
            model="Gemini 3.5 Flash (High)",
            prompt="Say hello",
            workspace_path=workspace,
            attempt_logs_dir=logs_dir
        )
        assert res.success is True
        assert res.output.strip() == "Hello from agy"
        assert res.quota_exhausted is False
        
        # Verify call args contains absolute workspace path for --add-dir
        cmd_args = mock_subprocess.call_args[0][0]
        assert "--model" in cmd_args
        assert "Gemini 3.5 Flash (High)" in cmd_args
        assert "--add-dir" in cmd_args
        add_dir_idx = cmd_args.index("--add-dir")
        assert Path(cmd_args[add_dir_idx + 1]).is_absolute()
        assert Path(cmd_args[add_dir_idx + 1]) == workspace.resolve()
        assert "--dangerously-skip-permissions" in cmd_args

def test_agy_run_attempt_quota_exhausted(tmp_path):
    adapter = AgyAdapter()
    logs_dir = tmp_path / "logs"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    
    def run_side_effect(cmd, stdin, stdout, stderr, timeout, **kwargs):
        stdout.write("Error: Rate limit exceeded. Please try again. Reset at 2026-06-15T16:00:00Z\n")
        return mock_proc

    with patch("subprocess.run", side_effect=run_side_effect):
        res = adapter.run_attempt(
            model="Gemini 3.5 Flash (High)",
            prompt="Say hello",
            workspace_path=workspace,
            attempt_logs_dir=logs_dir
        )
        assert res.success is False
        assert res.quota_exhausted is True
        assert res.quota_reset == "2026-06-15T16:00:00Z"


def test_codex_run_attempt_reasoning(tmp_path):
    adapter = CodexAdapter()
    logs_dir = tmp_path / "logs"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    mock_proc = MagicMock()
    mock_proc.returncode = 0

    def run_side_effect(cmd, stdin, stdout, stderr, timeout, **kwargs):
        stdout.write('{"event": "message", "role": "assistant", "content": "Succeeded"}\n')
        return mock_proc

    with patch("subprocess.run", side_effect=run_side_effect) as mock_subprocess:
        res = adapter.run_attempt(
            model="gpt-5.4-mini",
            prompt="Test reasoning",
            workspace_path=workspace,
            attempt_logs_dir=logs_dir,
            reasoning_level="high"
        )
        assert res.success is True
        cmd_args = mock_subprocess.call_args[0][0]
        assert "-c" in cmd_args
        c_idx = cmd_args.index("-c")
        assert cmd_args[c_idx + 1] == "model_reasoning_effort=high"


def test_codex_adapter_real_smoke_test(tmp_path):
    codex_bin = "/usr/local/bin/codex"
    if not Path(codex_bin).exists():
        pytest.skip("Real Codex CLI not found")
        
    adapter = CodexAdapter(binary_path=codex_bin)
    logs_dir = tmp_path / "logs"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    # Run a harmless short prompt
    res = adapter.run_attempt(
        model="gpt-5.4-mini",
        prompt="Reply with exactly 'OK'",
        workspace_path=workspace,
        attempt_logs_dir=logs_dir
    )
    
    assert res.exit_code != 2, f"Codex CLI rejected arguments: {res.error}"
