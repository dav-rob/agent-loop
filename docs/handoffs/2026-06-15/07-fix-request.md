# Execution Fixes: Third Pass

## Handoff Metadata

- Handoff-ID: 2026-06-15-07
- Type: request

## Execution Contract

Read `docs/handoffs/README.md` and
`docs/handoffs/2026-06-15/06-supervisor-review.md` completely before changing
files. Do not edit, delete, skip, or weaken any existing test without stopping
for human approval. Add new regression tests for behavioral fixes.

Write `docs/handoffs/2026-06-15/08-executor-response.md`, account for every ID
below, and validate it against this request before replying.

### FIX3-01: Implement distinct review actions

Create explicit state-machine behavior for `follow_up`, `assessment`, and
`block`. A follow-up must create auditable follow-up work; assessment must route
to an architectural assessment action; block must record the stop condition.
Keep rejection retry-bounded. Add new tests for every action and the limit.

### FIX3-02: Correct required-route quota gating

Track required route capabilities separately from alternative routes. Resume
only when every capability required by current ready/review work has at least
one usable route. Honor a false quota-recovery result immediately in `run_loop`.
Add tests for simultaneous planning and implementation requirements and for an
auth-required stop that schedules no further work.

### FIX3-03: Make partial-work recovery crash-idempotent

Remove the transaction/filesystem crash window. Repeated recovery after a crash
at any phase must preserve an inspectable patch or commit reference, clean up
the worktree exactly once, and not duplicate attempts or evidence. Cover staged,
unstaged, untracked, and binary changes where practical.

### FIX3-04: Enforce test-migration records fail-closed

Require a real stable migration identifier in the skip reason. Associate the
specific replacement test and behavioral evidence with that identifier. A
detection or persistence error must block completion rather than being swallowed.
Add negative tests for generic `migration` text, unrelated replacement tests,
and detector failure.

### FIX3-05: Make executable resolution portable

Use explicit configuration or `PATH`. Remove user-specific fallback paths and
produce a clear missing-binary result. Add new tests without changing existing
tests; if an existing assertion must change, stop and request approval.

### FIX3-06: Invoke the UI Lab brief workflow

Integrate the documented UI Lab brief workflow rather than substituting a local
questionnaire. Offer it only for UI goals. Cover brainstorm, UI Lab, autonomous,
and non-interactive policy paths with new tests.

### FIX3-07: Isolate tests and update reports

Ensure the exact suite leaves tracked files unchanged by routing generated views
to temporary paths. Update committed `plan.md`, `progress.md`, and other stale
documentation to the current trusted-host behavior and current verification
state. Remove or account for generated `scratch/` artifacts.

### CHECK3-01: Supply complete requirement evidence

For each behavioral change, record the new test's expected pre-fix failure and
post-fix pass. Record the real Codex parser smoke command/result and notification
payload/deduplication tests.

### CHECK3-02: Run the exact complete suite cleanly

Run `.venv/bin/python -m pytest -q`, record the full summary, then prove
`git status --short` is unchanged except for intentional implementation and
handoff files.

### CHECK3-03: Run the end-to-end fixture

Run one fixture covering planning, parallel work, verification, review,
serialized integration, interruption/resume, quota waiting/recovery, regression
verification, and completion. Record state-transition evidence.

### CHECK3-04: Report commits, artifacts, and secret scan

List every created commit and every modified/untracked file. Report generated
artifacts. Run and record a redacted scan of repository files, SQLite state, and
logs for webhook URLs, OAuth tokens, and representative environment secrets.

## Completion Rule

Use `Overall-Status: complete` only if every requirement is complete, protected
tests were not changed without approval, validation passes, and the exact suite
leaves the repository in the reported state.
