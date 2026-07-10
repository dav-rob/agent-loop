# Agent-Loop: Where We Are And What To Do Next

Date: 2026-07-10

This is a working roadmap, not a specification. The point is to decide what is
worth building next without turning the project into an endless framework
exercise.

## The Short Version

The execution engine is now fairly capable. It can plan work, route across
different model binaries, run tasks in durable worktrees, preserve retry state,
escalate difficult tasks, review changes, merge approved work, and leave useful
handover evidence.

The next problem is not more retry machinery. It is teaching agent-loop how a
good development team changes its standards over time:

1. Get a runnable prototype in front of the user quickly.
2. Keep useful concerns without automatically fixing all of them.
3. Let later goals deliberately extend, focus, or harden the app.

That should now drive the roadmap.

## Where The Existing Plan Landed

The practical parts of
`docs/plans/continue-existing-task-branch.md` are substantially done:

- **Phase 1:** durable task branches and continuation retries are done and
  worked during the live run.
- **Phase 1.5:** typed recovery follow-ups and merge-conflict closure are done.
- **Phase 2:** explicit retry strategies, SHA metadata, patch preservation, and
  resume recovery are done.
- **Phase 2.5:** ordinary task reviews now run in the durable task worktree and
  worked live. Task-escalation reviews still need the same workspace treatment.
- **Phase 2.6:** nested timeout cleanup exists, although process ownership should
  still be tightened so unrelated workspace processes cannot be selected.

The remaining original phases are no longer a useful roadmap:

- **Phase 3 operator restart/archive commands:** shelve this until operators
  repeatedly need it. The underlying retry strategies already exist.
- **Phase 4 review tuning:** keep the intent, but replace its narrow
  continue/restart wording work with the prototype-first review model below.

## GitHub Issue Audit

No issue was closed during this audit because none currently meets its complete
acceptance criteria.

| Issue | Status | Assessment |
| --- | --- | --- |
| [#4 Run task reviewers in the task worktree](https://github.com/dav-rob/agent-loop/issues/4) | Partly complete | Commit `946ddef` and live monitoring prove ordinary task reviews are fixed. `task_escalation` still defaults to the repo root and lacks a regression. I added a GitHub comment recording the remaining scope. |
| [#3 Record provider and model metadata earlier](https://github.com/dav-rob/agent-loop/issues/3) | Open, still reproducible | Every live attempt showed provider/model as pending until the provider returned. |
| [#2 Show active review work in progress](https://github.com/dav-rob/agent-loop/issues/2) | Open, still reproducible | During task and feature reviews, progress reported no active task attempts even though a reviewer process was running. |
| [#1 Clean up detached review worktrees](https://github.com/dav-rob/agent-loop/issues/1) | Open | Recent task reviews used durable worktrees correctly, but there is no general controlled lifecycle for temporary worktrees created during broader reviews. |

## High Priority: The Next Product Work

### 1. Make Review Standards Match The Goal Stage

This is the highest-leverage change.

The first goal for a new app should default to a **prototype** stage. Its reviews
should block only when the app cannot build or run, required verification fails,
completed behavior regresses, or the central function of the task is absent.

Security, architecture, portability, dependency upgrades, edge cases, and
polish should still be noticed, but normally recorded for later rather than
causing another retry.

Later goals should have a simple intent such as:

- `extend`: add useful capability while preserving what works;
- `refine`: focus behavior and usability after user feedback;
- `harden`: deliberately raise security, reliability, test, and architecture
  standards.

The intake stage shoud infer these from the conversation, and let the user know what level they have inferred with a short explanation, the user can then approve - yeah good, approved etc

This does not need a complicated maturity framework. A small goal-stage field
in SQLite, a clear intake inference, and stage-aware reviewer prompts are
enough to start.

### 2. Separate Recommendations From Required Work

Today, a feature review decision of `follow_up` creates another implementation
task, leaves the feature pending, assigns empty file scope, and uses generic
verification. That is the opposite of a non-blocking recommendation.

Add a first-class `recommendations` record in SQLite. A recommendation should
have enough structure to remain useful:

- goal, feature, task, and source review links;
- category such as usability, security, architecture, reliability, testing, or
  maintenance;
- high, medium, or low priority;
- concise title, rationale, and evidence;
- status such as open, selected, deferred, declined, or resolved.

A non-blocking reviewer finding should create recommendations, mark the feature
complete, and allow the current goal to continue. It should not create a task.

### 3. Produce A Real Delivery Report

Completing a goal should produce `.agent-loop/delivery-report.md`, rendered from
SQLite rather than treated as the source of truth.

It should be short and useful to the person deciding whether the app is worth
continuing:

- what was built;
- how to run or open it;
- what was verified;
- what is known not to work yet;
- high, medium, and low recommendations;
- a clear invitation to start the next goal from selected recommendations or
  fresh user feedback.

For a web app, the final delivery gate should prove there is a working launch
command and provide the local URL. The finish line is not merely a green task
graph; it is something the user can assess.

### 4. Stop Final And Feature Reviews From Extending Prototype Goals By Default

Feature and final review currently have only two practical outcomes: approve or
create/block more work. That encourages reviewers to keep improving the app
before the user sees it.

For prototype goals:

- required functional failures may create repair work;
- non-blocking findings become recommendations;
- final review may complete the goal with known limitations;
- delivery should happen as soon as the app is runnable and recognizably serves
  the goal.

This is mainly a decision-policy and prompt change, not a new orchestration
subsystem.

## Medium Priority: Make The New Lifecycle Work Well

### 5. Let The Next Goal Adopt Recommendations

Once recommendations exist, intake should show the open recommendations from
the latest completed goal. The user can select some, ignore all of them, or
describe something new.

Selected recommendations become explicit inputs to the new goal and are marked
selected/resolved through normal execution. This creates the progression the
product needs: loose prototype first, then increasingly deliberate goals.

### 6. Measure Delivery Speed And Reviewer Cost

The Sol reviewer appeared more exacting and the monitored run made less visible
progress in 30 minutes. That is useful evidence, but not enough to blame the
model.

Record a few simple metrics in SQLite and the delivery report:

- time from goal start to first runnable app;
- time to goal completion;
- task-review rejection count;
- retries and escalations by reviewer model;
- time spent executing versus reviewing;
- number of blocking fixes versus deferred recommendations.

Then compare reviewer models and reasoning levels against the product outcome:
fast usable delivery with acceptable rework, not maximum issue discovery.

### 7. Finish The Small Continuity Gaps

These are bounded fixes, not a new roadmap:

- finish issue #4 by putting task-escalation review in the task worktree;
- make timeout cleanup distinguish attempt-owned processes from unrelated
  workspace processes;
- recover a stale `reviewing` task when a reviewer process is interrupted;
- close each issue when its focused regression and live behavior agree.

### 8. Improve Operator Visibility

Issues #2 and #3 belong together as one small observability pass:

- persist provider/model as soon as a route is selected;
- show active task, feature, escalation, timeout, and final reviews in progress;
- include reviewer route and log path when known.

This will not make apps better, but it will make agent-loop easier to trust and
monitor while it works autonomously.

## Low Priority: Keep On The Shelf

### 9. Temporary Review Worktree Cleanup

Issue #1 is real housekeeping, but recent durable task reviews no longer depend
on temporary checkouts. Add controlled cleanup when convenient or when another
live run reproduces accumulation.

### 10. Operator Restart And Archive Commands

Do not implement Phase 3 merely because it is in the old plan. Revisit it only
after operators repeatedly need to force a retry strategy manually.

### 11. Specialist Security And Architecture Personalities

These may be useful later, especially for a `harden` goal, but they are not the
next step. First establish recommendations and stage-aware review. A future
security or architecture personality can then contribute recommendations or
apply stricter gates when the user deliberately starts a hardening goal.

### 12. More Automatic Branch Sophistication

Auto-merging `main` into long-lived task branches, scoring useful timeout work,
and retaining completed worktrees are reasonable ideas. The current durable
branch behavior is working well enough. Leave these alone until live evidence
shows a concrete problem.

## Suggested Implementation Sequence

Keep the next development pass small enough to validate in another live run:

1. Add goal stage with `prototype` as the default for a first goal.
2. Add SQLite-backed recommendations and stop `follow_up` from creating tasks.
3. Make task/feature/final reviewer prompts stage-aware.
4. Render the delivery report with run instructions and recommendations.
5. Run the same 30-minute continuity experiment and measure whether a runnable
   app appears sooner.
6. Only after that, add recommendation selection to the next-goal intake.

The next live experiment should answer one practical question: did agent-loop
get something usable in front of the user faster without losing the ability to
remember what should be tightened later?

## Recommended Definition Of Success

Agent-loop is behaving like the intended development team when:

- the first goal ends with a runnable app the user can assess;
- reviewers prevent genuinely broken delivery without polishing indefinitely;
- useful concerns survive as structured recommendations;
- the user decides which concerns become later work;
- subsequent goals can extend quickly or deliberately harden the existing app;
- the project becomes more focused because of user feedback, not because the
  framework guessed every future requirement in advance.
