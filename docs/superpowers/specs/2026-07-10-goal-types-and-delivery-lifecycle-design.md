# Goal Types And Delivery Lifecycle Design

Date: 2026-07-10

## Purpose

Agent-loop should behave like a mature development team that changes its
operating mode to match the current goal. It should move quickly when proving
an idea, preserve what already works when extending it, focus tightly when
repairing or refining it, and apply stricter standards when deliberately
hardening it.

This design adds six connected capabilities:

1. infer and confirm a goal type during intake;
2. apply goal-type-aware review standards;
3. persist non-blocking recommendations separately from required work;
4. allow prototype feature and final reviews to complete with known limits;
5. render a useful delivery report from SQLite-backed state; and
6. let a later goal adopt recommendations from the latest completed goal.

The implementation should remain small and fit the existing SQLite,
repository, orchestrator, and markdown-rendering architecture.

## Goal Types

The application exposes a `GOAL_TYPES` constant with these values:

- `prototype`: produce a runnable first version of an app, feature, or risky
  idea quickly;
- `extend`: add useful capability while preserving existing behavior;
- `refine`: improve behavior, usability, or product focus after feedback;
- `repair`: correct a defect or regression without unnecessary scope growth;
- `harden`: deliberately raise security, reliability, testing, performance,
  or architectural standards; and
- `investigate`: answer a question through evidence, diagnosis, or research.

These values are operating modes, not maturity levels. There is no required
transition order. The first goal in a new repository normally defaults to
`prototype`, but a mature project may also use `prototype` for a new
experimental feature. `investigate` is stored and inferable now; richer
research-specific intake and execution can be added later.

SQLite stores validated text instead of a database enum so new goal types do
not require a table rebuild. Existing goals migrate to `prototype`.

## Intake And Confirmation

The intake model returns structured goal-type inference containing:

- the inferred `goal_type`;
- a short user-facing rationale; and
- enough normalized goal context for planning.

Interactive intake presents the inference conversationally before planning.
The user may approve it or correct it. Planning starts only after the type is
confirmed, ensuring the planner and every later reviewer receive the agreed
operating mode.

Unattended intake automatically accepts the inferred type under the existing
unattended approval behavior. The inferred type and rationale are still
persisted and displayed in status, progress, plans, and delivery evidence.

When a repository has a completed goal with open recommendations, intake shows
recommendations from the latest completed goal. The user may select any of
them, ignore all of them, or provide unrelated new direction. Selected
recommendations become explicit planning inputs rather than implicit prose.

## Persistence

The `runs` table gains:

- `goal_type`, validated against `GOAL_TYPES`; and
- `goal_type_rationale`, containing the confirmed intake explanation.

The `recommendations` table contains:

- source goal ID;
- optional source feature ID;
- optional source task ID;
- optional source review ID;
- category: `usability`, `security`, `architecture`, `reliability`, `testing`,
  or `maintenance`;
- priority: `high`, `medium`, or `low`;
- concise title;
- rationale;
- evidence;
- status: `open`, `selected`, `deferred`, `declined`, or `resolved`;
- optional adopting goal ID; and
- creation and update timestamps.

A recommendation becomes `selected` when adopted into a later goal. It becomes
`resolved` only when that adopting goal completes successfully. If the goal
blocks, fails, or is cancelled, the recommendation remains `selected`.

The `goal_deliveries` table stores one delivery record per goal containing:

- summary of what was built or learned;
- launch or open instructions;
- local URL when relevant;
- structured verification summary;
- structured known limitations; and
- investigation conclusion when the goal type is `investigate`.

Markdown reports are views of this state and never become the runtime source
of truth.

## Review Policy

All task, feature, escalation, and final reviewers receive the confirmed goal
type and its blocking policy.

### Prototype

Block only when:

- the app cannot build or run;
- required verification fails;
- completed behavior materially regresses; or
- the central function requested by the task or goal is absent.

Security, architecture, portability, dependency updates, edge cases, and
polish should still be reported, but normally as recommendations.

### Extend

Block when the requested capability is absent, required verification fails, or
existing working behavior materially regresses. Improvements outside the
requested extension become recommendations.

### Refine

Block when the stated behavioral or usability outcome is not met or existing
behavior materially regresses. Broader product or implementation improvements
become recommendations.

### Repair

Block when the target defect remains, its regression verification fails, or
the repair causes a material regression. Unrelated cleanup becomes a
recommendation.

### Harden

Apply deliberately stricter security, reliability, testing, performance, and
architecture standards within the stated scope. Findings against those
explicit standards may block.

### Investigate

Judge whether the stated question was answered with adequate evidence and a
clear conclusion. A runnable software delivery is not required unless the goal
explicitly asks for one.

## Structured Review Results

Reviewer output retains its existing decision and retry guidance and gains a
structured `recommendations` array. Each item contains category, priority,
title, rationale, and evidence.

Required functional failures remain blocking decisions and may create repair
work. Non-blocking findings are persisted as recommendations. They do not
create tasks or keep a feature pending.

For prototype goals:

- a feature review with only non-blocking findings completes the feature;
- a final review may complete the goal with known limitations; and
- only required functional failures extend the current goal.

Task-escalation `follow_up` remains retry guidance because it exists to recover
required work at the attempt limit. It is not converted into a recommendation.

## Delivery Report

Successful completion renders `.agent-loop/delivery-report.md` from SQLite.
The report is deliberately short and contains:

1. what was built or learned;
2. how to run, open, or inspect it;
3. what was verified;
4. known limitations;
5. High, Medium, and Low recommendations; and
6. an invitation to start the next goal from selected recommendations or new
   feedback.

For a web application, final delivery requires a concrete launch command, a
local URL, and review evidence that the URL responded while the application was
running. This is a functional delivery gate, not a demand to resolve every
known limitation.

For an investigation, the report substitutes the research question, evidence,
conclusion, and recommended actions for runnable-app instructions where
appropriate.

## Recommendation Adoption

Intake queries open recommendations from the latest completed goal. The
interactive flow displays compact numbered summaries and accepts zero or more
selections. Unattended starts accept an optional comma-separated
`--recommendations` list of recommendation IDs and otherwise select none; they
must not silently expand a new goal.

The planner receives selected recommendation IDs and their complete persisted
context. Selection sets their status and adopting goal ID atomically. Goal
completion resolves them atomically. Generated plans and delivery reports show
the relationship so the user can see how concerns progress across goals.

## Error Handling

- Invalid model-supplied goal types fall back to `prototype` for the first goal
  and `extend` for later goals; the fallback rationale is persisted.
- Invalid recommendation categories or priorities are rejected by repository
  validation and omitted from orchestration only with an evidence log entry.
- Failure to render markdown does not corrupt SQLite state, but goal completion
  reports the rendering failure to the operator.
- A final web delivery missing a launch command, URL, or launch evidence remains
  incomplete as a required functional failure.
- Recommendation status transitions reject invalid or cross-goal resolution.

## Testing

Implementation follows test-driven development with new regression tests. The
coverage must prove:

- schema migration and repository validation;
- first-goal prototype inference and later-goal inference;
- interactive approval/correction and unattended acceptance;
- goal type propagation into planning and all review prompts;
- prototype blocking thresholds and stricter harden behavior;
- structured recommendation parsing and persistence;
- non-blocking feature findings complete the feature without creating tasks;
- final prototype completion with known limitations;
- delivery record persistence and markdown rendering;
- web delivery launch metadata requirements;
- recommendation selection and resolution across goals; and
- compatibility with existing execution, retry, and review behavior.

Existing tests are baseline behavior and will not be edited without explicit
approval. New regressions will be added in new test files where possible.

## Live Continuity Experiment

After automated tests pass, the implementation will be validated in
`/Users/davidroberts/projects/quick-scripts/test-loop-worktree-continuity1`.
The existing contents may be deleted as authorized by the user.

The automated interaction performs only the minimum required steps:

1. submit `docs/test-broad-idea.txt` as the broad-idea input;
2. choose planning without further intake dialogue;
3. approve the inferred `prototype` goal type; and
4. approve the generated plan.

Scripted stdin or a small actions fixture may drive these inputs. The fixture
is test automation, not a new interactive framework feature.

The run lasts 30 minutes and is monitored from SQLite every 150 seconds. At the
deadline, agent-loop and its descendants are terminated. The report measures:

- time to the first runnable application;
- task, review, retry, and escalation counts;
- final goal status;
- whether delivery instructions and a local URL exist;
- whether non-blocking concerns became recommendations instead of tasks; and
- the usefulness of progress, handover, and delivery-report evidence.

The evaluation concerns agent-loop behavior. It does not review the produced
application as though that application's product quality were the framework's
primary deliverable.

## Delivery Sequence

Work will be committed in small, independently verified increments:

1. goal-type constants, schema, and repositories;
2. intake inference, confirmation, and propagation;
3. goal-type-aware reviewer prompts and structured recommendation parsing;
4. recommendation persistence and non-blocking feature/final policy;
5. SQLite-backed delivery records and report rendering;
6. next-goal recommendation adoption and resolution;
7. documentation and full regression verification; and
8. the 30-minute live continuity experiment and its findings.
