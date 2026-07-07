# Monitoring agent-loop

`agent-loop` is a local development orchestrator. A user gives it one goal; it turns that goal into a spec, plan, features, and tasks, then executes tasks in isolated Git worktrees. Runtime state for each target project is stored under that target project's `.agent-loop/` directory.

## Source of truth

Use the SQLite-backed state first. Logs are evidence for attempts, not the current state.

From a target project directory:

```sh
agent-loop status
agent-loop plan --details
```

Or inspect the database directly:

```sh
sqlite3 -header -column .agent-loop/agent-loop.db \
  "SELECT id,status,intake_mode,created_at,updated_at FROM runs;
   SELECT status,COUNT(*) AS count FROM tasks GROUP BY status;
   SELECT id,task_id,route,provider,model,outcome,created_at,updated_at FROM attempts ORDER BY id;"
```

## Runtime Files

In the target project:

- `.agent-loop/agent-loop.db`: authoritative goal, task, attempt, review, provider, and test state.
- `.agent-loop/plan.md`: readable plan generated from the database.
- `.agent-loop/progress.md`: readable current status, blockers, test results, and next step.
- `.agent-loop/handoffs/`: per-task handover reports rendered from the database, with executor summaries, reviewer findings, verification, commits, and task-scope blocking rationale.
- `.agent-loop/learning.md`: durable facts learned during the goal.
- `.agent-loop/logs/`: prompts, stdout/stderr, model logs, reviews, patches, and test output.
  Provider-specific logs may appear inside each attempt directory, such as
  `codex.log`, `codex_events.jsonl`, and `last_message.txt` for Codex, or
  `agy.log` for Antigravity/agy. Treat these as execution evidence for that
  provider; the database still decides current state.
- `worktrees/`: isolated task worktrees used while executing tasks.
- `agent-loop.toml`: committed project configuration, including model routes and worktree paths.

## Quick Checks

Check generated views:

```sh
sed -n '1,220p' .agent-loop/progress.md
sed -n '1,260p' .agent-loop/plan.md
sed -n '1,160p' .agent-loop/learning.md
```

Check live processes for a specific target directory:

```sh
ps -axo pid,ppid,stat,etime,command | rg 'agent_loop|agent-loop|agy|codex|TARGET_DIRECTORY_NAME' | rg -v 'rg '
```

Check active worktrees and logs:

```sh
find worktrees -maxdepth 2 -type d | sort
find .agent-loop/logs -maxdepth 4 -type f | sort
find .agent-loop/handoffs -maxdepth 1 -type f -name '*.md' -print -exec sed -n '1,220p' {} \;
```

## Rolling Concerns File

For active monitoring, keep a rolling concerns file in this repository's local `./tmp/` directory. Use the target directory name in the filename, for example:

```sh
mkdir -p ./tmp
$EDITOR ./tmp/TARGET_DIRECTORY_NAME-monitor-concerns.md
```

This file is monitoring scratch state, not a permanent report. Update it on every monitoring pass:

- Rewrite the current judgement with the latest common-sense assessment.
- Add new concerns when there is evidence of real risk: hangs, repeated rejected attempts, model/quota failover, stale DB/process mismatch, missing verification, bad retry behavior, merge conflicts, or orphaned child processes.
- Amend existing concerns with new evidence rather than duplicating them.
- Delete or move concerns to a cleared section when later evidence resolves them.
- Keep a short `Watch next` section for the next practical checks.

The concerns file should help the next monitor understand what matters without rereading every log. It should not become an append-only event stream; the database and logs already provide that evidence.

## Monitoring Report

A monitoring report should use common sense, not just paste command output. The purpose is to help the user understand whether the loop is doing the expected jobs and tasks, or whether it has run into problems.

Include:

- Current goal status and what the loop is meant to be doing next.
- Active task or review, including the route/provider/model when available.
- Recent attempts, test results, commits, and whether they show real progress.
- Relevant handover entries, especially whether reviewer rejections explain why findings block the current task now.
- Whether generated views, database state, worktrees, logs, and live processes agree with each other.
- Any concerns, such as repeated failures, no files changing, stale `running` tasks, unexpected long-running processes, auth/quota/model errors, malformed verification commands, missing credentials, or a blocked approval state.
- A clear judgement: `going as expected`, `waiting for user action`, `concerning but still progressing`, or `likely stuck/broken`.
- The next practical action, such as approve the plan, wait and poll again, inspect a specific log, resume the loop, fix configuration, or stop the run.

If everything looks normal, say that plainly and explain what evidence supports it. If something looks wrong, summarize the strongest evidence and distinguish between a confirmed failure and a suspicion.

## Status Meanings

- `awaiting_plan_approval`: planning is complete and execution is waiting for the user to approve the plan.
- `running`: tasks are being executed or reviewed.
- `blocked`: the loop hit a hard blocker or exhausted allowed retries.
- `complete`: all planned work finished and passed review.

## Notes

Task worktrees should default to a visible ignored directory such as `worktrees/`. Some model CLIs, including `agy`, may reject workspace paths under hidden directories such as `.agent-loop/worktrees`.

If tests suddenly take much longer than usual, assume a hang or external process issue until proven otherwise. Inspect processes and the current attempt before waiting indefinitely.
