from agent_loop.cli import _select_agy_model
from agent_loop.config import Config, DEFAULT_CONFIG


def test_default_route_order_prefers_pro_executor_and_strong_reviewers():
    routes = DEFAULT_CONFIG["routes"]

    assert routes["implementation"] == [
        {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
        {"provider": "agy", "model": "Claude Sonnet 4.6 (Thinking)", "reasoning_level": "high"},
        {"provider": "codex", "model": "gpt-5.4-mini", "reasoning_level": "high"},
    ]
    assert routes["planning"] == [
        {"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
        {"provider": "agy", "model": "Claude Opus 4.6 (Thinking)", "reasoning_level": "high"},
        {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
    ]

    all_models = [route["model"] for bucket in routes.values() for route in bucket]
    assert "Gemini 3.5 Flash (High)" not in all_models


def test_default_routes_keep_three_step_failover_for_executor_and_reviewer():
    routes = DEFAULT_CONFIG["routes"]

    assert [(route["provider"], route["model"]) for route in routes["implementation"]] == [
        ("agy", "Gemini 3.1 Pro (High)"),
        ("agy", "Claude Sonnet 4.6 (Thinking)"),
        ("codex", "gpt-5.4-mini"),
    ]
    assert [(route["provider"], route["model"]) for route in routes["planning"]] == [
        ("codex", "gpt-5.5"),
        ("agy", "Claude Opus 4.6 (Thinking)"),
        ("agy", "Gemini 3.1 Pro (High)"),
    ]


def test_agy_model_fallback_does_not_use_flash():
    config = Config({"routes": {"planning": [], "implementation": []}})

    assert _select_agy_model(config) == "Gemini 3.1 Pro (High)"
