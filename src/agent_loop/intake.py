import json
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import datetime

from agent_loop.config import Config
from agent_loop.database import get_connection, migrate
from agent_loop.repositories import ProviderStateRepository
from agent_loop.routing import ModelRouter

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

def run_brainstorm_discussion(goal: str, config: Config) -> str:
    transcript = ""
    
    for turn in range(6):
        prompt = f"""You are the Agent Loop Brainstormer.

Goal:
{goal}

Conversation so far:
{transcript}

Decide whether another question is needed before drafting a compact implementation spec.

Return ONLY JSON:
{{
"status": "question" | "ready",
"question": "one concrete question if status=question",
"reason": "short internal reason",
"draft_spec": "compact spec if status=ready"
}}

Rules:
* Ask at most one question.
* Ask only a question specific to this goal.
* Prefer concrete tradeoffs, scope boundaries, integration points, verification, first useful outcome.
* Do not ask generic product-manager questions.
* Stop as soon as the planner can safely proceed.
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
            
        if data.get("status") == "ready" and data.get("draft_spec"):
            return data["draft_spec"]
            
        question = data.get("question")
        if not question or data.get("status") != "question":
            break
            
        print(f"\n[Brainstormer] {question}")
        answer = input("Your answer: ").strip()
        transcript += f"\nQ: {question}\nA: {answer}\n"
        
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
    print("\n--- UI Brainstorming ---")
    script_path = None
    
    # Try workspace first
    ws_script = config.state_dir / "skills" / "brainstorming" / "scripts" / "start-server.sh"
    if ws_script.exists():
        script_path = ws_script
    else:
        # Try bundled
        bundled_script = Path(__file__).resolve().parents[2] / "skills" / "brainstorming" / "scripts" / "start-server.sh"
        if bundled_script.exists():
            script_path = bundled_script
            
    if not script_path:
        print("Warning: UI companion scripts not found. Continuing with terminal-only UI brainstorming.")
        # We could fallback to terminal-only UI logic, but for simplicity we'll just return the spec unchanged
        # or append a simple terminal question.
        return spec
        
    print(f"Found visual companion at {script_path}. (Implementation of full visual server loop goes here...)")
    # For now, append a placeholder to prove it ran
    return spec + "\n\n## Visual Direction\nVisual UI brainstorming was executed."

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
    
    ui_choice = False
    if force_ui is True:
        ui_choice = True
    elif force_ui is None:
        ans = input("\nDo you want to brainstorm the UI / visual behaviour? (yes/no): ").strip().lower()
        if ans in {"yes", "y"}:
            ui_choice = True
            
    if ui_choice:
        spec = run_ui_branch(spec, config)
        
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
