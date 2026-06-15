# AGENTS.md

## Mission

Work autonomously from the user's goal until complete, blocked, or unsafe.

Do not stop for conversational input unless a stop condition is hit.

## Files

- `plan.md` = current task breakdown
- `progress.md` = current execution state
- `learning.md` = durable project knowledge
- `skills/` = reusable methods and workflows

## Work loop

Repeat:

1. Read `AGENTS.md`, `plan.md`, `progress.md`, `learning.md`.
2. Choose the next smallest useful step.
3. Execute it.
4. Run the narrowest relevant tests.
5. Update `progress.md`.
6. Update `learning.md` only for reusable facts.
7. Continue unless a stop condition is hit.

## Stop conditions

Stop only when:

- task is complete
- protected test change is required
- credentials/secrets are missing
- destructive action is required
- product decision is genuinely ambiguous
- same failure repeats three times
- all useful local work is exhausted

## Test policy

Existing tests define baseline behaviour.

Allowed:

- add new tests
- add regression tests
- add fixtures for new tests

Requires human approval:

- editing existing tests
- deleting tests
- weakening assertions
- skipping tests
- changing snapshots

If an existing test appears wrong, stop and report it.

## Model routing

High model:

- planning
- architecture
- ambiguous bugs
- risky diffs
- security/auth/payment/data deletion
- protected test-change proposals
- final review

Cheap model:

- small implementation
- obvious tests
- docs
- formatting
- local refactors
- command execution
- progress updates

## Escalation

Start cheap unless the task is high risk.

Escalate to high model when:

- cheap model fails twice
- tests fail unexpectedly
- critical files are touched
- architecture decision is needed
- protected test change is proposed

## Handoff protocol

When the user prompt points to a handoff request:

1. Read `docs/handoffs/README.md` for the protocol.
2. Read the entire request before changing files.
3. Treat every requirement ID heading as mandatory.
4. Write the next sequenced executor response in the same dated handoff directory.
5. Account for every requirement as `complete`, `blocked`, or `shelved`.
6. Include evidence for completed items and a concrete reason for blocked or shelved items.
7. Run `agent-loop handoff validate <request> <response>` before replying.
8. Never claim completion unless validation passes and every requirement is complete.

Do not silently omit difficult requirements. Shelving is allowed only as an
explicit, auditable deferral and means the overall response is not complete.
