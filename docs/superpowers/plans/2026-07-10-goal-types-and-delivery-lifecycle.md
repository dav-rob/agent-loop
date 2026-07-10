# Goal Types And Delivery Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make agent-loop infer an operating goal type, apply matching review standards, preserve non-blocking recommendations, deliver an assessable result, and carry selected recommendations into later goals.

**Architecture:** Extend the existing SQLite/repository/orchestrator architecture rather than adding a policy framework. Keep goal-type rules and structured result types in focused modules, persist all lifecycle state before rendering markdown, and let CLI intake coordinate confirmation and recommendation selection.

**Tech Stack:** Python 3.11+, SQLite migrations, argparse CLI, pytest, existing multi-provider model router.

---

### Task 1: Goal Type And Recommendation Persistence

**Files:**
- Create: `src/agent_loop/goal_types.py`
- Modify: `src/agent_loop/database.py`
- Modify: `src/agent_loop/repositories.py`
- Create: `tests/test_goal_lifecycle_storage.py`

- [ ] Add failing tests for allowed goal types, run creation/update, recommendation validation/transitions, latest-completed-goal lookup, and one delivery record per goal.
- [ ] Run `uv run pytest -q tests/test_goal_lifecycle_storage.py` and confirm failures are caused by missing schema/APIs.
- [ ] Add `GOAL_TYPES`, validation helpers, migration 8, `RecommendationRepository`, and `GoalDeliveryRepository`.
- [ ] Extend `RunRepository` without breaking existing callers: default new goals to `prototype`, expose type/rationale, allow confirmation, and query the latest completed goal.
- [ ] Run the focused tests and `tests/test_database.py`.
- [ ] Commit as `feat: persist goal types and recommendations`.

### Task 2: Goal Type Inference And Confirmation

**Files:**
- Create: `src/agent_loop/goal_intake.py`
- Modify: `src/agent_loop/intake.py`
- Modify: `src/agent_loop/cli.py`
- Modify: `src/agent_loop/views.py`
- Create: `tests/test_goal_type_intake.py`

- [ ] Add failing tests for first-goal `prototype`, structured model inference, invalid-output fallbacks, interactive approval/correction, unattended acceptance, and type/rationale rendering.
- [ ] Run the new tests and confirm the expected failures.
- [ ] Implement a structured `GoalTypeInference`, model prompt/parser, deterministic fallback, and confirmation prompt.
- [ ] Infer before `RunRepository.create()`, persist the confirmed result, and automatically accept it for non-interactive starts.
- [ ] Include goal type and rationale in planning context, CLI status, plan markdown, and progress markdown.
- [ ] Run the new tests plus `tests/test_intake.py`, `tests/test_cli.py`, and `tests/test_views.py`.
- [ ] Commit as `feat: infer and confirm goal operating type`.

### Task 3: Goal-Type-Aware Reviews

**Files:**
- Create: `src/agent_loop/review_policy.py`
- Modify: `src/agent_loop/orchestrator.py`
- Create: `tests/test_goal_review_policy.py`

- [ ] Add failing tests for each type’s blocking policy, propagation to task/feature/final/escalation prompts, and `investigate` evidence-oriented completion wording.
- [ ] Run the new tests and confirm failures.
- [ ] Add a pure policy formatter and inject it centrally in `run_agent_review()` plus executor/planner context where required.
- [ ] Keep task-escalation follow-up semantics unchanged.
- [ ] Run focused and orchestrator regression tests.
- [ ] Commit as `feat: apply goal-type review standards`.

### Task 4: Structured Non-Blocking Recommendations

**Files:**
- Modify: `src/agent_loop/orchestrator.py`
- Modify: `src/agent_loop/repositories.py`
- Create: `tests/test_review_recommendations.py`

- [ ] Add failing parser tests for valid/invalid structured recommendations and orchestration tests proving source links are persisted.
- [ ] Add a failing prototype feature-review regression proving non-blocking `follow_up` completes the feature and creates no task.
- [ ] Extend `ReviewParseResult` with validated recommendations and retain the parsed result for feature/final policy handling.
- [ ] Persist recommendations after the source review row exists, linking goal/feature/task/review.
- [ ] Treat recommendation-only prototype feature and final findings as completion; retain required functional repair behavior for `rejected`.
- [ ] Run focused tests plus all review/retry/orchestrator tests.
- [ ] Commit as `feat: separate recommendations from required work`.

### Task 5: SQLite-Backed Delivery Reports

**Files:**
- Create: `src/agent_loop/delivery.py`
- Modify: `src/agent_loop/orchestrator.py`
- Modify: `src/agent_loop/config.py`
- Create: `tests/test_delivery_report.py`

- [ ] Add failing tests for delivery-result parsing, persistence, recommendation grouping, investigation reports, and `.agent-loop/delivery-report.md` rendering.
- [ ] Add failing web-delivery tests requiring launch command, local URL, and launch evidence before approval.
- [ ] Implement structured final-delivery fields, web metadata validation, delivery persistence, and a renderer that only reads repositories.
- [ ] Render the report after successful completion and expose the path in completion output/notifications.
- [ ] Run focused tests plus config/orchestrator regressions.
- [ ] Commit as `feat: render SQLite-backed delivery reports`.

### Task 6: Recommendation Adoption Across Goals

**Files:**
- Modify: `src/agent_loop/cli.py`
- Modify: `src/agent_loop/orchestrator.py`
- Modify: `src/agent_loop/views.py`
- Create: `tests/test_recommendation_adoption.py`

- [ ] Add failing tests for latest-completed-goal recommendation listing, interactive zero/multi-selection, `--recommendations` validation, and planning-context propagation.
- [ ] Add failing lifecycle tests proving selection is atomic, successful completion resolves selected recommendations, and blocked/failed goals leave them selected.
- [ ] Implement compact recommendation prompts and the non-interactive CLI option.
- [ ] Mark selected recommendations only after the adopting goal is created, and include full persisted context in planner prompts and generated views.
- [ ] Resolve selected recommendations in the successful-completion transaction path.
- [ ] Run focused CLI/intake/orchestrator/view tests.
- [ ] Commit as `feat: adopt recommendations in later goals`.

### Task 7: Documentation And Regression Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/delivery/technical-overview.md`
- Modify: `progress.md`
- Modify: `learning.md` only for reusable architectural facts

- [ ] Document goal types, intake confirmation, recommendations, delivery reports, and unattended selection.
- [ ] Run `uv run pytest -q` and `git diff --check`.
- [ ] Review schema/API compatibility and confirm no existing protected tests were edited.
- [ ] Commit as `docs: explain goal lifecycle and delivery reports`.

### Task 8: Thirty-Minute Continuity Experiment

**Files:**
- Read: `monitor.md`
- Read: `docs/test-broad-idea.txt`
- Create: `tmp/<timestamp>-goal-lifecycle-actions.txt`
- Create: `tmp/<timestamp>-goal-lifecycle-monitor.md`

- [ ] Record the installed branch/CLI identity and baseline database state.
- [ ] Delete the authorized contents of `/Users/davidroberts/projects/quick-scripts/test-loop-worktree-continuity1` and initialize a fresh target.
- [ ] Drive only broad-idea submission, no-dialogue planning, inferred-type approval, and plan approval through scripted input/actions.
- [ ] Poll SQLite every 150 seconds for 30 minutes, recording first runnable evidence and lifecycle counts.
- [ ] Terminate agent-loop and all attributable descendants at the deadline.
- [ ] Write a framework-focused comparison covering time to runnable app, reviewer behavior, recommendations, and handover/delivery quality.
- [ ] Commit only durable source/docs changes; leave timestamped experiment artifacts under ignored `tmp/`.

