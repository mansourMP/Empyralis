from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server_modules.auth import enforce_workspace_access
from server_modules.runtime_common import require_api_key
from server_modules.kill_switch_gate import KillSwitchBlockedError
from server_modules.safety_error_contract import kill_switch_error, to_http_body, to_http_status
from server_modules import (
    approval_contracts,
    channel_lane_contract_service,
    gateway_approval_service,
    rust_runtime_kernel_client,
    gateway_state_repository,
    personal_channels_service,
    security_audit_service,
)


router = APIRouter()


class PersonalOutboundRequest(BaseModel):
    remote_jid: str = Field(min_length=1)
    text: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    reply_to_external_message_id: Optional[str] = None


class WhatsAppPersonalSetupRequest(BaseModel):
    phone_number: Optional[str] = None
    custom_pairing_code: Optional[str] = None


class TelegramPersonalSetupRequest(BaseModel):
    api_id: Optional[int] = Field(default=None, ge=1)
    api_hash: Optional[str] = None
    phone_number: Optional[str] = None
    login_code: Optional[str] = None
    password: Optional[str] = None


LOCAL_BRIDGE_CHANNELS: Dict[str, Dict[str, str]] = {
    "signal_personal": {"provider": "signal_local_bridge", "label": "Signal"},
    "imessage_personal": {"provider": "bluebubbles_local_bridge", "label": "iMessage"},
    "wechat_personal": {"provider": "wechat_local_bridge", "label": "WeChat"},
}


def _personal_channel_governance_metadata(action: str, channel_key: str) -> dict:
    normalized_action = str(action or "").strip().lower()
    if normalized_action.endswith(".configure"):
        return {
            "action_class": "credential_change",
            "risk_level": "high",
            "governance_boundary": "paired_gateway",
            "requires_approval": False,
            "external_side_effect": False,
        }
    if normalized_action.endswith(".send"):
        return {
            "action_class": "channel_send",
            "risk_level": "critical",
            "governance_boundary": "paired_gateway",
            "requires_approval": True,
            "external_side_effect": True,
        }
    return {
        "action_class": f"{channel_key}_action",
        "risk_level": "moderate",
        "governance_boundary": "paired_gateway",
        "requires_approval": False,
        "external_side_effect": False,
    }


def _channel_approval_instruction(approval: dict, channel_key: str) -> dict:
    normalized = (approval or {}).get("normalized_approval")
    if not isinstance(normalized, dict):
        normalized = approval_contracts.normalize_gateway_approval(approval, channel=channel_key)
    else:
        normalized = {
            **normalized,
            "channel": str(channel_key or "").strip() or normalized.get("channel") or "channel",
        }
    return approval_contracts.channel_approval_instruction(normalized, channel_key=channel_key)



def _coerce_current_user_id(current_user) -> str:
    if not isinstance(current_user, dict):
        return ""
    return str(
        current_user.get("user_id")
        or current_user.get("sub")
        or current_user.get("id")
        or ""
    ).strip()


def _coerce_current_user_role(current_user) -> str:
    if not isinstance(current_user, dict):
        return ""
    return str(current_user.get("role") or "").strip().lower()


def _registration_owner_user_id(registration: dict) -> str:
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    return str(
        registration.get("user_id")
        or registration.get("owner_user_id")
        or registration.get("paired_user_id")
        or metadata.get("user_id")
        or metadata.get("owner_user_id")
        or metadata.get("paired_user_id")
        or metadata.get("auth_user_id")
        or ""
    ).strip()


def _require_accessible_gateway_registration(
    gateway_id: str,
    current_user,
    *,
    minimum_role: str,
) -> dict:
    registration = gateway_state_repository.get_gateway_registration(gateway_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Gateway registration was not found.")
    registration_workspace_id = str(registration.get("workspace_id") or "").strip() or "default"
    resolved_workspace_id = enforce_workspace_access(
        current_user,
        registration_workspace_id,
        minimum_role=minimum_role,
    )
    if resolved_workspace_id != registration_workspace_id:
        raise HTTPException(status_code=403, detail="Workspace is not accessible for this user.")
    owner_user_id = _registration_owner_user_id(registration)
    current_user_id = _coerce_current_user_id(current_user)
    current_role = _coerce_current_user_role(current_user)
    if owner_user_id:
        if not current_user_id or current_user_id != owner_user_id:
            raise HTTPException(status_code=403, detail="This personal gateway belongs to another user.")
    elif current_role not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="Gateway ownership is missing. Ask an owner to reconnect this Agent Computer.",
        )
    return registration


def _emit_personal_channel_audit(
    *,
    action: str,
    status: str,
    registration: dict,
    current_user,
    gateway_id: str,
    channel_key: str,
    detail: str,
    metadata: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    security_audit_service.emit_security_audit_event(
        action=action,
        status=status,
        tenant_id=str(registration.get("tenant_id") or "").strip() or None,
        workspace_id=str(registration.get("workspace_id") or "").strip() or None,
        current_user=current_user if isinstance(current_user, dict) else None,
        channel=channel_key,
        machine_id=str(gateway_id or "").strip() or None,
        detail=detail,
        metadata={
            "gateway_id": str(gateway_id or "").strip(),
            "channel_key": channel_key,
            **_personal_channel_governance_metadata(action, channel_key),
            **dict(metadata or {}),
        },
        idempotency_key=idempotency_key,
    )


def _enforce_personal_channel_approval_request(
    *,
    registration: dict,
    current_user,
    capability_id: str,
    run_id: str,
    trace_id: str,
    request_id: str,
) -> dict:
    metadata = dict(registration.get("metadata") or {})
    session_id = str(
        registration.get("active_session_id")
        or metadata.get("gateway_session_id")
        or metadata.get("session_id")
        or ""
    ).strip()
    payload = {
        "operation": "approval_request",
        "tenant_id": str(registration.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(registration.get("workspace_id") or "default").strip() or "default",
        "actor_id": str((current_user or {}).get("user_id") or "").strip() or "user",
        "actor_role": str((current_user or {}).get("role") or "").strip() or "member",
        "gateway_id": str(registration.get("gateway_id") or "").strip(),
        "session_id": session_id,
        "request_id": str(request_id or "").strip() or None,
        "capability_id": str(capability_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "critical",
        "policy_decision": "allow",
        "device_trust_state": str(registration.get("device_trust_state") or "trusted").strip() or "trusted",
        "approval_provided": False,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": bool(session_id),
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
    }
    decision = rust_runtime_kernel_client.gateway_service_decision(**payload)
    decision_class = str(decision.get("decision") or "").strip()
    next_action = str(decision.get("next_action") or "").strip()
    if decision_class == "block":
        detail = str(decision.get("reason") or "rust_personal_channel_request_denied").strip()
        raise HTTPException(
            status_code=409,
            detail=f"Rust gateway-service gate blocked request: {detail}",
        )
    return decision


async def _request_personal_channel_send_approval(
    *,
    action: str,
    registration: dict,
    current_user,
    gateway_id: str,
    channel_key: str,
    capability_id: str,
    remote_jid: str,
    text: str,
    idempotency_key: str,
    reply_to_external_message_id: Optional[str],
) -> Optional[JSONResponse]:
    governance_metadata = _personal_channel_governance_metadata(action, channel_key)
    if not bool(governance_metadata.get("requires_approval")):
        return None
    run_id = f"personal-channel-send-{channel_key}-{idempotency_key}"
    trace_id = f"personal-channel-send-{channel_key}-{idempotency_key}"
    _enforce_personal_channel_approval_request(
        registration=registration,
        current_user=current_user,
        capability_id=capability_id,
        run_id=run_id,
        trace_id=trace_id,
        request_id=idempotency_key,
    )
    approval = await gateway_approval_service.request_gateway_tool_approval(
        registration=registration,
        capability_id=capability_id,
        arguments={
            "channel_key": channel_key,
            "remote_jid": remote_jid,
            "text": text,
            "idempotency_key": idempotency_key,
            "reply_to_external_message_id": reply_to_external_message_id,
            "source": "manual_api",
        },
        run_id=run_id,
        trace_id=trace_id,
        request_id=idempotency_key,
    )
    _emit_personal_channel_audit(
        action=action,
        status="approval_required",
        registration=registration,
        current_user=current_user,
        gateway_id=gateway_id,
        channel_key=channel_key,
        detail="Owner approval is required before dispatching a manual personal-channel message.",
        metadata={
            "remote_jid": remote_jid,
            "text_length": len(text),
            "has_reply_target": bool(reply_to_external_message_id),
            "approval_id": approval.get("approval_id"),
        },
        idempotency_key=f"{action}.approval_required:{gateway_id}:{idempotency_key}",
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "approval_required",
            "gateway_id": gateway_id,
            "channel_key": channel_key,
            "approval": approval,
            "normalized_approval": approval.get("normalized_approval"),
            "channel_approval": _channel_approval_instruction(approval, channel_key),
        },
    )


@router.get("/personal-channels/gateways/{gateway_id}/channels")
async def get_personal_gateway_channel_surfaces(
    request: Request,
    gateway_id: str,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="viewer",
    )
    try:
        return personal_channels_service.get_gateway_personal_channel_surfaces(gateway_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/personal-channels/whatsapp/gateways/{gateway_id}")
async def get_whatsapp_personal_gateway_status(
    request: Request,
    gateway_id: str,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="viewer",
    )
    return personal_channels_service.get_whatsapp_gateway_view(gateway_id)


@router.post("/personal-channels/whatsapp/gateways/{gateway_id}/setup")
async def configure_whatsapp_personal_gateway(
    request: Request,
    gateway_id: str,
    body: WhatsAppPersonalSetupRequest,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    try:
        result = await personal_channels_service.configure_whatsapp_personal_gateway(
            gateway_id=gateway_id,
            registration=registration,
            phone_number=body.phone_number,
            custom_pairing_code=body.custom_pairing_code,
        )
        _emit_personal_channel_audit(
            action="personal_channel.whatsapp.configure",
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="whatsapp_personal",
            detail="WhatsApp personal channel setup was requested for a paired gateway.",
            metadata={
                "has_phone_number": bool(str(body.phone_number or "").strip()),
                "has_custom_pairing_code": bool(str(body.custom_pairing_code or "").strip()),
            },
        )
        return result
    except KillSwitchBlockedError as exc:
        _emit_personal_channel_audit(
            action="personal_channel.whatsapp.configure",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="whatsapp_personal",
            detail=exc.decision.detail,
            metadata={
                "has_phone_number": bool(str(body.phone_number or "").strip()),
                "has_custom_pairing_code": bool(str(body.custom_pairing_code or "").strip()),
                "kill_switch_scope": exc.decision.scope,
            },
        )
        error = kill_switch_error(
            scope=exc.decision.scope,
            detail=exc.decision.detail,
            trace_id=exc.decision.trace_id,
        )
        raise HTTPException(status_code=to_http_status(error), detail=to_http_body(error)) from exc
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.whatsapp.configure",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="whatsapp_personal",
            detail=detail,
            metadata={
                "has_phone_number": bool(str(body.phone_number or "").strip()),
                "has_custom_pairing_code": bool(str(body.custom_pairing_code or "").strip()),
            },
        )
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/personal-channels/whatsapp/gateways/{gateway_id}/disconnect")
async def disconnect_whatsapp_personal_gateway(
    request: Request,
    gateway_id: str,
    current_user=Depends(require_api_key),
):
    """Full reset — see disconnect_telegram_personal_gateway()'s doc comment
    and WhatsAppPersonalRuntime.handleDisconnect() for why this exists."""
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    try:
        result = await personal_channels_service.disconnect_whatsapp_personal_gateway(
            gateway_id=gateway_id,
            registration=registration,
        )
        _emit_personal_channel_audit(
            action="personal_channel.whatsapp.disconnect",
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="whatsapp_personal",
            detail="WhatsApp personal channel was disconnected and reset for a paired gateway.",
            metadata={},
        )
        return result
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.whatsapp.disconnect",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="whatsapp_personal",
            detail=detail,
            metadata={},
        )
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/personal-channels/whatsapp/gateways/{gateway_id}/messages")
async def send_whatsapp_personal_message(
    request: Request,
    gateway_id: str,
    body: PersonalOutboundRequest,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    approval_response = await _request_personal_channel_send_approval(
        action="personal_channel.whatsapp.send",
        registration=registration,
        current_user=current_user,
        gateway_id=gateway_id,
        channel_key="whatsapp_personal",
        capability_id="channel.whatsapp.personal.send",
        remote_jid=body.remote_jid,
        text=body.text,
        idempotency_key=body.idempotency_key,
        reply_to_external_message_id=body.reply_to_external_message_id,
    )
    if approval_response is not None:
        return approval_response
    try:
        result = await personal_channels_service.send_whatsapp_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            remote_jid=body.remote_jid,
            text=body.text,
            idempotency_key=body.idempotency_key,
            reply_to_external_message_id=body.reply_to_external_message_id,
        )
        _emit_personal_channel_audit(
            action="personal_channel.whatsapp.send",
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="whatsapp_personal",
            detail="A WhatsApp personal test or manual message was dispatched through the paired gateway.",
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
            },
            idempotency_key=f"personal_channel.whatsapp.send:{gateway_id}:{body.idempotency_key}",
        )
        return result
    except KillSwitchBlockedError as exc:
        _emit_personal_channel_audit(
            action="personal_channel.whatsapp.send",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="whatsapp_personal",
            detail=exc.decision.detail,
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
                "kill_switch_scope": exc.decision.scope,
            },
            idempotency_key=f"personal_channel.whatsapp.send.denied:{gateway_id}:{body.idempotency_key}",
        )
        error = kill_switch_error(
            scope=exc.decision.scope,
            detail=exc.decision.detail,
            trace_id=exc.decision.trace_id,
        )
        raise HTTPException(status_code=to_http_status(error), detail=to_http_body(error)) from exc
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.whatsapp.send",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="whatsapp_personal",
            detail=detail,
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
            },
            idempotency_key=f"personal_channel.whatsapp.send.denied:{gateway_id}:{body.idempotency_key}",
        )
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/personal-channels/telegram/gateways/{gateway_id}")
async def get_telegram_personal_gateway_status(
    request: Request,
    gateway_id: str,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="viewer",
    )
    return personal_channels_service.get_telegram_gateway_view(gateway_id)


@router.post("/personal-channels/telegram/gateways/{gateway_id}/setup")
async def configure_telegram_personal_gateway(
    request: Request,
    gateway_id: str,
    body: TelegramPersonalSetupRequest,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    try:
        result = await personal_channels_service.configure_telegram_personal_gateway(
            gateway_id=gateway_id,
            registration=registration,
            api_id=body.api_id,
            api_hash=body.api_hash,
            phone_number=body.phone_number,
            login_code=body.login_code,
            password=body.password,
        )
        _emit_personal_channel_audit(
            action="personal_channel.telegram.configure",
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="telegram_personal",
            detail="Telegram personal channel setup was requested for a paired gateway.",
            metadata={
                "has_api_id": body.api_id is not None,
                "has_api_hash": bool(str(body.api_hash or "").strip()),
                "has_phone_number": bool(str(body.phone_number or "").strip()),
                "has_login_code": bool(str(body.login_code or "").strip()),
                "has_password": bool(str(body.password or "").strip()),
            },
        )
        return result
    except KillSwitchBlockedError as exc:
        _emit_personal_channel_audit(
            action="personal_channel.telegram.configure",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="telegram_personal",
            detail=exc.decision.detail,
            metadata={
                "has_api_id": body.api_id is not None,
                "has_api_hash": bool(str(body.api_hash or "").strip()),
                "has_phone_number": bool(str(body.phone_number or "").strip()),
                "has_login_code": bool(str(body.login_code or "").strip()),
                "has_password": bool(str(body.password or "").strip()),
                "kill_switch_scope": exc.decision.scope,
            },
        )
        error = kill_switch_error(
            scope=exc.decision.scope,
            detail=exc.decision.detail,
            trace_id=exc.decision.trace_id,
        )
        raise HTTPException(status_code=to_http_status(error), detail=to_http_body(error)) from exc
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.telegram.configure",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="telegram_personal",
            detail=detail,
            metadata={
                "has_api_id": body.api_id is not None,
                "has_api_hash": bool(str(body.api_hash or "").strip()),
                "has_phone_number": bool(str(body.phone_number or "").strip()),
                "has_login_code": bool(str(body.login_code or "").strip()),
                "has_password": bool(str(body.password or "").strip()),
            },
        )
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/personal-channels/telegram/gateways/{gateway_id}/disconnect")
async def disconnect_telegram_personal_gateway(
    request: Request,
    gateway_id: str,
    current_user=Depends(require_api_key),
):
    """Full reset: tears down any live/stuck Telegram session and clears
    every persisted trace of the previous attempt (session, pending login,
    api_id/api_hash/phone_number) so the next setup call starts genuinely
    fresh. Exists because a bad first attempt can otherwise jam a phone
    number in an unrecoverable retry loop with no way out short of an
    operator hand-editing Gateway state files."""
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    try:
        result = await personal_channels_service.disconnect_telegram_personal_gateway(
            gateway_id=gateway_id,
            registration=registration,
        )
        _emit_personal_channel_audit(
            action="personal_channel.telegram.disconnect",
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="telegram_personal",
            detail="Telegram personal channel was disconnected and reset for a paired gateway.",
            metadata={},
        )
        return result
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.telegram.disconnect",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="telegram_personal",
            detail=detail,
            metadata={},
        )
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/personal-channels/telegram/gateways/{gateway_id}/messages")
async def send_telegram_personal_message(
    request: Request,
    gateway_id: str,
    body: PersonalOutboundRequest,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    approval_response = await _request_personal_channel_send_approval(
        action="personal_channel.telegram.send",
        registration=registration,
        current_user=current_user,
        gateway_id=gateway_id,
        channel_key="telegram_personal",
        capability_id="channel.telegram.personal.send",
        remote_jid=body.remote_jid,
        text=body.text,
        idempotency_key=body.idempotency_key,
        reply_to_external_message_id=body.reply_to_external_message_id,
    )
    if approval_response is not None:
        return approval_response
    try:
        result = await personal_channels_service.send_telegram_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            remote_jid=body.remote_jid,
            text=body.text,
            idempotency_key=body.idempotency_key,
            reply_to_external_message_id=body.reply_to_external_message_id,
        )
        _emit_personal_channel_audit(
            action="personal_channel.telegram.send",
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="telegram_personal",
            detail="A Telegram personal test or manual message was dispatched through the paired gateway.",
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
            },
            idempotency_key=f"personal_channel.telegram.send:{gateway_id}:{body.idempotency_key}",
        )
        return result
    except KillSwitchBlockedError as exc:
        _emit_personal_channel_audit(
            action="personal_channel.telegram.send",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="telegram_personal",
            detail=exc.decision.detail,
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
                "kill_switch_scope": exc.decision.scope,
            },
            idempotency_key=f"personal_channel.telegram.send.denied:{gateway_id}:{body.idempotency_key}",
        )
        error = kill_switch_error(
            scope=exc.decision.scope,
            detail=exc.decision.detail,
            trace_id=exc.decision.trace_id,
        )
        raise HTTPException(status_code=to_http_status(error), detail=to_http_body(error)) from exc
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action="personal_channel.telegram.send",
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key="telegram_personal",
            detail=detail,
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
            },
            idempotency_key=f"personal_channel.telegram.send.denied:{gateway_id}:{body.idempotency_key}",
        )
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/personal-channels/{channel_key}/gateways/{gateway_id}/messages")
async def send_local_bridge_personal_message(
    request: Request,
    channel_key: str,
    gateway_id: str,
    body: PersonalOutboundRequest,
    current_user=Depends(require_api_key),
):
    channel_lane_contract_service.assert_personal_route_path(str(request.url.path))
    normalized_channel_key = str(channel_key or "").strip().lower()
    spec = LOCAL_BRIDGE_CHANNELS.get(normalized_channel_key)
    if not spec:
        raise HTTPException(status_code=404, detail="Personal local-bridge channel was not found.")
    registration = _require_accessible_gateway_registration(
        gateway_id,
        current_user,
        minimum_role="member",
    )
    action_name = f"personal_channel.{normalized_channel_key.split('_', 1)[0]}.send"
    approval_response = await _request_personal_channel_send_approval(
        action=action_name,
        registration=registration,
        current_user=current_user,
        gateway_id=gateway_id,
        channel_key=normalized_channel_key,
        capability_id=f"{normalized_channel_key}.send",
        remote_jid=body.remote_jid,
        text=body.text,
        idempotency_key=body.idempotency_key,
        reply_to_external_message_id=body.reply_to_external_message_id,
    )
    if approval_response is not None:
        return approval_response
    try:
        result = await personal_channels_service.send_local_bridge_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            channel_key=normalized_channel_key,
            provider=spec["provider"],
            remote_jid=body.remote_jid,
            text=body.text,
            idempotency_key=body.idempotency_key,
            reply_to_external_message_id=body.reply_to_external_message_id,
        )
        _emit_personal_channel_audit(
            action=action_name,
            status="success",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=f"A {spec['label']} personal test or manual message was dispatched through the paired gateway bridge.",
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
            },
            idempotency_key=f"{action_name}:{gateway_id}:{body.idempotency_key}",
        )
        return result
    except ValueError as exc:
        detail = str(exc)
        _emit_personal_channel_audit(
            action=action_name,
            status="denied",
            registration=registration,
            current_user=current_user,
            gateway_id=gateway_id,
            channel_key=normalized_channel_key,
            detail=detail,
            metadata={
                "remote_jid": body.remote_jid,
                "text_length": len(body.text),
                "has_reply_target": bool(body.reply_to_external_message_id),
            },
            idempotency_key=f"{action_name}.denied:{gateway_id}:{body.idempotency_key}",
        )
        status_code = 409 if "not currently connected" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


# ── Stage 2: Cloud Session Manager inbound ──────────────────────

import hashlib
import hmac
import os

_CLOUD_HMAC_SECRET = os.getenv("CLOUD_SESSION_HMAC_SECRET", os.getenv("API_SECRET", "dev-secret-change-me"))


class CloudChannelInboundRequest(BaseModel):
    session_id: str = Field(min_length=1)
    workspace_id: str = Field(default="default")
    channel_key: str = Field(default="telegram_personal")
    message: dict
    signature: str = Field(min_length=1)
    signed_fields: str = Field(default="sessionId,messageId,timestamp")
    signed_payload: dict = Field(default_factory=dict)


def _verify_cloud_hmac(signature: str, payload: dict) -> bool:
    """Verify HMAC-SHA256 signature from cloud session manager."""
    if not signature or not payload:
        return False
    expected = hmac.new(
        _CLOUD_HMAC_SECRET.encode("utf-8"),
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


import json


@router.post("/personal-channels/cloud/inbound")
async def cloud_channel_inbound(
    request: Request,
    body: CloudChannelInboundRequest,
):
    """Receive inbound messages from the cloud session manager (Stage 2).

    The cloud session manager forwards Telegram/WhatsApp messages here.
    HMAC-signed for authentication (system-to-system, no user API key).
    """
    # Verify HMAC signature
    signed_payload = body.signed_payload or {}
    if not signed_payload:
        raise HTTPException(status_code=401, detail="Missing signed_payload for HMAC verification")
    if not _verify_cloud_hmac(body.signature, signed_payload):
        raise HTTPException(status_code=401, detail="HMAC signature verification failed")

    # Verify session_id matches
    if signed_payload.get("sessionId") != body.session_id:
        raise HTTPException(status_code=400, detail="session_id mismatch in signed payload")

    result = await personal_channels_service.handle_cloud_channel_inbound(
        session_id=body.session_id,
        channel_key=body.channel_key,
        message=body.message,
        workspace_id=body.workspace_id,
    )
    return JSONResponse(content=result)
