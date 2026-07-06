import tomllib

from agent_loop.config import Config, DEFAULT_CONFIG


def test_default_route_profiles_cover_all_personalities():
    routes = DEFAULT_CONFIG["routes"]

    assert set(routes) >= {
        "intake",
        "spec_reviewer",
        "planner",
        "executor",
        "executor_escalated",
        "reviewer",
        "escalation_reviewer",
    }
    assert routes["executor"] == [
        {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
        {"provider": "agy", "model": "Claude Sonnet 4.6 (Thinking)", "reasoning_level": "high"},
        {"provider": "codex", "model": "gpt-5.4-mini", "reasoning_level": "high"},
    ]
    assert routes["planner"] == [
        {"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
        {"provider": "agy", "model": "Claude Opus 4.6 (Thinking)", "reasoning_level": "high"},
        {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
    ]
    assert routes["executor_escalated"][0] == {
        "provider": "codex",
        "model": "gpt-5.5",
        "reasoning_level": "high",
    }

    all_models = [route["model"] for bucket in routes.values() for route in bucket]
    assert "Gemini 3.5 Flash (High)" not in all_models


def test_default_routes_keep_three_step_failover_for_executor_and_reviewer_profiles():
    routes = DEFAULT_CONFIG["routes"]

    assert [(route["provider"], route["model"]) for route in routes["executor"]] == [
        ("agy", "Gemini 3.1 Pro (High)"),
        ("agy", "Claude Sonnet 4.6 (Thinking)"),
        ("codex", "gpt-5.4-mini"),
    ]
    assert [(route["provider"], route["model"]) for route in routes["reviewer"]] == [
        ("codex", "gpt-5.5"),
        ("agy", "Claude Opus 4.6 (Thinking)"),
        ("agy", "Gemini 3.1 Pro (High)"),
    ]


def test_agy_model_fallback_does_not_use_flash():
    config = Config()

    assert config.routes_for("executor")[0]["model"] == "Gemini 3.1 Pro (High)"


def test_config_all_model_routes_deduplicates_profiles_and_legacy_aliases():
    config = Config()

    routes = config.all_model_routes()

    route_keys = [(route["provider"], route["model"], route.get("reasoning_level")) for route in routes]
    assert len(route_keys) == len(set(route_keys))
    assert ("codex", "gpt-5.5", "high") in route_keys
    assert ("agy", "Gemini 3.1 Pro (High)", "high") in route_keys


def test_config_routes_for_supports_legacy_route_buckets():
    legacy = Config({
        "routes": {
            "planning": [{"provider": "codex", "model": "planner-model"}],
            "implementation": [{"provider": "agy", "model": "executor-model"}],
        }
    })

    assert legacy.routes_for("planner") == [{"provider": "codex", "model": "planner-model"}]
    assert legacy.routes_for("reviewer") == [{"provider": "codex", "model": "planner-model"}]
    assert legacy.routes_for("executor") == [{"provider": "agy", "model": "executor-model"}]
    assert legacy.routes_for("executor_escalated") == [{"provider": "codex", "model": "planner-model"}]


def test_default_agent_loop_toml_contains_all_defaults(tmp_path):
    target = tmp_path / "agent-loop.toml"

    Config.write_default_toml(target)

    data = tomllib.loads(target.read_text())
    assert data["state_dir"] == ".agent-loop"
    assert data["retry_policy"]["max_attempts"] == 3
    assert data["routes"]["intake"]
    assert data["routes"]["spec_reviewer"]
    assert data["routes"]["planner"]
    assert data["routes"]["executor"]
    assert data["routes"]["executor_escalated"][0]["model"] == "gpt-5.5"
    assert data["routes"]["reviewer"]
    assert data["routes"]["escalation_reviewer"]
