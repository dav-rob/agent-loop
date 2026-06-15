# Execution Fixes: Second Pass

## Handoff Metadata

- Handoff-ID: 2026-06-15-04
- Type: request

## Execution Contract

Read `docs/handoffs/README.md` first, then read this file completely before
changing code. Every requirement ID below is mandatory and must appear exactly
once in the executor response compliance table.

Use test-driven development for behavioral changes. Do not weaken, delete, or
silently rewrite existing tests. If a requirement is genuinely unsuitable for
this pass, mark it `shelved` with a concrete technical reason and evidence of
the investigation. Shelving permits an auditable partial handoff; it does not
permit an overall completion claim.

Write the next response as
`docs/handoffs/2026-06-15/05-executor-response.md`. Run the validator before
replying:

```bash
agent-loop handoff validate \
  docs/handoffs/2026-06-15/04-fix-request.md \
  docs/handoffs/2026-06-15/05-executor-response.md
```

### FIX-01: Repair and smoke-test Codex invocation

Construct a valid unattended `codex exec` command for the installed CLI. Add a
test that checks the complete argument contract, and run one harmless real
adapter smoke test that proves the parser accepts the invocation. Record the
command and result without consuming a substantive task run.

### FIX-02: Bound review rejection and implement review decisions

Ensure repeated task review rejection observes the configured attempt limit.
Handle `follow_up`, `assessment`, and `block` as explicit state-machine actions
rather than generic retries. Add regression tests for each outcome and prove a
review loop cannot run indefinitely.

### FIX-03: Preserve partial work before cleanup

Before removing an interrupted, quota-failed, or otherwise abandoned worktree,
persist an inspectable patch or commit reference and record its path in attempt
evidence. Recovery must remain idempotent. Add a test that creates an
uncommitted change, recovers the run, and verifies the change remains
inspectable after worktree cleanup.

### FIX-04: Gate completion on broader regression verification

Execute the configured regression command after integration and before final
completion. Record it as a test run with output evidence. A failure must prevent
`complete`. Add success and failure tests.

### FIX-05: Complete the quota state machine across all active routes

Distinguish `auth_required`, `transient_failure`, `unavailable`, known reset,
unknown reset, and available states. Determine quota requirements from the
actual ready work, including planning/high-reasoning and review routes. Do not
transition back to execution until a usable required route is confirmed. Never
select a known-unavailable review route. Add fake-clock tests covering failed
refresh, no recovery, planning-route exhaustion, and eventual recovery.

### FIX-06: Complete the test-migration approval workflow

Require a migration identifier in the old test's skip reason and evidence that
the replacement test covers the stated behavior. Persist previous behavior,
replacement behavior, rationale, task, and commit. Add CLI commands to approve
or reject pending migrations and transition the run accordingly. Add acceptance
and rejection tests.

### FIX-07: Provide operational quota and lifecycle notifications

Quota alerts must include run/task identifiers where applicable, provider,
model, classification, evidence paths, known reset times and windows, fallback
action, and expected resume behavior. Add deduplicated notifications for
recovery, blocked runs, pending test review, and completion. Test payload content
without exposing webhook or OAuth secrets.

### FIX-08: Align intake with interactive and unattended contracts

Offer UI Lab only when the goal includes UI work and invoke the documented UI
Lab brief workflow rather than substituting arbitrary styling questions.
Non-interactive operation must not stop for conversational approval; record the
chosen unattended policy explicitly. Add coverage for brainstorm, UI Lab,
autonomous, and non-interactive paths.

### FIX-09: Make provider execution portable and timeout-aware

Resolve `codex`, `agy`, and optional `antigravity-usage` executables from
configuration or `PATH`, while allowing explicit overrides. Pass an `agy
--print-timeout` consistent with the subprocess timeout. Add command-construction
and missing-binary tests.

### FIX-10: Enforce configuration and reporting decisions

Reject or clamp `max_workers` values above four. Remove whole-plan risk from
`plan.md`; retain risk only on features and tasks. Update stale documentation
that still claims workspace sandboxing or reports the previous completion
state.

### CHECK-01: Run focused tests for every changed subsystem

Record the exact focused commands and results. New tests must fail for the
expected reason before their implementation is added.

### CHECK-02: Run the complete suite without environment workarounds

Run exactly:

```bash
.venv/bin/python -m pytest -q
```

Record the complete pass/fail summary.

### CHECK-03: Run an end-to-end fixture workflow

Exercise planning, parallel work, verification, review, serialized integration,
interruption, resume, quota waiting/recovery, regression verification, and final
completion using fake providers where necessary. Record the fixture command and
state-transition evidence.

### CHECK-04: Report repository and secret-scan state

Report all commits created, all remaining modified/untracked files, and any
generated artifacts. Scan SQLite and logs for webhook URLs, OAuth tokens, and
representative environment secrets; record the command and redacted result.

## Completion Rule

An overall `complete` response is valid only when every requirement above is
`complete`, all evidence fields are populated, and `agent-loop handoff validate`
passes. Otherwise use `partial` or `blocked` and state exactly what remains.
