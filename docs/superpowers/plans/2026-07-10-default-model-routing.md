# Default Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate new agent-loop projects with personality-specific Codex 5.6 and current agy model routes.

**Architecture:** Keep route selection data-driven in `src/agent_loop/config.py`, but split the previous shared strong route list into review and deep-reasoning tiers. Preserve existing config compatibility and provider order outside the new defaults.

**Tech Stack:** Python 3.10+, TOML, pytest, Codex CLI, agy CLI.

---

### Task 1: Specify New Defaults

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_adapters.py`

- [ ] Update default-route assertions with the approved personality mappings.
- [ ] Assert generated TOML contains Sol medium for intake, Terra high for
  executor fallback, Sol high for review, and Sol xhigh for deep profiles.
- [ ] Assert Codex discovery reports Sol and Terra.
- [ ] Run the focused tests and confirm they fail against the old defaults.

### Task 2: Implement Route And Discovery Defaults

**Files:**
- Modify: `src/agent_loop/config.py`
- Modify: `src/agent_loop/adapters.py`

- [ ] Add dedicated intake, review, and deep route constants.
- [ ] Assign every named personality to its approved route list.
- [ ] Update the static Codex capability snapshot with the selectable catalogue
  confirmed by `codex-cli 0.144.1`.
- [ ] Run configuration, CLI, adapter, intake, and routing tests.

### Task 3: Synchronize Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/delivery/technical-overview.md`
- Modify: `docs/specs/2026-06-15-agent-loop-orchestrator-design.md`
- Modify: `progress.md`
- Modify: `learning.md`

- [ ] Replace current-default examples with the approved mappings.
- [ ] Preserve historical monitoring evidence while adding the new current
  state to `progress.md`.
- [ ] Update durable catalogue and provider-order facts in `learning.md`.

### Task 4: Verify And Commit

- [ ] Run focused tests:
  `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_config.py tests/test_cli.py tests/test_adapters.py tests/test_intake.py tests/test_routing.py`.
- [ ] Generate and parse a default TOML in a temporary directory.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest -q`.
- [ ] Run `git diff --check` and inspect remaining old-model references.
- [ ] Commit behavior/tests separately from documentation.
