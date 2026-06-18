# progress.md

Use this file to monitor progress as the agent loops through tasks to achieve its goal.

## Goal

Implement the provider-neutral, resumable agent-loop orchestrator defined in docs/specs/2026-06-15-agent-loop-orchestrator-design.md.

## Current status

Final supervisor verification accepted the fifth executor pass. Role-based
routing is active in `_execute_task_impl`, notification deduplication and
webhook payload behavior are verified, and a lifecycle fixture exercises the
public orchestration flow through `plan_run` and `run_loop`.

Delivery documentation has been organized under `docs/delivery/` with a
technical overview and a plain-English operator guide. The plain-English guide
now includes the orchestrator/planner/executor/reviewer mechanism.
User-facing terminology is being aligned around `Goal ID`, while internal code
continues to use `run_id` and the `runs` table.
Runtime state is being moved into `.agent-loop/` for target repositories while
keeping root `agent-loop.toml` as optional project config.
Interactive `agent-loop start` now captures immediately pasted multi-line goals
as the goal text before asking intake/refinement prompts, preventing pasted
requirements from being consumed as later wizard answers.
Planning recovery was fixed after a real goal in `test-loop` failed because
Codex rejected the planner JSON schema. The planner schema now satisfies Codex
strict structured-output requirements, and `agent-loop resume` replans blocked
goals that have no generated features instead of trying to execute an empty
plan.
`agent-loop status` now keeps goal output concise by showing a single
70-character `Goal Description` and omitting the raw multi-line goal body.
`agent-loop start` now copies project-local skills into each target repository's
`.agent-loop/skills` directory and uses a concise multi-turn brainstorming
intake instead of a single generic refinement question.
Planning failure in `test-loop` was traced to adapter classification, not bad
planner JSON: successful Codex planner output mentioned "timeouts", which was
being scanned as a transient failure. Codex diagnostics are now separated from
assistant output, and agy print timeouts are passed as Go-duration values.
`agent-loop resume` now explains when a goal is paused in
`awaiting_plan_approval`, including the plan inspection command, markdown plan
path, and approval command.
Bare `agent-loop` now acts as a friendly dispatcher for the latest in-progress
goal. It shows a 200-character goal description, status, the direct plan file
path when approval is pending, and safe commands or interactive choices for
viewing, approving, resuming, checking status, or starting a new goal.
The pending-approval default screen now uses action labels such as `View Plan`
and `Approve and Start` instead of teaching command syntax in the primary UI.
Runaway execution in `test-loop` was traced to a repository with no initial
commit: `git worktree add ... main` failed before adapter execution, and that
early failure path reset the task to `ready` without applying retry limits.
Worktree setup failures now block the task once the retry limit is reached.
Workspace setup now bootstraps empty Git repositories with an empty initial
commit so normal task worktrees can be created in brand-new repos.
The empty-repo bootstrap also commits a default `.gitignore` entry for
`.agent-loop/`, uses the user's effective Git identity when available, falls
back to a local agent-loop identity only when needed, and skips existing repos
that already have `HEAD`.
Workspace setup now also initializes Git when the target directory has no
repository at all, then applies the same idempotent `.gitignore` and bootstrap
commit path.
Interactive brainstorm mode now tries to generate tailored follow-up questions
from the goal before falling back to the fixed questionnaire, making the intake
feel less like a form while keeping deterministic fallback behavior.
Startup guidance now makes the runtime SQLite database the explicit source of
truth for current goal state, so fresh-context agents should inspect
database-backed status/plan details before scanning `.agent-loop/logs/`.
Default routing now removes Gemini 3.5 Flash from configured defaults. Normal
executor tasks prefer `agy` Gemini 3.1 Pro High, then Claude Sonnet 4.6
Thinking, then Codex gpt-5.4-mini; planning/review remains Codex gpt-5.5 high,
then Claude Opus 4.6 Thinking, then Gemini 3.1 Pro High.
Recent fine-grained-commit handoff changes were adjusted to tolerate mocked or
non-Git worktree directories when collecting review diff SHAs, to use the final
task SHA when creating integration tasks, and to refresh task state after
feature-review follow-up tasks are created so final review cannot run before
the follow-up work.
Live failover monitoring in `test-loop` showed Gemini 3.1 Pro High being marked
unavailable while Antigravity quota still reported remaining capacity. The root
cause was `agy` command construction and diagnostics classification: `agy`
treats the token immediately after `--print` as prompt text, and successful
assistant output can mention `--print-timeout` without indicating provider
failure.

## Next step

No further executor handoff is required for this request.

## Tests run

- Status description cleanup: `tests/test_cli.py::test_cli_status_uses_goal_language tests/test_cli.py::test_goal_description_truncates_cleanly` passed in 0.22s; `tests/test_cli.py` passed with 9 tests in 0.29s; real `agent-loop status 1` in `test-loop` showed a single curtailed description; full suite passed with 87 tests in 5.81s.
- Multi-turn brainstorming intake: focused CLI/UI Lab workflow tests passed with 5 tests in 0.60s; full suite passed with 88 tests in 14.18s.
- Planner failure investigation: new adapter regressions first failed for Codex output mentioning timeouts and agy timeout formatting, then passed after the fix. Live `agent-loop resume 1` in `test-loop` moved the goal to `awaiting_plan_approval` with 8 features and 10 tasks.
- Resume approval UX: `tests/test_cli.py::test_cli_resume_explains_awaiting_plan_approval tests/test_cli.py::test_cli_resume tests/test_cli.py::test_cli_resume_replans_blocked_goal_without_features tests/test_cli.py::test_cli_approve_command` passed with 4 tests in 0.20s.
- Bare default command UX: new default-command tests passed with 2 tests in 0.30s; `tests/test_cli.py` passed with 13 tests in 0.40s.
- Default pending-approval copy: focused default-command tests passed with 2 tests in 0.18s; `tests/test_cli.py` passed with 13 tests in 0.51s.
- Worktree setup retry limit: `tests/test_orchestrator.py::test_worktree_creation_failures_respect_retry_limit tests/test_orchestrator.py::test_interrupted_attempt_recovery` passed with 2 tests in 0.16s; `tests/test_orchestrator.py` passed with 14 tests in 3.12s. Live `test-loop` runaway process was stopped and Goal 1 was marked blocked with task 1 blocked after 657 failed attempts.
- Empty-repo bootstrap: focused git/CLI bootstrap tests passed with 2 tests in 0.59s; `tests/test_git_utils.py tests/test_cli.py tests/test_orchestrator.py` passed with 31 tests in 5.16s. Live `test-loop` received an `agent-loop: initialize repository` empty commit and a worktree smoke test succeeded.
- Empty-repo `.gitignore` and identity behavior: `tests/test_git_utils.py tests/test_cli.py` passed with 20 tests in 2.73s.
- Missing-Git bootstrap: focused missing/empty repo tests passed with 8 tests in 2.50s; `tests/test_git_utils.py tests/test_cli.py` passed with 23 tests in 5.18s.
- Adaptive brainstorm intake: focused brainstorm tests passed with 3 tests in 0.99s.
- Runtime source-of-truth docs: tests not run; documentation-only update.
- Default model routing and recent orchestrator fixes: confirmed local `agy models` labels; new route-order tests passed with 2 tests in 0.09s; focused CLI start/brainstorm tests passed with 3 tests in 0.79s; targeted orchestrator regressions passed with 2 tests in 1.13s; full suite passed with 106 tests in 9.34s.
- Agy print-mode failover regression: focused adapter tests passed with 4 tests in 0.05s; full suite passed with 107 tests in 18.71s.
- Planner schema/recovery fix: `tests/test_adapters.py::test_plan_schema_is_strict_for_codex_structured_output` passed in 0.02s; `tests/test_cli.py::test_cli_resume tests/test_cli.py::test_cli_resume_replans_blocked_goal_without_features` passed in 0.26s; live `codex exec --output-schema` smoke accepted the schema and returned valid plan JSON; `agent-loop resume 1` in `test-loop` regenerated a plan and moved Goal ID 1 to `awaiting_plan_approval`; full suite passed with 87 tests in 5.83s.
- Interactive multiline intake fix: `tests/test_cli.py::test_cli_start_captures_pasted_multiline_goal` passed in 0.27s; `tests/test_cli.py` passed with 8 tests in 0.38s; full suite passed with 85 tests in 5.84s.
- Goal terminology update: `tests/test_cli.py` passed in 0.30s; CLI help verified for goal wording.
- `.agent-loop/` workspace update: targeted CLI/orchestrator path tests passed in 0.95s; view rendering tests passed in 0.16s; full suite passed with 84 tests in 26.34s.
- Delivery documentation update: tests not run; CLI help verified for documented command groups.
- Final targeted tests: 3 passed in 0.25s.
- Real Codex parser smoke: 1 passed in 10.58s.
- `.venv/bin/python -m pytest -q`: 81 passed in 18.68s; clean worktree.
- Handoff validation: passed for request 13 and response 14.

## Blockers

None.

## Handoff

Supervisor review: `docs/handoffs/2026-06-15/12-supervisor-review.md`.
Next request: `docs/handoffs/2026-06-15/13-fix-request.md`.
Response: `docs/handoffs/2026-06-15/14-executor-response.md` (validated).
Supervisor acceptance: `docs/handoffs/2026-06-15/15-supervisor-review.md`.

LOOP_STATUS: complete
