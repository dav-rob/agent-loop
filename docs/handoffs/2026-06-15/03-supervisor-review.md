# Supervisor Review: Execution Fixes

## Handoff Metadata

- Type: review
- Reviews: 02-executor-response.md
- Result: changes-required

## Verification

The reported test suite result was reproduced:

```text
.venv/bin/python -m pytest -q
46 passed in 7.48s
```

Passing tests do not establish completion because several required runtime
paths are mocked or absent from the suite.

## Findings

1. **Critical:** Codex execution is broken. The adapter passes `-a never` after
   `codex exec`; the installed CLI rejects it with exit code 2.
2. **High:** Task review rejection can retry indefinitely and structured
   `block`, `assessment`, and `follow_up` decisions are not implemented.
3. **High:** Interrupted and quota-failed attempts remove worktrees without
   preserving partial diffs for audit or provider handover.
4. **High:** A run can become `complete` without executing the configured
   regression-test command.
5. **High:** Quota state handling conflates transient failures with
   authentication failures, checks only implementation routes, and resumes
   execution without proving a route recovered.
6. **High:** Test migration detection does not prove that a new test replaces
   the skipped behavior, does not require a migration identifier, and provides
   no user approval command for pending migrations.
7. **High:** Notifications omit required provider, model, reset-window,
   evidence, and fallback details; completion and pending-migration alerts are
   absent.
8. **Medium:** UI Lab intake does not invoke the requested UI Lab workflow, and
   non-interactive brainstorm/UI modes can still stop for approval.
9. **Medium:** Provider executable paths are machine-specific and `agy` does
   not receive a matching `--print-timeout` value.
10. **Medium:** The four-worker maximum is not enforced and `plan.md` still
    assigns an overall plan risk.

## Handoff Process Finding

The previous request described required work but did not force a
requirement-by-requirement response. The executor could therefore report a
passing suite and broad completion summary while omitting requirements. The
next request uses stable IDs and must be validated before completion is
claimed.

