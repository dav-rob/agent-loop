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
Default routing now uses current Codex 5.6 models by personality. Intake is
Codex Sol medium, then `agy` Claude Opus 4.6 Thinking, then Gemini 3.5 Flash
High. Normal execution remains cross-binary and `agy`-first, falling back from
Gemini 3.1 Pro High to Claude Sonnet 4.6 Thinking and then Codex Terra high.
Planning and escalation use Codex Sol xhigh; normal review uses Sol high.
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
Live route monitoring in `test-loop` confirmed the default executor failover is
three-step by design: `agy` Gemini 3.1 Pro High, then `agy` Claude Sonnet 4.6
Thinking, then Codex gpt-5.4-mini. A stale state issue was found separately:
tasks can remain `running` after all attempts are already terminal, which can
starve ready Codex fallback tasks through worker/active-file accounting.
Recovery now resets such stale running tasks to `ready` or `blocked` based on
the retry limit. Resume now preserves `auth_required` provider states so known
dead `agy` routes are not revived before Codex fallback can be selected.
Spec intake model calls now use provider-neutral route fallback with useful
diagnostics. Intake follows its configured personality order and writes
model-call logs under `.agent-loop/logs/intake/` instead of disposable temp
directories.
Task worktrees now default back to visible root-level `worktrees/` so `agy` can
open them as workspaces; `.agent-loop/` remains the home for the database,
logs, generated plan/progress/learning views, and specs. Bootstrap `.gitignore`
now ignores both `.agent-loop/` and `worktrees/`.
Planner output now treats `required_verification` as an executable shell command
contract. Prose verification strings are dropped during planning and skipped
defensively at runtime so they cannot be executed as commands like `Run`.
Bootstrap `.gitignore` also ignores `node_modules/` to prevent dependency
directories from being staged by Node-based target attempts.
Interactive intake option 2 now robustly maps to `none` even when the user
enters a copied menu label or types the choice quickly after the goal prompt,
and the brainstorming summary action now says `create plan` instead of
`draft spec`.
Interactive intake option 2 also tolerates echoed prompts and terminal control
sequences around the choice, so input like `Choice [1-2]: 2` or bracketed-paste
wrapped `2` still bypasses spec intake and starts planning.
The interactive start prompt now asks whether to brainstorm implementation
rather than exposing internal `spec`/`none` intake labels, and the generated
plan message names the goal by a short quoted description instead of showing
the internal numeric ID in that sentence.
The start confirmation now also uses the short quoted goal description and
user-facing mode names, so `none` intake is announced as `plan mode`.
Live monitoring of `test-loop-intake-revamp5` showed an implementation-style
app skeleton task running on the `planner` route with Codex `gpt-5.5` instead
of the Gemini-first `executor` route. The planner had misclassified a
file-changing scaffold task as role `planning` with empty `writes`; route
selection returned `planner` before executor escalation logic could apply.
Plan ingestion now normalizes file-scoped/executable planning tasks to
`implementation`, repairs empty write scopes from `files`, and route selection
defensively sends such tasks through executor/escalation profiles.
The generated `.agent-loop/progress.md` view now reports a task that has been
marked `running` even before the attempt row exists, and it re-renders after
the attempt receives worktree/log paths. Active work now shows pending
provider/model metadata instead of claiming there are no active attempts.
Live monitoring of `test-loop-intake-revamp4` showed repeated task-review
rejections still using the normal executor route (`agy` Gemini 3.1 Pro High)
instead of the configured `executor_escalated` route. Execution routing now
uses `escalation_threshold`: after two failed/abandoned attempts or two
rejected task reviews, the next implementation attempt uses the escalated
executor profile. The default retry limit is now five attempts, so attempts
3-5 can use stronger models before the max-attempt escalation/block path.
Live monitoring also showed task 4 staying ready while task 3 ran because both
declared `src/db.js` in legacy `scope.files`. Scheduler conflict checks now use
write scope only. New planner output includes `scope.writes` and `scope.reads`,
with legacy `scope.files` retained as the combined compatibility list.
Live monitoring of `test-loop-intake-revamp5` showed an escalated Codex
executor timeout falling through to the Opus route in the same attempt, while
the Codex child process continued running. Route fallback now stops on
execution timeouts, provider commands run in killable process groups, Codex
writes an explicit `codex.log`, and labelled non-JSON reviewer decisions are
parsed instead of becoming bogus rejected reviews.
Live monitoring then showed timed-out Codex executor attempts doing useful work
without returning a final handover, so later retries lost the partial context.
Timeout failures now synthesize an executor handover from provider logs,
command/file-change events, verification output, commits, and preserved
patches; a timeout review records whether the next attempt should retry with
that handover, abandon the partial context, resume, or block. Retry prompts now
include previous timeout handovers and timeout-review findings.
The broader lifecycle layer is now explicit. Runtime lifecycle events are stored
in a `lifecycle_events` table through `TaskLifecycleRecorder`, and generated
`.agent-loop/progress.md` includes a recent event timeline covering task start,
attempt start, executor start/completion/failure, review start/completion,
retry, completion, and blocking callouts.
Task execution now uses one durable task branch/worktree per task by default
(`agent-loop-run-{goal_id}-task-{task_id}` and
`worktrees/run-{goal_id}-task-{task_id}`), while keeping logs attempt-scoped.
Normal reviewer rejection keeps the task worktree in place so the next attempt
continues from the existing branch instead of recreating work from prose.
Executor retry prompts now say when an attempt is continuing an existing task
branch, and task reviewer prompts ask for precise continuation-oriented repair
guidance with restart/block only when justified.
Merge-conflict recovery follow-ups now preserve the original blocked task link
through the whole recovery chain. If conflict resolution needs follow-up work,
that follow-up can complete the original task once approved instead of leaving
the goal blocked after successful recovery. Merge-conflict recovery tasks are
also routed as implementation work when they edit files.
Phase 2 retry strategy/recovery metadata is now implemented. Attempts persist
attempt boundary and strategy fields, task and timeout reviewers can return
retry strategies, interrupted resume recovery preserves useful patch/worktree
evidence instead of deleting it by default, and attempt startup executes the
recorded strategy (`continue_existing_branch`, restart variants,
`apply_patch_to_clean_branch`, or `block_for_human`). Generated progress and
task handover markdown now expose strategy metadata for monitoring.

## Next step

No further executor handoff is required for this request.

## Tests run

- Route-profile config and centralized model routing: focused routing/config/intake/quota/orchestrator slices passed with 36 tests; full suite initially exposed stale tests that were still patching old adapter paths or expecting legacy intake modes. Those were reconciled to the central router and current spec/none intake menu. Final verification: `PYTHONPATH=src ../agent-loop/.venv/bin/python -m pytest -q` passed with 134 tests in 19.84s.
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
- Executor/reviewer failover and stale recovery: focused route, sticky-auth, and recovery regressions passed with 4 tests in 0.16s.
- Retry-limit escalation follow-up crash: new regression first failed on the live crash path, then passed after adding `ReviewRepository.get_latest_for()`; `tests/test_config.py tests/test_orchestrator.py` passed with 22 tests in 3.06s.
- Idempotent retry reset crash: added regression for already-ready retry cleanup; focused config/orchestrator suite passed with 23 tests in 6.17s.
- Codex event parsing and execution follow-up extension: added adapter and orchestrator regressions; `tests/test_adapters.py tests/test_config.py tests/test_orchestrator.py` passed with 38 tests in 38.45s.
- Intake model fallback: `tests/test_intake.py tests/test_adapters.py` passed with 21 tests in 4.11s. Live brainstorm smoke from `test-loop-intake-revamp` fell back after `agy` auth-required diagnostics and produced a compact spec instead of the auto-draft failure.
- Visible task worktrees: focused config/git/CLI/orchestrator tests passed with 17 tests in 3.62s; full suite passed with 137 tests in 20.35s.
- Verification command contract: new regressions for prose verification planning/runtime handling and `node_modules/` ignore passed; full suite passed with 139 tests in 17.78s.
- Intake none-mode and summary-copy fix: targeted CLI/intake regressions passed with 3 tests in 0.88s; `tests/test_cli.py tests/test_intake.py` passed with 28 tests in 3.92s; full suite passed with 141 tests in 21.57s.
- Executor escalation threshold fix: new regressions first failed because `execution_profile_for_task` ignored failed attempts/rejected reviews and the live-style third task attempt still used `executor`; after the fix, focused escalation/config tests passed with 5 tests in 0.66s, `tests/test_config.py tests/test_routing.py tests/test_orchestrator.py` passed with 41 tests in 3.60s, and final full-suite verification passed with 144 tests in 19.75s.
- Write-scope scheduling fix: new parallel scheduler regressions first showed overlapping `writes` were ignored; after the fix, shared read scopes run concurrently while overlapping write scopes serialize. Focused scheduler tests passed with 2 tests in 3.29s, schema/planning smoke tests passed with 4 tests in 0.54s, the broader adapters/config/routing/orchestrator slice passed with 57 tests in 16.45s, and final full-suite verification passed with 146 tests in 23.36s.
- Intake prompt-echo parser fix: new regressions first failed because `Choice [1-2]: 2` defaulted to spec intake; after the fix, prompt-echo and bracketed-paste wrapped choices select the intended mode. Focused regressions passed with 2 tests in 0.38s, `tests/test_cli.py` passed with 20 tests in 3.65s, and full-suite verification passed with 148 tests in 17.79s.
- Intake wording fix: the start-flow regression first failed on the old `Select Intake Mode` prompt; after the copy update, focused intake prompt/message tests passed with 3 tests in 0.68s, the prior spec-mode mock regression was fixed, and final full-suite verification passed with 148 tests in 22.44s. The follow-up start-confirmation wording regression first failed on `Started goal 1 in none mode`; after the fix, focused start/intake tests passed with 5 tests in 0.65s and final full-suite verification passed with 148 tests in 23.00s.
- Misclassified planning-task routing fix: new regressions first failed because a file-scoped `planning` task stayed on the `planner` profile and bad plan output was stored unchanged; after the fix, focused regressions passed with 2 tests in 0.17s, the broader config/routing/orchestrator slice passed with 45 tests in 7.24s, and final full-suite verification passed with 150 tests in 17.89s.
- Progress view active-work fix: new view regressions first failed because a running task with no attempt row still rendered `No active task attempts` and pending provider/model metadata printed as blank values; after the fix, focused view regressions passed with 2 tests in 0.06s, the view/execution slice passed with 5 tests in 0.36s, and final full-suite verification passed with 152 tests in 22.28s.
- Codex timeout/fallback cleanup: new regressions first failed because timed-out execution still fell through to the Opus route, Codex lacked a provider-specific log file, and labelled `Decision: Approved` reviewer output was stored as rejected. After the fix, focused regressions passed with 4 tests in 0.58s, the affected adapter/router/orchestrator slice passed with 56 tests in 6.92s, and the local full suite passed with 155 tests in 14.19s with the explicit real Codex smoke test deselected.
- Timeout handover/review lifecycle: new regressions first failed because no timeout review hook existed and retry prompts omitted previous timeout handovers. After the fix, focused task-handover/review/router regressions passed with 7 tests in 0.33s, and final full-suite verification passed with 161 tests in 29.13s.
- Lifecycle event recorder: new regressions first failed because `LifecycleEventRepository` did not exist and orchestrator execution emitted no lifecycle events. After adding schema version 6, `TaskLifecycleRecorder`, progress timeline rendering, and orchestrator callouts, focused lifecycle regressions passed with 5 tests in 0.65s and full-suite verification passed with 163 tests in 28.20s.
- Durable task branch retry: new regression first failed because rejected retry attempts used attempt-scoped branches/worktrees and removed the worktree after rejection; after the fix, focused durable retry/reviewer prompt tests passed and `tests/test_orchestrator.py` plus related handover/view/git regression slices passed.
- Typed recovery follow-up closure: new regressions first failed because merge-conflict recovery follow-ups lost the original task link and recovery tasks were routed as planning work. After the fix, focused recovery tests passed with 2 tests, the merge/review interaction slice passed with 5 tests, and full-suite verification passed with 167 tests in 27.48s.
- Retry strategy/recovery review Phase 2: new regressions first failed for missing attempt strategy/SHA metadata, missing review strategy persistence, destructive interrupted-work cleanup, missing strategy-aware worktree preparation, `block_for_human` requeueing instead of blocking, and missing strategy display in progress/handover views. After the fix, `tests/test_retry_strategy.py` passed with 11 tests, the affected database/view/handover/orchestrator/recovery slice passed with 94 tests, and full-suite verification passed with 180 tests in 28.98s.
- Planner schema/recovery fix: `tests/test_adapters.py::test_plan_schema_is_strict_for_codex_structured_output` passed in 0.02s; `tests/test_cli.py::test_cli_resume tests/test_cli.py::test_cli_resume_replans_blocked_goal_without_features` passed in 0.26s; live `codex exec --output-schema` smoke accepted the schema and returned valid plan JSON; `agent-loop resume 1` in `test-loop` regenerated a plan and moved Goal ID 1 to `awaiting_plan_approval`; full suite passed with 87 tests in 5.83s.
- Interactive multiline intake fix: `tests/test_cli.py::test_cli_start_captures_pasted_multiline_goal` passed in 0.27s; `tests/test_cli.py` passed with 8 tests in 0.38s; full suite passed with 85 tests in 5.84s.
- Goal terminology update: `tests/test_cli.py` passed in 0.30s; CLI help verified for goal wording.
- `.agent-loop/` workspace update: targeted CLI/orchestrator path tests passed in 0.95s; view rendering tests passed in 0.16s; full suite passed with 84 tests in 26.34s.
- Delivery documentation update: tests not run; CLI help verified for documented command groups.
- Final targeted tests: 3 passed in 0.25s.
- Real Codex parser smoke: 1 passed in 10.58s.
- `.venv/bin/python -m pytest -q`: 81 passed in 18.68s; clean worktree.
- Handoff validation: passed for request 13 and response 14.
- Task-escalation review worktree fix: the new regression first failed because escalation reviews inherited the repository-root default; after passing the durable task worktree explicitly, the focused review/retry slice passed with 54 tests and the full suite passed with 183 tests in 23.73s.
- Goal lifecycle storage: migration 8 adds validated goal types, structured recommendations, and one delivery record per goal. New storage regressions and the existing database suite pass with 14 tests.
- Goal type intake: first goals default to prototype, later goals use structured inference with a deterministic fallback, interactive users can confirm or correct the type before planning, and unattended starts persist the inference automatically. Intake/CLI/view coverage passes with 41 tests.
- Goal-type review policy: a central policy formatter now supplies prototype, extend, refine, repair, harden, and investigate blocking standards to every reviewer. Policy/orchestrator/retry coverage passes with 63 tests.
- Structured recommendations: reviewer JSON now carries validated recommendation records linked to its review and subject. Confirmed feature follow-ups complete without spawning tasks, prototype final follow-ups can deliver with known limits, and legacy unconfirmed goals retain prior behavior. Review/orchestrator/retry coverage passes with 64 tests.
- Delivery reports: confirmed final reviews now persist structured delivery evidence; web goals require a launch command, local URL, and launch evidence, and successful goals render `.agent-loop/delivery-report.md` from SQLite with grouped recommendations. Delivery/config/orchestrator coverage passes with 58 tests.
- Recommendation adoption: intake can select open recommendations from the latest completed goal, unattended starts accept `--recommendations`, planning receives complete recommendation context, and successful completion resolves selected items. CLI/intake/view/orchestrator coverage passes with 77 tests.
- Goal lifecycle integration: documentation now covers the flat goal-type model, recommendation adoption, and delivery reports. Final full-suite verification passed with 220 tests in 21.41s and `git diff --check` passed.

## Blockers

None.

## Handoff

Supervisor review: `docs/handoffs/2026-06-15/12-supervisor-review.md`.
Next request: `docs/handoffs/2026-06-15/13-fix-request.md`.
Response: `docs/handoffs/2026-06-15/14-executor-response.md` (validated).
Supervisor acceptance: `docs/handoffs/2026-06-15/15-supervisor-review.md`.

LOOP_STATUS: complete
