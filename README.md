# Agent Loop Orchestrator

A provider-neutral, resumable agentic development loop orchestrator that turns a goal into features/tasks (DAG), executes them in isolated Git worktrees, tests and reviews each increment, and handles quota failover.

## Installation

Within the virtual environment, install the package in editable mode:

```bash
pip install -e .
```

This registers the `agent-loop` CLI command.

## Quick Start

### 1. Start a run (interactive mode)
```bash
agent-loop start
```

### 2. Start a run (non-interactive mode)
```bash
agent-loop start --non-interactive --goal "Implement standard login endpoints using oauth2 flow"
```

### 3. Check status of the latest run
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

### 6. Resume an interrupted run
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
db_path = ".agent-loop.db"
logs_dir = "logs"

[routes]
planning = [
    { provider = "codex", model = "gpt-5.5" },
    { provider = "agy", model = "Claude Opus 4.6 Thinking" }
]
implementation = [
    { provider = "agy", model = "Gemini 3.5 Flash High" },
    { provider = "codex", model = "gpt-5.4-mini" }
]

[retry_policy]
max_attempts = 3
escalation_threshold = 2
```

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
