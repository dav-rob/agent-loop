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
- For `agy` print mode, put operational flags before `--print` and append `--print <prompt>` last. The CLI treats the token immediately after `--print` as prompt text, so flags placed after `--print` can be consumed as the prompt.
- Default route labels must match the installed binaries exactly. As of 2026-07-10, Codex CLI 0.144.1 exposes `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`; agy 1.1.0 exposes `Gemini 3.5 Flash (High)` alongside Gemini 3.1 Pro, Claude Sonnet 4.6 Thinking, and Claude Opus 4.6 Thinking.
- Default routing is personality-specific and three-step. Intake prioritizes chat quality with Codex `gpt-5.6-sol` medium, then agy Claude Opus 4.6 Thinking and Gemini 3.5 Flash High. Executors remain cross-binary and agy-first: Gemini 3.1 Pro High, Claude Sonnet 4.6 Thinking, then Codex `gpt-5.6-terra` high. Planner/escalation profiles use Codex Sol xhigh first; normal review profiles use Sol high first, followed by agy Opus and Gemini 3.1 Pro.
- Recovery must also repair stale task state: if a task is `running` but has no `running` attempts left, `agent-loop resume` should reset it to `ready` or `blocked` based on the retry limit so ready Codex fallbacks are not starved by stale active-file/worker accounting.
- `agent-loop resume` must not automatically reset `auth_required` provider routes to `available`; that would revive known-dead routes and prevent failover from reaching later providers. Only transient provider errors should be reset automatically.
- Spec intake model calls should use configured route fallback rather than a hardcoded adapter. Respect the configured personality order and emit non-empty diagnostics for auth/quota/transient/unavailable failures.
- Antigravity/`agy` print-mode workspace setup rejects directories under hidden path segments such as `.agent-loop/worktrees` with `is hidden: ignore uri`; task worktrees should default to a visible ignored directory such as `worktrees/`, while DB/logs/views can stay under `.agent-loop/`.
- Planner `required_verification` is executed by the orchestrator as a shell command. It must be an executable non-interactive command such as `npm test` or `python -m pytest`, not prose such as "Run npm install and confirm..."; prose verification should be dropped/skipped before reaching `shell=True`.
- Retry-limit escalation follow-up depends on fetching the latest `task_escalation` review. `ReviewRepository.get_latest_for()` must exist and return the full review row; without it, a live resume can crash after writing escalation follow-up reviews, leaving the task stuck in `reviewing` and ready follow-up work unscheduled.
- Task retry reset must be idempotent. A concurrent recovery or failure path can already have moved a task back to `ready`; failure cleanup should leave `ready` tasks ready instead of attempting the invalid transition `ready -> failed`.
- Codex JSON output may contain current event records such as `{"type":"item.completed","item":{"type":"agent_message","text":"..."}}`; the adapter must extract that text instead of passing the JSON event stream to review/planning parsers.
- Execution-failure escalation follow-up extends the same task's retry budget with `extended_limit` and `escalation_hint` in task scope, instead of creating recursively nested follow-up tasks for executor/verification failures.
- Workspace setup must ensure task worktrees can be created: initialize Git if missing, create an empty bootstrap commit only when `HEAD` is absent, add `.agent-loop/` to `.gitignore` only if needed, and preserve any effective user Git identity before falling back to agent-loop local identity.
- Brainstorm intake should feel like a concise coworker conversation: prefer goal-specific follow-up questions from model intake, but retain the fixed questionnaire as a reliable fallback.
- Interactive intake choice parsing should tolerate copied labels such as `2) None (Start planning immediately)`, echoed prompt text such as `Choice [1-2]: 2`, bracketed-paste/control sequences, and fast-entered bare choices after goal input; option 2 must always bypass spec brainstorming and go straight to planning.
- Interactive start copy should describe the user's decision in plain language: ask whether to brainstorm implementation, not whether to choose a `spec` or `none` intake mode. Start confirmations and plan approval messages should identify the goal with a short quoted description, reserving numeric IDs for explicit commands/status contexts. Display `none` intake as `plan mode` in user-facing confirmations.
- Executor escalation is threshold-based, not only max-attempt based. With the default retry policy, two failed/abandoned executions or two rejected task reviews cause the next implementation attempt to use `executor_escalated`; the default max attempt limit is five so escalated attempts get room to repair before final escalation/blocking.
- Task scheduling conflicts should be based on write scope, not read scope. New planner scopes include `writes` and `reads`; legacy `files` remains a fallback write scope for older plans. Shared helper files should be `reads` unless a task is expected to edit them.
- Planner role classification controls model routing. Tasks that create/edit files, scaffold apps, implement boundaries, or have executable verification commands must be `implementation` tasks so they use the Gemini-first executor route; `planning` tasks are reserved for genuine architecture/risk decomposition with no implementation file work.
- Generated progress views should reassure the user as soon as execution starts. If a task is `running` before an attempt row exists, show it as starting; after attempt worktree/log paths are recorded, re-render so active work has useful route/log information even before provider/model metadata is known.
- Model-route fallback is for provider availability problems, not ordinary task execution failure. If Codex or another executor times out while working, do not immediately fall through to the next configured model in the same attempt; return the timeout as an execution failure so retry/escalation policy handles it.
- Provider CLI wrappers can leave child processes behind if only the wrapper process is killed on timeout. Run provider commands in their own process group/session and terminate the process group on timeout.
- Every provider adapter should leave provider-specific diagnostics alongside generic `stdout.log` and `stderr.log`; Codex writes `codex.log` plus `codex_events.jsonl`/`last_message.txt`, while agy writes `agy.log`.
- Reviewer output is expected to be strict JSON, but a clearly labelled non-JSON decision such as `Decision: Approved` should be parsed as that decision rather than stored as a rejected review and injected into the next executor prompt as a bogus failure.
- A timed-out executor may have made useful commits, edits, or diagnostic progress without returning a final structured handover. The orchestrator should synthesize a timeout handover from provider logs, command/file-change events, verification output, commits, and preserved patches, record a timeout review, and inject that context into the next retry prompt.
- Runtime lifecycle events are DB-backed in `lifecycle_events` and recorded through `TaskLifecycleRecorder`; generated `progress.md` should render a recent event timeline from that table instead of relying only on derived task/attempt state.
- Normal task execution uses durable task-scoped branches/worktrees by default: branch `agent-loop-run-{goal_id}-task-{task_id}` and worktree `worktrees/run-{goal_id}-task-{task_id}`. Attempt logs remain attempt-scoped. Reviewer rejection keeps the task worktree so the next attempt continues the branch; approved/block/restart-style paths clean up as before.
- A pre-existing worktree path is only reused when it is on the expected task branch. Otherwise setup calls `create_worktree` so real Git can either create/recover the expected worktree or fail through the normal retry/block path; this prevents a stale plain directory from silently launching an executor in the wrong workspace.
- Merge-conflict recovery is a blocking follow-up chain, not a normal non-blocking follow-up. Recovery tasks carry `follow_up_type = "blocking_recovery_follow_up"` and `origin_task_id`; recovery follow-ups must preserve that origin so a successful terminal approval can mark the original blocked task complete.
- Merge-conflict recovery tasks are implementation work when they resolve files, lockfiles, or verification failures. They should carry `writes` scope for conflicting files and route through executor profiles rather than planner-only profiles.
- Retry strategy is explicit attempt metadata. Attempts now persist `start_sha`, `base_sha`, `retry_strategy`, and `retry_strategy_reason`; task/timeout reviews may return a strategy, and missing ordinary rejected-review strategies default to `continue_existing_branch`.
- Resume recovery should preserve useful interrupted work instead of deleting it blindly. If a running attempt has a preserved patch, mark it abandoned with `apply_patch_to_clean_branch` and keep the worktree path; durable task worktrees without patches default to `continue_existing_branch`; only no-evidence attempts default to `restart_from_main`.
- Strategy execution happens before executor launch. `continue_existing_branch` reuses the task branch/worktree, restart strategies recreate the task branch from `main` or the selected commit, `apply_patch_to_clean_branch` applies the preserved patch to a clean branch, and `block_for_human` blocks without launching the executor.

## Useful commands

- Install package: `pip install -e .`
- Run test suite: `PYTHONPATH=src pytest tests`
- Start run: `agent-loop start --non-interactive --goal "..."`
- Inspect details: `agent-loop plan --details`

## Testing notes

- Mocks are used for git utilities and subprocess adapters to avoid hitting rate limits.
- Valid run status transitions must go through `planning` -> `running` -> `reviewing` -> `complete`.
- If tests suddenly take much longer than their usual runtime, assume a hang or
  unexpected external process until proven otherwise; inspect running processes
  and the active test before waiting.

## Architecture notes

- Cycle detection (DAG validation) is executed using three-color DFS marking.
- All secrets from variables matching keywords (key, secret, token, password, auth, webhook, url) are automatically redacted in raw logs.
## Goal lifecycle architecture

- Goal types are flat operating modes (`prototype`, `extend`, `refine`, `repair`, `harden`, `investigate`), not maturity stages. New interactive and unattended intake persists a confirmed type and rationale before planning.
- Non-blocking review findings belong in SQLite `recommendations` records. Only selected recommendations are planning inputs for a later goal, and they resolve only when that adopting goal completes.
- `.agent-loop/delivery-report.md` is a rendered view of `goal_deliveries` plus recommendations; SQLite remains authoritative.
- Planner verification commands must be validated against the orchestration host, not only checked for executable-looking syntax. A task retry cannot repair an immutable command such as `python -m pytest` when the host provides only `python3`, so otherwise valid work can exhaust every attempt before review.
- Goal-type policy must reach executors as well as planners and reviewers. Review-only prototype standards cannot prevent an escalated executor from expanding scope before the first user-assessable delivery.
- Deadline shutdown must own reviewer and provider process groups independently of current ancestry. A reviewer can be reparented to PID 1 while the main loop is stopping and survive a recursive parent-tree kill.
- Planner output must remain declarative. No plan, model, config, or database string may be executed as a project command by deterministic orchestration. Executors own their task environments; reviewers independently verify in the authoritative worktree; orchestration stores evidence and enforces decisions.
