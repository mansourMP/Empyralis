from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from server_modules import session_service
from server_modules import rust_runtime_kernel_client


DIAGNOSTICS_BUNDLE_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rust_diagnostics_redaction_enabled() -> bool:
    import os

    return os.environ.get("EMPYRALIS_USE_RUST_DIAGNOSTICS_REDACTION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _redact_with_rust_kernel(data: Any) -> Any:
    try:
        from server_modules.rust_runtime_kernel_client import redact_diagnostics

        response = redact_diagnostics(data)
    except Exception:
        return {
            "redaction_error": "rust_runtime_kernel_exception",
            "reason": "rust_diagnostics_redaction_failed_closed",
        }

    if (
        response.get("ok") is True
        and response.get("decision") == "allow"
        and str(response.get("next_action") or "").strip() == "return_redacted_diagnostics"
        and "redacted" in response
    ):
        return response["redacted"]

    return {
        "redaction_error": "rust_runtime_kernel_blocked",
        "reason": (
            response.get("reason")
            if str(response.get("next_action") or "").strip() == "return_redacted_diagnostics"
            else f"unexpected_next_action:{str(response.get('next_action') or 'missing').strip() or 'missing'}"
        ) or "rust_diagnostics_redaction_failed_closed",
    }


def _redact_secrets(data: Any) -> Any:
    if _rust_diagnostics_redaction_enabled():
        return _redact_with_rust_kernel(data)

    sensitive_keys = {
        "api_key", "secret", "token", "password", "credential",
        "authorization", "access_token", "refresh_token", "private_key",
        "api_secret", "client_secret", "secret_ref",
    }
    if isinstance(data, dict):
        return {
            k: "***REDACTED***" if k.lower() in sensitive_keys else _redact_secrets(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact_secrets(item) for item in data]
    if isinstance(data, str):
        lowered = data.lower()
        for term in ("bearer ", "api-key ", "sk-", "sk-ant-", "ghp_"):
            if term in lowered:
                return f"{data[:12]}...REDACTED"
    return data


class SessionDiagnosticsRustGateError(RuntimeError):
    pass


def _enforce_session_diagnostics_file_write(bundle: Dict[str, Any], output_path: Path, payload: str) -> None:
    metadata = {
        "output_path": str(output_path),
        "workspace_id": str(bundle.get("workspace_id") or ""),
        "diagnostics_version": bundle.get("diagnostics_version"),
        "payload_bytes": len(payload.encode("utf-8")),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_session_diagnostics_file",
            state_class="session_diagnostics_files",
            workspace_id=str(metadata["workspace_id"]),
            actor_id="system",
            status="active",
            payload=metadata,
            payload_bytes=int(metadata["payload_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_session_diagnostics_file":
            raise SessionDiagnosticsRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise SessionDiagnosticsRustGateError(exc.reason) from exc


async def export_session_trace(session_id: str) -> Dict[str, Any]:
    token = str(session_id or "").strip()
    if not token:
        return {"error": "session_id_required"}

    session = await session_service.get_session(token)
    if not isinstance(session, dict):
        return {"error": "session_not_found", "session_id": token}

    return {
        "diagnostics_version": DIAGNOSTICS_BUNDLE_VERSION,
        "generated_at": _utc_now_iso(),
        "session": {
            "session_id": str(session.get("session_id") or ""),
            "workspace_id": str(session.get("workspace_id") or ""),
            "tenant_id": str(session.get("tenant_id") or ""),
            "channel": str(session.get("channel") or ""),
            "created_at": str(session.get("created_at") or ""),
            "expires_at": str(session.get("expires_at") or ""),
            "status": str(session.get("status") or "active"),
        },
        "metadata": _redact_secrets(session.get("metadata", {})),
    }


async def export_diagnostics_bundle(workspace_id: str) -> Dict[str, Any]:
    resolved_workspace_id = str(workspace_id or "").strip()
    if not resolved_workspace_id:
        return {"error": "workspace_id_required"}

    sessions: List[Dict[str, Any]] = []
    try:
        raw_sessions = await session_service.list_workspace_runtime_sessions(
            resolved_workspace_id, limit=200, active_only=False
        )
        sessions = [
            {
                "session_id": str(s.get("session_id") or ""),
                "created_at": str(s.get("created_at") or ""),
                "expires_at": str(s.get("expires_at") or ""),
                "status": str(s.get("status") or ""),
                "channel": str(s.get("channel") or ""),
            }
            for s in raw_sessions
            if isinstance(s, dict)
        ]
    except Exception:
        pass

    bundle: Dict[str, Any] = {
        "diagnostics_version": DIAGNOSTICS_BUNDLE_VERSION,
        "generated_at": _utc_now_iso(),
        "workspace_id": resolved_workspace_id,
        "summary": {
            "total_sessions": len(sessions),
            "active_sessions": sum(1 for s in sessions if s.get("status") == "active"),
            "expired_sessions": sum(1 for s in sessions if s.get("status") == "expired"),
            "closed_sessions": sum(1 for s in sessions if s.get("status") == "closed"),
        },
        "sessions": sessions,
    }

    return bundle


def export_diagnostics_to_file(bundle: Dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    payload = json.dumps(bundle, ensure_ascii=False, indent=2, default=str)
    _enforce_session_diagnostics_file_write(bundle, path, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path
