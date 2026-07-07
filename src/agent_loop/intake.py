import json
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import datetime

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import ProviderStateRepository
from agent_loop.routing import ModelRouter

BRAINSTORM_MIN_QUESTIONS = 3
BRAINSTORM_MAX_QUESTIONS = 5


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Empty output from model")
        
    # Remove markdown code blocks if present
    import re
    md_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if md_match:
        cleaned = md_match.group(1).strip()
        
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise

def _diagnostic_for_result(res: Any) -> str:
    if getattr(res, "error", "").strip():
        return res.error.strip()
    flags = []
    if getattr(res, "auth_required", False):
        flags.append("auth required")
    if getattr(res, "quota_exhausted", False):
        flags.append("quota exhausted")
    if getattr(res, "transient_failure", False):
        flags.append("transient failure")
    if getattr(res, "unavailable", False):
        flags.append("model unavailable")
    if flags:
        return ", ".join(flags)
    output = getattr(res, "output", "").strip()
    if output.lower().startswith("error:"):
        return output.splitlines()[0]
    return f"exit code {getattr(res, 'exit_code', 'unknown')} with empty stderr"

def _call_intake_model(prompt: str, config: Config, phase: str, warning_label: str, profile: str = "intake") -> Optional[Any]:
    workspace_path = Path.cwd()
    conn = get_connection(config.db_path)
    try:
        migrate(conn)
        router = ModelRouter(config=config, provider_repo=ProviderStateRepository(conn))
        print(f"{warning_label} thinking...", flush=True)
        res = router.run(
            profile=profile,
            prompt=prompt,
            workspace_path=workspace_path,
            logs_root=config.logs_dir / "intake" / phase,
            timeout_seconds=120.0,
        )
    finally:
        conn.close()

    if res.success:
        return res

    diagnostic = _diagnostic_for_result(res)
    provider_label = f"{res.provider} {res.model}" if getattr(res, "provider", None) else profile
    print(f"Warning: {warning_label} model call failed ({provider_label}). Error: {diagnostic}")
    return res


def _fallback_brainstorm_question(index: int) -> str:
    questions = [
        "Who is the main user or operator, and what do they need this to do first?",
        "What should be true when the first useful version is complete?",
        "What integrations, data sources, persistence, or constraints matter most?",
        "What should be explicitly out of scope or easy to get wrong?",
        "How should agent-loop verify that the result works?",
    ]
    return questions[min(index, len(questions) - 1)]


def _brainstorm_summary(goal: str, transcript: str, current_understanding: str) -> str:
    summary = current_understanding.strip()
    if summary:
        return summary

    answers = [
        line[3:].strip()
        for line in transcript.splitlines()
        if line.startswith("A:") and line[3:].strip()
    ]
    if answers:
        return " ".join(answers)
    return f"The goal is to {goal.strip()}"


def _ask_after_summary(goal: str, transcript: str, current_understanding: str) -> str:
    print("\nBrainstorming summary:")
    print(_brainstorm_summary(goal, transcript, current_understanding))
    while True:
        choice = input("Continue brainstorming or draft spec? (continue/draft/edit): ").strip().lower()
        if choice in {"", "draft", "d", "yes", "y"}:
            return "draft"
        if choice in {"continue", "c", "no", "n"}:
            return "continue"
        if choice in {"edit", "e"}:
            return "edit"
        print("Please enter 'continue', 'draft', or 'edit'.")


def run_brainstorm_discussion(goal: str, config: Config) -> str:
    transcript = ""
    current_understanding = ""
    questions_asked = 0
    
    for turn in range(BRAINSTORM_MAX_QUESTIONS):
        prompt = f"""You are the Agent Loop Brainstormer.

Goal:
{goal}

Conversation so far:
{transcript}

Questions asked so far: {questions_asked}
Minimum questions before drafting: {BRAINSTORM_MIN_QUESTIONS}
Maximum questions: {BRAINSTORM_MAX_QUESTIONS}

Decide the next single question needed before drafting a compact implementation spec.

Return ONLY JSON:
{{
"status": "question" | "ready",
"question": "one concrete question if status=question",
"reason": "short internal reason",
"current_understanding": "one or two sentences summarizing what is known so far",
"draft_spec": "compact spec if status=ready"
}}

Rules:
* Ask at most one question.
* Ask at least {BRAINSTORM_MIN_QUESTIONS} questions before status=ready unless the goal is genuinely trivial.
* Ask only a question specific to this goal.
* Prefer concrete tradeoffs, scope boundaries, integration points, verification, first useful outcome.
* Do not ask generic product-manager questions.
* After each user answer, update current_understanding.
* Stop after {BRAINSTORM_MAX_QUESTIONS} questions even if more could be discussed.
* The compact spec must follow the project skill format.
* Keep the spec compact."""
        
        data = None
        res = _call_intake_model(prompt, config, "brainstorm", "Brainstorming", profile="intake")
        if res and res.success:
            try:
                data = _extract_json_object(res.output)
            except Exception as e:
                print(f"Warning: Brainstorming model returned invalid JSON. Error: {e}")
                print(f"Raw output: {repr(res.output)}")
                print(f"Stderr: {repr(res.error)}")
                
        if not data:
            print("Error: Brainstorming failed after 3 API attempts. Moving to auto-draft.")
            break

        current_understanding = (data.get("current_understanding") or current_understanding).strip()
            
        if data.get("status") == "ready" and data.get("draft_spec") and questions_asked >= BRAINSTORM_MIN_QUESTIONS:
            action = _ask_after_summary(goal, transcript, current_understanding)
            if action == "draft":
                return data["draft_spec"]
            if action == "edit":
                feedback = input("What should change in the summary?: ").strip()
                if feedback:
                    transcript += f"\nSummary revision: {feedback}\n"
                return draft_compact_spec(goal, transcript, config)
            if questions_asked >= BRAINSTORM_MAX_QUESTIONS:
                return data["draft_spec"]
            continue
            
        question = data.get("question")
        if not question or data.get("status") != "question":
            if questions_asked >= BRAINSTORM_MIN_QUESTIONS:
                break
            question = _fallback_brainstorm_question(questions_asked)
            
        print(f"\n[Brainstormer] {question}")
        answer = input("Your answer: ").strip()
        transcript += f"\nQ: {question}\nA: {answer}\n"
        questions_asked += 1

        if current_understanding:
            print(f"Current understanding: {current_understanding}")

        if questions_asked >= BRAINSTORM_MIN_QUESTIONS:
            action = _ask_after_summary(goal, transcript, current_understanding)
            if action == "draft":
                return draft_compact_spec(goal, transcript, config)
            if action == "edit":
                feedback = input("What should change in the summary?: ").strip()
                if feedback:
                    transcript += f"\nSummary revision: {feedback}\n"
                return draft_compact_spec(goal, transcript, config)
        
    # If we exit the loop without returning a spec, force draft it
    return draft_compact_spec(goal, transcript, config)

def draft_compact_spec(goal: str, transcript: str, config: Config) -> str:
    prompt = f"""You are the Agent Loop Brainstormer.

Goal:
{goal}

Conversation so far:
{transcript}

Draft a compact implementation spec from the goal and conversation.

Return ONLY JSON:
{{
"draft_spec": "compact spec"
}}

The compact spec format:
# Compact Spec
## Outcome
## Requirements
## Non-goals
## User / Operator Experience
## Visual Direction
## Verification
## First Cut
## Open Questions
Omit empty sections except Outcome, Requirements, Verification, First Cut.
Keep it compact."""

    res = _call_intake_model(prompt, config, "draft-spec", "Draft spec", profile="intake")
    if res and res.success:
        try:
            data = _extract_json_object(res.output)
            return data.get("draft_spec", "")
        except Exception as e:
            print(f"Warning: Draft spec model returned invalid JSON. Error: {e}")
            
    print("Error: Draft spec failed after 3 attempts.")
    return f"# Compact Spec\n\n## Outcome\n{goal}\n\n## Requirements\n(Auto-generated spec failed)"

def run_ui_branch(spec: str, config: Config) -> str:
    print("UI brainstorming is deferred for now; continuing with text spec intake.")
    return spec

def run_spec_review(spec: str, config: Config) -> Tuple[str, str]:
    prompt = f"""You are the Agent Loop Compact Spec Reviewer.

Review this compact spec before it is shown to the user.

Spec:
{spec}

Return ONLY markdown in this format:
# Review Result
Status: approved | revised | needs-user-answer

Reviewer Notes:
* short note only if material

# Revised Compact Spec
[full revised compact spec]

Rules:
* Fix the spec directly where possible.
* Do not write a long report.
* Only block if a missing answer would cause a bad plan.
* Remove bloat.
* Clarify ambiguity.
* Preserve user intent.
* Keep the revised spec compact."""

    res = _call_intake_model(prompt, config, "spec-review", "Spec review", profile="spec_reviewer")
    if not res or not res.success:
        return "approved", spec
        
    output = res.output
    status = "approved"
    revised_spec = spec
    
    # Parse the markdown response
    lines = output.splitlines()
    in_spec = False
    spec_lines = []
    
    for line in lines:
        if line.startswith("Status:"):
            status = line.split(":", 1)[1].strip().lower()
        elif line.startswith("# Revised Compact Spec"):
            in_spec = True
            continue
        elif in_spec:
            spec_lines.append(line)
            
    if spec_lines:
        revised_spec = "\n".join(spec_lines).strip()
        
    if status not in {"approved", "revised", "needs-user-answer"}:
        status = "approved"
        
    return status, revised_spec

def run_spec_intake(goal: str, config: Config, force_ui: Optional[bool] = None, spec_review: bool = True) -> Optional[str]:
    print("\n--- Spec Intake Phase ---")
    spec = run_brainstorm_discussion(goal, config)

    if force_ui is True:
        print("UI brainstorming is deferred for now; continuing with text spec intake.")
        
    while True:
        if spec_review:
            print("\nRunning internal spec review...")
            status, revised_spec = run_spec_review(spec, config)
            spec = revised_spec
            
            if status == "needs-user-answer":
                print("\n[Reviewer needs clarification]")
                # We could extract the specific question from Reviewer Notes, but for simplicity
                ans = input("Please clarify the open question: ")
                spec += f"\n\nClarification: {ans}"
                continue
        
        print("\nHere is the revised compact spec:\n")
        print("=========================================")
        print(spec)
        print("=========================================\n")
        
        ans = input("Approve this spec and start planning? (yes/no/edit): ").strip().lower()
        if ans in {"yes", "y"}:
            # Save the spec
            now = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
            slug = "-".join(goal.split()[:3]).lower()
            slug = "".join(c for c in slug if c.isalnum() or c == "-")
            save_path = config.state_dir / "specs" / f"{now}-{slug}.md"
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(spec, encoding="utf-8")
            print(f"Spec saved to {save_path}")
            return spec
        elif ans in {"edit", "e"}:
            feedback = input("What needs to change?: ")
            spec += f"\n\nUser Revision Request: {feedback}"
        else:
            print("Spec rejected. Exiting.")
            return None
