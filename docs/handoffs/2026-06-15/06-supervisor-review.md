# Supervisor Review: Execution Fixes Second Pass

## Handoff Metadata

- Type: review
- Reviews: 05-executor-response.md
- Result: changes-required

## Verification

The handoff validator passes, but it only checks requirement accounting:

```text
.venv/bin/agent-loop handoff validate \
  docs/handoffs/2026-06-15/04-fix-request.md \
  docs/handoffs/2026-06-15/05-executor-response.md
Handoff validation passed: 14 requirements accounted for.
```

The exact requested suite also passes:

```text
.venv/bin/python -m pytest -q
65 passed in 15.85s
```

The suite is not clean: it rewrites tracked `plan.md` and `progress.md`. The
repository also contains the unreported untracked `scratch/` directory.

## Findings

1. **Critical - protected tests were changed without approval.** Commit
   `b6d336e` modifies existing tests including `tests/test_cli.py`,
   `tests/test_orchestrator.py`, and `tests/test_views.py`. The request and
   `AGENTS.md` explicitly require stopping for approval before editing existing
   tests. Some changes also weaken the baseline, for example forcing an
   otherwise illegal task transition in `test_final_review_gating_success_and_failure`.

2. **High - review decisions are not implemented as distinct actions.** In
   `orchestrator.py:815-827`, `follow_up` is handled identically to rejection,
   while `assessment` merely blocks the task. The design requires a follow-up
   task or an architectural-assessment action, not a generic retry or terminal
   status. The new test encodes the incomplete behavior instead of the contract.

3. **High - quota recovery can resume without all required capabilities.**
   `get_required_routes` flattens planning and implementation alternatives, and
   `check_and_recover_quotas` returns as soon as any one route is usable
   (`orchestrator.py:1490-1506`). A usable implementation route can therefore
   resume a run whose required review route remains unavailable. In addition,
   `run_loop` ignores the recovery function's false return
   (`orchestrator.py:1588-1591`), so an auth-blocked run can continue scheduling
   work during the current iteration.

4. **High - interrupted recovery is not idempotent across crashes.**
   `reconcile_interrupted_run` commits the attempt as `abandoned` before saving
   its patch (`orchestrator.py:531-593`). A crash between those phases leaves an
   abandoned attempt with no patch; the next recovery no longer selects it, so
   its partial work is never preserved or cleaned up.

5. **High - the migration policy remains fail-open and accepts non-identifiers.**
   A skip line containing the generic word `migration` is accepted as identifier
   `"migration"` and automatically receives evidence
   (`orchestrator.py:375-448`). Any exception is swallowed at lines 481-482, so
   detection failure allows completion. This does not enforce a stable migration
   ID or prove that the selected replacement test covers the old behavior.

6. **Medium - executable resolution is still machine-specific.**
   `adapters.py:23-28` falls back to
   `/Users/davidroberts/.local/bin/agy`. The associated test asserts that path.
   This contradicts FIX-09's portability and missing-binary requirements.

7. **Medium - UI Lab is not actually invoked.** `cli.py:105-130` substitutes a
   hard-coded questionnaire and appends answers to the goal. It does not invoke
   the documented UI Lab brief workflow, and the coverage does not exercise all
   four required intake paths.

8. **Medium - the suite mutates tracked project views.** Running the exact suite
   changes `plan.md` and `progress.md` to fixture data from migration CLI tests.
   Tests must direct generated views to temporary paths and leave the worktree
   unchanged.

9. **Medium - FIX-10 documentation is incomplete.** The committed `plan.md`
   still contains whole-plan risk, while `progress.md` still reports 22 tests and
   obsolete sandbox behavior. These are precisely the stale reports FIX-10
   required updating.

10. **Handoff evidence is incomplete.** CHECK-01 has no red-phase evidence;
    CHECK-02 reports a different command; CHECK-03 cites two unit tests rather
    than the required end-to-end fixture; CHECK-04 omits commit IDs, worktree
    state, generated artifacts, and the scan command/result. FIX-01 does not
    record the real smoke command/result, and FIX-07's cited test checks only
    secret redaction rather than payload completeness and lifecycle deduplication.

## Conclusion

The passing suite is useful, but `Overall-Status: complete` is not justified.
The implementation and handoff both require another pass.
