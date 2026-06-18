import os
import re
import json
import subprocess
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

def resolve_binary(binary_name: str, config: Optional[Any] = None) -> str:
    # 1. Config override
    if config:
        config_key = f"{binary_name.replace('-', '_')}_path"
        override = config.data.get(config_key)
        if override:
            return str(override)
            
    # 2. PATH check
    path_resolved = shutil.which(binary_name)
    if path_resolved:
        return path_resolved
        
    raise FileNotFoundError(f"Could not resolve binary '{binary_name}' from configuration or PATH.")

class AttemptResult:
    def __init__(
        self,
        success: bool,
        exit_code: int,
        output: str,
        error: str,
        token_usage: Optional[Dict[str, int]] = None,
        quota_reset: Optional[str] = None,
        quota_exhausted: bool = False,
        auth_required: bool = False,
        transient_failure: bool = False,
        unavailable: bool = False
    ):
        self.success = success
        self.exit_code = exit_code
        self.output = output
        self.error = error
        self.token_usage = token_usage or {}
        self.quota_reset = quota_reset
        self.quota_exhausted = quota_exhausted
        self.auth_required = auth_required
        self.transient_failure = transient_failure
        self.unavailable = unavailable


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
    def __init__(self, binary_path: Optional[str] = None, config: Optional[Any] = None):
        self.binary_path = binary_path or resolve_binary("codex", config)

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
            "--dangerously-bypass-approvals-and-sandbox",
            "-o", str(output_msg_file)
        ]
        if reasoning_level:
            cmd += ["-c", f"model_reasoning_effort={reasoning_level}"]
        
        # Check if plan_schema.json exists and the prompt explicitly requests the planning schema
        schema_path = Path(__file__).parent / "plan_schema.json"
        if schema_path.exists() and "You are the Agent Loop Planner" in prompt:
            cmd += ["--output-schema", str(schema_path)]
            
        use_stdin = False
        if len(prompt) > 65000:
            cmd.append("-")
            use_stdin = True
        else:
            cmd.append(prompt)

        stdout_file = attempt_logs_dir / "stdout.log"
        stderr_file = attempt_logs_dir / "stderr.log"
        events_file = attempt_logs_dir / "codex_events.jsonl"

        try:
            # We redirect stdout/stderr to files to capture logs append-only
            with stdout_file.open("w") as out_f, stderr_file.open("w") as err_f:
                process = subprocess.run(
                    cmd,
                    input=prompt.encode("utf-8") if use_stdin else None,
                    stdin=subprocess.DEVNULL if not use_stdin else None,
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

            diagnostic_parts = [stderr_content]
            for event in events:
                if event.get("event") == "error":
                    diagnostic_parts.append(json.dumps(event))
            if process.returncode != 0:
                diagnostic_parts.append(stdout_content)
            lowered_all = "\n".join(diagnostic_parts).lower()
            if any(q in lowered_all for q in ["insufficient_quota", "rate_limit_exceeded", "rate limit", "quota exceeded", "status code 429"]):
                quota_exhausted = True

            auth_required = False
            if any(a in lowered_all for a in ["login required", "unauthenticated", "missing credentials", "expired credentials", "expired token", "authentication required", "not logged in", "run antigravity-usage login", "run agy login", "401 unauthorized", "http error: 401"]):
                auth_required = True

            transient_failure = False
            if any(x in lowered_all for x in ["connection reset", "connection refused", "502 bad gateway", "503 service unavailable", "504 gateway timeout", "temporary error", "timeout"]):
                transient_failure = True
                
            unavailable = False
            if any(x in lowered_all for x in ["model not found", "model unavailable", "unsupported model", "invalid model", "does not exist", "404 not found", "model not supported"]):
                unavailable = True

            success = (process.returncode == 0) and not quota_exhausted and not auth_required and not transient_failure and not unavailable

            return AttemptResult(
                success=success,
                exit_code=process.returncode,
                output=agent_msg,
                error=stderr_content,
                token_usage=token_usage,
                quota_reset=quota_reset,
                quota_exhausted=quota_exhausted,
                auth_required=auth_required,
                transient_failure=transient_failure,
                unavailable=unavailable
            )

        except subprocess.TimeoutExpired as te:
            return AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=f"Timeout expired after {timeout_seconds} seconds.",
                quota_exhausted=False,
                transient_failure=True
            )
        except Exception as ex:
            # Check if exception represents transient/network failure
            err_msg = str(ex).lower()
            is_transient = any(x in err_msg for x in ["connection", "timeout", "network", "http"])
            return AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=str(ex),
                quota_exhausted=False,
                transient_failure=is_transient
            )

    def probe_availability(self) -> bool:
        cap = self.discover_capabilities()
        return cap["installed"]


class AgyAdapter(BaseAdapter):
    def __init__(self, binary_path: Optional[str] = None, config: Optional[Any] = None):
        self.binary_path = binary_path or resolve_binary("agy", config)

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
            "--print-timeout", f"{int(timeout_seconds)}s",
            "--dangerously-skip-permissions",
            "--model", model,
            "--log-file", str(agy_log_file),
            "--add-dir", str(workspace_path)
        ]
        if len(prompt) > 65000:
            cmd.append(f"Please read your instructions and full context from the file at '{prompt_file.absolute()}'. Execute the requested task strictly based on the contents of that file.")
        else:
            cmd.append(prompt)

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

            auth_required = False
            if any(a in lowered_all for a in ["login required", "unauthenticated", "missing credentials", "expired credentials", "expired token", "authentication required", "not logged in", "run antigravity-usage login", "run agy login", "401 unauthorized", "http error: 401"]):
                auth_required = True

            transient_failure = False
            if any(x in lowered_all for x in ["connection reset", "connection refused", "502 bad gateway", "503 service unavailable", "504 gateway timeout", "temporary error", "timeout"]):
                transient_failure = True
                
            unavailable = False
            if any(x in lowered_all for x in ["model not found", "model unavailable", "unsupported model", "invalid model", "does not exist", "404 not found", "model not supported"]):
                unavailable = True

            success = (process.returncode == 0) and not quota_exhausted and not auth_required and not transient_failure and not unavailable

            return AttemptResult(
                success=success,
                exit_code=process.returncode,
                output=stdout_content,
                error=stderr_content,
                token_usage={}, # agy doesn't report token usage
                quota_reset=quota_reset,
                quota_exhausted=quota_exhausted,
                auth_required=auth_required,
                transient_failure=transient_failure,
                unavailable=unavailable
            )

        except subprocess.TimeoutExpired as te:
            return AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=f"Timeout expired after {timeout_seconds} seconds.",
                quota_exhausted=False,
                transient_failure=True
            )
        except Exception as ex:
            err_msg = str(ex).lower()
            is_transient = any(x in err_msg for x in ["connection", "timeout", "network", "http"])
            return AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=str(ex),
                quota_exhausted=False,
                transient_failure=is_transient
            )

    def probe_availability(self) -> bool:
        cap = self.discover_capabilities()
        return cap["installed"]


def get_adapter(provider_name: str, config: Optional[Any] = None) -> BaseAdapter:
    if provider_name == "codex":
        return CodexAdapter(config=config)
    elif provider_name == "agy":
        return AgyAdapter(config=config)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")
