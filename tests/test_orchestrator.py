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

    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    
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

    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    
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
        p_state = orch.provider_repo.get("codex", "gpt-5.5")
        assert p_state["availability"] is False

def test_orchestrator_task_execution_loop(db_conn, tmp_path, monkeypatch):
    # Setup mock git functions in orchestrator
    mock_create_wt = MagicMock()
    mock_commit = MagicMock(return_value="mock_sha_123")
    mock_merge = MagicMock(return_value=(True, []))
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
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
            "planning": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    # Mock success run for Codex adapter
    mock_success = AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "LGTM"}', error="")
    
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

def test_run_verification_success_and_failure(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    
    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)
    
    run_id = run_repo.create("Test run", "autonomous")
    feat_id = feat_repo.create(run_id, "Core", "low")
    task_id = task_repo.create(run_id, feat_id, "Test task", "implementation", "low")
    attempt_id_1 = attempt_repo.create(run_id, task_id, "impl", "codex", "gpt-5", "high", str(worktree_dir), None, str(logs_dir))
    attempt_id_2 = attempt_repo.create(run_id, task_id, "impl", "codex", "gpt-5", "high", str(worktree_dir), None, str(logs_dir))
    
    # 1. Success command
    success = orch.run_verification(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id_1,
        command="echo hello",
        worktree_dir=worktree_dir,
        logs_dir=logs_dir
    )
    assert success is True
    
    test_runs = orch.test_run_repo.get_by_run(run_id)
    assert len(test_runs) == 1
    assert test_runs[0]["exit_status"] == 0
    assert test_runs[0]["command"] == "echo hello"
    out_path_data = json.loads(test_runs[0]["output_path"])
    assert "stdout" in out_path_data
    assert "stderr" in out_path_data
    assert Path(out_path_data["stdout"]).exists()
    assert Path(out_path_data["stderr"]).exists()
    
    # 2. Failure command
    failure = orch.run_verification(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id_2,
        command="exit 1",
        worktree_dir=worktree_dir,
        logs_dir=logs_dir
    )
    assert failure is False
    test_runs = orch.test_run_repo.get_by_run(run_id)
    assert len(test_runs) == 2
    assert test_runs[1]["exit_status"] == 1

def test_reviews_fail_closed(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Build engine", "autonomous")
    
    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter
        
        # Scenario 1: result.success = False
        mock_adapter.run_attempt.return_value = AttemptResult(success=False, exit_code=1, output="", error="API Timeout")
        decision = orch.run_agent_review(run_id, "task", 1, "Verify change")
        assert decision == "rejected"
        
        reviews = orch.review_repo.get_by_run(run_id)
        assert len(reviews) == 1
        assert reviews[0]["decision"] == "rejected"
        assert "timeout" in reviews[0]["findings"].lower() or "failed" in reviews[0]["findings"].lower()
        
        # Scenario 2: malformed JSON output
        mock_adapter.run_attempt.return_value = AttemptResult(success=True, exit_code=0, output="This is not JSON", error="")
        decision = orch.run_agent_review(run_id, "task", 2, "Verify change")
        assert decision == "rejected"
        
        reviews = orch.review_repo.get_by_run(run_id)
        assert len(reviews) == 2
        assert reviews[1]["decision"] == "rejected"
        assert "parse" in reviews[1]["findings"].lower() or "malformed" in reviews[1]["findings"].lower()
        
        # Scenario 3: valid rejection
        mock_adapter.run_attempt.return_value = AttemptResult(success=True, exit_code=0, output='{"decision": "rejected", "findings": "Complexity too high"}', error="")
        decision = orch.run_agent_review(run_id, "task", 3, "Verify change")
        assert decision == "rejected"
        
        reviews = orch.review_repo.get_by_run(run_id)
        assert len(reviews) == 3
        assert reviews[2]["decision"] == "rejected"
        assert reviews[2]["findings"] == "Complexity too high"

        # Scenario 4: valid approval
        mock_adapter.run_attempt.return_value = AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "LGTM"}', error="")
        decision = orch.run_agent_review(run_id, "task", 4, "Verify change")
        assert decision == "approved"
        
        reviews = orch.review_repo.get_by_run(run_id)
        assert len(reviews) == 4
        assert reviews[3]["decision"] == "approved"
        assert reviews[3]["findings"] == "LGTM"

def test_final_review_gating_success_and_failure(db_conn, tmp_path):
    config = Config()
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
    
    # 1. Mock run_final_review to fail (rejection)
    with patch.object(orch, "run_final_review", return_value=False) as mock_final:
        orch.run_loop(run_id)
        assert run_repo.get(run_id)["status"] == "blocked"
        mock_final.assert_called_once()
        
    # 2. Reset run status to running, and mock run_final_review to succeed
    run_repo.update_status(run_id, "running")
    with patch.object(orch, "run_final_review", return_value=True) as mock_final:
        orch.run_loop(run_id)
        assert run_repo.get(run_id)["status"] == "complete"

def test_parallel_workers_safe_concurrency(db_conn, tmp_path, monkeypatch):
    import time
    mock_create_wt = MagicMock()
    mock_commit = MagicMock(return_value="mock_sha_123")
    mock_remove_wt = MagicMock()
    
    merge_times = []
    
    def mock_merge_branch(repo_path, source_branch, target_branch):
        start = time.time()
        time.sleep(0.1)  # ensure overlap would happen if not locked
        end = time.time()
        merge_times.append((start, end))
        return True, []

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", mock_create_wt)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", mock_commit)
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", mock_merge_branch)
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Parallel task run", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")

    feat_id = feat_repo.create(run_id, "Feature 1", "low")

    # Task 1 (no deps, scope: file1.py)
    t1_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low", scope={"files": ["file1.py"]})
    # Task 2 (no deps, scope: file2.py)
    t2_id = task_repo.create(run_id, feat_id, "Task 2", "implementation", "low", scope={"files": ["file2.py"]})

    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "max_workers": 2,
        "retry_policy": {"max_attempts": 3, "escalation_threshold": 2},
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
            "planning": [{"provider": "codex", "model": "gpt-5.4-mini"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    task_execution_times = []
    
    def mock_run_attempt(model, prompt, workspace_path, attempt_logs_dir, **kwargs):
        start = time.time()
        time.sleep(0.2)  # force execution overlap
        end = time.time()
        if "Agent Loop Reviewer" not in prompt:
            task_execution_times.append((start, end))
        return AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "LGTM"}', error="")

    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.side_effect = mock_run_attempt
        mock_get_adapter.return_value = mock_adapter

        # Run loop
        orch.run_loop(run_id)

    # Check both tasks completed
    assert task_repo.get(t1_id)["status"] == "complete"
    assert task_repo.get(t2_id)["status"] == "complete"
    
    # Assert that task executions overlapped in time (parallel workers)
    assert len(task_execution_times) == 2
    t1_start, t1_end = task_execution_times[0]
    t2_start, t2_end = task_execution_times[1]
    assert max(t1_start, t2_start) < min(t1_end, t2_end)
    
    # Assert that git merges were serialized (intervals do not overlap)
    assert len(merge_times) == 2
    m1_start, m1_end = merge_times[0]
    m2_start, m2_end = merge_times[1]
    assert max(m1_start, m2_start) >= min(m1_end, m2_end)

def test_interrupted_attempt_recovery(db_conn, tmp_path):
    config = Config()
    # Mock max_attempts = 3
    config.data["retry_policy"] = {"max_attempts": 3, "escalation_threshold": 2}
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Interrupted run", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    # Set task to running and add attempt 1 in status running
    task_repo.update_status(task_id, "ready")
    task_repo.update_status(task_id, "running")
    att_id_1 = attempt_repo.create(run_id, task_id, "impl", "codex", "gpt-5", "high", str(tmp_path / "wt1"), None, str(tmp_path / "logs1"))

    # 1. Recover first time: should mark att_id_1 as abandoned, task to ready
    count = orch.reconcile_interrupted_run(run_id)
    assert count == 1
    assert attempt_repo.get(att_id_1)["outcome"] == "abandoned"
    assert task_repo.get(task_id)["status"] == "ready"

    # 2. Repeated resume call: should return 0, keep task ready, no changes
    count2 = orch.reconcile_interrupted_run(run_id)
    assert count2 == 0
    assert task_repo.get(task_id)["status"] == "ready"

    # 3. Simulate another attempt running and getting interrupted
    task_repo.update_status(task_id, "running")
    att_id_2 = attempt_repo.create(run_id, task_id, "impl", "codex", "gpt-5", "high", str(tmp_path / "wt2"), None, str(tmp_path / "logs2"))

    count3 = orch.reconcile_interrupted_run(run_id)
    assert count3 == 1
    assert attempt_repo.get(att_id_2)["outcome"] == "abandoned"
    assert task_repo.get(task_id)["status"] == "ready"

    # 4. Simulate a third attempt running and getting interrupted (reaching max_attempts = 3)
    task_repo.update_status(task_id, "running")
    att_id_3 = attempt_repo.create(run_id, task_id, "impl", "codex", "gpt-5", "high", str(tmp_path / "wt3"), None, str(tmp_path / "logs3"))

    count4 = orch.reconcile_interrupted_run(run_id)
    assert count4 == 1
    assert attempt_repo.get(att_id_3)["outcome"] == "abandoned"
    # Task should now be blocked!
    assert task_repo.get(task_id)["status"] == "blocked"


def test_worktree_creation_failures_respect_retry_limit(db_conn, tmp_path, monkeypatch):
    config = Config({
        "db_path": ":memory:",
        "retry_policy": {"max_attempts": 2, "escalation_threshold": 2},
        "routes": {
            "planning": [{"provider": "codex", "model": "gpt-5.5"}],
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
        },
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("No base commit", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Inspect stack", "planning", "low")
    task_repo.update_status(task_id, "ready")

    monkeypatch.setattr(
        "agent_loop.orchestrator.create_worktree",
        MagicMock(side_effect=RuntimeError("invalid reference: main")),
    )

    assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is False
    assert task_repo.get(task_id)["status"] == "ready"

    assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is False
    assert task_repo.get(task_id)["status"] == "blocked"
    assert len(attempt_repo.get_by_run(run_id)) == 2


def test_execute_task_uses_agent_loop_worktrees_by_default(db_conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = Config({
        "db_path": ":memory:",
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
            "planning": [{"provider": "codex", "model": "gpt-5.5"}]
        },
        "commands": {
            "narrow_test": "",
            "regression_test": ""
        }
    })
    orch = Orchestrator(db_conn, config)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Worktree placement test", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "ready")
    task = task_repo.get(task_id)

    created_worktrees = []

    def fake_create_worktree(repo_path, worktree_path, branch_name):
        created_worktrees.append(Path(worktree_path))

    mock_adapter = MagicMock()
    mock_adapter.run_attempt.side_effect = [
        AttemptResult(success=True, exit_code=0, output="task done", error=""),
        AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "ok"}', error="")
    ]

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", fake_create_worktree)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda worktree_dir, message: "abc123")
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo_path, source_branch, target_branch: (True, []))
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo_path, worktree_path: None)
    monkeypatch.setattr("agent_loop.orchestrator.get_adapter", lambda provider, config: mock_adapter)

    assert orch._execute_task_impl(run_id, task) is True

    assert created_worktrees == [
        tmp_path / ".agent-loop" / "worktrees" / f"run-{run_id}-task-{task_id}-attempt-1"
    ]


def test_merge_conflict_integration_lifecycle(db_conn, tmp_path, monkeypatch):
    mock_create_wt = MagicMock()
    mock_commit = MagicMock(return_value="mock_sha_123")
    mock_remove_wt = MagicMock()

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", mock_create_wt)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", mock_commit)
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Merge conflict task run", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "ready")

    config_data = {
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "max_workers": 1,
        "retry_policy": {"max_attempts": 3, "escalation_threshold": 2},
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
            "planning": [{"provider": "codex", "model": "gpt-5.5"}]
        }
    }
    config = Config(config_data)
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    # 1. First execution fails with merge conflict
    mock_merge_fail = MagicMock(return_value=(False, ["conflict_file.py"]))
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", mock_merge_fail)

    mock_impl_result = AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "LGTM"}', error="")

    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.return_value = mock_impl_result
        mock_get_adapter.return_value = mock_adapter

        # Run execute_task for original task
        task = task_repo.get(task_id)
        success = orch.execute_task(run_id, task)
        assert success is True

    # Original task must now be blocked
    assert task_repo.get(task_id)["status"] == "blocked"

    # Integration task should be created
    tasks = task_repo.get_by_run(run_id)
    integration_task = next(t for t in tasks if "Resolve merge conflict" in t["name"])
    assert integration_task is not None
    assert integration_task["status"] == "pending"

    scope_data = integration_task["scope"]
    assert isinstance(scope_data, dict)
    assert scope_data["original_task_id"] == task_id
    assert "conflict_file.py" in scope_data["conflicting_files"]

    # 2. Now run integration task and let it succeed
    mock_merge_success = MagicMock(return_value=(True, []))
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", mock_merge_success)

    # Make the integration task ready
    task_repo.update_status(integration_task["id"], "ready")

    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.return_value = mock_impl_result
        mock_get_adapter.return_value = mock_adapter

        success2 = orch.execute_task(run_id, integration_task)
        assert success2 is True

    # Both integration and original task must now be complete
    assert task_repo.get(integration_task["id"])["status"] == "complete"
    assert task_repo.get(task_id)["status"] == "complete"


def test_review_decision_states(db_conn, tmp_path, monkeypatch):
    config = Config()
    config.data["retry_policy"] = {"max_attempts": 2, "escalation_threshold": 2}
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Review decision test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")

    # Mocks for git
    mock_create_wt = MagicMock()
    mock_commit = MagicMock(return_value="mock_sha_123")
    mock_merge = MagicMock(return_value=(True, []))
    mock_remove_wt = MagicMock()

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", mock_create_wt)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", mock_commit)
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", mock_merge)
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    with patch("agent_loop.orchestrator.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter

        # 1. Test "block" decision
        task_id = task_repo.create(run_id, feat_id, "Task Block", "implementation", "low", required_verification="")
        task_repo.update_status(task_id, "ready")
        task = task_repo.get(task_id)

        # First attempt (adapter returns success, reviewer returns "block")
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done", error=""), # Implementation
            AttemptResult(success=True, exit_code=0, output='{"decision": "block", "findings": "Not allowed"}', error="") # Reviewer
        ]

        success = orch._execute_task_impl(run_id, task)
        assert success is True
        assert task_repo.get(task_id)["status"] == "blocked"

        # 2. Test "assessment" decision
        task_id2 = task_repo.create(run_id, feat_id, "Task Assess", "implementation", "low", required_verification="")
        task_repo.update_status(task_id2, "ready")
        task2 = task_repo.get(task_id2)

        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "assessment", "findings": "Needs manual check"}', error="")
        ]

        success = orch._execute_task_impl(run_id, task2)
        assert success is True
        assert task_repo.get(task_id2)["status"] == "blocked"

        # 3. Test "follow_up" under limit
        task_id3 = task_repo.create(run_id, feat_id, "Task Followup", "implementation", "low", required_verification="")
        task_repo.update_status(task_id3, "ready")
        task3 = task_repo.get(task_id3)

        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "follow_up", "findings": "Clarify X"}', error="")
        ]

        success = orch._execute_task_impl(run_id, task3)
        assert success is True
        assert task_repo.get(task_id3)["status"] == "complete"
        
        # Verify exactly one follow-up task is created, pending, and depends on original task
        tasks = task_repo.get_by_run(run_id)
        followup_tasks = [t for t in tasks if t["name"] == "Follow-up: Task Followup"]
        assert len(followup_tasks) == 1
        assert followup_tasks[0]["status"] == "pending"
        assert followup_tasks[0]["dependencies"] == ["Task Followup"]

        # 4. Test "rejected" limit bound
        # Max attempts is 2. Let's run attempt 1 (which gets rejected, sets to ready).
        # Then run attempt 2 (which gets rejected, sets to blocked).
        task_id4 = task_repo.create(run_id, feat_id, "Task Max Rejects", "implementation", "low", required_verification="")
        task_repo.update_status(task_id4, "ready")
        task4 = task_repo.get(task_id4)

        # Attempt 1
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done 1", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "rejected", "findings": "Bad code 1"}', error="")
        ]
        success = orch._execute_task_impl(run_id, task4)
        assert success is True
        assert task_repo.get(task_id4)["status"] == "ready"

        # Attempt 2
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done 2", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "rejected", "findings": "Bad code 2"}', error="")
        ]
        success = orch._execute_task_impl(run_id, task4)
        assert success is True
        assert task_repo.get(task_id4)["status"] == "blocked"


def test_preserve_partial_work_on_recovery(db_conn, tmp_path, monkeypatch):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Recovery test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    # Create an attempt marked 'running'
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

    # Simulate some uncommitted changes in the worktree
    # Let's mock subprocess.run inside _preserve_uncommitted_changes to return a dummy diff
    dummy_diff = "diff --git a/file.py b/file.py\n+new line"
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = dummy_diff

    def run_side_effect(cmd, *args, **kwargs):
        if "diff" in cmd:
            return mock_run
        # Mock status porcelain empty
        mock_status = MagicMock(returncode=0, stdout="")
        return mock_status

    # We also mock remove_worktree to verify it got called
    mock_remove_wt = MagicMock()
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    with patch("subprocess.run", side_effect=run_side_effect):
        # Trigger recovery
        orch.reconcile_interrupted_run(run_id)

    # Verify remove_worktree was called
    mock_remove_wt.assert_called_once()

    # Verify that patch file was written and is inspectable
    # Patch path is stored in the database for the attempt
    attempt = attempt_repo.get(attempt_id)
    assert attempt["outcome"] == "abandoned"
    assert attempt["patch_path"] is not None

    patch_file = Path(attempt["patch_path"])
    assert patch_file.exists()
    assert patch_file.read_text() == dummy_diff




