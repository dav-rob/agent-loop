---
name: brainstorming
description: Use during agent-loop intake to turn a broad goal into a clearer goal before planning. Ask focused follow-up questions one at a time, summarize concisely, and avoid verbose design documents unless explicitly requested.
---

# Brainstorming for Agent Loop Intake

Use this skill when a user starts a goal in brainstorm mode.

## Intent

Refine a broad goal enough for the planner to create useful features and tasks.
Keep the process lightweight: ask a few targeted questions, then summarize the
answers as concise notes appended to the goal.

## Question Flow

Ask one question at a time. Each question is optional; blank answers mean "skip".

1. Who is the main user or audience?
2. What should be true when this goal is complete?
3. What constraints, preferences, integrations, or style choices matter?
4. What should be out of scope, risky, or easy to get wrong?
5. How should the agent verify the result?

## Summary Format

Append only non-empty answers:

```markdown
Brainstorming Notes:
- User / audience: ...
- Success criteria: ...
- Constraints / preferences: ...
- Non-goals / risks: ...
- Verification: ...
```

## Tone

Be practical and concise. The notes should help planning, not become a design
document. Preserve the user's own terms where possible.
