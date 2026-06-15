# Handoff Request

## Handoff Metadata

- Handoff-ID: YYYY-MM-DD-NN
- Type: request

## Execution Contract

Read `docs/handoffs/README.md` first, then read the entire request before
changing code. Every requirement ID is mandatory. If an item cannot be
completed, mark it `blocked` or `shelved` in the response and provide a
concrete reason.

### FIX-01: Describe the required change

State the behavior and evidence expected.

### CHECK-01: Run verification

State the exact command or observable result required.

## Response Requirements

Write the next sequenced response in this dated directory and validate it with
`agent-loop handoff validate` before reporting completion.
