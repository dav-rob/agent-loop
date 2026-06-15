# Execution Fixes: Fourth Pass

## Handoff Metadata

- Handoff-ID: 2026-06-15-10
- Type: request

## Execution Contract

Read `docs/handoffs/README.md` and
`docs/handoffs/2026-06-15/09-supervisor-review.md` completely. Treat every
requirement below as mandatory. Do not edit an existing test without explicit
human approval. Add new regression tests for the remaining behavior.

Create fine-grained commits grouped by purpose. Do not use a single aggregate
commit. Write `docs/handoffs/2026-06-15/11-executor-response.md` and validate it
against this request before replying.

### FIX4-01: Fail safely when partial-work preservation fails

Check every preservation command result. Do not abandon or clean a worktree
unless an inspectable patch or commit reference was persisted, or the worktree
is proven clean. Preserve the recovery state across crashes at each phase. Add
new tests for command failure, staged, unstaged, untracked, binary, and repeated
recovery behavior.

### FIX4-02: Make review-action tasks self-contained

Persist reviewer findings and original-task context in follow-up and
architectural-assessment task scope. Preserve relevant file scope and required
verification. Define and test how a completed assessment resolves or replaces
the blocked original task so the run can continue without losing auditability.

### FIX4-03: Route ready work by role and capability

Use task role as well as risk/escalation when determining required capabilities
and execution routes. A low-risk planning task must require and select a
planning route. Add a regression test that would fail under risk-only routing.

### FIX4-04: Use portable executables for quota probes

Resolve Codex and optional `antigravity-usage` binaries through configuration or
`PATH` in quota probing as well as execution adapters. Treat a missing optional
usage tool conservatively without a machine-specific path. Add command and
missing-binary tests.

### FIX4-05: Reconcile artifacts and project reports

Remove `scratch/test_agy_usage.py` if it is only a generated diagnostic, or
document and relocate it if it is intentional. Remove whole-plan risk from the
committed `plan.md`. Commit the approved `learning.md` instruction. Ensure the
repository state is fully reported.

### CHECK4-01: Record red-green and smoke evidence

Record each new regression test's pre-fix failure and post-fix pass. Record the
exact harmless real Codex parser smoke command/result. Record notification
payload completeness and deduplication commands/results.

### CHECK4-02: Run one end-to-end fixture

Run a single named fixture that covers planning, parallel work, verification,
review actions, serialized integration, interruption/resume, quota wait and
recovery, regression verification, and final completion. Record its ordered
state transitions.

### CHECK4-03: Run the exact suite and report repository state

Run `.venv/bin/python -m pytest -q`. Then report every commit created, every
modified/untracked file, and every generated artifact. Prove the suite adds no
unexpected worktree changes.

### CHECK4-04: Run and record the secret scan

Scan repository files, SQLite state, and logs for webhook URLs, OAuth tokens,
and representative environment secrets. Record the exact commands and redacted
results; do not merely state that a scan occurred.

## Completion Rule

Use `Overall-Status: complete` only when all requirements are complete, the
validator passes, the exact suite passes, evidence is concrete, and the reported
repository state matches `git status --short`.
