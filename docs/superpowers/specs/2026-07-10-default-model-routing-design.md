# Default Model Routing Design

## Goal

Update newly generated `agent-loop.toml` files for the current Codex and agy
catalogues while preserving cross-binary execution and improving intake quality.

## Route Design

The default personalities use these ordered routes:

- `intake`: Codex `gpt-5.6-sol` medium, agy Claude Opus 4.6 Thinking, agy
  Gemini 3.5 Flash High.
- `planner`: Codex `gpt-5.6-sol` xhigh, agy Claude Opus 4.6 Thinking, agy
  Gemini 3.1 Pro High.
- `spec_reviewer` and `reviewer`: Codex `gpt-5.6-sol` high, agy Claude Opus
  4.6 Thinking, agy Gemini 3.1 Pro High.
- `executor`: agy Gemini 3.1 Pro High, agy Claude Sonnet 4.6 Thinking, Codex
  `gpt-5.6-terra` high.
- `executor_escalated` and `escalation_reviewer`: Codex `gpt-5.6-sol` xhigh,
  agy Claude Opus 4.6 Thinking, agy Gemini 3.1 Pro High.

Intake is deliberately Codex-first because it should behave like a capable chat
application. Normal execution remains agy-first so live goals exercise both
provider binaries. Existing project-owned `agent-loop.toml` files are not
migrated or overwritten.

## Related Surfaces

- Update the in-code defaults used by `Config` and `Config.write_default_toml`.
- Update Codex capability discovery to report the current selectable catalogue.
- Update default-route and generated-config regressions.
- Update README, delivery documentation, the orchestrator design, progress, and
  durable learning where they describe current defaults.
- Retain older model names in tests when they are fixtures for configurable
  routing, quota state, persistence, or rendering rather than default assertions.

## Verification

Focused configuration, CLI, adapter, intake, and routing tests must pass before
the complete test suite. A generated TOML smoke check must confirm the exact
personality order and reasoning levels.
