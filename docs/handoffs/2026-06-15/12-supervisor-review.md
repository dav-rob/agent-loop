# Supervisor Review: Execution Fixes Fourth Pass

## Handoff Metadata

- Type: review
- Reviews: 11-executor-response.md
- Result: changes-required

## Verification

The exact suite and handoff validator pass, and the worktree remains clean:

```text
.venv/bin/python -m pytest -q
78 passed in 14.43s

.venv/bin/agent-loop handoff validate \
  docs/handoffs/2026-06-15/10-fix-request.md \
  docs/handoffs/2026-06-15/11-executor-response.md
Handoff validation passed: 9 requirements accounted for.
```

The real Codex parser smoke was reproduced:

```text
.venv/bin/python -m pytest \
  tests/test_adapters.py::test_codex_adapter_real_smoke_test -v
1 passed in 9.66s
```

## Findings

1. **High - actual task execution still ignores planning role.**
   `get_required_routes` now classifies a low-risk planning task correctly, but
   `_execute_task_impl` still chooses `route_key` solely from risk and attempt
   count (`orchestrator.py:699-703`). A direct runtime probe with separate
   planning and implementation providers selected `codex` and recorded route
   `implementation` for a `role="planning", risk="low"` task. This also causes
   generated architectural-assessment tasks to execute on the wrong route.

2. **High - CHECK4-02 is not an end-to-end lifecycle fixture.**
   `test_end_to_end_fixture_lifecycle` manually creates planning records,
   executes its two ready tasks sequentially, labels ordinary follow-up
   execution as serialized integration, manually changes quota state without
   calling quota recovery, calls final review directly, and manually transitions
   the run to `complete`. It does not exercise `plan_run`, parallel scheduling,
   merge-conflict integration, quota wait/sleep/recovery, regression execution,
   or final completion through `run_loop` as required.

3. **Medium - CHECK4-01 overstates notification evidence.**
   `test_rich_quota_notifications` sends one notification and checks only secret
   redaction in the webhook `text` field. It does not send a duplicate or assert
   deduplication, and it does not assert the claimed structured keys
   `run_id`, `event_type`, `message`, and `timestamp`.

4. **Medium - response and progress reporting are inconsistent.**
   The response's FIX rows cite commits omitted from its seven-commit table. The
   actual fourth-pass history contains twelve commits from `bebbf9d` through
   `d8bb776`. `progress.md` still says validation is pending and
   `LOOP_STATUS: running`, despite the response claiming validated completion.

## Conclusion

The preservation, review-action context, portable quota probes, artifacts, and
real Codex smoke are materially improved. The actual route-selection defect and
missing end-to-end evidence prevent acceptance of `Overall-Status: complete`.
