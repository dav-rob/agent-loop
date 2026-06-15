#!/usr/bin/env python3
import subprocess
import time
from pathlib import Path
from datetime import datetime

ROOT = Path.cwd()

PROGRESS = ROOT / "progress.md"
LEARNING = ROOT / "learning.md"
PLAN = ROOT / "plan.md"
LOG = ROOT / ".agent-loop.log"

MAX_CYCLES = 30
SLEEP_SECONDS = 2

HIGH = ["codex", "run", "--model", "gpt-5.5"]
CHEAP = ["codex", "run", "--model", "gpt-5.5-mini"]

PROMPTS = {
    "plan": """
Read AGENTS.md, progress.md, learning.md.

Create or update plan.md.

Classify risk as: low, medium, high.

Use high-risk only for architecture, security, protected tests, auth, payments,
data deletion, deployment, or ambiguous product decisions.

End by writing LOOP_STATUS: continue to progress.md unless blocked.
""",

    "work": """
Read AGENTS.md, plan.md, progress.md, learning.md.

Execute the next smallest useful task.

Use existing skills where relevant.

Do not ask the user anything unless a stop condition is hit.

Update progress.md.

Update learning.md only for reusable knowledge.

End by writing exactly one status line to progress.md:

LOOP_STATUS: continue
LOOP_STATUS: blocked
LOOP_STATUS: complete
""",

    "review": """
Read AGENTS.md, plan.md, progress.md, learning.md.

Review the current diff skeptically.

Check:
- correctness
- regressions
- protected test changes
- unnecessary complexity
- missing tests
- security issues

Do not make large implementation changes.

Update progress.md with review result.

End with exactly one:

LOOP_STATUS: continue
LOOP_STATUS: blocked
LOOP_STATUS: complete
"""
}

def append_log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n"
    with LOG.open("a") as f:
        f.write(line)

def ensure_files() -> None:
    if not PROGRESS.exists():
        PROGRESS.write_text("# progress.md\n\n## Goal\n\n## Current status\n\nLOOP_STATUS: continue\n")
    if not LEARNING.exists():
        LEARNING.write_text("# learning.md\n\n## Durable project facts\n\n")
    if not PLAN.exists():
        PLAN.write_text("# plan.md\n\n## Objective\n\n## Tasks\n\n## Risk level\n\nlow\n")

def text(path: Path) -> str:
    return path.read_text(errors="ignore") if path.exists() else ""

def status() -> str:
    p = text(PROGRESS)
    if "LOOP_STATUS: complete" in p:
        return "complete"
    if "LOOP_STATUS: blocked" in p:
        return "blocked"
    return "continue"

def risk() -> str:
    p = text(PLAN).lower()
    if "high" in p:
        return "high"
    if "medium" in p:
        return "medium"
    return "low"

def run(cmd: list[str], prompt: str) -> int:
    append_log(f"running: {' '.join(cmd[:4])}")
    result = subprocess.run(cmd + [prompt], cwd=ROOT, text=True)
    append_log(f"exit: {result.returncode}")
    return result.returncode

def main() -> None:
    ensure_files()

    run(HIGH, PROMPTS["plan"])

    failures = 0

    for cycle in range(1, MAX_CYCLES + 1):
        append_log(f"cycle {cycle}")

        model = HIGH if risk() == "high" or failures >= 2 else CHEAP
        code = run(model, PROMPTS["work"])

        if code != 0:
            failures += 1
        else:
            failures = 0

        s = status()
        if s in {"blocked", "complete"}:
            run(HIGH, PROMPTS["review"])
            print(f"loop stopped: {status()}")
            return

        if cycle % 5 == 0 or risk() in {"medium", "high"}:
            run(HIGH, PROMPTS["review"])

        time.sleep(SLEEP_SECONDS)

    print("loop stopped: max cycles reached")

if __name__ == "__main__":
    main()