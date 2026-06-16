# Agent Loop Orchestrator Design

Date: 2026-06-15

## Purpose

Build a provider-neutral, resumable agentic development loop that can accept a broad goal, turn it into features and tasks, execute suitable work in parallel, test and review each increment, and continue until the goal is complete or a genuine stop condition is reached.

The orchestrator will use Codex and Antigravity CLI (`agy`) through adapters. Routing is based on the role and risk of each task, not on a single preferred provider. Provider exhaustion causes failover at task-attempt boundaries.

## Product Principles

- Optimize for completing substantial development work without unattended drift.
- Keep plans concise for humans while retaining complete machine state.
- Treat features, tasks, tests, attempts, and reviews as explicit records.
- Use fine-grained commits as review and rollback boundaries.
- Spend stronger models where reasoning is valuable, not across an entire plan.
- Parallelize whenever independent work exists, up to four workers.
- Preserve evidence when behavior or the test baseline changes.
- Prefer explicit state transitions over inferring state from prose.
- Keep raw execution output out of the database while making it easy to locate.

## User Experience

The first interface is a CLI wizard with three intake modes:

1. `Brainstorm`
   Ask concise questions one at a time, propose a plan, and request approval before execution.
2. `Brainstorm with UI Lab`
   Start with `$ui-lab:brief` and continue through relevant UI Lab stages. Offer this only when the goal includes a user interface.
3. `Autonomous`
   Accept a goal and let the planning route make reasonable product and technical decisions. Record those decisions for later review.

The user can choose "just get on with it" for small work. This bypasses extended discussion, not planning: the system still creates a minimal feature and task structure.

Non-interactive operation must support starting, resuming, monitoring, and inspecting a run without the wizard.

Representative commands:

```text
agent-loop start
agent-loop start --non-interactive --goal "..."
agent-loop resume <run-id>
agent-loop status [<run-id>]
agent-loop plan [<run-id>]
agent-loop plan --details [<run-id>]
agent-loop notify test
```

## Architecture

### Orchestrator

The orchestrator is a Python state machine. It advances a run through intake, planning, execution, integration, feature review, final review, and completion. Every transition is persisted before more work starts so process termination is recoverable.

### State Store

SQLite is the authoritative store for plans and runtime state. Markdown files are generated views and must not be parsed to determine status.

Core records:

- `runs`: goal, intake mode, status, configuration snapshot, timestamps.
- `features`: outcome, acceptance criteria, dependencies, risk, review status.
- `tasks`: feature, role, dependencies, scope, risk, required verification, status.
- `attempts`: route, provider, model, reasoning level, worktree, commit, logs, outcome.
- `test_runs`: command, scope, exit status, duration, output path.
- `reviews`: subject, reviewer route, findings, decision, evidence paths.
- `provider_state`: capability snapshot, availability, quota windows, resets, probes.
- `notifications`: event, destination, attempts, delivery status.
- `decisions`: user-approved and autonomous product or architecture decisions.
- `test_migrations`: old and replacement tests, rationale, evidence, approval status.

Database migrations must be versioned and transactional.

### Human-Readable Views

`.agent-loop/plan.md` summarizes the objective, acceptance criteria, features, tasks, dependencies, risk, and progress. It must include this note near the top:

> This file is a human-readable summary. Full task metadata, dependencies, attempts, and evidence are stored in the agent-loop SQLite database. Run `agent-loop plan --details` to inspect them.

`.agent-loop/progress.md` summarizes current work, completed outcomes, active blockers, test results, provider state, and the next expected action.

`.agent-loop/learning.md` remains an optional curated record of reusable project facts. It is not a catch-all transcript and is not authoritative state.

### Logs

SQLite stores searchable log metadata and concise summaries. Raw, append-only artifacts live under:

```text
.agent-loop/logs/<run-id>/<task-id>/<attempt-id>/
```

Artifacts can include:

- normalized prompt and context manifest
- provider stdout and stderr
- Codex JSONL events
- `agy` log output
- test output
- review reports
- handover records
- webhook delivery attempts

Runtime logs, isolated worktrees, generated Markdown views, and the SQLite database live under `.agent-loop/` and are ignored by Git by default. Secrets and sensitive environment values must be redacted or omitted before writing any artifact.

### Provider Adapters

Codex and `agy` implement one adapter contract:

- discover installed version and model capabilities
- start and cancel an attempt
- capture structured and unstructured output
- classify exit conditions
- report known token usage and quota state
- produce a normalized attempt result
- probe availability without modifying the workspace

Capabilities must be detected at runtime. The orchestrator must not trust static skill metadata because locally installed CLI versions and model catalogs change.

Codex execution uses `codex exec`, JSONL output where available, explicit workspace configuration, and model reasoning settings. Antigravity execution uses `agy --print`, an explicit `--add-dir`, a sufficient print timeout, a dedicated log file, and configured permissions or trusted-host behavior.

`agy` does not currently provide the same structured streaming and quota information as Codex. Its adapter must report unavailable data as unknown rather than inventing usage or reset times.

## Planning Model

The planner creates:

- a concise objective
- explicit acceptance criteria
- independently reviewable features
- tasks within each feature
- task and feature dependencies forming a directed acyclic graph
- expected scope and likely files when known
- execution role and risk per task
- required tests and review gates

Planning detail should be sufficient for execution and verification without becoming an implementation diary. The full graph and metadata live in SQLite; `.agent-loop/plan.md` remains a compact summary.

Risk is assigned to features and tasks, never to the entire plan. High-risk categories include architecture, security, authentication, payments, deployment, destructive operations, ambiguous product behavior, protected data, and unusually broad changes.

Ordinary tasks begin on the normal implementation route. A task escalates when evidence shows it needs stronger reasoning.

## Role-Based Routing

Routes are ordered configuration, not hard-coded provider preference.

Default planning, assessment, review, integration-conflict, and high-risk route:

1. Codex `gpt-5.5` with high reasoning.
2. `agy` Claude Opus 4.6 Thinking.
3. `agy` Gemini 3.1 Pro High.

Default implementation route:

1. `agy` Gemini 3.5 Flash High.
2. Codex `gpt-5.4-mini` with high reasoning.

The installed Codex catalog currently provides `gpt-5.4-mini`, not `gpt-5.5-mini`. All model names remain configurable so catalog changes do not require code changes.

A provider or model can be temporarily removed from consideration after quota exhaustion, authentication failure, unsupported capability detection, or repeated infrastructure failure.

## Task Execution

Each task attempt:

1. Starts from the task's recorded baseline in an isolated Git worktree.
2. Receives only the relevant goal, feature, task, decisions, scope, and evidence.
3. Uses test-driven development when behavior can be tested meaningfully.
4. Runs the narrowest relevant tests before claiming completion.
5. Produces a fine-grained commit, structured handover, and evidence record.
6. Passes independent review before integration.

Failed or abandoned attempts remain recorded and are not integrated.

Provider failover occurs only at task boundaries. If quota is exhausted during an attempt, the orchestrator preserves its logs and partial diff, marks the attempt accordingly, and starts a fresh attempt from the task baseline using the next available route. The replacement receives a structured handover and may inspect the failed diff, but incomplete edits are not automatically carried forward.

## Parallelism And Integration

The scheduler launches any ready tasks whose dependencies are satisfied and whose likely file ownership does not create an avoidable conflict. It may run up to four workers concurrently.

Each worker uses an isolated Git worktree. The orchestrator integrates reviewed task commits into the run branch in dependency order.

When reviewed task commits conflict, the orchestrator creates a dedicated integration task using the high-reasoning route. That task resolves the conflict in a fresh worktree, reruns verification required by both source tasks, and creates its own auditable commit.

## Review Model

Review occurs at three levels:

- Task review checks scope, correctness, regression risk, required tests, and unnecessary complexity before integration.
- Feature review checks acceptance criteria and interactions after all feature tasks integrate.
- Final review runs broader regression tests and checks the complete result against the original goal and decisions.

Review findings are structured records. A review can approve, request a follow-up task, require architectural assessment, or block for a stop condition.

## Debugging Policy

Routine debugging uses a compact evidence-based protocol:

1. Reproduce and capture the exact failure.
2. Inspect relevant changes, logs, and component boundaries.
3. Record one root-cause hypothesis.
4. Add a regression test when practical.
5. Apply one scoped fix and verify it.

After two failed attempts, the task moves to a reasoning route with accumulated evidence. After three failed fixes, or evidence that the architecture is the source of repeated failure, the task stops for architectural review instead of continuing speculative changes.

The full `$systematic-debugging` skill is escalation guidance for complex or repeated failures. It is not injected into every routine task.

## Test Baseline Changes

The loop may continue unattended when an existing test encodes behavior that must legitimately change, but the change must be explicit and auditable.

The agent may:

1. Add a replacement test before changing the existing test.
2. Mark the old test skipped with a clear reason and a test-migration identifier.
3. Record old and new test paths, previous and replacement behavior, rationale, evidence, task, and commit.
4. Continue implementation without deleting the old test.

Feature and final review must assess whether coverage was weakened. A run containing unresolved test migrations ends as `complete_pending_test_review`, not `complete`.

The final report must begin with `TEST BASELINE CHANGES` whenever migrations exist. This section appears before success summaries and lists every rewritten or superseded test with its explanation and approval status.

## Quotas, Failover, And Sleeping

Provider-state handling distinguishes:

- available
- quota limited with known reset
- quota limited with unknown reset
- authentication required
- unavailable or incompatible
- infrastructure failure

Codex structured events and rate-limit interfaces are used when available to record token usage, quota windows, percentages, and reset timestamps. `agy` output and its log file are classified conservatively; exact resets are reported only when observed.

When a route is unavailable, the scheduler tries its next configured route at the next task attempt. When every route required by ready work is quota limited, the orchestrator:

1. Persists all state.
2. Sends a deduplicated notification.
3. Sleeps until the earliest known reset.
4. Periodically probes providers whose reset is unknown.
5. Resumes automatically when a usable route returns.

Authentication and missing credentials remain stop conditions rather than quota conditions.

## Notifications

The MVP notification transport is a generic JSON webhook compatible with a Slack incoming webhook configuration. Notifications cover:

- all usable routes exhausted
- provider recovery
- blocked run
- repeated failure or architectural review required
- pending test migration review
- run completion

Messages include run and task identifiers, provider and model, classified cause, relevant evidence paths, known reset times, fallback actions taken, and expected resume behavior. Repeated alerts for the same condition are deduplicated.

The webhook URL is supplied through an environment variable and never stored in project files or logs.

### Slack Incoming Webhook Setup

These steps follow Slack's official [incoming webhook documentation](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).

1. Go to <https://api.slack.com/apps> and select `Create New App`.
2. Choose `From scratch`, name the app, and select the target Slack workspace.
3. Open `Incoming Webhooks` and enable incoming webhooks.
4. Select `Add New Webhook to Workspace` and choose the notification channel.
5. Store the generated URL outside the repository, for example:

   ```bash
   export AGENT_LOOP_WEBHOOK_URL='https://hooks.slack.com/services/...'
   ```

6. Run `agent-loop notify test` and verify that the channel receives a test event.

The webhook URL is a secret and Slack may revoke URLs that are exposed publicly. For persistent local use, add the environment variable through the user's secret manager or shell environment without committing it. Teams or other destinations can be supported later through the same notifier interface or a transport-specific formatter.

## Configuration

Project configuration defines:

- ordered routes by role
- model and reasoning settings
- maximum worker count, default `4`
- workspace and network permissions
- retry, escalation, quota-probe, and sleep policies
- webhook environment-variable name
- database and log locations
- narrow and regression test commands
- protected paths and approval-required operations

Workspace writes and network access are permitted autonomously. The loop stops for missing credentials or secrets, deployment, destructive operations, writes outside the configured workspace, and unresolved product decisions that cannot safely be inferred.

Test baseline changes follow the audited migration workflow rather than stopping immediately.

The system must validate configuration and route capabilities before starting expensive work.

## Recovery

On startup or resume, the orchestrator:

1. Opens and migrates the state database transactionally.
2. Reconciles recorded worktrees, branches, commits, and active processes.
3. Marks interrupted `running` attempts as `abandoned` unless a live worker is verified.
4. Preserves partial diffs and logs for assessment.
5. Regenerates Markdown views.
6. Continues from the next valid state transition.

Recovery must be idempotent. Re-running it must not duplicate commits, notifications, reviews, or task attempts.

## Run Statuses

At minimum:

- `draft`
- `planning`
- `awaiting_plan_approval`
- `running`
- `waiting_for_quota`
- `blocked`
- `reviewing`
- `complete_pending_test_review`
- `complete`
- `failed`
- `cancelled`

Task and attempt statuses use their own explicit transition sets.

## Out Of Scope For The First Implementation

- Web monitoring dashboard
- Remote multi-host workers
- More than four concurrent workers
- Automatic deployment
- Automatic secret acquisition
- Deleting superseded tests
- Perfect cross-provider session continuation
- Storing large raw logs in SQLite
- General-purpose workflow-engine integration

## Acceptance Criteria

The first complete implementation must demonstrate that it can:

- start an interactive or non-interactive run
- create a feature and task DAG from a broad goal
- render concise `.agent-loop/plan.md` and `.agent-loop/progress.md` views from SQLite
- inspect full plan details through the CLI
- route planning and implementation through configured Codex and `agy` adapters
- fail over to the next route only through a fresh task attempt
- run up to four independent worktree workers
- commit, review, and integrate task-sized changes
- create a dedicated integration task for a merge conflict
- record tests and review evidence
- recover after the orchestrator is terminated
- detect or classify quota exhaustion without fabricating reset times
- sleep and resume when all routes are quota limited
- send and deduplicate Slack-compatible webhook notifications
- enforce the audited test-migration workflow and pending-review status
- produce a final report that puts test baseline changes first

## Design Decisions

- Provider neutrality is implemented through adapters and role-based routes.
- SQLite is authoritative; Markdown is a human-readable projection.
- Raw logs live in files and searchable metadata lives in SQLite.
- Risk and escalation are attached to features, tasks, and attempts.
- Cross-provider failover restarts from a clean task baseline.
- Fine-grained task commits are the primary rollback boundary.
- Systematic debugging is used as escalation guidance, not universal prompt boilerplate.
- The CLI is the first UI; a dashboard can consume the same state model later.
