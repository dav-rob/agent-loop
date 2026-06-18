# learning.md

Use this file to record learnings, so that agents do not have to repeat work already done.

## Durable project facts

- All commits must be fine-grained commits so that the purpose of each change can be tracked easily.
- Schema is transactionally versioned and stored in SQLite.
- Markdown views are generated directly from DB state. Product runtime views live under `.agent-loop/`; this repository also has root manual-loop `plan.md` and `progress.md` files.
- For a target repository's live agent-loop state, `.agent-loop/agent-loop.db` is authoritative. On fresh context, inspect database-backed commands like `agent-loop status` and `agent-loop plan --details` before looking at `.agent-loop/logs/`; logs are evidence for attempts, not the source of truth for current goal/task status.
- `agy` CLI requires passing `stdin=subprocess.DEVNULL` to run non-interactively without hanging.
- Codex CLI supports `--json` output and structured schema constraints via `--output-schema`.
- Transactional DDL in Python's `sqlite3` requires setting `isolation_level = None` temporarily to prevent Python's wrapper from executing implicit commits before DDL statements.
- Subprocess execution for execution environments (like Codex and Agy) must explicitly pass `cwd=workspace_path` to prevent writes from leaking into the parent workspace.
- Multi-threaded status updates on a single SQLite connection require `check_same_thread=False` during connection setup.
- Task scheduling conflicts are avoided by checking overlaps between the `files` array in tasks' `scope` metadata.
- Supervisor/executor work is recorded in dated, sequential handoff files under `docs/handoffs/`.
- Handoff requirements use stable IDs; executor responses must account for each ID and pass `agent-loop handoff validate` before claiming completion.
- A requirement may be explicitly `shelved` with a reason, but shelved work prevents an overall `complete` status.
- User-facing language should call the top-level tracked request a goal. Internally this is still called a run (`runs` table, `run_id`, `RunRepository`), so `Goal ID` maps to the internal run ID.
- Agent-loop runtime state for target repositories belongs under `.agent-loop/` (`agent-loop.db`, generated views, learning notes, logs, worktrees, goal/plan/spec folders). Root `agent-loop.toml` remains the optional committed project config.
- Interactive `agent-loop start` must preserve immediately pasted multi-line goal text before prompting for intake choices, because Python `input()` otherwise consumes only the first pasted line.
- Codex structured-output schemas used with `--output-schema` must set `additionalProperties: false` on every object and require every property listed in each object schema.
- If planning fails before any features are generated, the goal is `blocked` with an empty plan; `agent-loop resume` should re-enter planning rather than transition directly to task execution.
- Project-local skills live under repository root `skills/` and are copied into target runtime state under `.agent-loop/skills/` during workspace initialization.
- Adapter transient-failure detection must not scan successful assistant output for generic words like "timeout"; inspect diagnostics/errors instead so valid plans discussing timeout handling are not rejected.
- `agy --print-timeout` expects Go-duration syntax such as `123s`, `30m`, or `1h`, not a bare integer.
- Default `agy` route labels should match `agy models` exactly. As of 2026-06-18, defaults use `Gemini 3.1 Pro (High)`, `Claude Sonnet 4.6 (Thinking)`, and `Claude Opus 4.6 (Thinking)`; do not use `Gemini 3.5 Flash (High)` as a default route.
- Workspace setup must ensure task worktrees can be created: initialize Git if missing, create an empty bootstrap commit only when `HEAD` is absent, add `.agent-loop/` to `.gitignore` only if needed, and preserve any effective user Git identity before falling back to agent-loop local identity.
- Brainstorm intake should feel like a concise coworker conversation: prefer goal-specific follow-up questions from model intake, but retain the fixed questionnaire as a reliable fallback.

## Useful commands

- Install package: `pip install -e .`
- Run test suite: `PYTHONPATH=src pytest tests`
- Start run: `agent-loop start --non-interactive --goal "..."`
- Inspect details: `agent-loop plan --details`

## Testing notes

- Mocks are used for git utilities and subprocess adapters to avoid hitting rate limits.
- Valid run status transitions must go through `planning` -> `running` -> `reviewing` -> `complete`.

## Architecture notes

- Cycle detection (DAG validation) is executed using three-color DFS marking.
- All secrets from variables matching keywords (key, secret, token, password, auth, webhook, url) are automatically redacted in raw logs.
