import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import RunRepository, FeatureRepository, TaskRepository, DecisionRepository, AttemptRepository
from agent_loop.orchestrator import validate_dag, Orchestrator
from agent_loop.adapters import AttemptResult

def test_dag_validation():
    # 1. Valid DAG
    features = [
        {"name": "Feat1", "dependencies": []},
        {"name": "Feat2", "dependencies": ["Feat1"]}
    ]
    tasks = [
        {"name": "Task1", "feature_name": "Feat1", "dependencies": []},
        {"name": "Task2", "feature_name": "Feat2", "dependencies": ["Task1"]}
    ]
    assert validate_dag(features, tasks) is True

    # 2. Cycle in features
    features_cycle = [
        {"name": "Feat1", "dependencies": ["Feat2"]},
        {"name": "Feat2", "dependencies": ["Feat1"]}
    ]
    assert validate_dag(features_cycle, tasks) is False

    # 3. Cycle in tasks
    tasks_cycle = [
        {"name": "Task1", "feature_name": "Feat1", "dependencies": ["Task2"]},
        {"name": "Task2", "feature_name": "Feat2", "dependencies": ["Task1"]}
    ]
    assert validate_dag(features, tasks_cycle) is False

    # 4. Task referencing non-existent feature
    tasks_invalid_feat = [
        {"name": "Task1", "feature_name": "NonExistent", "dependencies": []}
    ]
    assert validate_dag(features, tasks_invalid_feat) is False

@pytest.fixture
def db_conn():
    conn = get_connection(Path(":memory:"))
    migrate(conn)
    yield conn
    conn.close()

def test_orchestrator_planning_success(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Implement login page", "autonomous")
    
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "planning": [{"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"}]
        }
    }
    config = Config(config_data)
    
    plan_json = {
        "objective": "Implement login page",
        "decisions": [
            {"decision_type": "product", "summary": "Use OAuth2 client credentials flow", "details": ""}
        ],
        "features": [
            {"name": "Auth", "risk": "high", "acceptance_criteria": "Secure login endpoints", "dependencies": []}
        ],
        "tasks": [
            {
                "name": "Write schemas",
                "feature_name": "Auth",
                "role": "implementation",
                "risk": "low",
                "dependencies": [],
                "required_verification": "pytest tests"
            }
        ]
    }

    mock_result = AttemptResult(
        success=True,
        exit_code=0,
        output=json.dumps(plan_json),
        error=""
    )

    orch = Orchestrator(db_conn, config)
    
    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.return_value = mock_result
        mock_get_adapter.return_value = mock_adapter

        success = orch.plan_run(run_id)
        assert success is True
        
        # Verify status transitions
        run = run_repo.get(run_id)
        assert run["status"] == "running"  # Autonomous transition

        # Verify DB population
        features = FeatureRepository(db_conn).get_by_run(run_id)
        assert len(features) == 1
        assert features[0]["name"] == "Auth"
        assert features[0]["risk"] == "high"

        tasks = TaskRepository(db_conn).get_by_run(run_id)
        assert len(tasks) == 1
        assert tasks[0]["name"] == "Write schemas"
        assert tasks[0]["role"] == "implementation"

        decisions = DecisionRepository(db_conn).get_by_run(run_id)
        assert len(decisions) == 1
        assert decisions[0]["summary"] == "Use OAuth2 client credentials flow"
        assert decisions[0]["is_autonomous"] is True

def test_orchestrator_planning_route_failover(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Implement login page", "brainstorm")
    
    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "planning": [
                {"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"},
                {"provider": "agy", "model": "Claude Opus 4.6 Thinking", "reasoning_level": "high"}
            ]
        }
    }
    config = Config(config_data)

    mock_fail = AttemptResult(success=False, exit_code=1, output="", error="Quota exhausted", quota_exhausted=True)
    
    plan_json = {
        "objective": "Implement login page",
        "decisions": [],
        "features": [{"name": "Auth", "risk": "low", "acceptance_criteria": "Done", "dependencies": []}],
        "tasks": [{"name": "T1", "feature_name": "Auth", "role": "implementation", "risk": "low", "dependencies": [], "required_verification": "pytest"}]
    }
    mock_success = AttemptResult(success=True, exit_code=0, output=json.dumps(plan_json), error="")

    orch = Orchestrator(db_conn, config)
    
    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_codex = MagicMock()
        mock_codex.run_attempt.return_value = mock_fail
        
        mock_agy = MagicMock()
        mock_agy.run_attempt.return_value = mock_success
        
        def get_adapter_side_effect(provider, *args, **kwargs):
            if provider == "codex":
                return mock_codex
            return mock_agy
            
        mock_get_adapter.side_effect = get_adapter_side_effect

        success = orch.plan_run(run_id)
        assert success is True
        
        # Verify status: since intake_mode="brainstorm", transitions to awaiting_plan_approval
        run = run_repo.get(run_id)
        assert run["status"] == "awaiting_plan_approval"

        # Verify codex marked unavailable in DB
        p_state = orch.provider_repo.get("codex")
        assert p_state["availability"] is False

def test_orchestrator_task_execution_loop(db_conn, tmp_path, monkeypatch):
    # Setup mock git functions in orchestrator
    mock_create_wt = MagicMock()
    mock_commit = MagicMock(return_value="mock_sha_123")
    mock_merge = MagicMock(return_value=True)
    mock_remove_wt = MagicMock()

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", mock_create_wt)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", mock_commit)
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", mock_merge)
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Build engine", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")

    feat_id = feat_repo.create(run_id, "Core", "low")
    
    # Task 1 (no deps)
    t1_id = task_repo.create(run_id, feat_id, "Init DB", "implementation", "low", dependencies=[])
    # Task 2 (depends on Task 1)
    t2_id = task_repo.create(run_id, feat_id, "Queries", "implementation", "low", dependencies=["Init DB"])

    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "max_workers": 2,
        "retry_policy": {"max_attempts": 3, "escalation_threshold": 2},
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config)

    # Mock success run for Codex adapter
    mock_success = AttemptResult(success=True, exit_code=0, output="Completed task", error="")
    
    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.return_value = mock_success
        mock_get_adapter.return_value = mock_adapter

        # Run loop (which runs until the entire run completes/stops)
        orch.run_loop(run_id)

        # Check both tasks completed and run is complete
        assert task_repo.get(t1_id)["status"] == "complete"
        assert task_repo.get(t2_id)["status"] == "complete"
        assert run_repo.get(run_id)["status"] == "complete"

        # Verify git helper mocks were called
        mock_create_wt.assert_called()
        mock_commit.assert_called()
        mock_merge.assert_called()
        mock_remove_wt.assert_called()

