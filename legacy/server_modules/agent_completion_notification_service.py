from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from server_modules import control_plane_repository
from server_modules import gateway_state_repository
from server_modules import outbox_service


TRAY_NOTIFY_URL = "http://127.0.0.1:7790/notify"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, fallback: str = "") -> str:
    token = str(value or "").strip()
    return token or fallback


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _summary(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("summary", "result", "reply", "answer", "message", "output"):
            token = _text(value.get(key))
            if token:
                return token
    return _text(value)


def _notification_channel_id(metadata: Dict[str, Any]) -> str:
    settings = _coerce_dict(metadata.get("settings"))
    return _text(settings.get("notification_channel_id") or metadata.get("notification_channel_id"))


def _parse_notification_channel_id(channel_id: str) -> Dict[str, str]:
    token = _text(channel_id)
    if not token:
        return {}
    parts = [part.strip() for part in token.split(":")]
    if len(parts) < 2:
        return {"channel": parts[0], "connector_id": "", "recipient": ""}
    return {
        "channel": parts[0],
        "connector_id": parts[1],
        "recipient": ":".join(parts[2:]).strip() if len(parts) > 2 else "",
    }


def _active_gateway_connected(workspace_id: str) -> bool:
    try:
        registrations = gateway_state_repository.list_workspace_gateway_registrations(
            workspace_id,
            include_revoked=False,
        )
    except Exception:
        return False
    for item in registrations or []:
        if _text((item or {}).get("status")).lower() == "active":
            return True
    return False


def _post_tray_notification(*, title: str, body: str) -> None:
    payload = json.dumps({"title": title, "body": body}).encode("utf-8")
    request = urllib.request.Request(
        TRAY_NOTIFY_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=1.5).read()
    except Exception:
        return


async def notify_completion(
    *,
    workspace_id: str,
    agent_name: str,
    result_summary: Any,
    artifact_url: Optional[str] = None,
) -> None:
    workspace_token = _text(workspace_id)
    if not workspace_token:
        return
    summary = _summary(result_summary)[:100] or "Completed."
    name = _text(agent_name, "Agent")
    artifact = _text(artifact_url)
    message = f"✓ {name} finished\n{summary}"
    if artifact:
        message = f"{message}\n{artifact}"

    workspace = await control_plane_repository.get_workspace_by_id(workspace_token)
    metadata = _coerce_dict((workspace or {}).get("metadata"))
    channel_id = _notification_channel_id(metadata)
    tenant_id = _text((workspace or {}).get("tenant_id"), "default")

    if channel_id:
        idempotency_material = f"{workspace_token}:{channel_id}:{name}:{summary}:{artifact}"
        idempotency_hash = hashlib.sha256(idempotency_material.encode("utf-8")).hexdigest()[:24]
        channel_config = _parse_notification_channel_id(channel_id)
        try:
            if channel_config.get("channel") and channel_config.get("connector_id"):
                outbox_service.emit_channel_run_delivery_event(
                    channel=channel_config["channel"],
                    tenant_id=tenant_id,
                    workspace_id=workspace_token,
                    run_id=f"completion-notification-{idempotency_hash}",
                    connector_id=channel_config["connector_id"],
                    trace_id="",
                    idempotency_key=f"agent_completion_notification:{idempotency_hash}",
                    payload={
                        "action": "agent_completion_notification",
                        "notification_channel_id": channel_id,
                        "recipient": channel_config.get("recipient") or "",
                        "notification_text": message,
                        "agent_name": name,
                        "result_summary": summary,
                        "artifact_url": artifact or None,
                        "tenant_id": tenant_id,
                        "emitted_at": _utc_now_iso(),
                    },
                )
            else:
                outbox_service.emit_runtime_event(
                    event_type="agent_completion_notification",
                    tenant_id=tenant_id,
                    workspace_id=workspace_token,
                    trace_id="",
                    idempotency_key=f"agent_completion_notification:{idempotency_hash}",
                    payload={
                        "action": "agent_completion_notification",
                        "notification_channel_id": channel_id,
                        "agent_name": name,
                        "result_summary": summary,
                        "artifact_url": artifact or None,
                        "text": message,
                        "emitted_at": _utc_now_iso(),
                    },
                )
        except Exception:
            pass

    if _active_gateway_connected(workspace_token):
        _post_tray_notification(title=f"{name} finished", body=summary)
