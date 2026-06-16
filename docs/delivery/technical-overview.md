# Agent Loop Delivery Guide

This guide is for running `agent-loop` as a local orchestration tool. It covers
the normal operator workflow and a few small examples you can run in a real
repository.

`agent-loop` turns one broad user goal into a plan, executes tasks through
configured agent CLIs, records attempts in SQLite, writes human-readable
`plan.md` and `progress.md` views, and can resume after interruption.

The public term is `goal`. Internally, the code and database still call the
tracked execution container a `run`, so `Goal ID` in the CLI maps to the
internal `run_id`.

## Before You Run It

Install the tool from this `agent-loop` checkout, then run it from the
repository you want it to modify.

The default execution mode is `trusted-host`, which means the agent commands run
with the same filesystem and credential access as your shell user. Use it in a
Git repository where you are comfortable with automated edits, and commit or
stash unrelated work first.

From the `agent-loop` checkout, install the package in the virtual environment:

```bash
python -m pip install -e .
```

Verify the CLI is available:

```bash
agent-loop --help
```

After installation, the `agent-loop` command can be run from another repository
as long as the same environment is active.

The orchestrator expects the configured provider CLIs to be installed and
authenticated. By default it uses `codex`, `agy`, and `antigravity-usage` from
`PATH` when needed. You can override binary paths in `agent-loop.toml`.

## Configuration

Create `agent-loop.toml` in the repository root when the defaults are not
enough:

```toml
max_workers = 4
db_path = ".agent-loop.db"
logs_dir = "logs"
webhook_env_var = "AGENT_LOOP_WEBHOOK_URL"

# Optional binary overrides.
codex_path = "/opt/homebrew/bin/codex"
agy_path = "/opt/homebrew/bin/agy"
antigravity_usage_path = "/opt/homebrew/bin/antigravity-usage"

[routes]
planning = [
  { provider = "codex", model = "gpt-5.5", reasoning_level = "high" },
  { provider = "agy", model = "Claude Opus 4.6 (Thinking)", reasoning_level = "high" }
]

implementation = [
  { provider = "agy", model = "Gemini 3.5 Flash (High)", reasoning_level = "high" },
  { provider = "codex", model = "gpt-5.4-mini", reasoning_level = "high" }
]

[retry_policy]
max_attempts = 3
escalation_threshold = 2

[commands]
narrow_test = "pytest {test_path}"
regression_test = "pytest tests"
```

Runtime files are written under the current repository:

- `.agent-loop.db`: goal/run, task, attempt, decision, quota, and migration state
- `plan.md`: current objective, features, tasks, dependencies, and status
- `progress.md`: current goal state, blockers, tests, and next action
- `logs/`: provider prompts, stdout/stderr, patches, reviews, and test output
- `worktrees/`: isolated task worktrees used during execution

## Basic Workflow

Start with the interactive wizard:

```bash
agent-loop start
```

Start without prompts:

```bash
agent-loop start \
  --non-interactive \
  --goal "Add pagination to the issues list and update tests"
```

Use an explicit intake mode when you know how much discovery you want:

```bash
agent-loop start --goal "Improve the settings page layout" --intake ui_lab
agent-loop start --goal "Add CSV export for reports" --intake brainstorm
agent-loop start --goal "Fix flaky retry tests" --intake autonomous
```

`ui_lab` is only accepted for UI-related goals. `autonomous` is best for narrow,
well-understood implementation tasks.

In non-interactive mode, plan approval is automatic by default:

```bash
agent-loop start \
  --non-interactive \
  --goal "Add a --version flag to the CLI" \
  --unattended-policy approve
```

Leave the plan waiting for review instead:

```bash
agent-loop start \
  --non-interactive \
  --goal "Refactor the billing adapter" \
  --unattended-policy reject
```

Inspect the latest goal:

```bash
agent-loop status
agent-loop plan
agent-loop plan --details
```

Inspect a specific goal:

```bash
agent-loop status 3
agent-loop plan --details 3
```

Approve a generated plan that is waiting in `awaiting_plan_approval`:

```bash
agent-loop approve 3
```

Resume after interruption, quota recovery, or a blocked/waiting state:

```bash
agent-loop resume
agent-loop resume 3
```

Send a test webhook notification:

```bash
export AGENT_LOOP_WEBHOOK_URL="https://hooks.slack.com/services/..."
agent-loop notify test
```

If no webhook environment variable is set, notification delivery falls back to
stdout for local visibility.

## Test Migrations

When the agent proposes a test baseline migration, the goal can stop in
`complete_pending_test_review`. Inspect the details first:

```bash
agent-loop plan --details
```

Approve or reject the migration by ID:

```bash
agent-loop migration approve 12
agent-loop migration reject 12
```

Approving all pending migrations completes the goal. Rejecting a migration
blocks the goal so the change remains auditable.

## Handoff Validation

Supervisor/executor handoff files can be validated with:

```bash
agent-loop handoff validate \
  docs/handoffs/2026-06-15/13-fix-request.md \
  docs/handoffs/2026-06-15/14-executor-response.md
```

The validator requires every requirement ID from the request to be accounted for
as `complete`, `blocked`, or `shelved`.

## Runnable Examples

### Example 1: Try a Tiny Python Change

Use this after installing `agent-loop` into your active environment. Run it in a
disposable repository so you can see the full loop without risking important
work:

```bash
mkdir /tmp/agent-loop-demo
cd /tmp/agent-loop-demo
git init

cat > calculator.py <<'PY'
def add(a, b):
    return a + b
PY

mkdir tests
cat > tests/test_calculator.py <<'PY'
from calculator import add


def test_add():
    assert add(2, 3) == 5
PY

python -m pytest -q
agent-loop start \
  --non-interactive \
  --intake autonomous \
  --goal "Add subtract(a, b) to calculator.py with tests. Keep add(a, b) unchanged."
```

Then inspect what happened:

```bash
agent-loop status
agent-loop plan --details
git log --oneline --decorate -5
```

### Example 2: Generate a Docs-Only Change

Run this from a real project that already has a README:

```bash
agent-loop start \
  --non-interactive \
  --intake autonomous \
  --goal "Create docs/quickstart.md from the README. Include install, test, and run commands. Do not change source code."
```

Review the result:

```bash
agent-loop plan --details
git diff -- docs/quickstart.md
```

### Example 3: Review a Plan Before Execution

Use this when the goal is broad enough that you want to inspect the plan first:

```bash
agent-loop start \
  --non-interactive \
  --goal "Add structured logging to the CLI commands" \
  --intake brainstorm \
  --unattended-policy reject

agent-loop plan --details
agent-loop approve
```

### Example 4: Resume After Stopping the Process

Start a goal, stop the terminal process with `Ctrl-C`, then resume:

```bash
agent-loop status
agent-loop resume
agent-loop plan --details
```

Resume reconciles interrupted attempts, regenerates `plan.md` and
`progress.md`, and continues execution when the goal is runnable.

### Example 5: Use UI Lab Intake for UI Work

Run this only for an actual UI goal:

```bash
agent-loop start \
  --goal "Improve the empty state for the dashboard page" \
  --intake ui_lab
```

The UI Lab path gathers additional UI context before planning. For non-UI goals,
the CLI rejects `--intake ui_lab`.

## Troubleshooting

If a provider binary is missing, either install it on `PATH` or set the matching
`*_path` key in `agent-loop.toml`.

If authentication is missing or expired, authenticate the provider CLI directly,
then run:

```bash
agent-loop resume
```

If quota is exhausted, inspect the goal:

```bash
agent-loop status
agent-loop plan --details
```

The orchestrator records route state and can resume when a usable route
recovers.

If tests fail, start with the attempt logs shown by:

```bash
agent-loop plan --details
```

The log paths point to the provider output, generated patch, and verification
command output for the failing attempt.
