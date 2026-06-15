# learning.md

Use this file to record learnings, so that agents do not have to repeat work already done.

## Durable project facts

- All commits must be fine-grained commits so that the purpose of each change can be tracked easily.
- Schema is transactionally versioned and stored in SQLite.
- Markdown views (`plan.md` and `progress.md`) are generated directly from DB state.
- `agy` CLI requires passing `stdin=subprocess.DEVNULL` to run non-interactively without hanging.
- Codex CLI supports `--json` output and structured schema constraints via `--output-schema`.
- Transactional DDL in Python's `sqlite3` requires setting `isolation_level = None` temporarily to prevent Python's wrapper from executing implicit commits before DDL statements.
- Subprocess execution for execution environments (like Codex and Agy) must explicitly pass `cwd=workspace_path` to prevent writes from leaking into the parent workspace.
- Multi-threaded status updates on a single SQLite connection require `check_same_thread=False` during connection setup.
- Task scheduling conflicts are avoided by checking overlaps between the `files` array in tasks' `scope` metadata.
- Supervisor/executor work is recorded in dated, sequential handoff files under `docs/handoffs/`.
- Handoff requirements use stable IDs; executor responses must account for each ID and pass `agent-loop handoff validate` before claiming completion.
- A requirement may be explicitly `shelved` with a reason, but shelved work prevents an overall `complete` status.

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
