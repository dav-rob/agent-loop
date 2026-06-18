import subprocess
from pathlib import Path
from typing import Optional

DEFAULT_GITIGNORE_ENTRY = ".agent-loop/"

def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True
    )

def init_git_repo(repo_path: Path) -> None:
    run_git(["init", "-b", "main"], repo_path)
    run_git(["config", "user.name", "Agent Loop Test"], repo_path)
    run_git(["config", "user.email", "test@agentloop.local"], repo_path)
    # Create an initial commit
    readme = repo_path / "README.md"
    readme.write_text("# Test Repo\n")
    run_git(["add", "README.md"], repo_path)
    run_git(["commit", "-m", "Initial commit"], repo_path)

def ensure_git_repository(repo_path: Path) -> bool:
    try:
        run_git(["rev-parse", "--is-inside-work-tree"], repo_path)
        return False
    except subprocess.CalledProcessError:
        run_git(["init", "-b", "main"], repo_path)
        return True

def ensure_default_gitignore(repo_path: Path) -> bool:
    gitignore_path = repo_path / ".gitignore"
    existing = gitignore_path.read_text() if gitignore_path.exists() else ""
    lines = existing.splitlines()
    if DEFAULT_GITIGNORE_ENTRY in {line.strip() for line in lines}:
        return False

    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    gitignore_path.write_text(prefix + DEFAULT_GITIGNORE_ENTRY + "\n")
    return True

def ensure_initial_commit(repo_path: Path) -> bool:
    try:
        run_git(["rev-parse", "--verify", "HEAD"], repo_path)
        return False
    except subprocess.CalledProcessError:
        pass

    try:
        run_git(["config", "user.name"], repo_path)
    except subprocess.CalledProcessError:
        run_git(["config", "user.name", "Agent Loop"], repo_path)
    try:
        run_git(["config", "user.email"], repo_path)
    except subprocess.CalledProcessError:
        run_git(["config", "user.email", "agent-loop@local"], repo_path)

    if ensure_default_gitignore(repo_path):
        run_git(["add", ".gitignore"], repo_path)

    run_git(["commit", "--allow-empty", "-m", "agent-loop: initialize repository"], repo_path)
    return True

def create_worktree(repo_path: Path, worktree_path: Path, branch_name: str, base_commit: str = "main") -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    # Check if branch exists
    try:
        run_git(["show-ref", "--verify", f"refs/heads/{branch_name}"], repo_path)
        branch_exists = True
    except subprocess.CalledProcessError:
        branch_exists = False

    if branch_exists:
        # Create worktree using existing branch
        run_git(["worktree", "add", str(worktree_path), branch_name], repo_path)
    else:
        # Create worktree creating a new branch
        run_git(["worktree", "add", "-b", branch_name, str(worktree_path), base_commit], repo_path)

def remove_worktree(repo_path: Path, worktree_path: Path) -> None:
    if not worktree_path.exists():
        return
    try:
        run_git(["worktree", "remove", "--force", str(worktree_path)], repo_path)
    except subprocess.CalledProcessError:
        pass

def commit_changes(worktree_path: Path, message: str) -> Optional[str]:
    # Check if there are changes
    res_status = run_git(["status", "--porcelain"], worktree_path)
    if not res_status.stdout.strip():
        return None  # No changes to commit
        
    run_git(["add", "-A"], worktree_path)
    run_git(["commit", "-m", message], worktree_path)
    res_sha = run_git(["rev-parse", "HEAD"], worktree_path)
    return res_sha.stdout.strip()

def merge_branch(repo_path: Path, source_branch: str, target_branch: str) -> tuple[bool, list[str]]:
    # Checkout target branch
    run_git(["checkout", target_branch], repo_path)
    try:
        # Merge source_branch
        run_git(["merge", "--no-ff", "-m", f"Merge branch {source_branch}", source_branch], repo_path)
        return True, []
    except subprocess.CalledProcessError as e:
        # Extract conflicting files
        import re
        conflicting_files = []
        combined = (e.stdout or "") + "\n" + (e.stderr or "")
        for line in combined.splitlines():
            match = re.search(r"CONFLICT.*in\s+(.+)$", line)
            if match:
                conflicting_files.append(match.group(1).strip())
        # Merge conflict occurred, abort the merge if one is in progress
        try:
            run_git(["merge", "--abort"], repo_path)
        except subprocess.CalledProcessError:
            pass # No merge in progress to abort
        return False, conflicting_files
