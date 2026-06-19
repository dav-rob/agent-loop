---

name: debugging
description: Use when fixing bugs, failed tests, flaky behaviour, regressions, build failures, runtime errors, performance problems, state pollution, or unexplained behaviour.
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# Debugging

## Core principle

Use judgement, but do not guess.

Fix the cause, not the visible symptom.

Debugging is complete only when:

* the failure is understood
* the fix addresses the cause
* the fix has been verified
* temporary debugging mess has been removed

## When to use

Use this skill for:

* failing tests
* flaky tests
* production bugs
* build failures
* runtime errors
* regressions
* async/timing failures
* performance anomalies
* bad data moving through a system
* unwanted files, state, database rows, caches, or side effects
* any bug where the first fix did not work

Use it especially when:

* the obvious fix is tempting
* the user says “just fix this”
* the failure is intermittent
* several components are involved
* the stack trace is deep
* a bad value appears far from where it originated
* you have already tried one fix
* you feel pressure to move quickly

## Modes

### light mode

Use for simple, local, obvious failures.

Do this:

1. read the full error
2. reproduce the failure
3. inspect the smallest relevant code path
4. check recent changes if relevant
5. make one focused fix
6. verify
7. clean up

Do not turn light mode into a ritual.

### deep mode

Use when the cause is unclear, the system has several layers, or a previous fix failed.

Do this:

1. reproduce or isolate the failure
2. read logs, stack traces, and assertions carefully
3. trace control flow and data flow backwards
4. add temporary instrumentation only where it answers a specific question
5. form one hypothesis at a time
6. test the smallest change that confirms or disproves that hypothesis
7. fix the cause
8. add a regression test where practical
9. verify
10. clean up

### stabilise mode

Use only for outage-style or destructive failures where immediate mitigation matters.

Do this:

1. make the smallest reversible mitigation
2. avoid broad speculative changes
3. record that this is mitigation, not root-cause fix
4. continue root-cause debugging afterwards
5. remove or formalise the mitigation once the real cause is fixed

Examples:

* temporarily disable a broken feature flag
* roll back a release
* restore a known-good config
* add a narrow guard around destructive behaviour

Do not pretend a mitigation is the real fix.

## Investigation

Before proposing a fix, gather enough evidence to answer:

* what failed?
* where did it fail?
* what input/state/config existed at the failure point?
* what changed recently?
* where did the bad value or bad state originate?
* is this a local bug, cross-component bug, timing bug, test pollution, or design problem?

Minimum investigation:

* read the full error message
* read the relevant stack trace
* inspect the failing assertion or symptom
* identify the code path that produced the failure
* reproduce locally or state why reproduction is not available

## Root-cause tracing

Use `root-cause-tracing.md` when:

* the bug appears deep in the stack
* a dangerous operation receives a bad value
* a file/database/network operation happens in the wrong place
* the visible failure is probably not the source

Pattern:

1. observe the symptom
2. find the immediate cause
3. ask “what called this?”
4. inspect the value passed at each layer
5. keep tracing backwards
6. stop only when you find the original trigger
7. fix at the source
8. add guards at downstream layers only if useful

Do not fix only where the error appears.

## Instrumentation

Add logs, traces, dumps, or probes only to answer a specific question.

Good instrumentation:

* logs boundary inputs and outputs
* includes relevant IDs, paths, config, state, and timing
* captures stack traces before dangerous operations
* distinguishes expected from unexpected states
* is temporary unless intentionally retained

Bad instrumentation:

* logs everything everywhere
* adds noise without a hypothesis
* hides the race by changing timing
* remains after the bug is fixed
* exposes secrets or private data
* turns tests into log soup

In tests, direct `console.error` or equivalent may be better than a suppressed logger.

## Hypothesis discipline

Work one hypothesis at a time.

Format:

* I think the cause is X
* because evidence Y
* I will test it by doing Z
* success means A
* failure means B

Rules:

* do not bundle multiple speculative fixes
* do not refactor while debugging unless the refactor is the fix
* do not make “while I’m here” changes
* do not keep adding patches after a failed patch
* use failed fixes as evidence

If a fix fails:

1. stop
2. inspect what changed
3. update the hypothesis
4. return to investigation if needed

If three fixes fail:

1. stop
2. reassess the architecture or model of the system
3. look for hidden shared state, wrong abstraction, invalid assumption, missing lifecycle boundary, or unsuitable test design
4. do not attempt a fourth speculative fix

## Flaky and async tests

Use `condition-based-waiting.md` when:

* tests use `sleep`, `setTimeout`, `time.sleep`, fixed delays, or arbitrary waits
* tests pass locally but fail in CI
* tests fail under load or in parallel
* async work may not have completed before assertions

Prefer:

* wait for event
* wait for state
* wait for count
* wait for file
* wait for queue drain
* wait for process exit
* wait for observable condition

Avoid:

* increasing sleeps
* guessing machine timing
* masking races with long delays

Arbitrary timeout is allowed only when testing real timing behaviour.

If using one:

* first wait for the triggering condition
* base the timeout on known timing
* comment why the timeout is required
* keep it as small as the behaviour permits

## Defence in depth

Use `defense-in-depth.md` after finding root cause when:

* bad data can enter through several paths
* mocks or tests can bypass the normal entry point
* a dangerous operation must never happen in certain contexts
* fixing one layer would still allow future bypasses

Possible layers:

1. entry-point validation
2. business-logic validation
3. environment guard
4. diagnostic instrumentation

Add multiple layers only when they make the bug structurally harder to reintroduce.

Do not use defence-in-depth as an excuse for not finding the original cause.

## Test pollution

Use `find-polluter.sh` when:

* a test creates unwanted files or directories
* a test mutates shared state
* a test leaves `.git`, cache, database, temp, config, lock, or generated files behind
* pollution appears only after a full test run
* individual tests pass but the suite fails

Pattern:

1. identify the polluted file/state
2. confirm it does not exist before the run
3. run tests one by one or by bisection
4. stop at the first polluter
5. inspect that test’s setup and teardown
6. fix lifecycle/cleanup/root cause
7. rerun the suite

## Fixing

A good fix is:

* small
* causal
* verified
* covered by a test where practical
* free of unrelated edits
* robust against the same bug returning

Before editing:

* know the likely cause
* know the expected effect of the change
* know how to verify it

After editing:

* run the smallest relevant verification first
* then run the broader affected test set
* then inspect the diff

## Cleanup

Before declaring completion, remove:

* temporary console logs
* debug prints
* stack dumps
* timing probes
* scratch scripts
* arbitrary sleeps
* broad retries
* temporary TODOs
* commented-out code
* generated files
* polluted test state
* accidental formatting churn
* unrelated refactors

Keep diagnostics only when they are intentional.

Retained diagnostics must be:

* structured
* gated if noisy
* safe for secrets/privacy
* useful for future operation
* documented where needed

Always inspect the final diff.

## Completion checklist

Before saying the bug is fixed, confirm:

* root cause identified
* fix made at the right level
* no shotgun patches remain
* relevant tests pass
* regression test added where practical
* temporary instrumentation removed
* arbitrary waits removed or justified
* generated/polluted files removed
* diff contains only intentional changes

## Completion report

Report in this form:

```text
root cause:
fix:
verification:
cleanup:
remaining risk:
```

Keep it short.

## Red flags

Stop and re-investigate if you think:

* “I’ll just try this”
* “This is probably it”
* “One more patch”
* “The test only needs a longer sleep”
* “I’ll clean the logs later”
* “This is good enough”
* “The symptom is gone, so it’s fixed”
* “I don’t understand it, but the change works”
* “This refactor might fix it”
* “The senior person says this is the pattern, so no need to check”

## Companion files

Use these when the bug shape matches:

* `root-cause-tracing.md`
  Trace backwards through the call chain to find the original trigger.

* `condition-based-waiting.md`
  Replace arbitrary sleeps with waiting for the actual condition.

* `defense-in-depth.md`
  Add validation at multiple layers after the root cause is known.

* `find-polluter.sh`
  Find the test or command that creates unwanted files or state.

## Default behaviour

When asked to “fix this” or “debug this”:

1. start in light mode
2. switch to deep mode if the cause is unclear
3. use stabilise mode only for outage/destructive cases
4. apply companion techniques only when relevant
5. always clean up before completion
