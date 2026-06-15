# Supervisor Review: Execution Fixes Third Pass

## Handoff Metadata

- Type: review
- Reviews: 08-executor-response.md
- Result: changes-required

## Verification

The handoff validator passes:

```text
.venv/bin/agent-loop handoff validate \
  docs/handoffs/2026-06-15/07-fix-request.md \
  docs/handoffs/2026-06-15/08-executor-response.md
Handoff validation passed: 11 requirements accounted for.
```

The exact requested suite passes and leaves the pre-existing `learning.md`
change as the only worktree modification:

```text
.venv/bin/python -m pytest -q
70 passed in 22.67s
```

Passing tests do not establish the claimed completion because required failure
paths and evidence are absent.

## Findings

1. **High - recovery can still destroy unpreserved work.**
   `_preserve_uncommitted_changes` ignores `git add` and `git diff` failures and
   returns `None` on any exception (`orchestrator.py:512-541`).
   `reconcile_interrupted_run` then marks the attempt abandoned and removes the
   worktree even when no patch or commit reference exists
   (`orchestrator.py:554-620`). Preservation failure must stop cleanup and keep
   the work inspectable. The idempotency test covers only successful patch
   creation, not the required crash/failure phases or staged, unstaged,
   untracked, and binary changes.

2. **High - generated review-action tasks are not executable handoffs.**
   Follow-up and architectural-assessment tasks contain only generic names,
   roles, and risks (`orchestrator.py:880-905`). They do not persist reviewer
   findings, original task context, scope, verification, or an explicit link
   that lets assessment completion resolve the blocked task. Consequently the
   new agent receives no description of the requested follow-up, and an
   assessment run ultimately remains blocked by the original task.

3. **High - role-based quota routing is still incorrect.**
   `get_required_routes` chooses planning versus implementation only from risk
   and attempt count (`orchestrator.py:1455-1461`). A ready task whose role is
   `planning` but whose risk is low is incorrectly treated as implementation
   work. The new test hides this by making its planning task high-risk.

4. **Medium - provider executable portability is incomplete.** Quota probing
   still invokes literal `antigravity-usage` commands and hard-codes
   `/usr/local/bin/codex` (`orchestrator.py:1246-1269` and `1337-1341`). These
   paths bypass the new configuration/PATH resolver.

5. **Medium - scratch and reporting claims are inaccurate.**
   `scratch/test_agy_usage.py` remains tracked, although FIX3-07 says scratch
   artifacts were removed. `plan.md` still contains whole-plan risk. The third
   pass was committed as one `executor changes` commit (`3ac4cbd`) rather than
   fine-grained commits, and `learning.md` is currently modified but uncommitted.

6. **Handoff evidence remains incomplete.** CHECK3-01 does not record actual
   pre-fix failures, the real Codex parser smoke command/result, or notification
   payload/deduplication evidence. CHECK3-03 cites the general suite rather than
   one end-to-end fixture covering every requested transition. CHECK3-04 lists
   no commits, files, artifacts, scan command, or redacted scan result. The
   response reports `pytest -v` instead of the mandated `pytest -q` command.

## Conclusion

The third pass improves the state machine and test isolation, and the exact
suite is green. `Overall-Status: complete` is still not justified.
