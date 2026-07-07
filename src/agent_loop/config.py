import os
import copy
from pathlib import Path
import tomllib
from typing import Any, Dict, List

PLANNING_ROUTES = [
    {"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
    {"provider": "agy", "model": "Claude Opus 4.6 (Thinking)", "reasoning_level": "high"},
    {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
]

EXECUTOR_ROUTES = [
    {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
    {"provider": "agy", "model": "Claude Sonnet 4.6 (Thinking)", "reasoning_level": "high"},
    {"provider": "codex", "model": "gpt-5.4-mini", "reasoning_level": "high"},
]

STRONG_ROUTES = [
    {"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
    {"provider": "agy", "model": "Claude Opus 4.6 (Thinking)", "reasoning_level": "high"},
    {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
]

ROUTE_PROFILE_ALIASES = {
    "planning": "planner",
    "implementation": "executor",
}

LEGACY_PROFILE_FALLBACKS = {
    "intake": "planning",
    "spec_reviewer": "planning",
    "planner": "planning",
    "reviewer": "planning",
    "escalation_reviewer": "planning",
    "executor": "implementation",
    "executor_escalated": "planning",
}

DEFAULT_CONFIG = {
    "state_dir": ".agent-loop",
    "db_path": None,
    "logs_dir": None,
    "worktrees_dir": "worktrees",
    "plan_path": None,
    "progress_path": None,
    "learning_path": None,
    "max_workers": 4,
    "webhook_env_var": "AGENT_LOOP_WEBHOOK_URL",
    "execution_mode": "trusted-host",
    "routes": {
        "intake": EXECUTOR_ROUTES,
        "spec_reviewer": STRONG_ROUTES,
        "planner": STRONG_ROUTES,
        "executor": EXECUTOR_ROUTES,
        "executor_escalated": STRONG_ROUTES,
        "reviewer": STRONG_ROUTES,
        "escalation_reviewer": STRONG_ROUTES,
        # Backwards-compatible aliases for existing configs and call sites.
        "planning": PLANNING_ROUTES,
        "implementation": EXECUTOR_ROUTES,
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

DEFAULT_TOML_PROFILE_ORDER = [
    "intake",
    "spec_reviewer",
    "planner",
    "executor",
    "executor_escalated",
    "reviewer",
    "escalation_reviewer",
]

def _deepcopy_default() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged

def _toml_value(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

def _route_toml(route: Dict[str, Any]) -> str:
    parts = [f"{key} = {_toml_value(value)}" for key, value in route.items()]
    return "{ " + ", ".join(parts) + " }"

class Config:
    def __init__(self, data: Dict[str, Any] = None):
        self._explicit_routes = copy.deepcopy((data or {}).get("routes", {}))
        self.data = _deep_merge(DEFAULT_CONFIG, data or {})

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

    def routes_for(self, profile: str) -> List[Dict[str, Any]]:
        routes = self.routes
        normalized = ROUTE_PROFILE_ALIASES.get(profile, profile)
        legacy_key = LEGACY_PROFILE_FALLBACKS.get(normalized)

        if normalized not in self._explicit_routes and legacy_key in self._explicit_routes:
            return copy.deepcopy(self._explicit_routes[legacy_key])

        if normalized in routes:
            return copy.deepcopy(routes[normalized])

        if legacy_key and legacy_key in routes:
            return copy.deepcopy(routes[legacy_key])

        default_routes = DEFAULT_CONFIG["routes"].get(normalized, [])
        return copy.deepcopy(default_routes)

    def all_model_routes(self) -> List[Dict[str, Any]]:
        profile_order = list(DEFAULT_TOML_PROFILE_ORDER) + ["planning", "implementation"]
        profile_order.extend(key for key in self.routes.keys() if key not in profile_order)
        seen = set()
        all_routes: List[Dict[str, Any]] = []
        for profile in profile_order:
            for route in self.routes_for(profile):
                key = (
                    route.get("provider"),
                    route.get("model"),
                    route.get("reasoning_level"),
                )
                if key in seen:
                    continue
                seen.add(key)
                all_routes.append(copy.deepcopy(route))
        return all_routes

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
            return cls(_deepcopy_default())
        try:
            with path.open("rb") as f:
                loaded = tomllib.load(f)
            return cls(loaded)
        except Exception:
            return cls(_deepcopy_default())

    @classmethod
    def write_default_toml(cls, path: Path) -> None:
        lines = [
            "# agent-loop project configuration",
            "# This file is committed project configuration. Runtime state lives in .agent-loop/.",
            "",
            f"state_dir = {_toml_value(DEFAULT_CONFIG['state_dir'])}",
            "db_path = \".agent-loop/agent-loop.db\"",
            "logs_dir = \".agent-loop/logs\"",
            "worktrees_dir = \"worktrees\"",
            "plan_path = \".agent-loop/plan.md\"",
            "progress_path = \".agent-loop/progress.md\"",
            "learning_path = \".agent-loop/learning.md\"",
            f"max_workers = {DEFAULT_CONFIG['max_workers']}",
            f"webhook_env_var = {_toml_value(DEFAULT_CONFIG['webhook_env_var'])}",
            f"execution_mode = {_toml_value(DEFAULT_CONFIG['execution_mode'])}",
            "",
            "[routes]",
        ]

        for profile in DEFAULT_TOML_PROFILE_ORDER:
            lines.append(f"# {profile}: model fallback order for this loop personality.")
            lines.append(f"{profile} = [")
            for route in DEFAULT_CONFIG["routes"][profile]:
                lines.append(f"    {_route_toml(route)},")
            lines.append("]")
            lines.append("")

        lines.extend([
            "[retry_policy]",
            f"max_attempts = {DEFAULT_CONFIG['retry_policy']['max_attempts']}",
            f"escalation_threshold = {DEFAULT_CONFIG['retry_policy']['escalation_threshold']}",
            "",
            "[commands]",
            f"narrow_test = {_toml_value(DEFAULT_CONFIG['commands']['narrow_test'])}",
            f"regression_test = {_toml_value(DEFAULT_CONFIG['commands']['regression_test'])}",
            "",
        ])

        path.write_text("\n".join(lines), encoding="utf-8")
