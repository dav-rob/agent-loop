import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import RunRepository, FeatureRepository, TaskRepository, DecisionRepository, AttemptRepository, ReviewRepository
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
    
    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
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


def test_planning_uses_router_planner_profile(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Implement login page", "none")
    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})
    plan_json = {
        "objective": "Implement login page",
        "decisions": [],
        "features": [
            {"name": "Auth", "risk": "low", "acceptance_criteria": "Works", "dependencies": []}
        ],
        "tasks": [
            {
                "name": "Write auth",
                "feature_name": "Auth",
                "role": "implementation",
                "risk": "low",
                "scope": {"files": []},
                "dependencies": [],
                "required_verification": "pytest",
            }
        ],
    }
    routed = MagicMock()
    routed.success = True
    routed.output = json.dumps(plan_json)
    routed.error = ""

    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.run.return_value = routed
        mock_router_cls.return_value = mock_router

        assert orch.plan_run(run_id) is True

    assert mock_router.run.call_args.kwargs["profile"] == "planner"


def test_plan_run_drops_prose_required_verification(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Build a local dashboard", "none")
    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})
    plan_json = {
        "objective": "Build a local dashboard",
        "decisions": [],
        "features": [
            {"name": "App", "risk": "low", "acceptance_criteria": "Starts locally", "dependencies": []}
        ],
        "tasks": [
            {
                "name": "Create app",
                "feature_name": "App",
                "role": "implementation",
                "risk": "low",
                "scope": {"files": ["package.json"]},
                "dependencies": [],
                "required_verification": "Run npm install and start the app; confirm it opens.",
            }
        ],
    }
    routed = MagicMock()
    routed.success = True
    routed.output = json.dumps(plan_json)
    routed.error = ""

    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.run.return_value = routed
        mock_router_cls.return_value = mock_router

        assert orch.plan_run(run_id) is True

    prompt = mock_router.run.call_args.kwargs["prompt"]
    assert "required_verification must be an executable shell command" in prompt

    tasks = TaskRepository(db_conn).get_by_run(run_id)
    assert tasks[0]["required_verification"] == ""


def test_plan_run_normalizes_file_scoped_planning_task_to_implementation(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Build a local dashboard", "none")
    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})
    plan_json = {
        "objective": "Build a local dashboard",
        "decisions": [],
        "features": [
            {"name": "App", "risk": "medium", "acceptance_criteria": "Starts locally", "dependencies": []}
        ],
        "tasks": [
            {
                "name": "Define app skeleton, data model, and scheduler boundaries",
                "feature_name": "App",
                "role": "planning",
                "risk": "medium",
                "scope": {
                    "files": ["package.json", "src/**", "server/**", "app/**", "README.md"],
                    "writes": [],
                    "reads": ["package.json", "src/**", "server/**", "app/**", "README.md"],
                },
                "dependencies": [],
                "required_verification": "test -f package.json",
            }
        ],
    }
    routed = MagicMock()
    routed.success = True
    routed.output = json.dumps(plan_json)
    routed.error = ""

    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.run.return_value = routed
        mock_router_cls.return_value = mock_router

        assert orch.plan_run(run_id) is True

    tasks = TaskRepository(db_conn).get_by_run(run_id)
    assert tasks[0]["role"] == "implementation"
    assert tasks[0]["scope"]["writes"] == ["package.json", "src/**", "server/**", "app/**", "README.md"]
    assert tasks[0]["scope"]["reads"] == []


def test_review_uses_router_reviewer_profile(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Implement login page", "none")
    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})
    routed = MagicMock()
    routed.success = True
    routed.output = json.dumps({"decision": "approved", "findings": "Looks good"})
    routed.error = ""
    routed.provider = "codex"
    routed.model = "gpt-5.5"

    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.run.return_value = routed
        mock_router_cls.return_value = mock_router

        decision = orch.run_agent_review(run_id, "final", run_id, "Review it")

    assert decision == "approved"
    assert mock_router.run.call_args.kwargs["profile"] == "reviewer"
    reviews = orch.review_repo.get_by_run(run_id)
    assert reviews[0]["reviewer_route"] == "codex:gpt-5.5"


def test_review_accepts_clear_labelled_approved_output(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Implement login page", "none")
    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})
    routed = MagicMock()
    routed.success = True
    routed.output = "Done.\\n\\n**Decision: Approved**\\n\\nNo blocking issues."
    routed.error = ""
    routed.provider = "agy"
    routed.model = "Claude Opus 4.6 (Thinking)"

    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.run.return_value = routed
        mock_router_cls.return_value = mock_router

        decision = orch.run_agent_review(run_id, "task", 1, "Review it")

    assert decision == "approved"
    reviews = orch.review_repo.get_by_run(run_id)
    assert reviews[0]["decision"] == "approved"
    assert "labelled non-JSON" in reviews[0]["findings"]


def test_execution_profile_stays_executor_before_threshold(db_conn, tmp_path):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "retry_policy": {"max_attempts": 5, "escalation_threshold": 2},
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    task = {"role": "implementation", "risk": "high", "scope": {"files": []}}
    attempts = [
        {"outcome": "failed"},
    ]

    assert orch.execution_profile_for_task(task, attempts) == "executor"


def test_execution_profile_uses_escalated_profile_after_failed_attempt_threshold(db_conn, tmp_path):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "retry_policy": {"max_attempts": 5, "escalation_threshold": 2},
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    task = {"role": "implementation", "risk": "high", "scope": {"files": []}}
    attempts = [
        {"outcome": "failed"},
        {"outcome": "abandoned"},
    ]

    assert orch.execution_profile_for_task(task, attempts) == "executor_escalated"


def test_execution_profile_uses_escalated_profile_after_rejected_review_threshold(db_conn, tmp_path):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "retry_policy": {"max_attempts": 5, "escalation_threshold": 2},
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    task = {"role": "implementation", "risk": "high", "scope": {"files": []}}
    attempts = [
        {"outcome": "completed"},
        {"outcome": "completed"},
    ]

    assert orch.execution_profile_for_task(task, attempts, rejected_review_count=2) == "executor_escalated"


def test_execution_profile_uses_escalated_profile_after_escalation_hint(db_conn, tmp_path):
    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    task = {
        "role": "implementation",
        "risk": "medium",
        "scope": json.dumps({"files": [], "escalation_hint": "Use a stronger model"}),
    }

    assert orch.execution_profile_for_task(task, []) == "executor_escalated"


def test_execution_profile_uses_planner_for_planning_tasks(db_conn, tmp_path):
    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    task = {"role": "planning", "risk": "high", "scope": {"files": []}}

    assert orch.execution_profile_for_task(task, []) == "planner"


def test_execution_profile_routes_file_scoped_planning_tasks_to_executor(db_conn, tmp_path):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "retry_policy": {"max_attempts": 5, "escalation_threshold": 2},
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    task = {
        "role": "planning",
        "risk": "medium",
        "required_verification": "test -f package.json",
        "scope": {
            "files": ["package.json", "src/**", "server/**", "app/**", "README.md"],
            "writes": [],
            "reads": ["package.json", "src/**", "server/**", "app/**", "README.md"],
        },
    }

    assert orch.execution_profile_for_task(task, [{"outcome": "completed"}], rejected_review_count=1) == "executor"
    assert orch.execution_profile_for_task(task, [{"outcome": "completed"}], rejected_review_count=2) == "executor_escalated"


def test_task_execution_uses_router_executor_profile_and_records_selected_route(db_conn, tmp_path, monkeypatch):
    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", lambda repo, worktree, branch: Path(worktree).mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda worktree, message: "mock_sha")
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo, branch, target: (True, []))
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo, worktree: None)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Build engine", "none")
    feat_id = feat_repo.create(run_id, "Core", "low")
    task_id = task_repo.create(run_id, feat_id, "Implement CLI", "implementation", "low", scope={"files": []})
    task_repo.update_status(task_id, "ready")
    task = task_repo.get(task_id)

    config = Config({"db_path": ":memory:", "logs_dir": str(tmp_path / "logs")})
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    routed = MagicMock()
    routed.success = True
    routed.output = ""
    routed.error = ""
    routed.provider = "agy"
    routed.model = "Claude Sonnet 4.5"
    routed.reasoning_level = "medium"

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls, \
         patch.object(orch, "_ensure_workspace_deps"), \
         patch.object(orch, "run_task_review", return_value="approved"):
        mock_router = MagicMock()
        mock_router.run.return_value = routed
        mock_router_cls.return_value = mock_router

        assert orch._execute_task_impl(run_id, task) is True

    assert mock_router.run.call_args.kwargs["profile"] == "executor"
    attempts = AttemptRepository(db_conn).get_by_run(run_id)
    assert len(attempts) == 1
    assert attempts[0]["route"] == "executor"
    assert attempts[0]["provider"] == "agy"
    assert attempts[0]["model"] == "Claude Sonnet 4.5"


def test_task_execution_escalates_route_after_two_rejected_reviews(db_conn, tmp_path, monkeypatch):
    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", lambda repo, worktree, branch: Path(worktree).mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda worktree, message: "mock_sha")
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo, branch, target: (True, []))
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo, worktree: None)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)
    review_repo = ReviewRepository(db_conn)

    run_id = run_repo.create("Build engine", "none")
    feat_id = feat_repo.create(run_id, "Core", "low")
    task_id = task_repo.create(run_id, feat_id, "Implement CLI", "implementation", "medium", scope={"files": []})
    task_repo.update_status(task_id, "ready")

    for _ in range(2):
        attempt_id = attempt_repo.create(run_id, task_id, route="executor", provider="agy", model="Gemini 3.1 Pro (High)")
        attempt_repo.update_outcome(attempt_id, "completed", commit_sha="old_sha")
        review_repo.create(
            run_id,
            "task",
            task_id,
            "rejected",
            reviewer_route="codex:gpt-5.5",
            findings="Still wrong",
        )

    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "retry_policy": {"max_attempts": 5, "escalation_threshold": 2},
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    routed = MagicMock()
    routed.success = True
    routed.output = ""
    routed.error = ""
    routed.provider = "codex"
    routed.model = "gpt-5.5"
    routed.reasoning_level = "high"

    with patch("agent_loop.orchestrator.ModelRouter") as mock_router_cls, \
         patch.object(orch, "_ensure_workspace_deps"), \
         patch.object(orch, "run_task_review", return_value="approved"):
        mock_router = MagicMock()
        mock_router.run.return_value = routed
        mock_router_cls.return_value = mock_router

        assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is True

    assert mock_router.run.call_args.kwargs["profile"] == "executor_escalated"
    attempts = [attempt for attempt in attempt_repo.get_by_run(run_id) if attempt["task_id"] == task_id]
    assert attempts[-1]["route"] == "executor_escalated"
    assert attempts[-1]["provider"] == "codex"
    assert attempts[-1]["model"] == "gpt-5.5"

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
    
    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
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
    
    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
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


def test_run_verification_skips_prose_instead_of_executing_shell(db_conn, tmp_path):
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
    attempt_id = attempt_repo.create(run_id, task_id, "impl", "codex", "gpt-5", "high", str(worktree_dir), None, str(logs_dir))

    with patch("agent_loop.orchestrator.subprocess.run") as mock_run:
        success = orch.run_verification(
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            command="Run npm install and start the app; confirm tables exist.",
            worktree_dir=worktree_dir,
            logs_dir=logs_dir,
        )

    assert success is True
    mock_run.assert_not_called()
    test_runs = orch.test_run_repo.get_by_run(run_id)
    assert test_runs[0]["exit_status"] == 0
    assert test_runs[0]["command"] == ""
    assert "Skipped prose verification" in (logs_dir / "test_run_stdout.log").read_text()


def test_reviews_fail_closed(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    
    run_repo = RunRepository(db_conn)
    run_id = run_repo.create("Build engine", "autonomous")
    
    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
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

def test_feature_review_follow_up_prevents_final_review_until_task_runs(db_conn, tmp_path):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "planning": [{"provider": "codex", "model": "gpt-5.5"}],
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
        },
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Feature follow-up test", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "complete", force=True)

    with patch.object(orch, "run_feature_review", return_value="follow_up") as mock_feature_review, \
         patch.object(orch, "run_final_review", return_value=True) as mock_final_review:
        orch.run_loop(run_id)

    mock_feature_review.assert_called_once_with(run_id, feat_id)
    mock_final_review.assert_not_called()
    assert run_repo.get(run_id)["status"] == "running"
    assert feat_repo.get(feat_id)["review_status"] == "pending"

    tasks = task_repo.get_by_run(run_id)
    follow_up_tasks = [task for task in tasks if task["name"] == "Address feature review feedback for Feature 1"]
    assert len(follow_up_tasks) == 1
    assert follow_up_tasks[0]["status"] == "pending"

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

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
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


def test_parallel_workers_do_not_serialize_shared_read_scope(db_conn, tmp_path, monkeypatch):
    import time

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", lambda repo, worktree, branch: Path(worktree).mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda worktree, message: "mock_sha_123")
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo, branch, target: (True, []))
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo, worktree: None)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Parallel read-scope run", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")

    task_repo.create(
        run_id,
        feat_id,
        "News refresh",
        "implementation",
        "medium",
        scope={"writes": ["src/services/news.js"], "reads": ["src/db.js"]},
    )
    task_repo.create(
        run_id,
        feat_id,
        "YouTube refresh",
        "implementation",
        "medium",
        scope={"writes": ["src/services/youtube.js"], "reads": ["src/db.js"]},
    )

    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "max_workers": 2,
        "retry_policy": {"max_attempts": 5, "escalation_threshold": 2},
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    task_execution_times = []

    def mock_run_attempt(model, prompt, workspace_path, attempt_logs_dir, **kwargs):
        start = time.time()
        time.sleep(0.2)
        end = time.time()
        if "Agent Loop Reviewer" not in prompt:
            task_execution_times.append((start, end))
        return AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "OK"}', error="")

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.side_effect = mock_run_attempt
        mock_get_adapter.return_value = mock_adapter

        orch.run_loop(run_id)

    assert len(task_execution_times) == 2
    first_start, first_end = task_execution_times[0]
    second_start, second_end = task_execution_times[1]
    assert max(first_start, second_start) < min(first_end, second_end)


def test_parallel_workers_serialize_overlapping_write_scope(db_conn, tmp_path, monkeypatch):
    import time

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", lambda repo, worktree, branch: Path(worktree).mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda worktree, message: "mock_sha_123")
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo, branch, target: (True, []))
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo, worktree: None)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Serialized write-scope run", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")

    task_repo.create(
        run_id,
        feat_id,
        "News DB changes",
        "implementation",
        "medium",
        scope={"writes": ["src/services/news.js", "src/db.js"], "reads": []},
    )
    task_repo.create(
        run_id,
        feat_id,
        "YouTube DB changes",
        "implementation",
        "medium",
        scope={"writes": ["src/services/youtube.js", "src/db.js"], "reads": []},
    )

    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "max_workers": 2,
        "retry_policy": {"max_attempts": 5, "escalation_threshold": 2},
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")
    task_execution_times = []

    def mock_run_attempt(model, prompt, workspace_path, attempt_logs_dir, **kwargs):
        start = time.time()
        time.sleep(0.1)
        end = time.time()
        if "Agent Loop Reviewer" not in prompt:
            task_execution_times.append((start, end))
        return AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "OK"}', error="")

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.side_effect = mock_run_attempt
        mock_get_adapter.return_value = mock_adapter

        orch.run_loop(run_id)

    assert len(task_execution_times) == 2
    first_start, first_end = task_execution_times[0]
    second_start, second_end = task_execution_times[1]
    assert max(first_start, second_start) >= min(first_end, second_end)

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


def test_recovery_resets_running_task_with_no_running_attempt(db_conn, tmp_path):
    config = Config()
    config.data["retry_policy"] = {"max_attempts": 3, "escalation_threshold": 2}
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Stale running task", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "ready")
    task_repo.update_status(task_id, "running")

    att_id_1 = attempt_repo.create(run_id, task_id, "impl", "agy", "Gemini 3.1 Pro (High)", "high")
    attempt_repo.update_outcome(att_id_1, "failed")
    att_id_2 = attempt_repo.create(run_id, task_id, "impl", "agy", "Claude Sonnet 4.6 (Thinking)", "high")
    attempt_repo.update_outcome(att_id_2, "failed")

    count = orch.reconcile_interrupted_run(run_id)

    assert count == 0
    assert task_repo.get(task_id)["status"] == "ready"


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


def test_execute_task_uses_visible_worktrees_by_default(db_conn, tmp_path, monkeypatch):
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
    monkeypatch.setattr("agent_loop.routing.get_adapter", lambda provider, config: mock_adapter)

    assert orch._execute_task_impl(run_id, task) is True

    assert created_worktrees == [
        tmp_path / "worktrees" / f"run-{run_id}-task-{task_id}"
    ]


def test_rejected_retry_continues_same_task_worktree(db_conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = Config({
        "db_path": ":memory:",
        "worktrees_dir": str(tmp_path / "worktrees"),
        "logs_dir": str(tmp_path / ".agent-loop" / "logs"),
        "retry_policy": {"max_attempts": 3, "escalation_threshold": 2},
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
            "planning": [{"provider": "codex", "model": "gpt-5.5"}],
        },
        "commands": {
            "narrow_test": "",
            "regression_test": "",
        },
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Continue rejected work", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "ready")

    expected_worktree = tmp_path / "worktrees" / f"run-{run_id}-task-{task_id}"
    expected_branch = f"agent-loop-run-{run_id}-task-{task_id}"
    created_worktrees = []
    removed_worktrees = []
    prompts = []

    def fake_create_worktree(repo_path, worktree_path, branch_name):
        created_worktrees.append((Path(worktree_path), branch_name))
        Path(worktree_path).mkdir(parents=True, exist_ok=True)

    def fake_remove_worktree(repo_path, worktree_path):
        removed_worktrees.append(Path(worktree_path))

    class FakeRouter:
        provider = "codex"
        model = "gpt-5.4-mini"
        reasoning_level = "high"

        def run(self, profile, prompt, workspace_path, logs_root):
            prompts.append(prompt)
            return type("RoutedResult", (), {
                "provider": self.provider,
                "model": self.model,
                "reasoning_level": self.reasoning_level,
                "result": AttemptResult(success=True, exit_code=0, output="implemented", error=""),
            })()

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", fake_create_worktree)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda worktree_dir, message: f"sha-{len(attempt_repo.get_by_run(run_id))}")
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", fake_remove_worktree)
    monkeypatch.setattr(orch, "_model_router", lambda: FakeRouter())
    monkeypatch.setattr(orch, "_worktree_matches_branch", lambda worktree_path, branch_name: Path(worktree_path) == expected_worktree and branch_name == expected_branch)
    monkeypatch.setattr(orch, "run_task_review", MagicMock(side_effect=["rejected", "approved"]))
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo_path, source_branch, target_branch: (True, []))

    assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is True
    assert task_repo.get(task_id)["status"] == "ready"
    assert created_worktrees == [(expected_worktree, expected_branch)]
    assert removed_worktrees == []

    assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is True
    assert task_repo.get(task_id)["status"] == "complete"
    assert created_worktrees == [(expected_worktree, expected_branch)]
    assert removed_worktrees == [expected_worktree]
    assert [Path(attempt["worktree_path"]) for attempt in attempt_repo.get_by_run(run_id)] == [
        expected_worktree,
        expected_worktree,
    ]
    assert "continuation attempt on the existing task branch" in prompts[1]


def test_task_review_prompt_guides_continuation_feedback(db_conn, tmp_path, monkeypatch):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / ".agent-loop" / "logs"),
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Review continuation guidance", "autonomous")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    captured = {}

    def fake_run_agent_review(run_id_arg, subject_type, subject_id, prompt, attempt_id=None, workspace_path=None):
        captured["prompt"] = prompt
        captured["workspace_path"] = workspace_path
        return "approved"

    monkeypatch.setattr(orch, "run_agent_review", fake_run_agent_review)

    assert orch.run_task_review(run_id, task_id, None, None, attempt_id=1) == "approved"
    assert "task branch that will normally be continued" in captured["prompt"]
    assert "Prefer precise repair instructions" in captured["prompt"]
    assert "Reject only for blocking issues" in captured["prompt"]
    assert captured["workspace_path"] == orch._task_worktree_dir(run_id, task_id)


def test_implementation_route_failover_reaches_codex_after_agy_routes_unavailable(db_conn, tmp_path, monkeypatch):
    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [
                {"provider": "agy", "model": "Gemini 3.1 Pro (High)", "reasoning_level": "high"},
                {"provider": "agy", "model": "Claude Sonnet 4.6 (Thinking)", "reasoning_level": "high"},
                {"provider": "codex", "model": "gpt-5.4-mini", "reasoning_level": "high"},
            ],
            "planning": [{"provider": "codex", "model": "gpt-5.5", "reasoning_level": "high"}],
        },
        "commands": {
            "narrow_test": "",
            "regression_test": "",
        },
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Executor failover test", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")
    task_repo.update_status(task_id, "ready")

    orch.provider_repo.save(
        "agy",
        "Gemini 3.1 Pro (High)",
        {"models": ["Gemini 3.1 Pro (High)"]},
        False,
        "auth_required",
    )
    orch.provider_repo.save(
        "agy",
        "Claude Sonnet 4.6 (Thinking)",
        {"models": ["Claude Sonnet 4.6 (Thinking)"]},
        False,
        "auth_required",
    )

    mock_adapter = MagicMock()
    mock_adapter.run_attempt.side_effect = [
        AttemptResult(success=True, exit_code=0, output="done", error=""),
        AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "ok"}', error=""),
    ]

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", lambda repo_path, worktree_path, branch_name: None)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", lambda worktree_dir, message: "abc123")
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", lambda repo_path, source_branch, target_branch: (True, []))
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", lambda repo_path, worktree_path: None)
    monkeypatch.setattr("agent_loop.routing.get_adapter", lambda provider, config: mock_adapter)

    assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is True

    attempts = attempt_repo.get_by_run(run_id)
    assert [(attempt["provider"], attempt["model"]) for attempt in attempts] == [
        ("codex", "gpt-5.4-mini"),
    ]
    assert task_repo.get(task_id)["status"] == "complete"


def test_reset_provider_errors_preserves_auth_required_routes(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    orch.provider_repo.save(
        "agy",
        "Gemini 3.1 Pro (High)",
        {"models": ["Gemini 3.1 Pro (High)"]},
        False,
        "auth_required",
    )
    orch.provider_repo.save(
        "agy",
        "Claude Sonnet 4.6 (Thinking)",
        {"models": ["Claude Sonnet 4.6 (Thinking)"]},
        False,
        "transient_failure",
    )

    orch.reset_provider_errors()

    auth_state = orch.provider_repo.get("agy", "Gemini 3.1 Pro (High)")
    transient_state = orch.provider_repo.get("agy", "Claude Sonnet 4.6 (Thinking)")
    assert auth_state["availability"] is False
    assert auth_state["quota_state"] == "auth_required"
    assert transient_state["availability"] is True
    assert transient_state["quota_state"] == "available"


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

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
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

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.return_value = mock_impl_result
        mock_get_adapter.return_value = mock_adapter

        success2 = orch.execute_task(run_id, integration_task)
        assert success2 is True

    # Both integration and original task must now be complete
    assert task_repo.get(integration_task["id"])["status"] == "complete"
    assert task_repo.get(task_id)["status"] == "complete"


def test_merge_conflict_recovery_follow_up_closes_original_task(db_conn, tmp_path, monkeypatch):
    mock_create_wt = MagicMock()
    mock_commit = MagicMock(side_effect=["sha-original", "sha-integration", "sha-followup"])
    mock_remove_wt = MagicMock()

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", mock_create_wt)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", mock_commit)
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Merge conflict recovery follow-up run", "autonomous")
    run_repo.update_status(run_id, "planning")
    run_repo.update_status(run_id, "running")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(
        run_id,
        feat_id,
        "Task 1",
        "implementation",
        "low",
        scope={"writes": ["package-lock.json"]},
        required_verification="",
    )
    task_repo.update_status(task_id, "ready")

    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "max_workers": 1,
        "retry_policy": {"max_attempts": 3, "escalation_threshold": 2},
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
            "planning": [{"provider": "codex", "model": "gpt-5.5"}],
        },
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    merge_results = [
        (False, ["package-lock.json"]),
        (True, []),
        (True, []),
    ]
    monkeypatch.setattr("agent_loop.orchestrator.merge_branch", MagicMock(side_effect=merge_results))

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="original done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "LGTM"}', error=""),
            AttemptResult(success=True, exit_code=0, output="conflict resolved", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "follow_up", "findings": "Remove obsolete test shim"}', error=""),
            AttemptResult(success=True, exit_code=0, output="follow-up done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "approved", "findings": "Clean"}', error=""),
        ]
        mock_get_adapter.return_value = mock_adapter

        assert orch.execute_task(run_id, task_repo.get(task_id)) is True
        assert task_repo.get(task_id)["status"] == "blocked"

        integration_task = next(t for t in task_repo.get_by_run(run_id) if t["name"] == "Resolve merge conflict on Task 1")
        task_repo.update_status(integration_task["id"], "ready")
        assert orch.execute_task(run_id, integration_task) is True
        assert task_repo.get(task_id)["status"] == "blocked"

        recovery_follow_up = next(t for t in task_repo.get_by_run(run_id) if t["name"] == "Follow-up: Resolve merge conflict on Task 1")
        assert recovery_follow_up["scope"]["origin_task_id"] == task_id
        task_repo.update_status(recovery_follow_up["id"], "ready")
        assert orch.execute_task(run_id, recovery_follow_up) is True

    assert task_repo.get(recovery_follow_up["id"])["status"] == "complete"
    assert task_repo.get(integration_task["id"])["status"] == "complete"
    assert task_repo.get(task_id)["status"] == "complete"


def test_merge_conflict_recovery_tasks_route_as_executor_work(db_conn, tmp_path):
    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Recovery routing run", "autonomous")
    feat_id = feat_repo.create(run_id, "Feature 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task 1", "implementation", "low")

    config = Config({
        "db_path": ":memory:",
        "logs_dir": str(tmp_path / "logs"),
        "routes": {
            "implementation": [{"provider": "codex", "model": "gpt-5.4-mini"}],
            "planning": [{"provider": "codex", "model": "gpt-5.5"}],
        },
    })
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    orch.create_integration_task(
        run_id=run_id,
        task=task_repo.get(task_id),
        branch_name="agent-loop-run-1-task-1",
        source_commit="abc123",
        target_baseline="main",
        conflicting_files=["package-lock.json"],
    )

    integration_task = next(t for t in task_repo.get_by_run(run_id) if t["name"] == "Resolve merge conflict on Task 1")
    assert integration_task["role"] == "implementation"
    assert integration_task["scope"]["writes"] == ["package-lock.json"]
    assert orch.execution_profile_for_task(integration_task, attempts=[]) == "executor"


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

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
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


def test_retry_limit_follow_up_uses_latest_escalation_findings(db_conn, tmp_path, monkeypatch):
    config = Config()
    config.data["retry_policy"] = {"max_attempts": 2, "escalation_threshold": 2}
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Retry limit follow-up test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task Max Followup", "implementation", "low", required_verification="")

    mock_create_wt = MagicMock()
    mock_commit = MagicMock(return_value="mock_sha_123")
    mock_remove_wt = MagicMock()

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", mock_create_wt)
    monkeypatch.setattr("agent_loop.orchestrator.commit_changes", mock_commit)
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter

        task_repo.update_status(task_id, "ready")
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done 1", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "rejected", "findings": "Bad code 1"}', error="")
        ]
        assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is True
        assert task_repo.get(task_id)["status"] == "ready"

        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="done 2", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "rejected", "findings": "Bad code 2"}', error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "follow_up", "findings": "Try a narrower update"}', error="")
        ]

        assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is True

    assert task_repo.get(task_id)["status"] == "complete"
    tasks = task_repo.get_by_run(run_id)
    followup_tasks = [t for t in tasks if t["name"] == "Follow-up: Task Max Followup"]
    assert len(followup_tasks) == 1
    assert followup_tasks[0]["status"] == "pending"
    assert followup_tasks[0]["scope"]["reviewer_findings"] == "Try a narrower update"


def test_reset_task_for_retry_is_idempotent_when_already_ready(db_conn, tmp_path):
    config = Config()
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)

    run_id = run_repo.create("Retry reset test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(run_id, feat_id, "Task Retry", "implementation", "low")

    task_repo.update_status(task_id, "ready")
    orch._reset_task_for_retry(task_id)
    assert task_repo.get(task_id)["status"] == "ready"

    task_repo.update_status(task_id, "running")
    orch._reset_task_for_retry(task_id)
    assert task_repo.get(task_id)["status"] == "ready"


def test_execution_failure_follow_up_escalates_attempts(db_conn, tmp_path, monkeypatch):
    config = Config()
    config.data["retry_policy"] = {"max_attempts": 1, "escalation_threshold": 1}
    orch = Orchestrator(db_conn, config, plan_path=tmp_path / "plan.md", progress_path=tmp_path / "progress.md")

    run_repo = RunRepository(db_conn)
    feat_repo = FeatureRepository(db_conn)
    task_repo = TaskRepository(db_conn)
    attempt_repo = AttemptRepository(db_conn)

    run_id = run_repo.create("Execution follow-up test", "autonomous")
    feat_id = feat_repo.create(run_id, "Feat 1", "low")
    task_id = task_repo.create(
        run_id,
        feat_id,
        "Task Execution Followup",
        "implementation",
        "low",
        scope={"files": []},
        required_verification="npm test"
    )

    monkeypatch.setattr("agent_loop.orchestrator.create_worktree", MagicMock())
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", MagicMock())
    monkeypatch.setattr(orch, "run_verification", MagicMock(return_value=False))

    with patch("agent_loop.routing.get_adapter") as mock_get_adapter:
        mock_adapter = MagicMock()
        mock_adapter.run_attempt.side_effect = [
            AttemptResult(success=True, exit_code=0, output="implementation done", error=""),
            AttemptResult(success=True, exit_code=0, output='{"decision": "follow_up", "findings": "Fix type errors"}', error="")
        ]
        mock_get_adapter.return_value = mock_adapter

        task_repo.update_status(task_id, "ready")
        assert orch._execute_task_impl(run_id, task_repo.get(task_id)) is False

    task = task_repo.get(task_id)
    assert task["status"] == "ready"
    assert task["scope"].get("extended_limit") is None
    assert task["scope"]["escalation_hint"] == "Fix type errors"
    assert [t for t in task_repo.get_by_run(run_id) if t["name"].startswith("Follow-up:")] == []
    
    attempts = attempt_repo.get_by_run(run_id)
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "escalated"


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

    # Preserved interrupted work should keep the worktree available for retry.
    mock_remove_wt = MagicMock()
    monkeypatch.setattr("agent_loop.orchestrator.remove_worktree", mock_remove_wt)

    with patch("subprocess.run", side_effect=run_side_effect):
        # Trigger recovery
        orch.reconcile_interrupted_run(run_id)

    mock_remove_wt.assert_not_called()

    # Verify that patch file was written and is inspectable
    # Patch path is stored in the database for the attempt
    attempt = attempt_repo.get(attempt_id)
    assert attempt["outcome"] == "abandoned"
    assert attempt["patch_path"] is not None
    assert attempt["worktree_path"] == str(wt_dir)
    assert attempt["retry_strategy"] == "apply_patch_to_clean_branch"

    patch_file = Path(attempt["patch_path"])
    assert patch_file.exists()
    assert patch_file.read_text() == dummy_diff
