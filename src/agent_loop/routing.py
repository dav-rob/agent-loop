from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from agent_loop.adapters import AttemptResult, get_adapter
from agent_loop.config import Config


@dataclass
class RoutedAttemptResult:
    result: AttemptResult
    profile: str
    provider: Optional[str] = None
    model: Optional[str] = None
    reasoning_level: Optional[str] = None
    logs_dir: Optional[Path] = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.result, name)


def _safe_path_part(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-")[:60] or "route"


class ModelRouter:
    def __init__(self, config: Config, provider_repo: Any):
        self.config = config
        self.provider_repo = provider_repo

    def available_routes(self, profile: str) -> list[Dict[str, Any]]:
        routes = []
        for route in self.config.routes_for(profile):
            provider = route["provider"]
            model = route["model"]
            p_state = self.provider_repo.get(provider, model)
            if p_state and not p_state.get("availability", True):
                continue
            if p_state and p_state.get("quota_state") in {
                "auth_required",
                "limited_known_reset",
                "limited_unknown_reset",
                "transient_failure",
                "unavailable",
            }:
                continue
            routes.append(route)
        return routes

    def run(
        self,
        profile: str,
        prompt: str,
        workspace_path: Path,
        logs_root: Path,
        timeout_seconds: float = 600.0,
    ) -> RoutedAttemptResult:
        last_result: Optional[RoutedAttemptResult] = None

        for index, route in enumerate(self.config.routes_for(profile), start=1):
            provider = route["provider"]
            model = route["model"]
            reasoning_level = route.get("reasoning_level")

            if route not in self.available_routes(profile):
                continue

            logs_dir = (
                Path(logs_root)
                / f"{index:02d}-{profile}-{provider}-{_safe_path_part(str(model))}"
            ).resolve()
            logs_dir.mkdir(parents=True, exist_ok=True)

            try:
                adapter = get_adapter(provider, self.config)
                result = adapter.run_attempt(
                    model=model,
                    prompt=prompt,
                    workspace_path=workspace_path,
                    attempt_logs_dir=logs_dir,
                    timeout_seconds=timeout_seconds,
                    reasoning_level=reasoning_level,
                )
            except Exception as exc:
                result = AttemptResult(
                    success=False,
                    exit_code=-1,
                    output="",
                    error=str(exc),
                )

            routed = RoutedAttemptResult(
                result=result,
                profile=profile,
                provider=provider,
                model=model,
                reasoning_level=reasoning_level,
                logs_dir=logs_dir,
            )
            last_result = routed

            if result.success:
                return routed

            if not self._should_try_next_route(result):
                return routed

            self._record_failure_state(route, result)

        if last_result:
            return last_result

        return RoutedAttemptResult(
            result=AttemptResult(
                success=False,
                exit_code=-1,
                output="",
                error=f"No available routes for profile '{profile}'.",
            ),
            profile=profile,
        )

    def _should_try_next_route(self, result: AttemptResult) -> bool:
        if getattr(result, "timed_out", False):
            return False
        return bool(
            result.quota_exhausted
            or result.auth_required
            or result.transient_failure
            or result.unavailable
        )

    def _record_failure_state(self, route: Dict[str, Any], result: AttemptResult) -> None:
        provider = route["provider"]
        model = route["model"]
        capability_snapshot = {"models": [model]}

        if result.quota_exhausted:
            self.provider_repo.save(
                provider=provider,
                model=model,
                capability_snapshot=capability_snapshot,
                availability=False,
                quota_state="limited_known_reset" if result.quota_reset else "limited_unknown_reset",
                quota_limit_reset=result.quota_reset,
            )
        elif result.auth_required:
            self.provider_repo.save(
                provider=provider,
                model=model,
                capability_snapshot=capability_snapshot,
                availability=False,
                quota_state="auth_required",
            )
        elif result.transient_failure:
            self.provider_repo.save(
                provider=provider,
                model=model,
                capability_snapshot=capability_snapshot,
                availability=False,
                quota_state="transient_failure",
            )
        elif result.unavailable:
            self.provider_repo.save(
                provider=provider,
                model=model,
                capability_snapshot=capability_snapshot,
                availability=False,
                quota_state="unavailable",
            )
