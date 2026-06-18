import subprocess
from pathlib import Path
import pytest
from agent_loop.git_utils import (
    init_git_repo,
    ensure_git_repository,
    ensure_initial_commit,
    create_worktree,
    remove_worktree,
    commit_changes,
    merge_branch,
    run_git
)

def test_git_operations(tmp_path):
    repo_path = tmp_path / "main_repo"
    repo_path.mkdir()
    
    # Initialize main repo
    init_git_repo(repo_path)
    
    # Check that it has a commit
    res = run_git(["log", "-n", "1", "--oneline"], repo_path)
    assert "Initial commit" in res.stdout
    
    # Create worktree
    wt_path = tmp_path / "worktrees" / "task-branch"
    create_worktree(repo_path, wt_path, "feature/task-1")
    assert wt_path.exists()
    assert (wt_path / "README.md").exists()
    
    # Make change in worktree
    new_file = wt_path / "code.py"
    new_file.write_text("print('hello')\n")
    
    sha = commit_changes(wt_path, "Add code.py")
    assert sha is not None
    
    # Verify commit exists on the branch
    res_log = run_git(["log", "-n", "1", "--oneline"], wt_path)
    assert "Add code.py" in res_log.stdout
    
    # Remove worktree
    remove_worktree(repo_path, wt_path)
    assert not wt_path.exists()
    
    # Merge branch into main
    merge_success, _ = merge_branch(repo_path, "feature/task-1", "main")
    assert merge_success is True
    
    # Verify file is merged into main repo
    assert (repo_path / "code.py").exists()

def test_ensure_initial_commit_bootstraps_empty_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    repo_path = tmp_path / "empty_repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)

    before = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo_path, capture_output=True, text=True)
    assert before.returncode != 0

    created = ensure_initial_commit(repo_path)
    assert created is True
    assert run_git(["config", "--local", "user.name"], repo_path).stdout.strip() == "Agent Loop"
    assert run_git(["config", "--local", "user.email"], repo_path).stdout.strip() == "agent-loop@local"
    assert (repo_path / ".gitignore").read_text() == ".agent-loop/\n"
    assert ".gitignore" in run_git(["ls-tree", "--name-only", "HEAD"], repo_path).stdout.splitlines()

    after = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo_path, capture_output=True, text=True)
    assert after.returncode == 0

    wt_path = tmp_path / "worktrees" / "task-branch"
    create_worktree(repo_path, wt_path, "feature/task-1")
    assert wt_path.exists()

    created_again = ensure_initial_commit(repo_path)
    assert created_again is False

def test_ensure_initial_commit_preserves_existing_git_identity(tmp_path):
    repo_path = tmp_path / "empty_repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    run_git(["config", "user.name", "Existing User"], repo_path)
    run_git(["config", "user.email", "existing@example.com"], repo_path)

    created = ensure_initial_commit(repo_path)
    assert created is True
    assert run_git(["config", "--local", "user.name"], repo_path).stdout.strip() == "Existing User"
    assert run_git(["config", "--local", "user.email"], repo_path).stdout.strip() == "existing@example.com"

def test_ensure_initial_commit_uses_global_git_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "home").mkdir()

    subprocess.run(["git", "config", "--global", "user.name", "Global User"], check=True, capture_output=True)
    subprocess.run(["git", "config", "--global", "user.email", "global@example.com"], check=True, capture_output=True)

    repo_path = tmp_path / "empty_repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)

    created = ensure_initial_commit(repo_path)
    assert created is True
    assert run_git(["config", "user.name"], repo_path).stdout.strip() == "Global User"
    assert run_git(["config", "user.email"], repo_path).stdout.strip() == "global@example.com"

    local_name = subprocess.run(["git", "config", "--local", "user.name"], cwd=repo_path, capture_output=True, text=True)
    local_email = subprocess.run(["git", "config", "--local", "user.email"], cwd=repo_path, capture_output=True, text=True)
    assert local_name.returncode != 0
    assert local_email.returncode != 0

def test_ensure_initial_commit_skips_repos_with_head(tmp_path):
    repo_path = tmp_path / "repo_with_head"
    repo_path.mkdir()
    init_git_repo(repo_path)
    initial_head = run_git(["rev-parse", "HEAD"], repo_path).stdout.strip()

    created = ensure_initial_commit(repo_path)
    assert created is False
    assert run_git(["rev-parse", "HEAD"], repo_path).stdout.strip() == initial_head
    assert not (repo_path / ".gitignore").exists()

def test_ensure_git_repository_skips_existing_repo(tmp_path):
    repo_path = tmp_path / "existing_repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)

    created = ensure_git_repository(repo_path)
    assert created is False

def test_ensure_git_repository_initializes_missing_repo(tmp_path):
    repo_path = tmp_path / "missing_repo"
    repo_path.mkdir()

    created = ensure_git_repository(repo_path)
    assert created is True
    assert run_git(["rev-parse", "--is-inside-work-tree"], repo_path).stdout.strip() == "true"

def test_git_merge_conflict(tmp_path):
    repo_path = tmp_path / "main_repo"
    repo_path.mkdir()
    init_git_repo(repo_path)
    
    # Branch 1 edits README
    wt1 = tmp_path / "wt1"
    create_worktree(repo_path, wt1, "branch-1")
    (wt1 / "README.md").write_text("# Test Repo\nEdition 1\n")
    commit_changes(wt1, "Edit README on branch 1")
    remove_worktree(repo_path, wt1)
    
    # Branch 2 edits README differently
    wt2 = tmp_path / "wt2"
    create_worktree(repo_path, wt2, "branch-2")
    (wt2 / "README.md").write_text("# Test Repo\nEdition 2\n")
    commit_changes(wt2, "Edit README on branch 2")
    remove_worktree(repo_path, wt2)
    
    success1, _ = merge_branch(repo_path, "branch-1", "main")
    assert success1 is True
    
    # Merge branch-2 into main: conflicts
    success2, conflicting_files = merge_branch(repo_path, "branch-2", "main")
    assert success2 is False
    assert "README.md" in conflicting_files
