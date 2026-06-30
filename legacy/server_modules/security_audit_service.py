from __future__ import annotations

import logging
from typing import Any, Dict, Optional
import uuid

from server_modules import outbox_service
from server_modules import secret_redaction_service


LOGGER = logging.getLogger(__name__)

def sanitize_security_audit_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return secret_redaction_service.sanitize_mapping(metadata)


def emit_security_audit_event(
    *,
    action: str,
    status: str = "success",
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    current_user: Optional[Dict[str, Any]] = None,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    actor_auth_type: Optional[str] = None,
    channel: Optional[str] = None,
    run_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    trace_id: str = "",
    detail: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> Optional[outbox_service.OutboxEvent]:
    action_token = str(action or "").strip()
    if not action_token:
        return None
    current_user = current_user if isinstance(current_user, dict) else {}
    resolved_tenant_id = str(
        tenant_id
        or current_user.get("tenant_id")
        or current_user.get("current_tenant_id")
        or "default"
    ).strip() or "default"
    resolved_workspace_id = str(
        workspace_id
        or current_user.get("workspace_id")
        or current_user.get("current_workspace_id")
        or "default"
    ).strip() or "default"
    payload = {
        "action": action_token,
        "status": str(status or "").strip() or "success",
        "actor_user_id": str(actor_user_id or current_user.get("user_id") or "").strip() or None,
        "actor_email": secret_redaction_service.redact_text(str(actor_email or current_user.get("email") or "").strip().lower()) or None,
        "actor_auth_type": str(actor_auth_type or current_user.get("auth_type") or "").strip().lower() or None,
        "channel": str(channel or "").strip().lower() or None,
        "detail": secret_redaction_service.redact_text(str(detail or "").strip()) or None,
        "metadata": sanitize_security_audit_metadata(metadata),
        "emitted_at": outbox_service._utc_now_iso(),
    }
    secret_redaction_service.assert_secrets_free(payload, context="security_audit_event")
    try:
        return outbox_service.emit_runtime_event(
            event_type="security_audit",
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            run_id=str(run_id or "").strip() or None,
            machine_id=str(machine_id or "").strip() or None,
            trace_id=str(trace_id or "").strip(),
            idempotency_key=str(idempotency_key or "").strip() or f"security_audit:{action_token}:{uuid.uuid4().hex}",
            payload=payload,
        )
    except Exception as exc:
        LOGGER.warning("Failed to emit security audit event %s: %s", action_token, exc)
        return None
