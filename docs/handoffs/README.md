# Handoff Protocol

Supervisor and executor agents communicate through ordered Markdown files. The
files are the authoritative contract; chat prompts should only point at the
active request.

## Directory Layout

Use one directory per date and increment sequence numbers without rewriting
earlier records:

```text
docs/handoffs/2026-06-15/
  01-execution-fixes-request.md
  02-executor-response.md
  03-supervisor-review.md
  04-fix-request.md
  05-executor-response.md
```

Global protocol documentation and templates may live directly under
`docs/handoffs/`; conversation records belong in dated directories.

## Request Contract

A request must contain:

```markdown
## Handoff Metadata

- Handoff-ID: 2026-06-15-04
- Type: request
```

Every mandatory item must have a stable heading:

```markdown
### FIX-01: Repair provider invocation
### CHECK-01: Run the complete test suite
```

Requirement IDs are the machine-checkable contract. Prose without an ID is
context, not a separately validated requirement.

## Executor Response Contract

The response must use the next available sequence number and contain:

```markdown
## Handoff Metadata

- Type: response
- Responds-To: 04-fix-request.md
- Overall-Status: complete

## Requirement Compliance

| Requirement | Status | Evidence | Reason |
| --- | --- | --- | --- |
| FIX-01 | complete | Commit abc123; smoke test passed | - |
| CHECK-01 | complete | `.venv/bin/python -m pytest -q`: 50 passed | - |
```

Allowed requirement statuses:

- `complete`: evidence is mandatory.
- `blocked`: an external stop condition exists; a reason is mandatory.
- `shelved`: deliberately deferred because the work is unsuitable for this
  pass; a reason is mandatory.

Allowed overall statuses are `complete`, `partial`, and `blocked`. A response
cannot claim `complete` unless every requirement is `complete`.

Before reporting back, run:

```bash
agent-loop handoff validate \
  docs/handoffs/2026-06-15/04-fix-request.md \
  docs/handoffs/2026-06-15/05-executor-response.md
```

The validator checks accounting and evidence fields. It does not prove that
the implementation or evidence is correct; supervisor review remains required.

## Standard Executor Prompt

Use a short prompt that makes the file authoritative:

```text
First read docs/handoffs/README.md for the handoff protocol. Then execute the
handoff at <request-path>. Read the request completely and treat every
requirement ID as mandatory. Write the next sequenced executor response in the
same dated directory, account for every requirement as complete, blocked, or
shelved, and run `agent-loop handoff validate` before replying. Do not claim
completion unless validation passes and every requirement is complete.
```
