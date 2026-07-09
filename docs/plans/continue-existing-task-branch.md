# Continue Existing Task Branch Plan

## Problem

`agent-loop` currently creates a new worktree and branch for each task attempt:

- `worktrees/run-1-task-10-attempt-26`
- `worktrees/run-1-task-10-attempt-28`
- `worktrees/run-1-task-10-attempt-32`

This preserves textual memory through review findings, timeout handovers, and logs, but it does not preserve code state by default. A retry often starts from `main` and asks the next model to recreate or repair work from prose.

That is the wrong default for normal review feedback. Human review usually means “continue from this branch and fix these issues”, not “start again from scratch”.

## Goal

Make the default retry behavior continue on the same task branch/worktree, while keeping explicit escape hatches for cases where restarting is safer.

## Desired Model

Use one durable branch/worktree per task:

```text
branch:   agent-loop-run-1-task-10
worktree: worktrees/run-1-task-10

attempt 1 starts at SHA A, ends at SHA B
review rejects
attempt 2 starts at SHA B, ends at SHA C
review rejects
attempt 3 starts at SHA C, ends at SHA D
review approves
merge task branch to main
```

Attempts remain separate DB records, but they operate on the same evolving task branch unless a retry strategy explicitly chooses otherwise.

## Retry Strategies

Add an explicit retry strategy concept. Initial set:

- `continue_existing_branch`: default after normal reviewer rejection or timeout with useful work.
- `restart_from_main`: use when prior work is bad, corrupt, irrelevant, or explicitly rejected as the wrong approach.
- `restart_from_last_good_commit`: use when the current branch has gone bad but an earlier attempt commit was useful.
- `apply_patch_to_clean_branch`: use when only a preserved patch exists and the branch/worktree cannot be trusted.
- `block_for_human`: use when automated recovery is unsafe or ambiguous.

Record the selected strategy in lifecycle events and, ideally, in DB attempt metadata.

## Reviewer Prompt Change

Reviewers should be told they are reviewing an existing task branch, not a disposable attempt.

Add guidance like:

```text
You are reviewing a task branch that will normally be continued on the next attempt.
Prefer precise repair instructions that can be applied to the current branch.

Classify your guidance:
- continue: keep the branch and fix these specific issues.
- partial_revert: keep these parts, remove or replace these parts.
- restart: discard this approach and restart from a clean base because ...
- block: stop for human decision.

Reject only for blocking issues. If you recommend restart, explain why continuing the branch is unsafe or wasteful.
```

The JSON review schema can stay compatible initially by keeping `decision`, but include a parseable `retry_strategy` field later:

```json
{
  "decision": "rejected",
  "retry_strategy": "continue_existing_branch",
  "findings": "..."
}
```

During migration, default missing `retry_strategy` to `continue_existing_branch` for `rejected` and to existing behavior for incompatible states.

## Executor Prompt Change

For continued attempts, make the prompt explicit:

```text
This is a continuation attempt on the existing task branch.
Do not rebuild from scratch.
Read the current diff and commits first.
Apply the reviewer feedback as a targeted repair.
If you believe the branch should be discarded, explain that in the handover and stop after making no broad rewrite.
```

For restart strategies, say the opposite explicitly:

```text
This attempt intentionally starts from a clean base because ...
Do not assume previous files are present except as evidence in logs/handoffs.
```

## Orchestrator Changes

### 1. Stable Branch And Worktree Names

Change default task execution from attempt-scoped names:

```text
branch_name = agent-loop-run-{run_id}-task-{task_id}-att-{attempt_id}
worktree = worktrees/run-{run_id}-task-{task_id}-attempt-{attempt_id}
```

to task-scoped names:

```text
branch_name = agent-loop-run-{run_id}-task-{task_id}
worktree = worktrees/run-{run_id}-task-{task_id}
```

Keep attempt-scoped logs:

```text
.agent-loop/logs/{run_id}/{task_id}/{attempt_id}/
```

### 2. Attempt Start SHA

Each attempt should record:

- `start_sha`
- `end_sha` / existing `commit_sha`
- `retry_strategy`
- `base_branch` or `base_sha`

If schema migration is too much for the first patch, write these into lifecycle event metadata first, then migrate DB columns later.

### 3. Worktree Creation Logic

Pseudo-flow:

```python
branch_name = task_branch_name(run_id, task_id)
worktree_dir = task_worktree_dir(run_id, task_id)

strategy = select_retry_strategy(run_id, task_id, task, previous_attempts, latest_review)

if strategy == "continue_existing_branch":
    if worktree exists:
        use it
    elif branch exists:
        git worktree add worktree branch
    else:
        git worktree add -b branch worktree main

elif strategy == "restart_from_main":
    archive/delete old task branch/worktree
    git worktree add -b branch worktree main

elif strategy == "restart_from_last_good_commit":
    git worktree add -B branch worktree selected_commit

elif strategy == "apply_patch_to_clean_branch":
    git worktree add -B branch worktree main
    git apply preserved_patch
```

Important: before reusing an existing worktree, inspect it:

- `git status --porcelain`
- current branch
- current HEAD
- whether a rebase/merge is in progress

If dirty, either commit with an attempt checkpoint message or preserve a patch and choose a recovery strategy.

### 4. Commit Behavior

Before each attempt starts, record `start_sha`.

After the executor returns:

- commit any uncommitted changes as today.
- record `end_sha`.
- review diff should be `start_sha..end_sha` for attempt-specific review.
- task/feature review can inspect the full task branch against main.

### 5. Review Rejection Behavior

Current rejected behavior:

```python
_reset_task_for_retry(task_id)
remove_worktree(...)
```

New default:

```python
record retry_strategy
_reset_task_for_retry(task_id)
keep worktree and branch
```

Only remove/archive the worktree when:

- task is approved and merged.
- retry strategy is restart.
- task is blocked/cancelled.
- human cleanup command is run.

### 6. Timeout Behavior

If executor times out:

- preserve patch as today.
- synthesize timeout handover as today.
- inspect whether branch has commits after `start_sha`.
- default to `continue_existing_branch` if:
  - branch has commits, or
  - patch exists, or
  - logs show useful file changes.
- timeout review can override to `restart_from_main` or `block_for_human`.

Do not delete the worktree immediately if it contains useful code. Keep it for the next retry unless restart/block is chosen.

### 7. Resume Behavior

Current `resume` marks running attempts abandoned and removes their worktrees.

Change this carefully:

- If a running attempt belongs to a durable task worktree:
  - preserve patch.
  - mark attempt abandoned.
  - leave task branch/worktree in place if it has useful committed or uncommitted work.
  - reset task to ready.
- If worktree is corrupt or has merge/rebase state:
  - preserve patch.
  - archive/remove worktree.
  - choose `apply_patch_to_clean_branch` or `restart_from_main`.

This is a high-risk area and needs focused tests.

## Merge And Parallelism

For parallel tasks, each task still has its own task branch/worktree. That isolation remains.

When a task is approved:

1. Merge task branch to `main`.
2. If merge succeeds, mark complete and remove task worktree.
3. If merge conflicts, create an integration task as today.

Long-lived task branches may drift as other tasks merge. Before each retry on an existing branch, consider merging current `main` into the task branch:

- Safe default: do not auto-merge before every retry in the first implementation.
- Later improvement: if task branch is behind main and touched files do not overlap, auto-merge main.
- If conflicts arise, create an integration task or ask executor to resolve.

## Tests

Add or update focused tests:

1. Rejected attempt keeps task worktree and branch.
2. Next attempt starts from previous rejected attempt `commit_sha`.
3. Review diff uses attempt `start_sha..end_sha`, not full branch history.
4. Approved task merges task branch and removes task worktree.
5. Timeout with commits preserves branch and retries from branch HEAD.
6. Timeout with only patch records patch and applies or references it according to strategy.
7. `resume` marks running attempt abandoned but does not delete useful durable task worktree.
8. `restart_from_main` explicitly archives/deletes prior task worktree and starts clean.
9. Integration-task behavior still works when approved durable branches conflict with `main`.

## Migration Plan

### Phase 1: Minimal Durable Branch

- Use task-scoped branch/worktree names for new attempts.
- Keep worktree on rejected review.
- Remove worktree only on approved merge/block/restart.
- Record start/end SHAs in lifecycle metadata if DB migration is deferred.
- Update prompts to say continuation vs restart.

### Phase 1.5: Typed Follow-Ups And Recovery Closure

Live continuity testing showed the durable branch behavior working, but exposed
a bug in merge-conflict recovery: an approved task can hit merge conflicts,
spawn a conflict-resolution task, and then remain blocked even after the
conflict-resolution chain completes successfully.

Fix this before the larger retry-strategy phase by making follow-up semantics
explicit:

- `non_blocking_follow_up`: the original task is complete enough to proceed.
  Mark the original task complete, create the follow-up as separate dependent
  work, and allow downstream tasks to run.
- `blocking_recovery_follow_up`: the original task is still blocked until the
  recovery chain completes. Preserve `origin_task_id` across all recovery
  follow-ups, not just the immediate parent task.
- `retry_extension_follow_up`: the original task needs another guided attempt
  rather than a separate downstream task. Keep this as retry/escalation
  behavior, not a normal task dependency.

Merge-conflict tasks should use `blocking_recovery_follow_up` semantics:

1. When an approved task fails to merge, mark the original task blocked with a
   merge-conflict reason and create a recovery task with `origin_task_id`.
2. Create recovery and recovery-follow-up tasks with implementation-style
   routing when they are expected to edit files, resolve conflicts, regenerate
   lockfiles, or run verification. Do not route file-changing recovery work as
   pure planning just because it was spawned by an integration path.
3. If the recovery task itself creates follow-up work, copy the same
   `origin_task_id` into the follow-up scope.
4. When the recovery chain reaches an approved terminal state, mark the
   original task complete, unblock dependents, and continue scheduling.
5. If recovery fails or is explicitly blocked, keep the original task blocked
   with the latest recovery findings.

Add focused regressions:

- Approved task with merge conflict creates a blocking recovery task linked to
  the original task.
- Successful recovery task marks the original task complete.
- Recovery task with non-blocking follow-up keeps `origin_task_id` through the
  follow-up chain.
- Successful recovery follow-up closes the original blocked task and allows
  dependent tasks to become ready.
- Normal non-blocking reviewer follow-up still marks the original task complete
  immediately and creates a separate dependent follow-up task.
- Merge-conflict and recovery-follow-up tasks that edit files are classified
  and routed as implementation/executor work, not planner-only work.

### Phase 2: Retry Strategy And Recovery Review

Retry strategy metadata and patch/resume recovery are one mechanism, not two
separate systems. The orchestrator gathers evidence, a reviewer or recovery
reviewer chooses a strategy, and the orchestrator records and executes that
strategy.

- Add DB support for retry strategy/start SHA/base SHA.
- Gather recovery evidence for rejected, timed-out, interrupted, or dirty
  worktree states:
  - current branch and HEAD
  - committed changes since attempt start
  - uncommitted patch
  - merge/rebase state
  - changed files
  - provider logs and synthesized handover
- Parse optional `retry_strategy` from task reviews and timeout/recovery
  reviews.
- Default missing `retry_strategy` to `continue_existing_branch` for ordinary
  rejected reviews.
- Execute the selected strategy:
  - `continue_existing_branch`: keep the task branch/worktree.
  - `restart_from_main`: archive/remove current branch/worktree and recreate
    from `main`.
  - `restart_from_last_good_commit`: restart from a selected useful attempt
    commit.
  - `apply_patch_to_clean_branch`: recreate clean and apply the preserved
    patch.
  - `block_for_human`: block with evidence.
- Display strategy and evidence in status/progress/handoffs.
- Improve `resume` to keep useful durable task worktrees and route ambiguous
  or unsafe states through the same strategy decision.

### Phase 3: Operator Restart/Archive Commands

- Add CLI commands only as thin wrappers over the same retry strategy executor.
- Support forcing restart/archive of a task branch when continuation is clearly
  wrong.
- Defer broader UX if it starts introducing a second orchestration path.

### Phase 4: Review Tuning

- Update reviewer prompts to provide continuation-oriented guidance.
- Add tests around `continue`, `partial_revert`, `restart`, and `block` guidance.

## Open Questions

- Should task worktrees be kept after completion for inspection, or removed after merge as now?
- Should retries auto-merge current `main` into the durable task branch before each attempt?
- How should the system score “useful work” after timeout: commits only, patch size, files touched, successful tests, or timeout review decision?
- Should the user get a CLI command to force a task restart from main?

## Initial Recommendation

Implement Phase 1 first. It should remove the worst retry churn with the smallest conceptual change:

- One branch/worktree per task.
- Rejected attempts continue that branch.
- Approved attempts merge and clean up.
- Restart remains possible but explicit.

This gives the reviewer and executor a natural human-style workflow without overbuilding the full retry strategy system immediately.
