import os
from pathlib import Path
import tomllib
from typing import Any, Dict, List

DEFAULT_CONFIG = {
    "db_path": ".agent-loop.db",
    "logs_dir": "logs",
    "max_workers": 4,
    "webhook_env_var": "AGENT_LOOP_WEBHOOK_URL",
    "execution_mode": "trusted-host",
    "routes": {
        "planning": [
            {"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
            {"provider": "agy", "model": "Claude Opus 4.6 (Thinking)", "reasoning_level": "high"},
            {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
        ],
        "implementation": [
            {"provider": "agy", "model": "Gemini 3.5 Flash (High)", "reasoning_level": "high"},
            {"provider": "codex", "model": "gpt-5.4-mini", "reasoning_level": "high"},
        ]
    },
    "retry_policy": {
        "max_attempts": 3,
        "escalation_threshold": 2
    },
    "commands": {
        "narrow_test": "pytest {test_path}",
        "regression_test": "pytest tests"
    }
}

class Config:
    def __init__(self, data: Dict[str, Any] = None):
        self.data = data or DEFAULT_CONFIG.copy()

    @property
    def db_path(self) -> Path:
        return Path(self.data.get("db_path", DEFAULT_CONFIG["db_path"]))

    @property
    def logs_dir(self) -> Path:
        return Path(self.data.get("logs_dir", DEFAULT_CONFIG["logs_dir"]))

    @property
    def max_workers(self) -> int:
        return int(self.data.get("max_workers", DEFAULT_CONFIG["max_workers"]))

    @property
    def webhook_env_var(self) -> str:
        return str(self.data.get("webhook_env_var", DEFAULT_CONFIG["webhook_env_var"]))

    @property
    def routes(self) -> Dict[str, List[Dict[str, Any]]]:
        return self.data.get("routes", DEFAULT_CONFIG["routes"])

    @property
    def retry_policy(self) -> Dict[str, Any]:
        return self.data.get("retry_policy", DEFAULT_CONFIG["retry_policy"])

    @property
    def execution_mode(self) -> str:
        return str(self.data.get("execution_mode", DEFAULT_CONFIG["execution_mode"]))

    @property
    def commands(self) -> Dict[str, str]:
        return self.data.get("commands", DEFAULT_CONFIG["commands"])

    @classmethod
    def load(cls, path: Path = None) -> "Config":
        if path is None:
            path = Path("agent-loop.toml")
        if not path.exists():
            return cls(DEFAULT_CONFIG.copy())
        try:
            with path.open("rb") as f:
                loaded = tomllib.load(f)
            # Merge with defaults
            merged = DEFAULT_CONFIG.copy()
            for k, v in loaded.items():
                if isinstance(v, dict) and k in merged:
                    merged[k] = {**merged[k], **v}
                else:
                    merged[k] = v
            return cls(merged)
        except Exception:
            return cls(DEFAULT_CONFIG.copy())
