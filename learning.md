# learning.md

Use this file to record learnings, so that agents do not have to repeat work already done.

## Durable project facts

- Schema is transactionally versioned and stored in SQLite.
- Markdown views (`plan.md` and `progress.md`) are generated directly from DB state.
- `agy` CLI requires passing `stdin=subprocess.DEVNULL` to run non-interactively without hanging.
- Codex CLI supports `--json` output and structured schema constraints via `--output-schema`.

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
