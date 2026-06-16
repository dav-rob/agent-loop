# Agent Loop

You kick off the app with one user goal. It makes a plan,
breaks that goal into features and tasks, sends those tasks to agent CLIs,
reviews the work, keeps going where it can, and stops when it either finishes or
hits something it should not guess about.

It is not meant to be a chat session where you babysit every step. It is closer
to saying, "Here is the job. Go and do the sensible version of it, but stop if
you hit a real decision or a safety boundary."

One bit of terminology: the product internally calls a tracked goal a `run`. So if you see a variable or
database table called `run_id`, read that as the internal name for `Goal ID`.

## How To Get Something Done?

Go to the repository you want changed and run:

```bash
agent-loop start
```

That is the normal human-friendly entry point. It will ask for the goal if you
did not provide one, then ask what kind of intake you want.

For a straightforward task, say something like:

```text
Add CSV export to the reports command and update the tests.
```

For a broader task, say what you would say to another developer:

```text
The settings page is confusing. Improve the layout, make dangerous actions less
easy to hit by accident, and keep the existing settings behavior working.
```

Then the app plans the goal and either starts executing or waits for plan
approval, depending on the intake mode. Later, if you want an added feature, you
usually start a new goal.

## Other Flags?

Use this when you want to fire off a clear task without prompts:

```bash
agent-loop start --non-interactive --goal "Add CSV export to reports and update tests"
```

Use this when you want it to make reasonable decisions and keep moving:

```bash
agent-loop start --non-interactive --intake autonomous --goal "Fix the flaky retry tests"
```

Use this when you want it to plan, then stop for your approval:

```bash
agent-loop start \
  --non-interactive \
  --intake brainstorm \
  --unattended-policy reject \
  --goal "Refactor the notification code"
```

Then inspect and approve:

```bash
agent-loop plan --details
agent-loop approve
```

## What Happens Under The Hood

### How It Works

The orchestrator calls the planner to create the whole feature/task graph up
front and stores it in SQLite. Then each executor attempt gets one task.

One detail to be precise about: the planner runs before plan approval. Approval
does not create the plan; approval decides whether the already-created plan is
allowed to start execution.

The orchestrator's scheduler may run several independent tasks at the same time,
up to `max_workers`, but that means several separate executor attempts, not one
executor receiving the whole plan.

A task is runnable when:

- its dependencies are complete
- its status is `ready`
- it does not overlap active task file scope
- a suitable model route is available

The executor prompt currently contains the goal, task name, required
verification, task scope, and previous reviewer rejection if there was one. It is
not a conversational handoff.

### Process Details

The orchestrator is the traffic controller. It calls different agent CLIs for
different jobs, for example "planner", "executor", and "reviewer", stores their
outputs in SQLite, logs, and Git, and decides what happens next.

The mechanism is basically:

```text
plan created and approved
  -> tasks stored in SQLite
  -> scheduler picks ready task(s)
  -> executor agent gets one task prompt in an isolated worktree
  -> executor exits with files changed / logs / success or failure
  -> orchestrator runs verification
  -> orchestrator commits the task work
  -> reviewer agent gets the diff
  -> reviewer returns JSON decision
  -> orchestrator updates SQLite and either continues, retries, creates follow-up, or stops
```

So the executor does not "reply to the reviewer" directly. The executor replies
to the orchestrator by finishing its CLI run. The reviewer then reviews the
resulting commit/diff.

### Executor-Reviewer Process

The orchestrator automatically sends work to review after this sequence:

```text
executor run succeeds
verification command passes
orchestrator commits the work
orchestrator sends the commit diff to reviewer
```

The orchestrator asks the reviewer for a JSON response:

```json
{
  "decision": "approved",
  "findings": "..."
}
```

Allowed decisions are:

- `approved`: merge task commit and mark task complete
- `rejected`: retry the task if attempts remain; block after too many attempts
- `follow_up`: merge the useful work, create a dependent follow-up task, continue
- `assessment`: block the original task and create a high-reasoning planning/assessment task
- `block`: stop this task because it hit a real stop condition

The `assessment` path is worth spelling out: the original task is blocked while
the assessment task is created. If the assessment is approved, the original task
can be put back to `ready` and retried with that extra reasoning recorded.

The executor/adapters can effectively signal:

- success
- normal failure
- quota exhausted
- authentication required
- transient provider failure
- unavailable/incompatible route

The orchestrator then decides:

- retry the same task
- try another route
- wait for quota
- block for missing credentials
- preserve partial diff/logs
- mark the task blocked after repeated failure

The reviewer can also force help by returning `block` or `assessment`.

## When Does The Loop Actually Stop?

The loop stops cleanly when the goal reaches one of these states:

- `complete`: the work passed task review, feature review, final review, and regression tests
- `complete_pending_test_review`: the work is otherwise done, but a test baseline migration needs your approval
- `blocked`: it hit a stop condition that should not be guessed through
- `failed`: final regression verification failed
- `awaiting_plan_approval`: a plan exists, but you asked to approve before execution
- `waiting_for_quota`: all required routes are quota-limited, so it is waiting and probing

`waiting_for_quota` is a bit different. The command may sleep and keep checking
until a route recovers. If the process is stopped while waiting, use `resume`
later.

## What Is `resume` For?

`resume` is not a magic "next step" button that you press after every task. It is
for recovering or continuing an existing goal.

Use it when:

- your terminal died
- you hit `Ctrl-C`
- the machine restarted
- quota was exhausted and you want to continue later
- credentials were missing, you logged in, and now want to continue
- the goal was left in a runnable waiting state

Run:

```bash
agent-loop resume
```

That resumes the latest goal.

Or:

```bash
agent-loop resume 3
```

That resumes goal `3`.

On resume, the app reconciles interrupted attempts, preserves partial diffs where
it can, marks interrupted attempts as abandoned, regenerates `plan.md` and
`progress.md`, then continues planning or execution if the goal is runnable.

One important caveat: if a goal is blocked because a task itself is blocked,
`resume` will not magically invent a safe answer. You need to inspect the plan,
logs, and blocker first.

## What Are All Those Numbers?

They are database IDs.

The app stores goals, tasks, attempts, and test migrations in `.agent-loop.db`.
The numbers let you point at the exact thing you mean.

The common ones are:

- goal ID: the whole user goal, for example `Goal ID: 3`
- task ID: one planned unit of work inside the goal
- attempt ID: one try at executing a task
- migration ID: one proposed test baseline change

Most commands default to the latest goal, so you often do not need the number:

```bash
agent-loop status
agent-loop plan --details
agent-loop resume
```

Use the number when you want an older run:

```bash
agent-loop status 2
agent-loop plan --details 2
agent-loop resume 2
```

For test migrations, the number is required because approving the wrong
migration would be bad:

```bash
agent-loop migration approve 12
agent-loop migration reject 12
```

## What Should I Look At While It Runs?

Start with:

```bash
agent-loop status
```

Then:

```bash
agent-loop plan --details
```

Also look at:

- `progress.md` for the current state and next action
- `plan.md` for the human-readable task plan
- `logs/` for provider output, test output, and review evidence
- `git log --oneline` for task-sized commits

If something failed, `agent-loop plan --details` is usually the best first stop
because it prints attempt IDs, provider/model choices, logs, worktrees, commits,
and test migration records.

## What I Would Actually Do In Practice

For a small well-defined job:

```bash
agent-loop start --non-interactive --intake autonomous --goal "Fix the retry test flake and keep behavior unchanged"
```

Then later:

```bash
agent-loop status
agent-loop plan --details
```

For a job where I want to approve the plan:

```bash
agent-loop start --goal "Add webhook retry support"
```

Choose brainstorm mode, read `plan.md`, then approve if it looks sane.

For UI work:

```bash
agent-loop start --goal "Improve the dashboard empty state" --intake ui_lab
```

For a stopped run:

```bash
agent-loop resume
```

For a pending test migration:

```bash
agent-loop plan --details
agent-loop migration approve 12
```

## Spec Versus Current App: Gaps And Rough Edges

I compared the original design in
`docs/specs/2026-06-15-agent-loop-orchestrator-design.md` with the current
implementation. The broad shape is there, but these are the main gaps or rough
edges to keep in mind:

- The interactive brainstorm is simpler than the spec describes. It asks for a
  goal, intake mode, and optional constraints; it is not yet a rich one-question-
  at-a-time product conversation.
- UI Lab currently runs the brief-style intake path. It does not yet walk
  through the full set of UI Lab stages described in the spec.
- `resume` can restart a goal from `blocked`, but if the underlying blocked task
  is still blocked and no other task is runnable, the goal will just become
  blocked again. The app needs a clearer operator workflow for resolving
  blockers.
- The statuses include `cancelled`, but there is no obvious `agent-loop cancel`
  CLI command yet.
- Configuration loading is forgiving in a way that can be surprising: if the
  config file cannot be read or parsed, the current code falls back to defaults.
  The spec calls for stronger configuration validation before expensive work.
- Provider capability discovery exists in pieces, but the full adapter contract
  in the spec is broader than what the current CLI experience exposes.
- The app validates handoff files, but normal product execution does not yet
  generate supervisor/executor handoff documents. Those files are still mainly
  part of our manual development process for building the app.
- The current CLI is moving toward user-facing `Goal ID` language, but some
  internals and older docs still use `run` because the database table and
  repository are named that way.

The practical takeaway: you can use it now as a local agent loop, especially for
small and medium development tasks, but the operator experience still needs a
clearer "what happened and what should I do next?" layer around blocked states,
test migrations, and older runs.
