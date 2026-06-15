import subprocess
from pathlib import Path
import pytest
from agent_loop.git_utils import (
    init_git_repo,
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
