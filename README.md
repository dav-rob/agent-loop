# Agent Loop Orchestrator

A provider-neutral, resumable agentic development loop orchestrator that turns a goal into features/tasks (DAG), executes them in isolated Git worktrees, tests and reviews each increment, and handles quota failover.

## Installation

Within the virtual environment, install the package in editable mode:

```bash
pip install -e .
```

This registers the `agent-loop` CLI command.

User-facing commands and docs refer to a tracked request as a goal. Internally,
the SQLite schema and Python repositories still call the same object a run, so
`Goal ID` maps to the internal `run_id`.

Runtime state is kept under `.agent-loop/` in the target repository and is
ignored by Git by default. Keep `agent-loop.toml` at the repository root if you
want committed project configuration.

## Quick Start

### 1. Start a goal (interactive mode)
```bash
agent-loop start
```

### 2. Start a goal (non-interactive mode)
```bash
agent-loop start --non-interactive --goal "Implement standard login endpoints using oauth2 flow"
```

During intake, agent-loop infers and explains the goal's operating type before
planning. The available types are `prototype`, `extend`, `refine`, `repair`,
`harden`, and `investigate`. They are working modes rather than maturity levels;
a mature application can use `prototype` for a new experimental feature.

### 3. Check status of the latest goal
```bash
agent-loop status
```

### 4. Inspect the execution plan and DAG
```bash
agent-loop plan
```

### 5. Inspect full details (attempts, decisions, commits, test migrations)
```bash
agent-loop plan --details
```

### 6. Resume an interrupted goal
```bash
agent-loop resume
```

### 7. Validate an executor handoff

```bash
agent-loop handoff validate \
  docs/handoffs/2026-06-15/04-fix-request.md \
  docs/handoffs/2026-06-15/05-executor-response.md
```

See `docs/handoffs/README.md` for the dated request/response convention,
requirement statuses, templates, and standard executor prompt.

## Configuration

Settings can be customized in `agent-loop.toml` in the project root:

```toml
max_workers = 4
state_dir = ".agent-loop"
db_path = ".agent-loop/agent-loop.db"
logs_dir = ".agent-loop/logs"
worktrees_dir = "worktrees"
delivery_report_path = ".agent-loop/delivery-report.md"

[routes]
intake = [
    { provider = "codex", model = "gpt-5.6-sol", reasoning_level = "medium" },
    { provider = "agy", model = "Claude Opus 4.6 (Thinking)", reasoning_level = "high" },
    { provider = "agy", model = "Gemini 3.5 Flash (High)", reasoning_level = "high" }
]
planner = [
    { provider = "codex", model = "gpt-5.6-sol", reasoning_level = "xhigh" },
    { provider = "agy", model = "Claude Opus 4.6 (Thinking)", reasoning_level = "high" },
    { provider = "agy", model = "Gemini 3.1 Pro (High)", reasoning_level = "high" }
]
executor = [
    { provider = "agy", model = "Gemini 3.1 Pro (High)", reasoning_level = "high" },
    { provider = "agy", model = "Claude Sonnet 4.6 (Thinking)", reasoning_level = "high" },
    { provider = "codex", model = "gpt-5.6-terra", reasoning_level = "high" }
]
executor_escalated = [
    { provider = "codex", model = "gpt-5.6-sol", reasoning_level = "xhigh" },
    { provider = "agy", model = "Claude Opus 4.6 (Thinking)", reasoning_level = "high" },
    { provider = "agy", model = "Gemini 3.1 Pro (High)", reasoning_level = "high" }
]
reviewer = [
    { provider = "codex", model = "gpt-5.6-sol", reasoning_level = "high" },
    { provider = "agy", model = "Claude Opus 4.6 (Thinking)", reasoning_level = "high" },
    { provider = "agy", model = "Gemini 3.1 Pro (High)", reasoning_level = "high" }
]
spec_reviewer = [
    { provider = "codex", model = "gpt-5.6-sol", reasoning_level = "high" },
    { provider = "agy", model = "Claude Opus 4.6 (Thinking)", reasoning_level = "high" },
    { provider = "agy", model = "Gemini 3.1 Pro (High)", reasoning_level = "high" }
]
escalation_reviewer = [
    { provider = "codex", model = "gpt-5.6-sol", reasoning_level = "xhigh" },
    { provider = "agy", model = "Claude Opus 4.6 (Thinking)", reasoning_level = "high" },
    { provider = "agy", model = "Gemini 3.1 Pro (High)", reasoning_level = "high" }
]

[retry_policy]
max_attempts = 3
escalation_threshold = 2
```

`agent-loop start` creates a default `agent-loop.toml` with all supported route
profiles. Older `planning` and `implementation` route buckets are still accepted
as compatibility aliases.

## Goal Lifecycle

Review standards follow the confirmed goal type. Prototype reviews block only
functional delivery failures such as an app that cannot run, failed required
verification, material regressions, or a missing central function. Other useful
security, architecture, reliability, testing, usability, and maintenance work
is stored as recommendations rather than silently extending the goal.

Successful completion writes `.agent-loop/delivery-report.md` from SQLite. It
summarizes what was built or learned, how to run or open it, verification,
known limitations, and recommendations grouped by priority. Web deliveries
must provide a verified launch command and local URL.

When starting a later goal interactively, agent-loop offers open
recommendations from the latest completed goal. Unattended starts can select
them explicitly:

```bash
agent-loop start \
  --non-interactive \
  --goal "Address the selected reliability work" \
  --recommendations 3,5
```

Selected recommendations resolve only when the adopting goal completes.

## Slack Webhook Setup

Notifications are sent via a generic JSON webhook.

1. Create a webhook URL on Slack.
2. Set the environment variable:
   ```bash
   export AGENT_LOOP_WEBHOOK_URL="https://hooks.slack.com/services/..."
   ```
3. Test the notification:
   ```bash
   agent-loop notify test
   ```
