from __future__ import annotations

import os
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib import parse as urlparse
from urllib import request as urlrequest

from ..core import Choice, http_json
from ..errors import CompatibilityError, ValidationFailed, map_runtime_exception, should_retry_error
from .runtime_env import env_bool, env_float, env_int, version_lt, version_tuple
from .runtime_inbox import (
    list_inbox_events as runtime_list_inbox_events,
    list_inbox_sessions as runtime_list_inbox_sessions,
    stream_inbox_events as runtime_stream_inbox_events,
)
from .runtime_providers import (
    list_provider_catalog as runtime_list_provider_catalog,
    list_provider_models as runtime_list_provider_models,
    list_providers_raw as runtime_list_providers_raw,
    list_workspace_credentials as runtime_list_workspace_credentials,
    list_workspace_profiles as runtime_list_workspace_profiles,
)


@dataclass
class RuntimeClient:
    api_url: str
    api_key: str
    cli_version: str = field(default_factory=lambda: str(os.getenv("ORION_CLI_VERSION") or "2026.2.26"))
    http_retries: int = field(default_factory=lambda: max(0, env_int("ORION_CLI_HTTP_RETRIES", 2)))
    http_backoff_seconds: float = field(default_factory=lambda: max(0.05, env_float("ORION_CLI_HTTP_BACKOFF_SECONDS", 0.35)))
    http_backoff_max_seconds: float = field(default_factory=lambda: max(0.2, env_float("ORION_CLI_HTTP_BACKOFF_MAX_SECONDS", 2.0)))
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    enforce_runtime_compat: bool = field(default_factory=lambda: env_bool("ORION_CLI_ENFORCE_RUNTIME_COMPAT", True))

    def _call(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        method_u = str(method or "GET").upper().strip() or "GET"
        idem_key = str(idempotency_key or "").strip()
        mutating = method_u in {"POST", "PUT", "PATCH", "DELETE"}
        if mutating and not idem_key:
            idem_key = f"orion-cli-{self.session_id}-{uuid.uuid4().hex[:16]}"

        headers = {
            "X-Empyralis-CLI-Version": self.cli_version,
            "X-Empyralis-CLI-Session": self.session_id,
            "X-orion-cli-version": self.cli_version,
            "X-orion-cli-session": self.session_id,
        }
        if idem_key:
            headers["X-Idempotency-Key"] = idem_key

        max_attempts = max(1, int(self.http_retries) + 1)
        for attempt in range(max_attempts):
            try:
                return http_json(
                    self.api_url,
                    path,
                    self.api_key,
                    method=method_u,
                    payload=payload,
                    extra_headers=headers,
                )
            except Exception as exc:
                mapped = map_runtime_exception(exc, method=method_u, path=path)
                allow_unsafe_retry = bool(mutating and idem_key)
                can_retry = should_retry_error(mapped, allow_unsafe_retry=allow_unsafe_retry)
                if attempt >= (max_attempts - 1) or not can_retry:
                    raise mapped from exc
                backoff = min(self.http_backoff_max_seconds, self.http_backoff_seconds * (2 ** attempt))
                jitter = random.uniform(0.0, min(0.2, backoff * 0.25))
                time.sleep(backoff + jitter)
        raise RuntimeError("Runtime call failed after retries.")

    def _assert_runtime_compatibility(self, health_payload: Dict[str, Any]) -> None:
        if not self.enforce_runtime_compat:
            return
        min_cli = str(health_payload.get("runtime_api_min_cli_version") or "").strip()
        runtime_api_version = str(health_payload.get("runtime_api_version") or "").strip()
        if min_cli and version_lt(self.cli_version, min_cli):
            raise CompatibilityError(
                (
                    f"CLI version {self.cli_version} is older than runtime minimum {min_cli}. "
                    "Upgrade Empyralis CLI or use a compatible runtime."
                )
            )
        # Runtime may omit this during transition window; only validate if advertised.
        if runtime_api_version:
            expected_major_raw = str(os.getenv("ORION_CLI_RUNTIME_API_MAJOR", "1")).strip()
            if expected_major_raw.isdigit() and env_bool("ORION_CLI_ENFORCE_RUNTIME_API_MAJOR", False):
                runtime_major = version_tuple(runtime_api_version)[0]
                expected_major = int(expected_major_raw)
                if runtime_major != expected_major:
                    raise CompatibilityError(
                        (
                            f"Runtime API major {runtime_major} is incompatible with CLI expected major {expected_major}. "
                            "Set ORION_CLI_ENFORCE_RUNTIME_API_MAJOR=0 to bypass temporarily."
                        )
                    )

    # System
    def get_health(self, enforce_compatibility: bool = True) -> Dict[str, Any]:
        payload = self._call("/health")
        if enforce_compatibility:
            self._assert_runtime_compatibility(payload)
        return payload

    def get_contract(self, enforce_compatibility: bool = True) -> Dict[str, Any]:
        try:
            payload = self._call("/contract")
        except ValidationFailed as exc:
            # Backward compatibility: older runtimes may not expose /contract yet.
            if exc.context.status_code == 404:
                fallback = self.get_health(enforce_compatibility=enforce_compatibility)
                return {"ok": bool(fallback.get("ok")), "contract": fallback, "source": "health_fallback"}
            raise

        contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else payload
        if enforce_compatibility and isinstance(contract, dict):
            self._assert_runtime_compatibility(contract)
        return payload

    def get_workers_status(self) -> Dict[str, Any]:
        return self._call("/runtime/runtimes/status")

    def get_metrics(self) -> Dict[str, Any]:
        return self._call("/metrics")

    def get_doctor(self) -> Dict[str, Any]:
        return self._call("/doctor")

    # Providers / credentials / profiles
    def list_providers_raw(self) -> List[Dict[str, Any]]:
        return runtime_list_providers_raw(self._call)

    def list_provider_catalog(self) -> List[Choice]:
        return runtime_list_provider_catalog(self._call)

    def list_workspace_credentials(
        self,
        workspace_id: str,
        provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return runtime_list_workspace_credentials(self._call, workspace_id, provider)

    def create_credential(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("/credentials/vault", method="POST", payload=payload)

    def list_workspace_profiles(
        self,
        workspace_id: str,
        provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return runtime_list_workspace_profiles(self._call, workspace_id, provider)

    def create_provider_profile(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("/providers/profiles", method="POST", payload=payload)

    def list_provider_models(
        self,
        provider_id: str,
        workspace_id: str,
        credential_id: Optional[str] = None,
    ) -> List[str]:
        return runtime_list_provider_models(self._call, provider_id, workspace_id, credential_id)

    # Setup sessions
    def _setup_session_call(
        self,
        onboarding_path: str,
        setup_path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            return self._call(onboarding_path, method=method, payload=payload)
        except ValidationFailed as exc:
            if exc.context.status_code in {404, 405, 410}:
                return self._call(setup_path, method=method, payload=payload)
            raise

    def create_setup_session(
        self,
        workspace_id: str,
        provider: Optional[str] = None,
        flow: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"workspace_id": workspace_id}
        if provider:
            payload["provider"] = provider
        if flow:
            payload["flow"] = flow
        data = self._setup_session_call(
            "/onboarding/sessions",
            "/setup/sessions",
            method="POST",
            payload=payload,
        )
        session = data.get("session")
        if isinstance(session, dict):
            return session
        raise RuntimeError("Setup session was not created.")

    def get_setup_session(self, session_id: str) -> Dict[str, Any]:
        data = self._setup_session_call(
            f"/onboarding/sessions/{session_id}",
            f"/setup/sessions/{session_id}",
            method="GET",
            payload=None,
        )
        session = data.get("session")
        if isinstance(session, dict):
            return session
        raise RuntimeError("Setup session was not found.")

    def setup_action(
        self,
        session_id: str,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"action": action, "payload": payload or {}}
        data = self._setup_session_call(
            f"/onboarding/sessions/{session_id}/actions",
            f"/setup/sessions/{session_id}/actions",
            method="POST",
            payload=body,
        )
        session = data.get("session")
        if isinstance(session, dict):
            return session
        raise RuntimeError(f"Setup action '{action}' returned no session payload.")

    def complete_setup_session(self, session_id: str) -> Dict[str, Any]:
        return self.setup_action(session_id, "complete", {})

    def cancel_setup_session(self, session_id: str) -> Dict[str, Any]:
        data = self._setup_session_call(
            f"/onboarding/sessions/{session_id}/cancel",
            f"/setup/sessions/{session_id}/cancel",
            method="POST",
            payload={},
        )
        session = data.get("session")
        if isinstance(session, dict):
            return session
        raise RuntimeError("Setup session cancel returned no session payload.")

    def resume_setup_session(self, session_id: str) -> Dict[str, Any]:
        data = self._setup_session_call(
            f"/onboarding/sessions/{session_id}/resume",
            f"/setup/sessions/{session_id}/resume",
            method="POST",
            payload={},
        )
        session = data.get("session")
        if isinstance(session, dict):
            return session
        raise RuntimeError("Setup session resume returned no session payload.")

    # Connectors
    def list_connectors(self) -> List[Dict[str, Any]]:
        payload = self._call("/connectors")
        items = payload.get("connectors")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def list_connectors_vault(self, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        path = "/connectors/vault"
        if workspace_id:
            path += f"?workspace_id={urlparse.quote(workspace_id)}"
        payload = self._call(path)
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def test_connector_vault(self, credential_id: str, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        path = f"/connectors/vault/{urlparse.quote(credential_id)}/test"
        if workspace_id:
            path += f"?workspace_id={urlparse.quote(workspace_id)}"
        return self._call(path, method="POST", payload={})
    def create_connector(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("/connectors/vault", method="POST", payload=payload)

    def send_telegram_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("/channels/telegram/send", method="POST", payload=payload)

    # Runs
    def precheck_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("/runs/precheck", method="POST", payload=payload)

    def start_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("/turn", method="POST", payload=payload)

    def get_run(self, run_id: str) -> Dict[str, Any]:
        return self._call(f"/runs/{urlparse.quote(run_id)}")

    def post_run_decision(self, run_id: str, decision: str) -> None:
        self._call(f"/runs/{urlparse.quote(run_id)}/decision", method="POST", payload={"decision": decision})

    def resolve_approval(
        self,
        run_id: str,
        approval_id: str,
        decision: str,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {"decision": decision, "note": note}
        return self._call(
            f"/runs/{urlparse.quote(run_id)}/approvals/{urlparse.quote(approval_id)}/resolve",
            method="POST",
            payload=payload,
        )

    def list_cognitive_approvals(self, limit: int = 20) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        payload = self._call(f"/cognitive/approvals?limit={safe_limit}")
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def resolve_cognitive_approval(
        self,
        event_id: str,
        decision: str,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {"decision": decision, "note": note}
        return self._call(
            f"/cognitive/approvals/{urlparse.quote(event_id)}/resolve",
            method="POST",
            payload=payload,
        )

    def replay_run(self, run_id: str) -> Dict[str, Any]:
        return self._call(f"/runs/{urlparse.quote(run_id)}/replay", method="POST", payload={})

    def list_history_runs(
        self,
        workspace_id: str,
        limit: int = 20,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        path = f"/history/runs?workspace_id={urlparse.quote(workspace_id)}&limit={safe_limit}"
        if status:
            path += f"&status={urlparse.quote(status)}"
        payload = self._call(path)
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def get_local_queue(self, workspace_id: str, limit: int = 20) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit), 200))
        return self._call(
            f"/runs/queue/local?workspace_id={urlparse.quote(workspace_id)}&limit={safe_limit}"
        )

    def list_inbox_events(
        self,
        workspace_id: Optional[str] = None,
        limit: int = 80,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        direction: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
        group_errors: bool = False,
        error_window_seconds: Optional[int] = None,
        include_raw_errors: bool = True,
    ) -> List[Dict[str, Any]]:
        return runtime_list_inbox_events(
            self._call,
            workspace_id=workspace_id,
            limit=limit,
            channel=channel,
            session_key=session_key,
            direction=direction,
            action=action,
            run_id=run_id,
            group_errors=group_errors,
            error_window_seconds=error_window_seconds,
            include_raw_errors=include_raw_errors,
        )

    def stream_inbox_events(
        self,
        workspace_id: Optional[str] = None,
        limit: int = 120,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        direction: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
        since_id: Optional[str] = None,
        since_ts: Optional[str] = None,
        include_backlog: bool = False,
        timeout_seconds: float = 0.35,
        poll_seconds: float = 0.1,
        heartbeat_seconds: float = 5.0,
        max_events: int = 120,
    ) -> List[Dict[str, Any]]:
        return runtime_stream_inbox_events(
            api_url=self.api_url,
            api_key=self.api_key,
            cli_version=self.cli_version,
            session_id=self.session_id,
            http_retries=self.http_retries,
            http_backoff_seconds=self.http_backoff_seconds,
            http_backoff_max_seconds=self.http_backoff_max_seconds,
            workspace_id=workspace_id,
            limit=limit,
            channel=channel,
            session_key=session_key,
            direction=direction,
            action=action,
            run_id=run_id,
            since_id=since_id,
            since_ts=since_ts,
            include_backlog=include_backlog,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            heartbeat_seconds=heartbeat_seconds,
            max_events=max_events,
            request_cls=urlrequest.Request,
            urlopen_fn=urlrequest.urlopen,
        )

    def list_inbox_sessions(
        self,
        workspace_id: Optional[str] = None,
        limit: int = 80,
        channel: Optional[str] = None,
        session_key: Optional[str] = None,
        direction: Optional[str] = None,
        action: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return runtime_list_inbox_sessions(
            self._call,
            workspace_id=workspace_id,
            limit=limit,
            channel=channel,
            session_key=session_key,
            direction=direction,
            action=action,
            run_id=run_id,
        )
