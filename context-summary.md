# Context Summary - 2026-06-18

This file records the state after the live `agent-loop` monitoring/debugging
session, so a fresh agent context can resume without re-discovering the same
facts.

## Repositories

- Agent-loop repo: `/Users/davidroberts/projects/quick-scripts/agent-loop`
- Live target repo: `/Users/davidroberts/projects/quick-scripts/test-loop`
- Target runtime DB source of truth:
  `/Users/davidroberts/projects/quick-scripts/test-loop/.agent-loop/agent-loop.db`

Do not infer live status from logs first. Query the target DB or use
`agent-loop status`/`agent-loop plan --details` first.

## Current agent-loop repo state

Local `main` is ahead of `origin/main` by five commits:

21. `8614227 Fix escalation follow-up review lookup`
22. `059431a fix: orchestrator transition and repositories.py enhancements`
23. `098e91a Make task retry reset idempotent`
24. `5e05fdf Remove duplicate review lookup method`
25. `d135329 Handle Codex event messages and extend retry follow-ups`
26. `3b26ebc Refactor task escalation to use attempt outcome reset instead of scope limit hacks`

There are no known uncommitted `src/agent_loop/orchestrator.py` changes after
this summary was updated. A previous uncommitted attempt to centralize
task-specific retry-limit handling was reverted at the user's request.

## Why the five commits exist

### `8614227 Fix escalation follow-up review lookup`

The live loop crashed after a retry-limit escalation because the code called
`ReviewRepository.get_latest_for(...)`, but that method did not exist.

This commit added `get_latest_for()` and a regression test proving a retry-limit
follow-up can read the latest escalation findings.

Assessment: correct targeted fix. It did not directly affect the target app
build.

### `059431a fix: orchestrator transition and repositories.py enhancements`

This was a partial fix for task status transition crashes during failure
cleanup. It added a guard around `failed -> ready` transitions and accidentally
introduced a duplicate `get_latest_for()` method.

Assessment: partial/rushed fix. It reduced one failure shape but did not fully
solve idempotent retry reset. The duplicate method was later removed.

### `098e91a Make task retry reset idempotent`

This commit added `_reset_task_for_retry(task_id)` and replaced many direct
`failed -> ready` status transitions with that helper.

The live crash being addressed was:

```text
ValueError: Invalid task status transition from 'ready' to 'failed'
```

Assessment: this is the cleaner fix for re-entrant/stale failure cleanup. It
likely reduced parent process crashes that were leaving child Codex/Node
processes running.

### `5e05fdf Remove duplicate review lookup method`

This removed the duplicate `ReviewRepository.get_latest_for()` introduced by
the partial fix.

Assessment: cleanup only; no intended behavior change.

### `d135329 Handle Codex event messages and extend retry follow-ups`

This commit fixed two live issues:

- Codex JSONL output now sometimes reports assistant text as
  `{"type":"item.completed","item":{"type":"agent_message","text":"..."}}`.
  The adapter now extracts that text instead of passing raw event JSONL to
  review parsing.
- Execution-failure escalation with `follow_up` now extends the same task by
  adding `extended_limit` and `escalation_hint` to scope, instead of creating
  recursively nested `Follow-up: Follow-up: ...` tasks.

Assessment: the Codex event parsing fix was necessary. The retry extension behavior using `extended_limit` in the scope was flawed because it missed updating several hardcoded `max_attempts` checks throughout the orchestrator lifecycle, leading to immediate task blockages.

### `3b26ebc Refactor task escalation to use attempt outcome reset instead of scope limit hacks`

This commit replaced the flawed `extended_limit` hack introduced in `d135329`. 
Instead of patching task limits across the codebase, when a reviewer grants an escalation extension, the orchestrator now uses `AttemptRepository.escalate_failed_attempts` to change the outcome of all previous `failed` and `abandoned` attempts to `escalated`.

Assessment: The correct architectural fix. Since the orchestrator calculates exhausted limits by counting attempts where `outcome` is `failed` or `abandoned`, changing the outcome to `escalated` naturally resets the failure count to 0, granting the task a fresh slate of `max_attempts` without any complex state modifications or nested dependencies.

## Test evidence for the five commits

Recorded verification during the session:

- After `8614227`:
  `.venv/bin/python -m pytest tests/test_config.py tests/test_orchestrator.py -q`
  -> `22 passed`
- After `098e91a`:
  `.venv/bin/python -m pytest tests/test_config.py tests/test_orchestrator.py -q`
  -> `23 passed`
- After `5e05fdf`:
  focused tests for retry-limit follow-up and idempotent reset -> `2 passed`
- After `d135329`:
  `.venv/bin/python -m pytest tests/test_adapters.py tests/test_config.py tests/test_orchestrator.py -q`
  -> `38 passed`

## Live target state after monitoring

The target run in `/Users/davidroberts/projects/quick-scripts/test-loop` ended
blocked, not complete.

Last queried target DB state:

- Run 1: `blocked`
- Tasks: `22 complete`, `5 blocked`, `1 reviewing`, `10 pending`
- Blocked tasks included: 11, 26, 33, 34, 35
- Task 9 was still `reviewing`

Latest recorded attempts:

- Attempt 93: task 36, Codex `gpt-5.5`, completed at
  `2026-06-18 11:41:33 UTC`
- Attempt 94: task 37, Codex `gpt-5.4-mini`, completed at
  `2026-06-18 11:44:08 UTC`
- Review 72 then rejected feature 5 at `2026-06-18 11:46:04 UTC`

Review 72 said `npm run build` failed with TypeScript errors in:

- `src/server/index.ts`
- `src/server/jobs/hourly-refresh.ts`
- `src/server/store.ts`

The target repo itself was dirty, with modified app/server files and leftover
test output files such as `test_stdout.txt` and `test_stderr.txt`.

## Provider routing and failback

Failover to Codex did work. Example observed path:

- Task 27 failed on `agy` Gemini 3.1 Pro High.
- Then failed on `agy` Claude Sonnet.
- Then Codex handled the follow-up path.

Failback to first-choice `agy` Gemini 3.1 Pro did not happen according to the
target DB.

Last recorded `agy` attempt:

- Attempt 59, task 27, `agy` Claude Sonnet 4.6, failed at
  `2026-06-18 05:17:31 UTC`

Last recorded `agy` Gemini 3.1 Pro attempt:

- Attempt 57, task 27, failed at `2026-06-18 05:14:42 UTC`

After that, recorded attempts were Codex. Provider state still marked:

- `agy/Gemini 3.1 Pro (High)` as unavailable / `auth_required`
- `agy/Claude Sonnet 4.6 (Thinking)` as unavailable / `auth_required`

There was no later recorded probe that restored those routes. If Antigravity
usage showed later token consumption, the target agent-loop DB does not show
agent-loop consuming those tokens after `05:17:31 UTC`.

## Process pile-up explanation

The many Codex/Node/Vitest processes the user killed were probably caused by
parent `agent-loop resume` crashes or stale runs leaving child processes alive.

Observed contributing issues:

- Missing `ReviewRepository.get_latest_for()` crashed escalation follow-up.
- Invalid task transition `ready -> failed` crashed failure cleanup.
- Codex event JSONL was sometimes passed through as raw output and broke review
  parsing.
- Long-running/stale Codex attempts and verification processes accumulated
  under target worktrees.

This cannot prove every killed process came from agent-loop, but it matches the
DB/log/process pattern seen during monitoring.

## Cautions before resuming the live target

- Do not run `antigravity-usage` loops. The user explicitly objected to that
  earlier.
- Do not restart `agent-loop resume` blindly against the dirty target repo.
- Query the DB first and inspect the dirty target diff.
- Treat failback as an unresolved product bug: routes marked `auth_required` or
  unavailable are not being automatically restored when agy tokens/auth recover.
- The target app build failure is a real review rejection, not just a wedged
  orchestrator state.

## Deferred considerations

The provider failback issue remains unaddressed. Routes marked `auth_required` or unavailable are not automatically restored when tokens/auth recover.

Also, the target build `test-loop` is currently broken and the orchestrator ran out of options on its tasks. The recommended path is to start a completely new goal in `test-loop` such as: "Debug the current TypeScript compilation errors and ensure the project builds and npm test passes" to verify the new retry escalation architecture.
