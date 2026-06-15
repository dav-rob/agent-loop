import os
import re
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

class AttemptResult:
    def __init__(
        self,
        success: bool,
        exit_code: int,
        output: str,
        error: str,
        token_usage: Optional[Dict[str, int]] = None,
        quota_reset: Optional[str] = None,
        quota_exhausted: bool = False
    ):
        self.success = success
        self.exit_code = exit_code
        self.output = output
        self.error = error
        self.token_usage = token_usage or {}
        self.quota_reset = quota_reset
        self.quota_exhausted = quota_exhausted


def redact_secrets(text: str) -> str:
    if not text:
        return text
    sensitive_values = set()
    for k, v in os.environ.items():
        k_lower = k.lower()
        # Look for common credential/secret keywords
        if any(sub in k_lower for sub in ["key", "secret", "token", "password", "auth", "webhook", "url"]):
            if v and len(v) > 4:
                sensitive_values.add(v)
    
    redacted = text
    # Sort by length descending to redact longer strings first (avoiding partial redacting of substrings)
    for val in sorted(sensitive_values, key=len, reverse=True):
        redacted = redacted.replace(val, "[REDACTED]")
    return redacted


class BaseAdapter:
    def discover_capabilities(self) -> Dict[str, Any]:
        raise NotImplementedError

    def run_attempt(
        self,
        model: str,
        prompt: str,
        workspace_path: Path,
        attempt_logs_dir: Path,
        timeout_seconds: float = 600.0,
        reasoning_level: Optional[str] = None
    ) -> AttemptResult:
        raise NotImplementedError

    def probe_availability(self) -> bool:
        raise NotImplementedError


class CodexAdapter(BaseAdapter):
    def __init__(self, binary_path: str = "/usr/local/bin/codex"):
        self.binary_path = binary_path

    def discover_capabilities(self) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                [self.binary_path, "--version"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=3.0
            )
            if res.returncode == 0:
                version = res.stdout.strip()
                return {
                    "installed": True,
                    "version": version,
                    # Codex doesn't have list-models command, return typical config models
                    "models": ["gpt-5.5", "gpt-5.4-mini"]
                }
        except Exception:
            pass
        return {"installed": False, "version": None, "models": []}

    def run_attempt(
        self,
        model: str,
        prompt: str,
        workspace_path: Path,
        attempt_logs_dir: Path,
        timeout_seconds: float = 600.0,
        reasoning_level: Optional[str] = None
    ) -> AttemptResult:
        workspace_path = Path(workspace_path).resolve()
        attempt_logs_dir = Path(attempt_logs_dir).resolve()
        attempt_logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Write prompt manifest
        prompt_file = attempt_logs_dir / "prompt.txt"
        prompt_file.write_text(redact_secrets(prompt))

        # Output message file
        output_msg_file = attempt_logs_dir / "last_message.txt"

        # Build command: codex exec --json --cd <workspace> -m <model> -o <msg_file> "<prompt>"
        # Run under trusted-host model using danger-full-access and never ask for approval to prevent hangs
        cmd = [
            self.binary_path,
            "exec",
            "--json",
            "--cd", str(workspace_path),
            "-m", model,
            "-s", "danger-full-access",
            "-a", "never",
            "-o", str(output_msg_file)
        ]
        if reasoning_level:
            cmd += ["-c", f"reasoning_level={reasoning_level}"]
        
        # Check if plan_schema.json exists in package dir to pass to Codex planning route
        schema_path = Path(__file__).parent / "plan_schema.json"
        if schema_path.exists() and ("schema" in prompt.lower() or "planner" in prompt.lower()):
            cmd += ["--output-schema", str(schema_path)]
            
        cmd.append(prompt)

        stdout_file = attempt_logs_dir / "stdout.log"
        stderr_file = attempt_logs_dir / "stderr.log"
        events_file = attempt_logs_dir / "codex_events.jsonl"

        try:
            # We redirect stdout/stderr to files to capture logs append-only
            with stdout_file.open("w") as out_f, stderr_file.open("w") as err_f:
                process = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=out_f,
                    stderr=err_f,
                    timeout=timeout_seconds,
                    cwd=workspace_path
                )
            
            # Read stdout/stderr content
            stdout_content = redact_secrets(stdout_file.read_text(errors="ignore"))
            stderr_content = redact_secrets(stderr_file.read_text(errors="ignore"))

            # Write redacted contents back
            stdout_file.write_text(stdout_content)
            stderr_file.write_text(stderr_content)

            # Parse JSONL events from stdout
            token_usage = {"input": 0, "output": 0}
            events = []
            quota_exhausted = False
            quota_reset = None

            for line in stdout_content.splitlines():
                if line.strip().startswith("{") and line.strip().endswith("}"):
                    try:
                        event = json.loads(line)
                        events.append(event)
                        if event.get("event") == "token_usage":
                            token_usage["input"] = event.get("input", 0)
                            token_usage["output"] = event.get("output", 0)
                        elif event.get("event") == "error":
                            err_data = event.get("error", {})
                            err_code = err_data.get("code", "")
                            if err_code in {"insufficient_quota", "rate_limit_exceeded", "quota_exceeded"}:
                                quota_exhausted = True
                            if "reset" in err_data:
                                quota_reset = err_data["reset"]
                    except json.JSONDecodeError:
                        pass
            
            # Write events to separate file
            if events:
                events_file.write_text(
                    "\n".join(json.dumps(e) for e in events) + "\n"
                )

            # Read the last message from agent
            agent_msg = ""
            if output_msg_file.exists():
                agent_msg = redact_secrets(output_msg_file.read_text(errors="ignore"))

            # Fall back to raw stdout if no structured last message file
            if not agent_msg:
                # Compile text messages from events
                msg_parts = []
                for event in events:
                    if event.get("event") == "message" and event.get("role") == "assistant":
                        msg_parts.append(event.get("content", ""))
                agent_msg = "".join(msg_parts) if msg_parts else stdout_content

            # Quota detection on stderr/stdout text
            lowered_all = (stdout_content + "\n" + stderr_content).lower()
            if any(q in lowered_all for q in ["insufficient_quota", "rate_limit_exceeded", "rate limit", "quota exceeded", "status code 429"]):
                quota_exhausted = True

            success = (process.returncode == 0) and not quota_exhausted

            return AttemptResult(
                success=success,
                exit_code=process.returncode,
                output=agent_msg,
                error=stderr_content,
                token_usage=token_usage,
                quota_reset=quota_reset,
                quota_exhausted=quota_exhausted
            )

        except subprocess.TimeoutExpired as te:
            return AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=f"Timeout expired after {timeout_seconds} seconds.",
                quota_exhausted=False
            )
        except Exception as ex:
            return AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=str(ex),
                quota_exhausted=False
            )

    def probe_availability(self) -> bool:
        cap = self.discover_capabilities()
        return cap["installed"]


class AgyAdapter(BaseAdapter):
    def __init__(self, binary_path: str = "/Users/davidroberts/.local/bin/agy"):
        self.binary_path = binary_path

    def discover_capabilities(self) -> Dict[str, Any]:
        try:
            # Check version
            res_ver = subprocess.run(
                [self.binary_path, "--version"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=3.0
            )
            if res_ver.returncode == 0:
                version = res_ver.stdout.strip()
                # Check models list (using stdin=DEVNULL to prevent hangs!)
                res_mod = subprocess.run(
                    [self.binary_path, "models"],
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    timeout=3.0
                )
                models = []
                if res_mod.returncode == 0:
                    models = [line.strip() for line in res_mod.stdout.splitlines() if line.strip()]
                return {
                    "installed": True,
                    "version": version,
                    "models": models
                }
        except Exception:
            pass
        return {"installed": False, "version": None, "models": []}

    def run_attempt(
        self,
        model: str,
        prompt: str,
        workspace_path: Path,
        attempt_logs_dir: Path,
        timeout_seconds: float = 600.0,
        reasoning_level: Optional[str] = None
    ) -> AttemptResult:
        workspace_path = Path(workspace_path).resolve()
        attempt_logs_dir = Path(attempt_logs_dir).resolve()
        attempt_logs_dir.mkdir(parents=True, exist_ok=True)

        prompt_file = attempt_logs_dir / "prompt.txt"
        prompt_file.write_text(redact_secrets(prompt))

        agy_log_file = attempt_logs_dir / "agy.log"
        stdout_file = attempt_logs_dir / "stdout.log"
        stderr_file = attempt_logs_dir / "stderr.log"

        # Build command: agy --print --dangerously-skip-permissions --model <model> --log-file <log_file> --add-dir <workspace> "<prompt>"
        cmd = [
            self.binary_path,
            "--print",
            "--dangerously-skip-permissions",
            "--model", model,
            "--log-file", str(agy_log_file),
            "--add-dir", str(workspace_path),
            prompt
        ]

        try:
            # Pass stdin=DEVNULL to ensure non-interactive execution avoids hangs!
            with stdout_file.open("w") as out_f, stderr_file.open("w") as err_f:
                process = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=out_f,
                    stderr=err_f,
                    timeout=timeout_seconds,
                    cwd=workspace_path
                )

            stdout_content = redact_secrets(stdout_file.read_text(errors="ignore"))
            stderr_content = redact_secrets(stderr_file.read_text(errors="ignore"))

            # Redact agy log file too
            if agy_log_file.exists():
                log_content = redact_secrets(agy_log_file.read_text(errors="ignore"))
                agy_log_file.write_text(log_content)

            stdout_file.write_text(stdout_content)
            stderr_file.write_text(stderr_content)

            # Quota classification on stdout/stderr/log
            combined_text = stdout_content + "\n" + stderr_content
            if agy_log_file.exists():
                combined_text += "\n" + agy_log_file.read_text(errors="ignore")

            lowered_all = combined_text.lower()
            quota_exhausted = False
            quota_reset = None
            if any(q in lowered_all for q in ["insufficient_quota", "rate_limit_exceeded", "rate limit", "quota exceeded", "status code 429"]):
                quota_exhausted = True

            # Try to parse observed reset time if printed (preserving original case)
            reset_match = re.search(r"reset at ([\d\-\:\stz]+)", combined_text, re.IGNORECASE)
            if reset_match:
                quota_reset = reset_match.group(1).strip()

            success = (process.returncode == 0) and not quota_exhausted

            return AttemptResult(
                success=success,
                exit_code=process.returncode,
                output=stdout_content,
                error=stderr_content,
                token_usage={}, # agy doesn't report token usage
                quota_reset=quota_reset,
                quota_exhausted=quota_exhausted
            )

        except subprocess.TimeoutExpired as te:
            return AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=f"Timeout expired after {timeout_seconds} seconds.",
                quota_exhausted=False
            )
        except Exception as ex:
            return AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=str(ex),
                quota_exhausted=False
            )

    def probe_availability(self) -> bool:
        cap = self.discover_capabilities()
        return cap["installed"]


def get_adapter(provider_name: str, config: Optional[Config] = None) -> BaseAdapter:
    if provider_name == "codex":
        # Check config for custom binary override if needed
        return CodexAdapter()
    elif provider_name == "agy":
        return AgyAdapter()
    else:
        raise ValueError(f"Unknown provider: {provider_name}")
