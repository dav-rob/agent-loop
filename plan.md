# Agent Loop Implementation Plan

## Objective

Implement the provider-neutral, resumable agent-loop orchestrator defined in
[`docs/specs/2026-06-15-agent-loop-orchestrator-design.md`](docs/specs/2026-06-15-agent-loop-orchestrator-design.md).

> This file is a human-readable execution summary. Full task metadata,
> dependencies, attempts, and evidence will be stored in the agent-loop SQLite
> database. Run `agent-loop plan --details` to inspect them once that command is
> implemented.

## Execution Rules

- Work milestone by milestone and keep commits task-sized.
- Add tests before or alongside behavior changes; do not weaken existing tests.
- Use medium reasoning for routine implementation.
- Escalate schema design, concurrency, worktree integration, provider failover,
  quota recovery, and final review to a high-reasoning route.
- Keep model names, routes, limits, paths, and timeouts configurable.
- Preserve the approved spec unless implementation evidence requires a design
  change; record any such decision explicitly.

## Milestones

### 1. Foundation And State

- [ ] Establish the Python package, CLI entry point, configuration model, and
  test harness.
- [ ] Add the versioned SQLite schema and transactional migration mechanism.
- [ ] Implement repositories and explicit state transitions for runs, features,
  tasks, attempts, tests, reviews, provider state, notifications, decisions, and
  test migrations.
- [ ] Add generated `plan.md` and `progress.md` views plus
  `agent-loop plan --details`.
- [ ] Verify schema migration, persistence, invalid transitions, and rendering.

### 2. Provider And Logging Layer

Depends on milestone 1.

- [ ] Define the provider adapter contract and normalized attempt result.
- [ ] Implement runtime capability discovery for Codex and `agy`.
- [ ] Implement Codex execution through `codex exec` with JSONL capture.
- [ ] Implement `agy` execution with explicit workspace, timeout, and log file.
- [ ] Add structured log metadata, append-only attempt artifacts, and secret
  redaction.
- [ ] Verify command construction, output classification, cancellation, and
  unavailable or unknown capability handling without live quota consumption.

### 3. Intake And Planning

Depends on milestones 1 and 2.

- [ ] Implement interactive brainstorm, UI Lab, autonomous, and
  non-interactive intake paths.
- [ ] Implement role-based route configuration and task or feature risk.
- [ ] Produce validated feature and task DAGs with acceptance criteria,
  dependencies, required verification, and review gates.
- [ ] Add plan approval and "just get on with it" behavior.
- [ ] Verify DAG validation, concise planning output, and autonomous decision
  recording.

### 4. Task Execution And Integration

Depends on milestones 1 through 3.

- [ ] Implement ready-task scheduling with a configurable maximum of four
  workers.
- [ ] Create isolated worktrees and clean task-attempt baselines.
- [ ] Implement task execution, narrow-test evidence, handovers, fine-grained
  commits, and independent task review.
- [ ] Integrate approved commits in dependency order.
- [ ] Create high-reasoning integration tasks for merge conflicts and rerun the
  affected verification.
- [ ] Verify parallel isolation, dependency ordering, failed-attempt handling,
  review rejection, conflict resolution, and rollback boundaries.

### 5. Failover, Quotas, Notifications, And Recovery

Depends on milestones 2 and 4.

- [ ] Implement ordered role routes and fresh-attempt failover at task
  boundaries.
- [ ] Track provider availability, known and unknown quota resets, and probes.
- [ ] Sleep and resume automatically when all usable routes are exhausted.
- [ ] Implement deduplicated Slack-compatible webhook notifications and
  `agent-loop notify test`.
- [ ] Reconcile interrupted attempts, processes, worktrees, branches, commits,
  and notifications idempotently on resume.
- [ ] Verify failover, waiting, provider recovery, webhook failure handling, and
  repeated crash recovery.

### 6. Review, Test Migrations, And Completion

Depends on milestones 1 through 5.

- [ ] Implement feature review and final system review against acceptance
  criteria.
- [ ] Implement the compact debugging protocol and high-reasoning escalation
  after repeated failure.
- [ ] Implement audited replacement-test and skipped-old-test migrations.
- [ ] Enforce `complete_pending_test_review` while migrations await approval.
- [ ] Generate final reports with `TEST BASELINE CHANGES` first when relevant.
- [ ] Verify completion states, migration auditability, blocked runs, and final
  report ordering.

### 7. End-To-End Hardening

Depends on all previous milestones.

- [ ] Run an end-to-end fixture project through planning, parallel execution,
  review, integration, interruption, recovery, and completion.
- [ ] Run the full test suite and review the final diff for correctness,
  security, unnecessary complexity, and missing coverage.
- [ ] Document installation, configuration, Slack setup, operation, recovery,
  and troubleshooting.
- [ ] Confirm runtime data and secrets are excluded from Git.

## First Implementation Slice

Start with milestone 1: create the package and tests, then implement the minimum
SQLite-backed run/task state needed to create a run, persist it, render its plan,
and inspect details through the CLI. Do not begin subprocess orchestration until
that state boundary is tested.

## Completion Gate

Implementation is complete only when the design acceptance criteria are
demonstrated, the full test suite passes, the final review has no unresolved
critical findings, and any test migrations have been explicitly approved.
