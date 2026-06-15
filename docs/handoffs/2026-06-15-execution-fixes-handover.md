# Agent Loop Execution Fixes Handover

Date: 2026-06-15

## Read First

Read these files before changing code:

- `docs/specs/2026-06-15-agent-loop-orchestrator-design.md`
- `plan.md`
- this handover

The current implementation is a useful scaffold, but it is not ready for
unattended execution. Existing tests pass but do not cover several critical
runtime paths. Add regression tests for each fix below.

## Revised Decisions

### Execution Trust Model

Do not attempt to enforce a common workspace-only sandbox across Codex, `agy`,
verification commands, Git, and build tools in this iteration.

Add an explicit configuration mode:

```toml
execution_mode = "trusted-host"
```

Meaning:

- Agents and verification commands inherit the current user's filesystem and
  network permissions.
- The operator is responsible for running on a suitable Mac, VM, dedicated
  machine, or other trusted environment.
- Startup must print this warning once:

  ```text
  Trusted-host execution: commands can access anything available to the current user.
  ```

- Remove misleading workspace-sandbox guarantees from the current adapters.
- Keep subprocess working directories set to the task worktree for predictable
  relative paths, but do not describe `cwd` as a security boundary.
- Continue to stop for missing credentials, deployment, and explicitly
  destructive product actions where the orchestrator can identify them.

`workspace-sandbox` may be a future execution mode. It is out of scope for this
fix pass.

### Antigravity Quota Source

Use the optional third-party `antigravity-usage` CLI as the primary structured
quota source for `agy` routes.

Verified locally:

```text
antigravity-usage 0.2.9
```

Machine-readable command:

```bash
antigravity-usage quota --json --method google
```

Use `--refresh` after an `agy` quota error and when a recorded reset time is
reached. Do not force refresh on every scheduler pass because the tool normally
caches quota data for five minutes.

Relevant JSON fields:

```json
{
  "timestamp": "2026-06-15T15:02:24.687Z",
  "method": "google",
  "email": "user@example.com",
  "models": [
    {
      "label": "Gemini 3.5 Flash (High)",
      "modelId": "gemini-3-flash-agent",
      "remainingPercentage": 0.6106822,
      "isExhausted": false,
      "resetTime": "2026-06-15T17:56:15Z",
      "timeUntilResetMs": 10430325,
      "isAutocompleteOnly": false
    }
  ]
}
```

Requirements:

- Capability-detect `antigravity-usage`; do not make it a hard package
  dependency.
- Match configured routes by exact label first. Preserve `modelId` in the quota
  snapshot. Duplicate labels may exist; treat a route as exhausted only when all
  matching non-autocomplete entries are exhausted.
- Distinguish missing login or expired authentication from quota exhaustion.
- If login is required, block with instructions to run
  `antigravity-usage login`.
- Never read or log its token files. Never include OAuth tokens in logs.
- If the tool is absent or fails, fall back to classifying actual `agy` output.
- For unknown resets, use exponential probe backoff rather than a tight loop.
  Suggested intervals: 15, 30, 60, then 120 minutes maximum.
- CLI installation or `agy models` success is not evidence that model quota is
  available.

Codex quota should use `account/rateLimits/read` from Codex App Server where
available. It exposes remaining or used percentages, reset timestamps, and
window durations. Retain JSONL error classification as a fallback.

## Blocking Fixes

Implement in this order.

### 1. Verification Execution

Current issue: `run_verification()` references `subprocess` without importing
it, so real verification records exit `-1`.

Required:

- Import and execute subprocesses correctly.
- In `trusted-host` mode, run the configured command from the absolute task
  worktree path.
- Record real exit code, duration, stdout path, and stderr path.
- A missing or failed required verification must prevent commit approval and
  integration.
- Avoid `shell=True` where a structured command is available. If shell commands
  remain supported, document that they run with trusted-host privileges.

### 2. Reviews Must Fail Closed

Current issues:

- Provider failures and exceptions are recorded as approved reviews.
- Final-review rejection is ignored and the run still becomes `complete`.

Required:

- Provider failure, malformed review output, timeout, or exception must not
  approve a review.
- Validate review output against a small explicit schema.
- Task rejection returns the task for a new attempt with findings included in
  its handover.
- Feature rejection creates follow-up work or blocks; it cannot be silently
  ignored.
- Final review must approve before completion.
- Persist reviewer provider, model, decision, findings, and evidence paths.

### 3. Absolute Runtime Paths

Current task worktree and log paths are relative while subprocess `cwd` is
changed, causing doubled or misplaced paths.

Required:

- Resolve repository root, worktree, database, logs, schema, and output paths to
  absolute paths before launching workers.
- Pass absolute worktree paths to Codex `--cd` and `agy --add-dir`.
- Pass absolute output and log paths.
- Add command-construction tests that assert absolute paths.

### 4. Safe Concurrency And Git Integration

Current issues:

- Worker threads share one SQLite connection.
- `check_same_thread=False` removes a guard but does not make shared connection
  usage safe.
- Workers can concurrently checkout and merge against the same main worktree.

Required:

- Give each worker its own SQLite connection, or route all database writes
  through one serialized coordinator.
- Keep worker editing and testing parallel.
- Serialize integration into the run branch with an integration lock or single
  coordinator.
- Never run concurrent `git checkout` or `git merge` against the same worktree.
- Add a deterministic two-worker test that proves overlap and safe serialized
  integration.

### 5. Recovery

Current issue: resume abandons attempts but leaves associated tasks `running`,
which can create phantom workers and an endless wait.

Required:

- Reconcile attempts and tasks together transactionally.
- Mark interrupted attempts `abandoned`.
- Preserve their logs and partial diffs.
- Return retryable tasks to `ready`, or block after the configured limit.
- Reconcile worktrees, branches, and commits idempotently.
- Repeated `resume` calls must not create duplicate attempts, merges, or
  notifications.

### 6. Versioned Database Upgrade

Current issue: migration 1 was edited in place. Existing version-1 databases
retain the old provider-only quota table.

Required:

- Restore migration 1 as the historical schema or otherwise preserve its
  versioned meaning.
- Add migration 2 to upgrade provider state to provider plus model, preserving
  existing data conservatively.
- Test migration from a real version-1 fixture and test rollback of a failed
  later migration.

### 7. Quota State Machine

Use explicit states:

```text
available
limited_known_reset
limited_unknown_reset
auth_required
transient_failure
unavailable
```

Required:

- Store state per provider and model route.
- Select another route immediately when one route is limited.
- Enter `waiting_for_quota` only when no route needed by ready work is usable.
- Wait until the earliest known reset, then refresh quota.
- Use exponential backoff for unknown reset times.
- Send deduplicated exhausted, recovery, auth-required, and blocked
  notifications.
- Inject a clock and sleeper so tests never wait in real time.

### 8. Merge Conflict Integration Tasks

Current integration tasks lack enough information to resolve the conflict and
the original task is immediately readied again.

Required:

- Persist source branches or commits, target baseline, conflicting files, and
  required combined verification in the integration task context.
- Keep the original task awaiting integration rather than rerunning it.
- Run integration work in a fresh worktree on the high-reasoning route.
- Serialize the resulting merge and record an auditable commit.

### 9. Test Migration Policy

Current detection records any modified test as a migration with identical old
and replacement paths. This does not implement the approved policy.

Required:

- Detect an existing test being skipped or superseded.
- Require a separately added replacement test before accepting the migration.
- Require the skip marker or reason to reference the migration record.
- Never delete the old test automatically.
- Record old path, replacement path, behavior change, rationale, evidence, task,
  and commit.
- End the run as `complete_pending_test_review` while any migration is pending.
- Final output must start with `TEST BASELINE CHANGES` when migrations exist.

### 10. Intake And Approval

Required:

- Make `--intake` effective.
- Implement concise brainstorm interaction.
- Invoke the UI Lab brief flow only for the UI Lab intake choice.
- Add a plan approval command or interaction that can move
  `awaiting_plan_approval` to `running`.
- Keep autonomous and non-interactive modes unattended.

### 11. Reasoning Configuration

Required:

- Pass `model_reasoning_effort`, not `reasoning_level`, to Codex.
- Pass reasoning configuration on planning, task, integration, feature review,
  and final review routes.
- Keep `agy` reasoning encoded by its exact model label because its CLI does not
  expose a separate reasoning-level flag.

## Tests To Add

The existing 22 tests must continue to pass, but they are insufficient. Add
focused tests for:

- real verification success and failure
- review provider failure, malformed output, rejection, and final-review gating
- absolute adapter paths
- two parallel workers with serialized database and Git integration
- interrupted attempt recovery and repeated resume
- migration from schema version 1 to version 2
- Codex quota snapshots and reset scheduling
- `antigravity-usage` available, exhausted, duplicate-label, auth-required,
  malformed JSON, absent binary, and refresh behavior
- fake-clock known-reset waiting and unknown-reset exponential backoff
- merge-conflict context and integration lifecycle
- accepted and rejected test migrations
- report ordering with `TEST BASELINE CHANGES` first
- brainstorm, UI Lab, autonomous, non-interactive, and plan approval paths

Store representative quota responses as redacted JSON fixtures. Real exhausted
quota is not required for testing.

## Completion Checks

Before reporting completion:

1. Run plain `.venv/bin/python -m pytest -q` without special `PYTHONPATH` setup.
2. Run the end-to-end fixture project through planning, parallel task work,
   verification, review, serialized integration, interruption, resume, and
   completion.
3. Simulate every quota state using fixtures, a fake clock, and a fake sleeper.
4. Confirm review failure cannot produce `complete`.
5. Confirm a failed required verification cannot be merged.
6. Confirm no OAuth token, Slack webhook URL, or environment secret is present
   in SQLite or logs.
7. Confirm the trusted-host warning is shown and documented.
8. Review the final diff against the original design spec and this handover.

## Scope Discipline

Do not rewrite the whole package. Preserve working repositories, views, CLI
shape, adapter parsing, and Git helpers where they remain suitable. Fix the
runtime contracts and add evidence-driven tests around them.
