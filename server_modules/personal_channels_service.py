from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from server_modules import (
    channel_blocking_policy_service,
    channel_lane_contract_service,
    gateway_state_repository,
    kill_switch_gate,
    personal_channel_sage_bridge_service,
    personal_channels_repository,
    rust_runtime_kernel_client,
    secret_redaction_service,
    security_audit_service,
)


class _LazyGatewayProtocolService:
    def __getattr__(self, name: str) -> Any:
        from server_modules import gateway_protocol_service as module

        return getattr(module, name)


gateway_protocol_service = _LazyGatewayProtocolService()


class _LazyGatewayExecutionService:
    def __getattr__(self, name: str) -> Any:
        from server_modules import gateway_execution_service as module

        return getattr(module, name)


gateway_execution_service = _LazyGatewayExecutionService()


WHATSAPP_PERSONAL_CHANNEL_KEY = "whatsapp_personal"
WHATSAPP_PERSONAL_PROVIDER = channel_lane_contract_service.assert_personal_gateway_channel(
    WHATSAPP_PERSONAL_CHANNEL_KEY
)["provider"]
TELEGRAM_PERSONAL_CHANNEL_KEY = "telegram_personal"
TELEGRAM_PERSONAL_PROVIDER = channel_lane_contract_service.assert_personal_gateway_channel(
    TELEGRAM_PERSONAL_CHANNEL_KEY
)["provider"]

TELEGRAM_PERSONAL_CONFIGURE_CAPABILITY = "channel.telegram.personal.configure"
WHATSAPP_PERSONAL_CONFIGURE_CAPABILITY = "channel.whatsapp.personal.configure"
TELEGRAM_PERSONAL_DISCONNECT_CAPABILITY = "channel.telegram.personal.disconnect"
WHATSAPP_PERSONAL_DISCONNECT_CAPABILITY = "channel.whatsapp.personal.disconnect"
WHATSAPP_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX = "whatsapp_personal:noreply:"
TELEGRAM_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX = "telegram_personal:noreply:"

LOCAL_BRIDGE_PERSONAL_CHANNELS: Dict[str, Dict[str, str]] = {
    "signal_personal": {"provider": "signal_local_bridge", "label": "Signal"},
    "imessage_personal": {"provider": "bluebubbles_local_bridge", "label": "iMessage"},
    "wechat_personal": {"provider": "wechat_local_bridge", "label": "WeChat"},
}


def _enforce_personal_gateway_config_decision(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    capability_id: str,
    run_id: str,
    trace_id: str,
) -> Dict[str, Any]:
    metadata = dict(registration.get("metadata") or {})
    session_id = str(
        registration.get("active_session_id")
        or metadata.get("gateway_session_id")
        or metadata.get("session_id")
        or metadata.get("auth_session_id")
        or metadata.get("runtime_session_id")
        or ""
    ).strip()
    if not session_id:
        session_id = str(gateway_id or registration.get("gateway_id") or "").strip()
    payload = {
        "operation": "tool_execute",
        "tenant_id": str(registration.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(registration.get("workspace_id") or "default").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": session_id,
        "request_id": str(trace_id or "").strip() or None,
        "capability_id": str(capability_id or "").strip(),
        "run_id": str(run_id or "").strip(),
        "trace_id": str(trace_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": str(registration.get("device_trust_state") or "trusted").strip() or "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": bool(session_id),
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "personal_gateway_config_denied").strip()
        raise ValueError(f"Rust gateway-service blocked tool_execute: {reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "dispatch_gateway_operation":
        raise ValueError(
            "Rust gateway-service returned unexpected next_action for "
            f"tool_execute: {next_action or 'missing'}"
        )
    return decision


def _enforce_personal_channel_dispatch_decision(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    capability_id: str,
    request_id: str,
) -> Dict[str, Any]:
    metadata = dict(registration.get("metadata") or {})
    session_id = str(
        registration.get("active_session_id")
        or metadata.get("gateway_session_id")
        or metadata.get("session_id")
        or metadata.get("auth_session_id")
        or metadata.get("runtime_session_id")
        or ""
    ).strip()
    if not session_id:
        session_id = str(gateway_id or registration.get("gateway_id") or "").strip()
    payload = {
        "operation": "protocol_route",
        "tenant_id": str(registration.get("tenant_id") or "default").strip() or "default",
        "workspace_id": str(registration.get("workspace_id") or "default").strip() or "default",
        "actor_id": "system",
        "actor_role": "owner",
        "gateway_id": str(gateway_id or "").strip(),
        "session_id": session_id,
        "request_id": str(request_id or "").strip() or None,
        "capability_id": str(capability_id or "").strip(),
        "trace_id": str(request_id or "").strip() or None,
        "quota_profile": "standard",
        "risk_level": "normal",
        "policy_decision": "allow",
        "device_trust_state": str(registration.get("device_trust_state") or "trusted").strip() or "trusted",
        "protocol_version": "gateway.v1",
        "approval_provided": True,
        "approval_memory_hit": False,
        "kill_switch_enabled": False,
        "quota_ok": True,
        "gateway_registered": True,
        "session_valid": bool(session_id),
        "websocket_token_present": True,
        "frame_valid": True,
        "payload_present": True,
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "gateway-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = str(getattr(exc, "reason", "") or "personal_channel_dispatch_denied").strip()
        raise ValueError(f"Rust gateway-service blocked protocol_route: {reason}") from exc
    next_action = str(decision.get("next_action") or "").strip()
    if next_action != "dispatch_gateway_operation":
        raise ValueError(
            "Rust gateway-service returned unexpected next_action for "
            f"protocol_route: {next_action or 'missing'}"
        )
    return decision


_LOCAL_BRIDGE_PERSONAL_CHANNEL_COPY: Dict[str, Dict[str, str]] = {
    "signal_personal": {
        "status_label": "Bridge required",
        "detail": "Signal runs through an Agent Computer local bridge.",
        "next_step": "Connect an Agent Computer with a Signal bridge to enable Sage messaging.",
    },
    "imessage_personal": {
        "status_label": "Mac bridge required",
        "detail": "iMessage runs through a user-owned Mac Agent Computer bridge.",
        "next_step": "Connect a Mac Agent Computer with an iMessage bridge to enable Sage messaging.",
    },
    "wechat_personal": {
        "status_label": "Bridge required",
        "detail": "WeChat personal runs through an Agent Computer local bridge.",
        "next_step": "Connect an Agent Computer with a WeChat bridge to enable Sage messaging.",
    },
}


# ── Handler registry ──────────────────────────────────────────────────

from server_modules.personal_channel_handler_registry import (
    PersonalChannelHandler,
    PersonalChannelHandlerRegistry,
)


class _WhatsAppPersonalChannelHandler(PersonalChannelHandler):
    """Delegates to the existing WhatsApp functions in this module."""

    @property
    def channel_key(self) -> str:
        return WHATSAPP_PERSONAL_CHANNEL_KEY

    @property
    def provider(self) -> str:
        return WHATSAPP_PERSONAL_PROVIDER

    def build_state_sync_payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "qr_code": str(message.get("qr_code") or "").strip() or None,
            "linked_jid": str(message.get("linked_jid") or "").strip() or None,
        }

    def audit_action_prefix(self) -> str:
        return "whatsapp"

    async def handle_inbound(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await _handle_whatsapp_gateway_channel_inbound(
            gateway_id=gateway_id,
            registration=registration,
            payload=payload,
        )

    async def deliver_reply(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        inbound: Dict[str, Any],
        remote_jid: str,
        external_message_id: str,
        text: str,
        push_name: Optional[str],
        duplicate: bool,
    ) -> Dict[str, Any]:
        return await _deliver_whatsapp_personal_reply(
            gateway_id=gateway_id,
            registration=registration,
            inbound=inbound,
            remote_jid=remote_jid,
            external_message_id=external_message_id,
            text=text,
            push_name=push_name,
            duplicate=duplicate,
        )

    async def send_message(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        remote_jid: str,
        text: str,
        idempotency_key: str,
        reply_to_external_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await send_whatsapp_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            remote_jid=remote_jid,
            text=text,
            idempotency_key=idempotency_key,
            reply_to_external_message_id=reply_to_external_message_id,
        )

    def get_view(self, gateway_id: str) -> Dict[str, Any]:
        return get_whatsapp_gateway_view(gateway_id)

    async def configure(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return await configure_whatsapp_personal_gateway(
            gateway_id=gateway_id,
            registration=registration,
            payload=kwargs,
        )


class _TelegramPersonalChannelHandler(PersonalChannelHandler):
    """Delegates to the existing Telegram functions in this module."""

    @property
    def channel_key(self) -> str:
        return TELEGRAM_PERSONAL_CHANNEL_KEY

    @property
    def provider(self) -> str:
        return TELEGRAM_PERSONAL_PROVIDER

    def build_state_sync_payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "login_hint": str(message.get("login_hint") or "").strip() or None,
            "linked_user_id": str(message.get("linked_user_id") or "").strip() or None,
            "linked_username": str(message.get("linked_username") or "").strip() or None,
            "linked_phone": str(message.get("linked_phone") or "").strip() or None,
        }

    def audit_action_prefix(self) -> str:
        return "telegram"

    async def handle_inbound(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await _handle_telegram_gateway_channel_inbound(
            gateway_id=gateway_id,
            registration=registration,
            payload=payload,
        )

    async def deliver_reply(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        inbound: Dict[str, Any],
        remote_jid: str,
        external_message_id: str,
        text: str,
        push_name: Optional[str],
        duplicate: bool,
    ) -> Dict[str, Any]:
        # Telegram handler currently inlines the deliver-reply logic inside
        # _handle_telegram_gateway_channel_inbound.  For registry dispatch we
        # route through the full handler which handles delivery internally.
        return await _handle_telegram_gateway_channel_inbound(
            gateway_id=gateway_id,
            registration=registration,
            payload={
                **inbound,
                "reply_text": text,
                "duplicate": duplicate,
            },
        )

    async def send_message(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        remote_jid: str,
        text: str,
        idempotency_key: str,
        reply_to_external_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await send_telegram_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            remote_jid=remote_jid,
            text=text,
            idempotency_key=idempotency_key,
            reply_to_external_message_id=reply_to_external_message_id,
        )

    def get_view(self, gateway_id: str) -> Dict[str, Any]:
        return get_telegram_gateway_view(gateway_id)

    async def configure(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return await configure_telegram_personal_gateway(
            gateway_id=gateway_id,
            registration=registration,
            payload=kwargs,
        )


class _LocalBridgePersonalChannelHandler(PersonalChannelHandler):
    """Generic Sage personal-channel handler for Agent Computer local bridges."""

    def __init__(self, channel_key: str, provider: str, label: str) -> None:
        self._channel_key = channel_key
        self._provider = provider
        self._label = label

    @property
    def channel_key(self) -> str:
        return self._channel_key

    @property
    def provider(self) -> str:
        return self._provider

    def build_state_sync_payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "linked_name": str(message.get("linked_name") or message.get("push_name") or "").strip() or None,
        }

    def audit_action_prefix(self) -> str:
        return self._channel_key.split("_", 1)[0]

    async def handle_inbound(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await _handle_local_bridge_gateway_channel_inbound(
            gateway_id=gateway_id,
            registration=registration,
            payload=payload,
            channel_key=self._channel_key,
            provider=self._provider,
            label=self._label,
        )

    async def deliver_reply(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        inbound: Dict[str, Any],
        remote_jid: str,
        external_message_id: str,
        text: str,
        push_name: Optional[str],
        duplicate: bool,
    ) -> Dict[str, Any]:
        return await _deliver_local_bridge_personal_reply(
            gateway_id=gateway_id,
            registration=registration,
            inbound=inbound,
            remote_jid=remote_jid,
            external_message_id=external_message_id,
            text=text,
            push_name=push_name,
            duplicate=duplicate,
            channel_key=self._channel_key,
            provider=self._provider,
            label=self._label,
        )

    async def send_message(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        remote_jid: str,
        text: str,
        idempotency_key: str,
        reply_to_external_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await send_local_bridge_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            channel_key=self._channel_key,
            provider=self._provider,
            remote_jid=remote_jid,
            text=text,
            idempotency_key=idempotency_key,
            reply_to_external_message_id=reply_to_external_message_id,
        )

    def get_view(self, gateway_id: str) -> Dict[str, Any]:
        return get_gateway_personal_channel_surfaces(gateway_id)

    async def configure(
        self,
        *,
        gateway_id: str,
        registration: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise ValueError(f"{self._label} is configured on Agent Computer through its local bridge.")


_handler_registry = PersonalChannelHandlerRegistry()
_handler_registry.register(_WhatsAppPersonalChannelHandler())
_handler_registry.register(_TelegramPersonalChannelHandler())
for _channel_key, _channel_spec in LOCAL_BRIDGE_PERSONAL_CHANNELS.items():
    _handler_registry.register(
        _LocalBridgePersonalChannelHandler(
            _channel_key,
            _channel_spec["provider"],
            _channel_spec["label"],
        )
    )




def _sender_role_from_message(message: Dict[str, Any]) -> Optional[str]:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    for candidate in (
        message.get("sender_role"),
        message.get("role"),
        metadata.get("sender_role"),
        metadata.get("role"),
    ):
        token = str(candidate or "").strip().lower()
        if token:
            return token
    return None


def _emit_automatic_reply_audit(
    *,
    action: str,
    status: str,
    registration: Dict[str, Any],
    gateway_id: str,
    channel_key: str,
    provider: str,
    detail: str,
    metadata: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    trace_id: str = "",
) -> None:
    security_audit_service.emit_security_audit_event(
        action=action,
        status=status,
        tenant_id=str(registration.get("tenant_id") or "").strip() or None,
        workspace_id=str(registration.get("workspace_id") or "").strip() or None,
        actor_user_id=str(registration.get("user_id") or "").strip() or None,
        actor_auth_type="paired_gateway",
        channel=channel_key,
        machine_id=str(gateway_id or "").strip() or None,
        detail=detail,
        trace_id=trace_id or "",
        metadata={
            "gateway_id": str(gateway_id or "").strip(),
            "channel_key": channel_key,
            "provider": provider,
            "action_class": "automatic_inbound_reply",
            "risk_level": "critical",
            "governance_boundary": "paired_gateway",
            "requires_approval": status == "approval_required",
            "external_side_effect": status not in {"approval_required", "blocked", "ignored"},
            **dict(metadata or {}),
        },
        idempotency_key=idempotency_key,
    )


def _control_command_block_result(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    inbound: Dict[str, Any],
    channel_key: str,
    provider: str,
    external_message_id: str,
    remote_jid: str,
    text: str,
    sender_role: Optional[str],
    duplicate: bool,
    no_reply_prefix: str,
    trace_id: str = "",
) -> Optional[Dict[str, Any]]:
    command_check = channel_blocking_policy_service.check_personal_channel_control_command(
        text=text,
        sender_role=sender_role,
    )
    if not command_check or not bool(command_check.get("blocked")):
        return None
    no_reply_idempotency_key = f"{no_reply_prefix}command_blocked:{external_message_id}"
    refreshed_inbound = personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        external_message_id=external_message_id,
        reply_idempotency_key=no_reply_idempotency_key,
    )
    _emit_automatic_reply_audit(
        action=f"personal_channel.{channel_key.split('_', 1)[0]}.control_command",
        status="denied",
        registration=registration,
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        detail="Personal-channel control command was blocked before reaching the model.",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "command": command_check.get("command"),
            "sender_role": sender_role,
            "policy_reason": command_check.get("reason"),
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.control_command.denied:{gateway_id}:{channel_key}:{external_message_id}",
    )
    return {
        "duplicate": duplicate,
        "inbound": refreshed_inbound or inbound,
        "outbound": None,
        "blocked": True,
        "policy": command_check,
    }


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_record_list(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _manifest_channel_key(item: Dict[str, Any]) -> str:
    return str(item.get("channel_key") or item.get("channelKey") or "").strip()


def _records_by_channel(items: Any) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for item in _as_record_list(items):
        channel_key = _manifest_channel_key(item)
        if channel_key:
            records[channel_key] = item
    return records


def _boolish(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"true", "1", "yes", "y", "on"}:
        return True
    if token in {"false", "0", "no", "n", "off"}:
        return False
    return default


def _personal_channel_state(gateway_id: str, channel_key: str) -> Optional[Dict[str, Any]]:
    normalized_gateway_id = str(gateway_id or "").strip()
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        return personal_channels_repository.get_whatsapp_state(
            normalized_gateway_id,
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        )
    if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        return personal_channels_repository.get_telegram_state(
            normalized_gateway_id,
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        )
    return None


def _recent_message_count(gateway_id: str, channel_key: str) -> int:
    if channel_key not in {WHATSAPP_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_CHANNEL_KEY}:
        return 0
    return len(
        personal_channels_repository.list_recent_gateway_messages(
            str(gateway_id or "").strip(),
            channel_key=channel_key,
        )
    )


def _safe_state_summary(state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(state, dict):
        return None
    metadata = _as_mapping(state.get("metadata"))
    summary = {
        "status": str(state.get("status") or "").strip() or None,
        "linked_name": str(state.get("linked_name") or "").strip() or None,
        "linked_username": str(state.get("linked_username") or "").strip() or None,
        "connected_at": str(state.get("connected_at") or "").strip() or None,
        "updated_at": metadata.get("updated_at"),
        "retryable": bool(metadata.get("retryable")),
    }
    return secret_redaction_service.sanitize_mapping({key: value for key, value in summary.items() if value is not None})


def _connected_identity_label(channel_key: str, state: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(state, dict):
        return None
    for key in ("linked_name", "linked_username"):
        value = str(state.get(key) or "").strip()
        if value:
            return secret_redaction_service.redact_text(value)
    status = str(state.get("status") or "").strip().lower()
    if status == "connected":
        if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
            return "Linked Telegram account"
        if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
            return "Linked WhatsApp account"
    return None


def get_gateway_personal_channel_surfaces(gateway_id: str) -> Dict[str, Any]:
    """Return safe per-channel capability/status projection for a paired Agent Computer.

    This is intentionally broader than the WhatsApp/Telegram detail endpoints:
    it includes local-bridge personal channels such as Signal, iMessage, and
    WeChat, while keeping them scoped to Sage/Agent Computer rather than Studio
    business/customer connectors.
    """

    normalized_gateway_id = str(gateway_id or "").strip()
    registration = gateway_state_repository.get_gateway_registration(normalized_gateway_id)
    if not registration:
        raise ValueError("Gateway registration was not found.")

    metadata = _as_mapping(registration.get("metadata"))
    manifests_by_key = _records_by_channel(metadata.get("personal_channel_manifests"))
    health_by_key = _records_by_channel(metadata.get("personal_channel_health"))
    catalog_by_key = {item["channel_key"]: dict(item) for item in channel_lane_contract_service.personal_channel_catalog()}
    platform_by_key = {
        item["channel_key"]: dict(item)
        for item in channel_lane_contract_service.platform_channel_catalog("sage")
        if channel_lane_contract_service.is_personal_channel_key(str(item.get("channel_key") or ""))
    }

    items: list[Dict[str, Any]] = []
    for channel_key, catalog in catalog_by_key.items():
        spec = channel_lane_contract_service.assert_personal_gateway_channel(channel_key)
        manifest = manifests_by_key.get(channel_key, {})
        health = health_by_key.get(channel_key, {})
        platform = platform_by_key.get(channel_key, {})
        state = _personal_channel_state(normalized_gateway_id, channel_key)
        state_status = str((state or {}).get("status") or "").strip()
        health_status = str(health.get("status") or "").strip()
        manifest_status = str(manifest.get("status") or "").strip()
        status = state_status or health_status or manifest_status or str(platform.get("status") or catalog.get("stage") or "agent_computer_bridge").strip()
        catalog_live_capable = _boolish(
            platform.get("live_capable", spec.get("live_capable")),
            default=str(spec.get("live_capable") or "").strip().lower() == "true",
        )
        advertised_live_capable = _boolish(
            manifest.get("live_capable")
            if "live_capable" in manifest
            else manifest.get("liveCapable")
            if "liveCapable" in manifest
            else platform.get("live_capable", spec.get("live_capable")),
            default=str(spec.get("live_capable") or "").strip().lower() == "true",
        )
        live_capable = bool(catalog_live_capable and advertised_live_capable)
        connected = bool(state_status == "connected" or health.get("connected") is True)
        running = bool(health.get("running") is True)
        items.append(
            {
                "channel_key": channel_key,
                "label": str(manifest.get("label") or platform.get("label") or catalog.get("label") or channel_key).strip(),
                "provider": str(manifest.get("provider") or platform.get("provider") or spec.get("provider") or "").strip(),
                "runtime_lane": str(manifest.get("runtime_lane") or manifest.get("runtimeLane") or spec.get("runtime_lane") or "").strip(),
                "stage": str(platform.get("stage") or catalog.get("stage") or manifest.get("stage") or "").strip(),
                "status": status,
                "status_label": _LOCAL_BRIDGE_PERSONAL_CHANNEL_COPY.get(channel_key, {}).get("status_label"),
                "live_capable": live_capable,
                "requires_agent_computer": _boolish(
                    manifest.get("requires_agent_computer")
                    if "requires_agent_computer" in manifest
                    else manifest.get("requiresAgentComputer")
                    if "requiresAgentComputer" in manifest
                    else platform.get("requires_agent_computer", True),
                    default=True,
                ),
                "connected": connected,
                "running": running,
                "connected_identity": _connected_identity_label(channel_key, state),
                "recent_message_count": _recent_message_count(normalized_gateway_id, channel_key),
                "capabilities": secret_redaction_service.sanitize_value(
                    manifest.get("capabilities")
                    if isinstance(manifest.get("capabilities"), list)
                    else platform.get("capabilities", []),
                ),
                "chat_types": secret_redaction_service.sanitize_value(
                    manifest.get("chat_types")
                    if isinstance(manifest.get("chat_types"), list)
                    else [],
                ),
                "media": secret_redaction_service.sanitize_mapping(_as_mapping(manifest.get("media"))),
                "safety": secret_redaction_service.sanitize_mapping(_as_mapping(manifest.get("safety"))),
                "manifest": secret_redaction_service.sanitize_mapping(manifest),
                "health": secret_redaction_service.sanitize_mapping(health),
                "state": _safe_state_summary(state),
                "detail": _LOCAL_BRIDGE_PERSONAL_CHANNEL_COPY.get(channel_key, {}).get("detail"),
                "next_step": _LOCAL_BRIDGE_PERSONAL_CHANNEL_COPY.get(channel_key, {}).get("next_step"),
            }
        )

    return {
        "gateway_id": normalized_gateway_id,
        "items": items,
    }


def sync_gateway_personal_channel_state(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    personal_channels = payload.get("personal_channels") if isinstance(payload.get("personal_channels"), dict) else {}
    synced_state: Optional[Dict[str, Any]] = None
    whatsapp_state = (
        personal_channels.get(WHATSAPP_PERSONAL_CHANNEL_KEY)
        if isinstance(personal_channels.get(WHATSAPP_PERSONAL_CHANNEL_KEY), dict)
        else {}
    )
    if whatsapp_state:
        whatsapp_spec = channel_lane_contract_service.assert_personal_gateway_channel(
            WHATSAPP_PERSONAL_CHANNEL_KEY,
            str(whatsapp_state.get("provider") or WHATSAPP_PERSONAL_PROVIDER).strip() or WHATSAPP_PERSONAL_PROVIDER,
        )
        synced_state = personal_channels_repository.upsert_whatsapp_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            provider=whatsapp_spec["provider"],
            status=str(whatsapp_state.get("status") or "idle").strip() or "idle",
            qr_code=str(whatsapp_state.get("qr_code") or "").strip() or None,
            linked_jid=str(whatsapp_state.get("linked_jid") or "").strip() or None,
            linked_name=str(whatsapp_state.get("linked_name") or "").strip() or None,
            connected_at=str(whatsapp_state.get("connected_at") or "").strip() or None,
            metadata={
                "retryable": bool(whatsapp_state.get("retryable")),
                "login_hint": str(whatsapp_state.get("login_hint") or "").strip() or None,
                "pairing_code": str(whatsapp_state.get("pairing_code") or "").strip() or None,
                "pairing_code_generated_at": str(
                    whatsapp_state.get("pairing_code_generated_at") or ""
                ).strip()
                or None,
                "last_disconnect_reason": str(whatsapp_state.get("last_disconnect_reason") or "").strip() or None,
                "last_disconnect_code": whatsapp_state.get("last_disconnect_code"),
                "updated_at": whatsapp_state.get("updated_at"),
            },
        )
    telegram_state = (
        personal_channels.get(TELEGRAM_PERSONAL_CHANNEL_KEY)
        if isinstance(personal_channels.get(TELEGRAM_PERSONAL_CHANNEL_KEY), dict)
        else {}
    )
    if telegram_state:
        telegram_spec = channel_lane_contract_service.assert_personal_gateway_channel(
            TELEGRAM_PERSONAL_CHANNEL_KEY,
            str(telegram_state.get("provider") or TELEGRAM_PERSONAL_PROVIDER).strip() or TELEGRAM_PERSONAL_PROVIDER,
        )
        synced_state = personal_channels_repository.upsert_telegram_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            provider=telegram_spec["provider"],
            status=str(telegram_state.get("status") or "idle").strip() or "idle",
            login_hint=str(telegram_state.get("login_hint") or "").strip() or None,
            linked_user_id=str(telegram_state.get("linked_user_id") or "").strip() or None,
            linked_username=str(telegram_state.get("linked_username") or "").strip() or None,
            linked_phone=str(telegram_state.get("linked_phone") or "").strip() or None,
            linked_name=str(telegram_state.get("linked_name") or "").strip() or None,
            connected_at=str(telegram_state.get("connected_at") or "").strip() or None,
            metadata={
                "retryable": bool(telegram_state.get("retryable")),
                "code_requested_at": str(telegram_state.get("code_requested_at") or "").strip() or None,
                "last_disconnect_reason": str(telegram_state.get("last_disconnect_reason") or "").strip() or None,
                "last_disconnect_code": telegram_state.get("last_disconnect_code"),
                "updated_at": telegram_state.get("updated_at"),
            },
        )
    return synced_state


def sync_gateway_channel_outbound_result(
    *,
    gateway_id: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    channel_key = str(payload.get("channel_key") or "").strip()
    if not channel_key:
        return None
    channel_lane_contract_service.assert_personal_gateway_channel(
        channel_key,
        str(payload.get("provider") or "").strip() or None,
    )
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return None
    if bool(payload.get("delivered")):
        return personal_channels_repository.mark_outbound_delivered(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            idempotency_key=idempotency_key,
            external_message_id=str(payload.get("external_message_id") or "").strip() or None,
            metadata={"dispatch_result": dict(payload or {})},
        )
    return personal_channels_repository.get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=idempotency_key,
    )


def _iter_personal_channel_records(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            candidates = value.get("items") or []
        else:
            candidates = []
            for key, item in value.items():
                if isinstance(item, dict):
                    enriched = dict(item)
                    enriched.setdefault("channel_key", str(key or "").strip())
                    candidates.append(enriched)
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []
    return [item for item in candidates if isinstance(item, dict)]


def _record_channel_key(record: Dict[str, Any]) -> str:
    return str(
        record.get("channel_key")
        or record.get("channelKey")
        or record.get("key")
        or record.get("id")
        or ""
    ).strip()


def _record_provider(record: Dict[str, Any]) -> str:
    return str(record.get("provider") or record.get("runtime_provider") or "").strip()


def _registration_capabilities(registration: Dict[str, Any]) -> list[str]:
    raw = registration.get("capabilities")
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return []


def _registration_advertises_personal_channel(
    registration: Dict[str, Any],
    channel_key: str,
) -> bool:
    normalized_channel = str(channel_key or "").strip().lower()
    if not normalized_channel:
        return False
    provider_prefix = normalized_channel.removesuffix("_personal")
    expected_fragments = {
        normalized_channel,
        normalized_channel.replace("_", "."),
        f"channel.{normalized_channel}",
        f"channel.{normalized_channel.replace('_', '.')}",
    }
    if provider_prefix and provider_prefix != normalized_channel:
        expected_fragments.add(f"channel.{provider_prefix}.personal")
    for capability in _registration_capabilities(registration):
        normalized_capability = capability.strip().lower()
        if normalized_capability in expected_fragments:
            return True
    return False


def _assert_gateway_advertised_personal_channel(
    *,
    registration: Dict[str, Any],
    channel_key: str,
    provider: str,
) -> None:
    metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
    manifests = _iter_personal_channel_records(metadata.get("personal_channel_manifests"))
    manifest = next((item for item in manifests if _record_channel_key(item) == channel_key), None)
    if manifest is None:
        if not _registration_advertises_personal_channel(registration, channel_key):
            raise ValueError(f"Gateway has not advertised personal channel support for {channel_key}.")
    else:
        manifest_provider = _record_provider(manifest)
        if manifest_provider and provider and manifest_provider != provider:
            raise ValueError(f"Gateway advertised {channel_key} with provider {manifest_provider}, not {provider}.")
        if manifest.get("liveCapable") is False or manifest.get("live_capable") is False:
            raise ValueError(f"Gateway personal channel {channel_key} is not live-capable.")
        manifest_status = str(manifest.get("status") or manifest.get("stage") or "").strip().lower()
        if manifest_status in {"unsupported", "unavailable", "disabled", "planned", "locked"}:
            raise ValueError(f"Gateway personal channel {channel_key} is {manifest_status}.")
    health_records = _iter_personal_channel_records(metadata.get("personal_channel_health"))
    health = next((item for item in health_records if _record_channel_key(item) == channel_key), None)
    if health is not None:
        health_status = str(health.get("status") or health.get("health") or "").strip().lower()
        if health_status in {"offline", "error", "failed", "disconnected", "unavailable", "disabled"}:
            raise ValueError(f"Gateway personal channel {channel_key} health is {health_status}.")


async def handle_gateway_channel_inbound(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_key = str(payload.get("channel_key") or "").strip()
    provider = str(payload.get("provider") or "").strip()
    channel_lane_contract_service.assert_personal_gateway_channel(
        channel_key,
        provider or None,
    )
    _assert_gateway_advertised_personal_channel(
        registration=registration,
        channel_key=channel_key,
        provider=provider,
    )
    # Generate or preserve trace_id once at the inbound boundary
    trace_id = str(payload.get("trace_id") or "").strip() or f"channel-{channel_key}-{uuid4().hex[:16]}"
    payload["trace_id"] = trace_id
    handler = _handler_registry.get(channel_key)
    return await handler.handle_inbound(
        gateway_id=gateway_id,
        registration=registration,
        payload=payload,
    )


async def _deliver_whatsapp_personal_reply(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    inbound: Dict[str, Any],
    remote_jid: str,
    external_message_id: str,
    text: str,
    push_name: Optional[str],
    duplicate: bool,
    trace_id: str = "",
) -> Dict[str, Any]:
    reply_idempotency_key = str(inbound.get("reply_idempotency_key") or "").strip() or None
    if reply_idempotency_key and reply_idempotency_key.startswith(WHATSAPP_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX):
        return {"duplicate": duplicate, "inbound": inbound, "outbound": None}

    outbound: Optional[Dict[str, Any]] = None
    idempotency_key = reply_idempotency_key or f"whatsapp_personal:{external_message_id}"
    if reply_idempotency_key:
        outbound = personal_channels_repository.get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            idempotency_key=reply_idempotency_key,
        )
    if outbound and str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": duplicate, "inbound": inbound, "outbound": outbound}

    if outbound is None:
        # Resolve linked user name from WhatsApp state for identity context
        # Validate workspace_id from registration exists before proceeding
        try:
            from server_modules.control_plane_repository import get_workspace_by_id
            _ws_id = str(registration.get("workspace_id") or "").strip()
            if _ws_id and _ws_id != "default":
                ws_check = await get_workspace_by_id(_ws_id)
                if not ws_check:
                    _ws_logger = __import__('logging').getLogger(__name__)
                    _ws_logger.error(
                        "whatsapp_personal: invalid workspace_id=%s in registration — message will be ignored",
                        _ws_id,
                    )
                    return {"ignored": True, "reason": "invalid_workspace_id", "workspace_id": _ws_id}
        except Exception:
            pass
        _wa_state = personal_channels_repository.get_whatsapp_state(
            str(gateway_id or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        )
        linked_user_name = str((_wa_state or {}).get("linked_name") or "").strip() or None
        # ── Shared command dispatcher ──
        from server_modules.sage_command_dispatcher import dispatch_command as _dispatch_cmd
        _ws_id = str(registration.get("workspace_id") or "").strip()
        _cmd_reply = await _dispatch_cmd(
            command=text,
            workspace_id=_ws_id,
            thread_id="sage-main",
            channel_origin="whatsapp_personal",
            sender_id=remote_jid or None,
        )
        if _cmd_reply is not None:
            _cmd_key = f"whatsapp_personal:cmd:{external_message_id}"
            outbound_cmd, _ = personal_channels_repository.create_or_get_outbound_message(
                gateway_id=str(gateway_id or "").strip(),
                channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
                idempotency_key=_cmd_key,
                remote_jid=remote_jid,
                text=_cmd_reply,
                reply_to_external_message_id=external_message_id,
            )
            personal_channels_repository.mark_inbound_processed(
                gateway_id=str(gateway_id or "").strip(),
                channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
                external_message_id=external_message_id,
                reply_idempotency_key=_cmd_key,
            )
            return {"duplicate": duplicate, "inbound": inbound, "outbound": outbound_cmd}

        reply = personal_channel_sage_bridge_service.build_whatsapp_personal_reply(
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            gateway_id=str(gateway_id or "").strip(),
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            source_event_id=external_message_id,
            linked_user_name=linked_user_name,
        )
        if not reply or not str(reply.get("text") or "").strip():
            no_reply_idempotency_key = f"{WHATSAPP_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX}{external_message_id}"
            refreshed_inbound = personal_channels_repository.mark_inbound_processed(
                gateway_id=str(gateway_id or "").strip(),
                channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
                external_message_id=external_message_id,
                reply_idempotency_key=no_reply_idempotency_key,
            )
            _emit_automatic_reply_audit(
                action="personal_channel.whatsapp.automatic_reply",
                status="skipped",
                registration=registration,
                gateway_id=gateway_id,
                channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
                provider=WHATSAPP_PERSONAL_PROVIDER,
                detail="Automatic WhatsApp personal reply was skipped because Sage returned no reply.",
                metadata={"remote_jid": remote_jid, "inbound_external_message_id": external_message_id},
                trace_id=trace_id,
                idempotency_key=f"personal_channel.whatsapp.automatic_reply.skipped:{gateway_id}:{external_message_id}",
            )
            return {"duplicate": duplicate, "inbound": refreshed_inbound or inbound, "outbound": None}

        outbound, _ = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            idempotency_key=idempotency_key,
            remote_jid=remote_jid,
            text=str(reply.get("text") or "").strip(),
            reply_to_external_message_id=external_message_id,
            metadata={"reply_source": str(reply.get("source") or "").strip() or None},
        )

    if str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": duplicate, "inbound": inbound, "outbound": outbound}

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id="channel.whatsapp.personal.send",
        request_id=str(idempotency_key or "").strip(),
    )
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider=WHATSAPP_PERSONAL_PROVIDER,
        remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
        text=str(outbound.get("text") or "").strip(),
        idempotency_key=idempotency_key,
        reply_to_external_message_id=external_message_id,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        idempotency_key=idempotency_key,
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        external_message_id=external_message_id,
        reply_idempotency_key=idempotency_key,
    )
    _emit_automatic_reply_audit(
        action="personal_channel.whatsapp.automatic_reply",
        status="delivered",
        registration=registration,
        gateway_id=gateway_id,
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider=WHATSAPP_PERSONAL_PROVIDER,
        detail="Automatic WhatsApp personal reply was dispatched immediately.",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "reply_text_length": len(str(outbound.get("text") or "")),
            "dispatched": True,
            "dispatch_external_message_id": str(dispatch_result.get("external_message_id") or "").strip() or None,
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.whatsapp.automatic_reply.delivered:{gateway_id}:{idempotency_key}",
    )
    return {"duplicate": duplicate, "inbound": inbound, "outbound": delivered or outbound}


async def _handle_whatsapp_gateway_channel_inbound(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        WHATSAPP_PERSONAL_CHANNEL_KEY,
        str(payload.get("provider") or WHATSAPP_PERSONAL_PROVIDER).strip() or WHATSAPP_PERSONAL_PROVIDER,
    )
    trace_id = str(payload.get("trace_id") or "").strip()
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    external_message_id = str(message.get("external_message_id") or "").strip()
    remote_jid = str(message.get("remote_jid") or "").strip()
    text = str(message.get("text") or "").strip()
    if not external_message_id or not remote_jid or not text:
        raise ValueError("channel.inbound requires external_message_id, remote_jid, and text.")
    if bool(message.get("from_me")) and not bool(message.get("is_self_chat")):
        return {"ignored": True, "reason": "from_me", "channel_key": WHATSAPP_PERSONAL_CHANNEL_KEY}
    # Group gate: skip group messages unless mentioned or replying to Sage.
    # The Gateway-side filter (runtime.ts) is the primary gate; this is a
    # backend safety net in case the Gateway bypasses it for any reason.
    if bool(message.get("is_group")):
        if not bool(message.get("is_mentioned")) and not bool(message.get("is_reply_to_sage")):
            return {
                "ignored": True,
                "reason": "group_no_mention",
                "channel_key": WHATSAPP_PERSONAL_CHANNEL_KEY,
            }
    sync_gateway_personal_channel_state(
        gateway_id=gateway_id,
        registration=registration,
        payload={
            "personal_channels": {
                WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "provider": str(payload.get("provider") or WHATSAPP_PERSONAL_PROVIDER).strip() or WHATSAPP_PERSONAL_PROVIDER,
                    "status": "connected",
                    "linked_jid": str(message.get("sender_jid") or "").strip() or None,
                }
            }
        },
    )
    inbound, created = personal_channels_repository.record_inbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        sender_jid=str(message.get("sender_jid") or "").strip() or None,
        push_name=str(message.get("push_name") or "").strip() or None,
        text=text,
        metadata={
            "provider": str(payload.get("provider") or WHATSAPP_PERSONAL_PROVIDER).strip() or WHATSAPP_PERSONAL_PROVIDER,
            "received_at": str(message.get("received_at") or "").strip() or None,
            "from_me": bool(message.get("from_me")),
        },
    )
    blocked_result = _control_command_block_result(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider=WHATSAPP_PERSONAL_PROVIDER,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        text=text,
        sender_role=_sender_role_from_message(message),
        duplicate=not created,
        no_reply_prefix=WHATSAPP_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX,
        trace_id=trace_id,
    )
    if blocked_result is not None:
        return blocked_result
    return await _deliver_whatsapp_personal_reply(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        remote_jid=remote_jid,
        external_message_id=external_message_id,
        text=text,
        push_name=str(message.get("push_name") or "").strip() or None,
        duplicate=not created,
        trace_id=trace_id,
    )


async def _handle_telegram_gateway_channel_inbound(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        TELEGRAM_PERSONAL_CHANNEL_KEY,
        str(payload.get("provider") or TELEGRAM_PERSONAL_PROVIDER).strip() or TELEGRAM_PERSONAL_PROVIDER,
    )
    trace_id = str(payload.get("trace_id") or "").strip()
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    external_message_id = str(message.get("external_message_id") or "").strip()
    remote_jid = str(message.get("remote_jid") or "").strip()
    text = str(message.get("text") or "").strip()
    if not external_message_id or not remote_jid or not text:
        raise ValueError("channel.inbound requires external_message_id, remote_jid, and text.")
    if bool(message.get("from_me")):
        return {"ignored": True, "reason": "from_me", "channel_key": TELEGRAM_PERSONAL_CHANNEL_KEY}
    sync_gateway_personal_channel_state(
        gateway_id=gateway_id,
        registration=registration,
        payload={
            "personal_channels": {
                TELEGRAM_PERSONAL_CHANNEL_KEY: {
                    "provider": str(payload.get("provider") or TELEGRAM_PERSONAL_PROVIDER).strip() or TELEGRAM_PERSONAL_PROVIDER,
                    "status": "connected",
                    "linked_user_id": str(message.get("sender_jid") or "").strip() or None,
                    "linked_name": str(message.get("push_name") or "").strip() or None,
                }
            }
        },
    )
    inbound, created = personal_channels_repository.record_inbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        sender_jid=str(message.get("sender_jid") or "").strip() or None,
        push_name=str(message.get("push_name") or "").strip() or None,
        text=text,
        metadata={
            "provider": str(payload.get("provider") or TELEGRAM_PERSONAL_PROVIDER).strip() or TELEGRAM_PERSONAL_PROVIDER,
            "received_at": str(message.get("received_at") or "").strip() or None,
            "from_me": bool(message.get("from_me")),
        },
    )
    blocked_result = _control_command_block_result(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider=TELEGRAM_PERSONAL_PROVIDER,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        text=text,
        sender_role=_sender_role_from_message(message),
        duplicate=not created,
        no_reply_prefix=TELEGRAM_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX,
        trace_id=trace_id,
    )
    if blocked_result is not None:
        return blocked_result
    reply_idempotency_key = str(inbound.get("reply_idempotency_key") or "").strip() or None
    if reply_idempotency_key and reply_idempotency_key.startswith(TELEGRAM_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX):
        return {"duplicate": not created, "inbound": inbound, "outbound": None}

    outbound: Optional[Dict[str, Any]] = None
    idempotency_key = reply_idempotency_key or f"telegram_personal:{external_message_id}"
    if reply_idempotency_key:
        outbound = personal_channels_repository.get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            idempotency_key=reply_idempotency_key,
        )
    if outbound and str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": not created, "inbound": inbound, "outbound": outbound}

    if outbound is None:
        reply = personal_channel_sage_bridge_service.build_telegram_personal_reply(
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            gateway_id=str(gateway_id or "").strip(),
            remote_jid=remote_jid,
            text=text,
            push_name=str(message.get("push_name") or "").strip() or None,
            source_event_id=external_message_id,
        )
        if not reply or not str(reply.get("text") or "").strip():
            no_reply_idempotency_key = f"{TELEGRAM_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX}{external_message_id}"
            refreshed_inbound = personal_channels_repository.mark_inbound_processed(
                gateway_id=str(gateway_id or "").strip(),
                channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
                external_message_id=external_message_id,
                reply_idempotency_key=no_reply_idempotency_key,
            )
            _emit_automatic_reply_audit(
                action="personal_channel.telegram.automatic_reply",
                status="skipped",
                registration=registration,
                gateway_id=gateway_id,
                channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
                provider=TELEGRAM_PERSONAL_PROVIDER,
                detail="Automatic Telegram personal reply was skipped because Sage returned no reply.",
                metadata={"remote_jid": remote_jid, "inbound_external_message_id": external_message_id},
                trace_id=trace_id,
                idempotency_key=f"personal_channel.telegram.automatic_reply.skipped:{gateway_id}:{external_message_id}",
            )
            return {"duplicate": not created, "inbound": refreshed_inbound or inbound, "outbound": None}

        outbound, _ = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            idempotency_key=idempotency_key,
            remote_jid=remote_jid,
            text=str(reply.get("text") or "").strip(),
            reply_to_external_message_id=external_message_id,
            metadata={"reply_source": str(reply.get("source") or "").strip() or None},
        )

    if str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": not created, "inbound": inbound, "outbound": outbound}

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id="channel.telegram.personal.send",
        request_id=str(idempotency_key or "").strip(),
    )
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider=TELEGRAM_PERSONAL_PROVIDER,
        remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
        text=str(outbound.get("text") or "").strip(),
        idempotency_key=idempotency_key,
        reply_to_external_message_id=external_message_id,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        idempotency_key=idempotency_key,
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        external_message_id=external_message_id,
        reply_idempotency_key=idempotency_key,
    )
    _emit_automatic_reply_audit(
        action="personal_channel.telegram.automatic_reply",
        status="delivered",
        registration=registration,
        gateway_id=gateway_id,
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider=TELEGRAM_PERSONAL_PROVIDER,
        detail="Automatic Telegram personal reply was dispatched immediately.",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "reply_text_length": len(str(outbound.get("text") or "")),
            "dispatched": True,
            "dispatch_external_message_id": str(dispatch_result.get("external_message_id") or "").strip() or None,
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.telegram.automatic_reply.delivered:{gateway_id}:{idempotency_key}",
    )
    return {"duplicate": not created, "inbound": inbound, "outbound": delivered or outbound}


async def _deliver_local_bridge_personal_reply(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    inbound: Dict[str, Any],
    remote_jid: str,
    external_message_id: str,
    text: str,
    push_name: Optional[str],
    duplicate: bool,
    channel_key: str,
    provider: str,
    label: str,
    trace_id: str = "",
) -> Dict[str, Any]:
    no_reply_prefix = f"{channel_key}:noreply:"
    reply_idempotency_key = str(inbound.get("reply_idempotency_key") or "").strip() or None
    if reply_idempotency_key and reply_idempotency_key.startswith(no_reply_prefix):
        return {"duplicate": duplicate, "inbound": inbound, "outbound": None}

    outbound: Optional[Dict[str, Any]] = None
    idempotency_key = reply_idempotency_key or f"{channel_key}:{external_message_id}"
    if reply_idempotency_key:
        outbound = personal_channels_repository.get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            idempotency_key=reply_idempotency_key,
        )
    if outbound and str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": duplicate, "inbound": inbound, "outbound": outbound}

    if outbound is None:
        reply = await personal_channel_sage_bridge_service.build_personal_channel_reply_async(
            surface_channel=channel_key,
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            gateway_id=str(gateway_id or "").strip(),
            remote_jid=remote_jid,
            text=text,
            push_name=push_name,
            fallback_label=label,
            source_event_id=external_message_id,
        )
        if not reply or not str(reply.get("text") or "").strip():
            no_reply_idempotency_key = f"{no_reply_prefix}{external_message_id}"
            refreshed_inbound = personal_channels_repository.mark_inbound_processed(
                gateway_id=str(gateway_id or "").strip(),
                channel_key=channel_key,
                external_message_id=external_message_id,
                reply_idempotency_key=no_reply_idempotency_key,
            )
            _emit_automatic_reply_audit(
                action=f"personal_channel.{channel_key.split('_', 1)[0]}.automatic_reply",
                status="skipped",
                registration=registration,
                gateway_id=gateway_id,
                channel_key=channel_key,
                provider=provider,
                detail=f"Automatic {label} personal reply was skipped because Sage returned no reply.",
                metadata={"remote_jid": remote_jid, "inbound_external_message_id": external_message_id},
                trace_id=trace_id,
                idempotency_key=f"personal_channel.{channel_key}.automatic_reply.skipped:{gateway_id}:{external_message_id}",
            )
            return {"duplicate": duplicate, "inbound": refreshed_inbound or inbound, "outbound": None}

        outbound, _ = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            idempotency_key=idempotency_key,
            remote_jid=remote_jid,
            text=str(reply.get("text") or "").strip(),
            reply_to_external_message_id=external_message_id,
            metadata={"reply_source": str(reply.get("source") or "").strip() or None},
        )

    if str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": duplicate, "inbound": inbound, "outbound": outbound}

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=f"{str(channel_key or '').strip()}.send",
        request_id=str(idempotency_key or "").strip(),
    )
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        provider=provider,
        remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
        text=str(outbound.get("text") or "").strip(),
        idempotency_key=idempotency_key,
        reply_to_external_message_id=external_message_id,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=idempotency_key,
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        external_message_id=external_message_id,
        reply_idempotency_key=idempotency_key,
    )
    _emit_automatic_reply_audit(
        action=f"personal_channel.{channel_key.split('_', 1)[0]}.automatic_reply",
        status="delivered",
        registration=registration,
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        detail=f"Automatic {label} personal reply was dispatched immediately.",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "reply_text_length": len(str(outbound.get("text") or "")),
            "dispatched": True,
            "dispatch_external_message_id": str(dispatch_result.get("external_message_id") or "").strip() or None,
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.{channel_key.split('_', 1)[0]}.automatic_reply.delivered:{gateway_id}:{idempotency_key}",
    )
    return {"duplicate": duplicate, "inbound": inbound, "outbound": delivered or outbound}


async def _handle_local_bridge_gateway_channel_inbound(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    payload: Dict[str, Any],
    channel_key: str,
    provider: str,
    label: str,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        channel_key,
        str(payload.get("provider") or provider).strip() or provider,
    )
    trace_id = str(payload.get("trace_id") or "").strip()
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    external_message_id = str(message.get("external_message_id") or "").strip()
    remote_jid = str(message.get("remote_jid") or "").strip()
    text = str(message.get("text") or "").strip()
    if not external_message_id or not remote_jid or not text:
        raise ValueError("channel.inbound requires external_message_id, remote_jid, and text.")
    if bool(message.get("from_me")):
        return {"ignored": True, "reason": "from_me", "channel_key": channel_key}
    inbound, created = personal_channels_repository.record_inbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        sender_jid=str(message.get("sender_jid") or "").strip() or None,
        push_name=str(message.get("push_name") or "").strip() or None,
        text=text,
        metadata={
            "provider": provider,
            "received_at": str(message.get("received_at") or "").strip() or None,
            "from_me": bool(message.get("from_me")),
            "agent_computer_bridge": True,
        },
    )
    blocked_result = _control_command_block_result(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        channel_key=channel_key,
        provider=provider,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        text=text,
        sender_role=_sender_role_from_message(message),
        duplicate=not created,
        no_reply_prefix=f"{channel_key}:noreply:",
        trace_id=trace_id,
    )
    if blocked_result is not None:
        return blocked_result
    return await _deliver_local_bridge_personal_reply(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        remote_jid=remote_jid,
        external_message_id=external_message_id,
        text=text,
        push_name=str(message.get("push_name") or "").strip() or None,
        duplicate=not created,
        channel_key=channel_key,
        provider=provider,
        label=label,
        trace_id=trace_id,
    )


async def send_local_bridge_personal_message(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    channel_key: str,
    provider: str,
    remote_jid: str,
    text: str,
    idempotency_key: str,
    reply_to_external_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(channel_key, provider)
    outbound, _ = personal_channels_repository.create_or_get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=str(idempotency_key or "").strip(),
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
        metadata={"source": "manual_api", "agent_computer_bridge": True},
    )
    if str(outbound.get("status") or "").strip() == "delivered":
        return outbound

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=f"{str(channel_key or '').strip()}.send",
        request_id=str(idempotency_key or "").strip(),
    )
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        provider=provider,
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        idempotency_key=str(idempotency_key or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=str(idempotency_key or "").strip(),
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    return delivered or outbound


async def send_whatsapp_personal_message(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    remote_jid: str,
    text: str,
    idempotency_key: str,
    reply_to_external_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        WHATSAPP_PERSONAL_CHANNEL_KEY,
        WHATSAPP_PERSONAL_PROVIDER,
    )
    outbound, _ = personal_channels_repository.create_or_get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        idempotency_key=str(idempotency_key or "").strip(),
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
        metadata={"source": "manual_api"},
    )
    if str(outbound.get("status") or "").strip() == "delivered":
        return outbound

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id="channel.whatsapp.personal.send",
        request_id=str(idempotency_key or "").strip(),
    )
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider=WHATSAPP_PERSONAL_PROVIDER,
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        idempotency_key=str(idempotency_key or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        idempotency_key=str(idempotency_key or "").strip(),
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    return delivered or outbound


def get_whatsapp_gateway_view(gateway_id: str) -> Dict[str, Any]:
    state = personal_channels_repository.get_whatsapp_state(
        str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
    )
    recent = personal_channels_repository.list_recent_gateway_messages(
        str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
    )
    return {
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": WHATSAPP_PERSONAL_CHANNEL_KEY,
        "state": state,
        "recent_messages": recent,
    }


async def configure_whatsapp_personal_gateway(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    phone_number: Optional[str] = None,
    custom_pairing_code: Optional[str] = None,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        WHATSAPP_PERSONAL_CHANNEL_KEY,
        WHATSAPP_PERSONAL_PROVIDER,
    )
    arguments: Dict[str, Any] = {}
    if str(phone_number or "").strip():
        arguments["phone_number"] = str(phone_number).strip()
    if str(custom_pairing_code or "").strip():
        arguments["custom_pairing_code"] = str(custom_pairing_code).strip()
    # Unlike Telegram, an empty call is valid here -- it means "begin/retry
    # the QR flow", which needs no fields at all. See the matching change in
    # WhatsAppPersonalRuntime.handleConfigure() (empyralis-gateway) for why.
    run_id = f"gateway-whatsapp-setup-{uuid4().hex[:12]}"
    trace_id = f"gateway-whatsapp-setup-{uuid4().hex[:12]}"
    _enforce_personal_gateway_config_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=WHATSAPP_PERSONAL_CONFIGURE_CAPABILITY,
        run_id=run_id,
        trace_id=trace_id,
    )
    execution = await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=str(gateway_id or "").strip(),
        capability_id=WHATSAPP_PERSONAL_CONFIGURE_CAPABILITY,
        arguments=arguments,
        run_id=run_id,
        trace_id=trace_id,
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        agent_scope="sage",
    )
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": WHATSAPP_PERSONAL_CHANNEL_KEY,
        **result,
    }


async def disconnect_whatsapp_personal_gateway(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
) -> Dict[str, Any]:
    """Full reset: tears down any live/stuck session and clears the entire
    persisted config (phone number, pairing state) so a subsequent setup
    call starts genuinely fresh rather than inheriting a stuck pending
    login — see WhatsAppPersonalRuntime.handleDisconnect()'s doc comment."""
    channel_lane_contract_service.assert_personal_gateway_channel(
        WHATSAPP_PERSONAL_CHANNEL_KEY,
        WHATSAPP_PERSONAL_PROVIDER,
    )
    run_id = f"gateway-whatsapp-disconnect-{uuid4().hex[:12]}"
    trace_id = f"gateway-whatsapp-disconnect-{uuid4().hex[:12]}"
    _enforce_personal_gateway_config_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=WHATSAPP_PERSONAL_DISCONNECT_CAPABILITY,
        run_id=run_id,
        trace_id=trace_id,
    )
    execution = await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=str(gateway_id or "").strip(),
        capability_id=WHATSAPP_PERSONAL_DISCONNECT_CAPABILITY,
        arguments={},
        run_id=run_id,
        trace_id=trace_id,
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        agent_scope="sage",
    )
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": WHATSAPP_PERSONAL_CHANNEL_KEY,
        **result,
    }


async def dispatch_approved_personal_channel_outbound(
    *,
    gateway_id: str,
    capability_id: str,
    arguments: Dict[str, Any],
    run_id: str,
    trace_id: str,
    workspace_id: str,
    timeout_seconds: int,
    request_id: Optional[str] = None,
    runtime_access_mode: Optional[str] = None,
    empyralis_approved: bool = False,
) -> Dict[str, Any]:
    channel_key = str(arguments.get("channel_key") or "").strip()
    if not channel_key:
        token = str(capability_id or "").strip().lower()
        if token == "channel.whatsapp.personal.send":
            channel_key = WHATSAPP_PERSONAL_CHANNEL_KEY
        elif token == "channel.telegram.personal.send":
            channel_key = TELEGRAM_PERSONAL_CHANNEL_KEY
    provider = str(arguments.get("provider") or "").strip()
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        provider = provider or WHATSAPP_PERSONAL_PROVIDER
    elif channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        provider = provider or TELEGRAM_PERSONAL_PROVIDER
    else:
        provider = provider or LOCAL_BRIDGE_PERSONAL_CHANNELS.get(channel_key, {}).get("provider", "")
    channel_lane_contract_service.assert_personal_gateway_channel(channel_key, provider)
    remote_jid = str(arguments.get("remote_jid") or "").strip()
    text = str(arguments.get("text") or "").strip()
    idempotency_key = str(arguments.get("idempotency_key") or request_id or "").strip()
    reply_to_external_message_id = str(arguments.get("reply_to_external_message_id") or "").strip() or None
    if not remote_jid or not text or not idempotency_key:
        raise ValueError("Approved personal-channel dispatch requires remote_jid, text, and idempotency_key.")
    outbound, _ = personal_channels_repository.create_or_get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=idempotency_key,
        remote_jid=remote_jid,
        text=text,
        reply_to_external_message_id=reply_to_external_message_id,
        metadata={
            "source": str(arguments.get("source") or "approved_personal_channel_send").strip(),
            "approval_run_id": run_id,
            "approval_trace_id": trace_id,
            "empyralis_approved": bool(empyralis_approved),
        },
    )
    if str(outbound.get("status") or "").strip() == "delivered":
        return {"status": "completed", "outbound": outbound, "gateway_id": str(gateway_id or "").strip(), "channel_key": channel_key}
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        provider=provider,
        remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
        text=str(outbound.get("text") or text).strip(),
        idempotency_key=idempotency_key,
        reply_to_external_message_id=str(outbound.get("reply_to_external_message_id") or "").strip() or reply_to_external_message_id,
        timeout_seconds=timeout_seconds,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=idempotency_key,
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result, "approval_run_id": run_id, "approval_trace_id": trace_id},
    )
    return {
        "status": "completed",
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": channel_key,
        "outbound": delivered or outbound,
        "dispatch_result": dispatch_result,
    }


async def send_telegram_personal_message(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    remote_jid: str,
    text: str,
    idempotency_key: str,
    reply_to_external_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        TELEGRAM_PERSONAL_CHANNEL_KEY,
        TELEGRAM_PERSONAL_PROVIDER,
    )
    outbound, _ = personal_channels_repository.create_or_get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        idempotency_key=str(idempotency_key or "").strip(),
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
        metadata={"source": "manual_api"},
    )
    if str(outbound.get("status") or "").strip() == "delivered":
        return outbound

    _enforce_personal_channel_dispatch_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id="channel.telegram.personal.send",
        request_id=str(idempotency_key or "").strip(),
    )
    dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        provider=TELEGRAM_PERSONAL_PROVIDER,
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        idempotency_key=str(idempotency_key or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        idempotency_key=str(idempotency_key or "").strip(),
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    return delivered or outbound


def get_telegram_gateway_view(gateway_id: str) -> Dict[str, Any]:
    state = personal_channels_repository.get_telegram_state(
        str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
    )
    recent = personal_channels_repository.list_recent_gateway_messages(
        str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
    )
    return {
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": TELEGRAM_PERSONAL_CHANNEL_KEY,
        "state": state,
        "recent_messages": recent,
    }


def _platform_telegram_single_api_id() -> Optional[int]:
    for name in ("EMPYRALIS_TELEGRAM_API_ID", "TELEGRAM_API_ID"):
        raw = str(os.getenv(name) or "").strip()
        if not raw:
            continue
        try:
            parsed = int(raw)
        except ValueError:
            continue
        if parsed > 0:
            return parsed
    return None


def _platform_telegram_single_api_hash() -> Optional[str]:
    for name in ("EMPYRALIS_TELEGRAM_API_HASH", "TELEGRAM_API_HASH"):
        raw = str(os.getenv(name) or "").strip()
        if raw:
            return raw
    return None


def _platform_telegram_credential_pool() -> List[Tuple[int, str]]:
    """Small pool of platform-level Telegram api_id/api_hash pairs, indexed
    EMPYRALIS_TELEGRAM_API_ID_1/EMPYRALIS_TELEGRAM_API_HASH_1, _2, _3, ...
    A single shared credential means Telegram's anti-abuse systems flagging
    or rate-limiting it breaks onboarding for every workspace at once; a
    pool bounds that blast radius to whichever slice of workspaces hash to
    the affected member. Falls back to the single un-indexed
    EMPYRALIS_TELEGRAM_API_ID/_API_HASH as a pool of size 1 if no indexed
    pool is configured, so existing single-credential deployments keep
    working unchanged."""
    pool: List[Tuple[int, str]] = []
    index = 1
    while True:
        raw_id = str(os.getenv(f"EMPYRALIS_TELEGRAM_API_ID_{index}") or "").strip()
        raw_hash = str(os.getenv(f"EMPYRALIS_TELEGRAM_API_HASH_{index}") or "").strip()
        if not raw_id and not raw_hash:
            break
        try:
            parsed_id = int(raw_id)
        except ValueError:
            parsed_id = 0
        if parsed_id > 0 and raw_hash:
            pool.append((parsed_id, raw_hash))
        index += 1
    if pool:
        return pool
    single_id = _platform_telegram_single_api_id()
    single_hash = _platform_telegram_single_api_hash()
    if single_id is not None and single_hash:
        return [(single_id, single_hash)]
    return []


def _platform_telegram_credentials_for_workspace(workspace_id: str) -> Tuple[Optional[int], Optional[str]]:
    pool = _platform_telegram_credential_pool()
    if not pool:
        return None, None
    normalized = str(workspace_id or "").strip() or "default"
    # A stable hash (not Python's built-in hash(), which is randomized per
    # process via PYTHONHASHSEED) so the same workspace always resolves to
    # the same pool member across restarts and re-pairing attempts.
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    selected = pool[int(digest, 16) % len(pool)]
    return selected


async def configure_telegram_personal_gateway(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    api_id: Optional[int] = None,
    api_hash: Optional[str] = None,
    phone_number: Optional[str] = None,
    login_code: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        TELEGRAM_PERSONAL_CHANNEL_KEY,
        TELEGRAM_PERSONAL_PROVIDER,
    )
    pool_api_id, pool_api_hash = _platform_telegram_credentials_for_workspace(
        str(registration.get("workspace_id") or "").strip()
    )
    resolved_api_id = api_id if api_id is not None else pool_api_id
    resolved_api_hash = str(api_hash or "").strip() or pool_api_hash
    arguments: Dict[str, Any] = {}
    if resolved_api_id is not None:
        arguments["api_id"] = int(resolved_api_id)
    if str(resolved_api_hash or "").strip():
        arguments["api_hash"] = str(resolved_api_hash).strip()
    if str(phone_number or "").strip():
        arguments["phone_number"] = str(phone_number).strip()
    if str(login_code or "").strip():
        arguments["login_code"] = str(login_code).strip()
    if str(password or "").strip():
        arguments["password"] = str(password).strip()
    if not arguments:
        raise ValueError("At least one Telegram personal setup field is required.")
    run_id = f"gateway-telegram-setup-{uuid4().hex[:12]}"
    trace_id = f"gateway-telegram-setup-{uuid4().hex[:12]}"
    _enforce_personal_gateway_config_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=TELEGRAM_PERSONAL_CONFIGURE_CAPABILITY,
        run_id=run_id,
        trace_id=trace_id,
    )
    execution = await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=str(gateway_id or "").strip(),
        capability_id=TELEGRAM_PERSONAL_CONFIGURE_CAPABILITY,
        arguments=arguments,
        run_id=run_id,
        trace_id=trace_id,
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        agent_scope="sage",
    )
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": TELEGRAM_PERSONAL_CHANNEL_KEY,
        **result,
    }


async def disconnect_telegram_personal_gateway(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
) -> Dict[str, Any]:
    """Full reset — see disconnect_whatsapp_personal_gateway()'s doc comment
    and TelegramPersonalRuntime.handleDisconnect() for why this exists."""
    channel_lane_contract_service.assert_personal_gateway_channel(
        TELEGRAM_PERSONAL_CHANNEL_KEY,
        TELEGRAM_PERSONAL_PROVIDER,
    )
    run_id = f"gateway-telegram-disconnect-{uuid4().hex[:12]}"
    trace_id = f"gateway-telegram-disconnect-{uuid4().hex[:12]}"
    _enforce_personal_gateway_config_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=TELEGRAM_PERSONAL_DISCONNECT_CAPABILITY,
        run_id=run_id,
        trace_id=trace_id,
    )
    execution = await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=str(gateway_id or "").strip(),
        capability_id=TELEGRAM_PERSONAL_DISCONNECT_CAPABILITY,
        arguments={},
        run_id=run_id,
        trace_id=trace_id,
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        agent_scope="sage",
    )
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": TELEGRAM_PERSONAL_CHANNEL_KEY,
        **result,
    }

# ── Stage 2: Cloud Session Manager integration ──────────────────

import os
import hashlib
import hmac as _hmac_module

_CLOUD_SESSION_MANAGER_URL = os.getenv("CLOUD_SESSION_MANAGER_URL", "http://localhost:3400")
_CLOUD_SESSION_HMAC_SECRET = os.getenv("CLOUD_SESSION_HMAC_SECRET", os.getenv("API_SECRET", "dev-secret-change-me"))
_CLOUD_SESSION_MANAGER_ENABLED = os.getenv("CLOUD_SESSION_MANAGER_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


async def handle_cloud_channel_inbound(
    *,
    session_id: str,
    channel_key: str,
    message: Dict[str, Any],
    workspace_id: str = "default",
) -> Dict[str, Any]:
    """Handle inbound message from cloud session manager (Stage 2).

    Follows the same pattern as _handle_telegram_gateway_channel_inbound
    but dispatches replies via HTTP to the cloud session manager instead of
    through the Gateway WebSocket.

    Args:
        session_id: cloud session manager session ID
        channel_key: "telegram_personal" or "whatsapp_personal"
        message: {external_message_id, sender_id, sender_name, text, received_at}
        workspace_id: workspace UUID from cloud session (defaults to "default" for backward compat)
    """
    if not _CLOUD_SESSION_MANAGER_ENABLED:
        return {"status": "disabled", "reason": "CLOUD_SESSION_MANAGER_ENABLED is false"}

    resolved_workspace_id = str(workspace_id or "default").strip() or "default"

    # Validate workspace_id exists before processing
    if resolved_workspace_id and resolved_workspace_id != "default":
        try:
            from server_modules.control_plane_repository import get_workspace_by_id
            _ws_check = await get_workspace_by_id(resolved_workspace_id)
            if not _ws_check:
                _csm_logger = __import__('logging').getLogger(__name__)
                _csm_logger.error(
                    "cloud_channel_inbound: invalid workspace_id=%s — message dropped",
                    resolved_workspace_id,
                )
                return {"status": "invalid_workspace", "workspace_id": resolved_workspace_id}
        except Exception:
            pass

    external_message_id = str(message.get("external_message_id") or "").strip()
    remote_jid = str(message.get("sender_id") or "").strip()
    text = str(message.get("text") or "").strip()
    push_name = str(message.get("sender_name") or "").strip() or None
    linked_username = str(message.get("linked_username") or "").strip() or None

    if not external_message_id or not text:
        raise ValueError("cloud_channel_inbound requires external_message_id and text")
    if not remote_jid:
        # sender_id (remote_jid) feeds straight into the Sage bridge's
        # channel_origin+sender_id sender-classification (mandate hardening
        # report). A missing sender_id there isn't "unclassifiable" — it's
        # silently read as no live sender at all, which defaults to owner
        # tier. Reject rather than let a malformed relay payload buy owner
        # authority.
        raise ValueError("cloud_channel_inbound requires a non-empty sender_id")

    # ── Shared command dispatcher ──
    from server_modules.sage_command_dispatcher import dispatch_command as _dispatch_cmd
    _cmd_reply = await _dispatch_cmd(
        command=text,
        workspace_id=resolved_workspace_id,
        thread_id="sage-main",
        channel_origin="telegram_personal",
        sender_id=remote_jid or None,
    )
    if _cmd_reply is not None:
        await dispatch_cloud_channel_outbound(
            session_id=session_id,
            text=_cmd_reply,
            remote_jid=remote_jid,
        )
        return {"status": "command_handled", "session_id": session_id, "reply_text": _cmd_reply[:200]}

    # Build Sage reply using the existing bridge — same as Gateway path
    reply = await personal_channel_sage_bridge_service.build_telegram_personal_reply_async(
        workspace_id=resolved_workspace_id,
        gateway_id=f"cloud:{session_id}",
        remote_jid=remote_jid,
        text=text,
        push_name=push_name,
        source_event_id=external_message_id,
    )

    if not reply or not str(reply.get("text") or "").strip():
        return {
            "status": "no_reply",
            "session_id": session_id,
            "external_message_id": external_message_id,
        }

    # Dispatch the reply to the cloud session manager
    reply_text = str(reply.get("text") or "").strip()
    dispatch_result = await dispatch_cloud_channel_outbound(
        session_id=session_id,
        text=reply_text,
        remote_jid=remote_jid,
    )

    return {
        "status": "replied",
        "session_id": session_id,
        "external_message_id": external_message_id,
        "reply_text": reply_text[:200],
        "dispatch": dispatch_result,
    }


async def dispatch_cloud_channel_outbound(
    *,
    session_id: str,
    text: str,
    remote_jid: str = "",
) -> Dict[str, Any]:
    """Send an outbound reply to the cloud session manager via HTTP (Stage 2).

    The cloud session manager delivers the message through its GramJS client.
    Request is HMAC-signed for authentication.
    """
    if not _CLOUD_SESSION_MANAGER_ENABLED:
        return {"ok": False, "error": "CLOUD_SESSION_MANAGER_ENABLED is false"}

    import httpx
    import json as _json

    if not session_id or not text or not text.strip():
        return {"ok": False, "error": "session_id and text are required"}

    url = f"{_CLOUD_SESSION_MANAGER_URL}/sessions/{session_id}/inbound"

    payload = {
        "text": text.strip(),
        "remote_jid": remote_jid or "me",
    }

    # HMAC sign the request
    signature = _hmac_module.new(
        _CLOUD_SESSION_HMAC_SECRET.encode("utf-8"),
        _json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Signature": signature,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            body = response.json() if response.text else {}
            if response.status_code >= 400:
                return {"ok": False, "status": response.status_code, "error": body.get("error", "dispatch_failed")}
            return {"ok": True, "status": response.status_code, "message_id": body.get("message_id")}
    except Exception as exc:
        return {"ok": False, "error": f"dispatch_error: {exc}"}
def resolve_cloud_telegram_session_id() -> Optional[str]:
    """Resolve the first connected cloud Telegram session ID.

    Queries the cloud-session-manager for all sessions and returns
    the ID of the first connected session. Used by heartbeat notify
    to deliver proactive messages via cloud-session-manager.

    Returns None if no connected session exists or cloud-session-manager
    is unreachable.
    """
    if not _CLOUD_SESSION_MANAGER_ENABLED:
        return None
    try:
        import httpx
        url = f"{_CLOUD_SESSION_MANAGER_URL}/sessions"
        response = httpx.get(url, timeout=5.0)
        if response.status_code >= 400:
            return None
        body = response.json() if response.text else {}
        sessions = body.get("sessions") if isinstance(body, dict) else []
        for session in sessions:
            if isinstance(session, dict) and session.get("status") == "connected":
                return str(session.get("sessionId") or "").strip() or None
        return None
    except Exception:
        return None

