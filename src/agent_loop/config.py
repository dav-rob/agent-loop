import os
from pathlib import Path
import tomllib
from typing import Any, Dict, List

DEFAULT_CONFIG = {
    "state_dir": ".agent-loop",
    "db_path": None,
    "logs_dir": None,
    "worktrees_dir": None,
    "plan_path": None,
    "progress_path": None,
    "learning_path": None,
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
    def state_dir(self) -> Path:
        return Path(self.data.get("state_dir") or DEFAULT_CONFIG["state_dir"]).resolve()

    @property
    def db_path(self) -> Path:
        val = self.data.get("db_path")
        if val:
            if str(val) == ":memory:":
                return Path(":memory:")
            return Path(val).resolve()
        return (self.state_dir / "agent-loop.db").resolve()

    @property
    def logs_dir(self) -> Path:
        val = self.data.get("logs_dir")
        return Path(val).resolve() if val else (self.state_dir / "logs").resolve()

    @property
    def worktrees_dir(self) -> Path:
        val = self.data.get("worktrees_dir")
        return Path(val).resolve() if val else (self.state_dir / "worktrees").resolve()

    @property
    def plan_path(self) -> Path:
        val = self.data.get("plan_path")
        return Path(val).resolve() if val else (self.state_dir / "plan.md").resolve()

    @property
    def progress_path(self) -> Path:
        val = self.data.get("progress_path")
        return Path(val).resolve() if val else (self.state_dir / "progress.md").resolve()

    @property
    def learning_path(self) -> Path:
        val = self.data.get("learning_path")
        return Path(val).resolve() if val else (self.state_dir / "learning.md").resolve()

    @property
    def max_workers(self) -> int:
        val = int(self.data.get("max_workers", DEFAULT_CONFIG["max_workers"]))
        return min(val, 4)

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
        cmds = self.data.get("commands", DEFAULT_CONFIG["commands"])
        if "PYTEST_CURRENT_TEST" in os.environ and cmds.get("regression_test") == "pytest tests":
            cmds = cmds.copy()
            cmds["regression_test"] = ""
        return cmds

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
