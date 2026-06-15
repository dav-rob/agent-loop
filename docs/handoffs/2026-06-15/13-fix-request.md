# Execution Fixes: Final Verification Pass

## Handoff Metadata

- Handoff-ID: 2026-06-15-13
- Type: request

## Execution Contract

Read `docs/handoffs/README.md` and
`docs/handoffs/2026-06-15/12-supervisor-review.md` completely. Do not modify an
existing test without explicit human approval. Add focused tests and create
fine-grained commits.

Write `docs/handoffs/2026-06-15/14-executor-response.md` and validate it against
this request before replying.

### FIX5-01: Use role-based routing during task execution

Apply the same role/capability decision to `_execute_task_impl` that quota
gating uses. A low-risk planning task, including an architectural-assessment
task, must create an attempt on a configured planning route. Add a regression
test that inspects the selected provider/model and persisted attempt route.

### FIX5-02: Provide real notification evidence

Add focused coverage that sends the same notification twice and proves only one
delivery is attempted. Assert the actual supported payload contract and all
required operational fields. Do not claim structured fields that the transport
does not provide.

### CHECK5-01: Exercise one genuine lifecycle fixture

Run one named fixture through the public orchestration flow. It must invoke
planning, schedule overlapping safe tasks through the worker pool, execute
verification and reviews, create and resolve a merge-conflict integration task,
recover an interrupted attempt, enter quota waiting and recover via the fake
clock/sleeper, execute the configured regression command, and reach `complete`
through `run_loop`. Record observed persisted transitions rather than manually
assigning the expected final states.

### CHECK5-02: Reconcile final evidence and state

Run the real Codex parser smoke, notification tests, genuine lifecycle fixture,
handoff validator, and `.venv/bin/python -m pytest -q`. List every commit from
the pass and report exact `git status --short` output. Update `progress.md` to
the verified final status only after all checks pass.

## Completion Rule

Use `Overall-Status: complete` only when every requirement is complete and the
reported commands, commits, transitions, and repository state are exact.
