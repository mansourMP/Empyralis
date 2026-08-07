from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from server_modules import (
    channel_blocking_policy_service,
    channel_lane_contract_service,
    gateway_state_repository,
    kill_switch_gate,
    mention_gating_service,
    personal_channel_sage_bridge_service,
    personal_channels_repository,
    rust_runtime_kernel_client,
    secret_redaction_service,
    security_audit_service,
)
from server_modules.channel_adapter import filter_channel_outbound_reply

_logger = logging.getLogger(__name__)


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

# iMessage (imsg) — unlike WhatsApp/Telegram there is no phone/code/QR step;
# these two capabilities are the in-app setup panel's only real actions,
# both dispatched live to ImsgIMessagePersonalChannelRuntime on the gateway
# (empyralis-gateway/src/channels/imsg-imessage-runtime.ts) via the same
# generic tool-invoke RPC WhatsApp/Telegram's configure/disconnect already
# use — see recheck_imessage_personal_gateway()/install_imessage_imsg_gateway()
# below. Provider stays "bluebubbles_local_bridge" — see imsg-imessage-
# runtime.ts's own doc comment on why that string wasn't renamed.
IMESSAGE_PERSONAL_CHANNEL_KEY = "imessage_personal"
IMESSAGE_PERSONAL_PROVIDER = channel_lane_contract_service.assert_personal_gateway_channel(
    IMESSAGE_PERSONAL_CHANNEL_KEY
)["provider"]
IMESSAGE_PERSONAL_RECHECK_CAPABILITY = "channel.imessage.personal.recheck"
IMESSAGE_PERSONAL_INSTALL_CAPABILITY = "channel.imessage.personal.install"

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
        "next_step": "Connect an Agent Computer with a Signal bridge to enable agent messaging.",
    },
    "imessage_personal": {
        "status_label": "Mac bridge required",
        "detail": "iMessage runs through a user-owned Mac Agent Computer bridge.",
        "next_step": "Connect a Mac Agent Computer with an iMessage bridge to enable agent messaging.",
    },
    "wechat_personal": {
        "status_label": "Bridge required",
        "detail": "WeChat personal runs through an Agent Computer local bridge.",
        "next_step": "Connect an Agent Computer with a WeChat bridge to enable agent messaging.",
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
        media: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return await send_whatsapp_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            remote_jid=remote_jid,
            text=text,
            idempotency_key=idempotency_key,
            reply_to_external_message_id=reply_to_external_message_id,
            media=media,
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
        media: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        return await send_telegram_personal_message(
            gateway_id=gateway_id,
            registration=registration,
            remote_jid=remote_jid,
            text=text,
            idempotency_key=idempotency_key,
            reply_to_external_message_id=reply_to_external_message_id,
            media=media,
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
        media: Optional[List[Dict[str, Any]]] = None,
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
            media=media,
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


# ── dmPolicy: sender allowlist / pairing enforcement ──────────────────
#
# The channel manifests (empyralis-gateway's PersonalChannelCapabilityManifest)
# declare `safety: {ownerPairingRequired: true, allowlistRequired: ...}` for
# every personal channel. Until this section existed that was a false claim:
# nothing server-side ever checked WHO was messaging before generating a
# reply — any contact who texted the owner's own WhatsApp/Telegram number
# got an automatic reply, indistinguishable from the owner themselves. This
# is the real gate. See docs/PLATFORM-MAP.md for the write-up.
#
# Modelled on OpenClaw's dmPolicy (extensions/signal/src/monitor/access-policy.ts,
# extensions/slack/src/monitor/dm-auth.ts, src/channels/message-access/sender-gates.ts):
# a per-channel policy of open | allowlist | pairing (+ a hard "disabled"
# there we don't need, since dropping the whole channel is a separate
# on/off switch already). We add owner_only as Empyralis's OWN strictest
# mode and make it the default — OpenClaw's channels don't have a concept
# of "the DM channel IS one specific person's own account", personal
# channels here do.

DM_POLICY_OWNER_ONLY = "owner_only"
DM_POLICY_ALLOWLIST = "allowlist"
DM_POLICY_PAIRING = "pairing"
DM_POLICY_OPEN = "open"
DM_POLICY_MODES = {DM_POLICY_OWNER_ONLY, DM_POLICY_ALLOWLIST, DM_POLICY_PAIRING, DM_POLICY_OPEN}
# Default OPEN (reply to everyone) to preserve existing live-agent behavior and
# NOT retroactively silence already-configured personal-channel agents; the
# channel manifest now honestly reflects the enforced mode. The owner can
# restrict any agent to owner_only / allowlist / pairing per channel. The strict
# default (owner_only) is a product decision deferred to the owner, who can test
# it on a live channel — flipping this constant is the one-line change to adopt it.
#
# SCOPE OF THIS CONSTANT — read before touching it. It governs ONE thing only:
# the mode a REAL, resolved agent install falls back to when it has never
# explicitly saved a dm_policy for this channel (_normalize_dm_policy_config's
# "mode not in DM_POLICY_MODES" branch below). It must never be read by, or
# substituted into, the SEPARATE fallback for when no real agent identity
# could be resolved AT ALL — see _unresolved_identity_dm_policy_config, always
# owner_only, hardcoded, independent of this constant.
#
# History of why that distinction now exists as two separate functions:
# ed9c2cdd60 added a safe owner_only default (one constant, both fallbacks).
# ee3fca4f7c flipped THIS constant to open as a deliberate live-agent-compat
# product decision — but because both fallbacks still shared the one
# constant at the time, that single-line change also silently reopened the
# identity-less fallback (agent_id=="" — the PERMANENT case for local-bridge
# Signal/iMessage/WeChat inbound, which has no per-agent identity table to
# resolve against at all) to "reply to any stranger," directly contradicting
# _load_agent_dm_policy_config's own docstring and the "every sender is
# blocked" comment at its local-bridge call site. Splitting the two fallbacks
# means a future change to this constant can never do that again.
DEFAULT_DM_POLICY_MODE = DM_POLICY_OPEN


def _unresolved_identity_dm_policy_config() -> Dict[str, Any]:
    """Fail-closed fallback for _load_agent_dm_policy_config when NO real
    per-agent install could even be identified: no agent_id resolved at all
    (personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID). Until this
    build this was the permanent case for every local-bridge channel
    (Signal/iMessage/WeChat), which had no per-agent state table to resolve
    an owner identity against at all. It no longer is —
    _resolve_local_bridge_agent_id now resolves a real agent_id for these
    three the same way WhatsApp/Telegram always have — but this fallback
    stays reachable whenever that resolution genuinely can't land (an
    ambiguous or not-yet-claimed preferred_gateway_id, or an install lookup
    that failed / returned nothing); see that function's own docstring for
    the full contract.

    Deliberately NOT DEFAULT_DM_POLICY_MODE: that constant is the
    configured-but-unset default for a REAL agent an owner can actually go
    open Settings and change the policy for (see its own comment). There is
    no agent to apply a per-agent policy to here, so there is no "the owner
    already saw this and can adjust it" story to justify anything looser
    than the strictest mode. Always owner_only, hardcoded, independent of
    DEFAULT_DM_POLICY_MODE, so a future change to that constant (a live
    product decision another engineer is entitled to make) can never
    silently reopen this identity-less fallback too — see
    DEFAULT_DM_POLICY_MODE's own comment for exactly that having already
    happened once (ee3fca4f7c)."""
    return {"mode": DM_POLICY_OWNER_ONLY, "allowlist": [], "pending_pairing": {}}


def _normalize_dm_policy_config(raw: Any) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in DM_POLICY_MODES:
        mode = DEFAULT_DM_POLICY_MODE
    allowlist = sorted({str(x).strip() for x in (data.get("allowlist") or []) if str(x or "").strip()})
    pending_raw = data.get("pending_pairing") if isinstance(data.get("pending_pairing"), dict) else {}
    pending = {
        str(sender_id): dict(entry)
        for sender_id, entry in pending_raw.items()
        if str(sender_id or "").strip() and isinstance(entry, dict)
    }
    return {"mode": mode, "allowlist": allowlist, "pending_pairing": pending}


async def _load_agent_dm_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
) -> Dict[str, Any]:
    """Read-only, safe-by-default. Persisted at
    install_metadata.dm_policy[channel_key] on the agent's own
    workspace_agent_installs row (the same "config lives in install
    metadata" convention triage_service.resolve_triage_config uses for
    its own per-agent config).

    Any lookup failure, missing install, or an unresolved agent_id
    (personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID — see
    _resolve_agent_id_for_inbound's docstring for when that happens)
    returns owner_only with an empty allowlist. This NEVER fails open to
    "open" or "allowlist" just because the config lookup itself failed —
    only an explicit, successfully-loaded config can relax the default.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return _unresolved_identity_dm_policy_config()
    try:
        from server_modules import agent_registry_repository as _repo

        install = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id,
            tenant_id=str(tenant_id or "default").strip() or "default",
            workspace_id=str(workspace_id or "default").strip() or "default",
        )
    except Exception:
        _logger.warning("dm_policy: install lookup failed for agent_id=%s — defaulting to owner_only", normalized_agent_id, exc_info=True)
        return _unresolved_identity_dm_policy_config()
    if not isinstance(install, dict):
        return _unresolved_identity_dm_policy_config()
    meta = dict(install.get("install_metadata") or install.get("metadata") or {})
    all_policies = meta.get("dm_policy") if isinstance(meta.get("dm_policy"), dict) else {}
    return _normalize_dm_policy_config(all_policies.get(channel_key))


async def _persist_agent_dm_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    config: Dict[str, Any],
) -> bool:
    """Write path for a future settings API / owner-approval command to call.
    Best-effort: returns False (never raises) on any failure — a persistence
    failure must never surface as a 500 in the middle of message handling.
    update_workspace_agent_install merges `metadata` shallowly at the TOP
    level only, so we read-modify-write the WHOLE dm_policy dict (all
    channels), not just this channel_key's entry, or a concurrent update
    to a sibling channel's policy could get clobbered."""
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return False
    resolved_tenant_id = str(tenant_id or "default").strip() or "default"
    resolved_workspace_id = str(workspace_id or "default").strip() or "default"
    try:
        from server_modules import agent_registry_repository as _repo

        install = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id, tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id,
        )
        if not isinstance(install, dict):
            return False
        meta = dict(install.get("install_metadata") or install.get("metadata") or {})
        all_policies = dict(meta.get("dm_policy")) if isinstance(meta.get("dm_policy"), dict) else {}
        all_policies[channel_key] = _normalize_dm_policy_config(config)
        updated = await _repo.update_workspace_agent_install(
            normalized_agent_id,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            metadata={"dm_policy": all_policies},
        )
        return updated is not None
    except Exception:
        _logger.warning("dm_policy: persist failed for agent_id=%s channel=%s", normalized_agent_id, channel_key, exc_info=True)
        return False


async def approve_dm_policy_pairing_request(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    sender_id: Optional[str] = None,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    """Owner-approval primitive: move a pending pairing request into the
    allowlist. Matches by sender_id OR by the one-time pairing code
    (whichever a future approval route/owner-facing command has on hand).
    Not yet wired to a route — this is the service-layer primitive for
    that; see this build's report for the follow-up.

    Returns {"approved": bool, "sender_id": Optional[str], "reason": Optional[str]}.
    """
    config = await _load_agent_dm_policy_config(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key,
    )
    pending = config["pending_pairing"]
    target_sender_id = str(sender_id or "").strip()
    if not target_sender_id and str(code or "").strip():
        normalized_code = str(code or "").strip()
        for candidate_id, entry in pending.items():
            if str(entry.get("code") or "").strip() == normalized_code:
                target_sender_id = candidate_id
                break
    if not target_sender_id or target_sender_id not in pending:
        return {"approved": False, "sender_id": target_sender_id or None, "reason": "pairing_request_not_found"}
    pending = dict(pending)
    pending.pop(target_sender_id, None)
    config["pending_pairing"] = pending
    if target_sender_id not in config["allowlist"]:
        config["allowlist"] = sorted(set(config["allowlist"]) | {target_sender_id})
    persisted = await _persist_agent_dm_policy_config(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key, config=config,
    )
    if not persisted:
        return {"approved": False, "sender_id": target_sender_id, "reason": "persist_failed"}
    return {"approved": True, "sender_id": target_sender_id, "reason": None}


def _channel_owner_linked_id(*, channel_key: str, state: Optional[Dict[str, Any]]) -> str:
    """The owner's own identity on this channel, as last established by a
    real login/connect event — see _resolve_linked_identity_for_sync's
    docstring for why this is NEVER derived from an inbound message's own
    sender fields."""
    if not isinstance(state, dict):
        return ""
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        return str(state.get("linked_jid") or "").strip()
    if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        return str(state.get("linked_user_id") or "").strip()
    return ""


def _resolve_linked_identity_for_sync(*, current_value: Optional[str], preserved_value: str) -> Optional[str]:
    """What to write for linked_jid/linked_user_id on a PER-MESSAGE state
    sync. `current_value` is the signal carried on THIS message (only ever
    non-empty for a genuine owner/self-chat event — callers must not pass
    an arbitrary contact's sender_jid here). `preserved_value` is whatever
    is already persisted.

    Why this function exists: personal_channels_repository.upsert_whatsapp_state
    / upsert_telegram_state overwrite linked_jid/linked_user_id UNCONDITIONALLY
    on every call (no COALESCE — unlike their own `metadata` column, which
    IS merged). The per-message sync in _handle_whatsapp_gateway_channel_inbound
    used to pass message.get("sender_jid") directly — which for a plain 1:1
    DM equals remote_jid, i.e. the CONTACT's own jid, not the owner's. That
    silently clobbered the real owner identity (set once at login) with
    whichever stranger most recently texted the owner, on every single
    inbound message. Fixed here: only a genuine owner signal overwrites the
    persisted identity; everything else preserves it unchanged.
    """
    resolved_current = str(current_value or "").strip()
    if resolved_current:
        return resolved_current
    resolved_preserved = str(preserved_value or "").strip()
    return resolved_preserved or None


def _dm_policy_sender_id(message: Dict[str, Any], *, remote_jid: str) -> str:
    """The identity dmPolicy authorizes against: the specific participant
    who sent the message (sender_jid — meaningful inside a WhatsApp group,
    where it differs from the group's own remote_jid) if present, else the
    chat/contact id (remote_jid — the normal 1:1 DM case, where sender_jid
    already equals remote_jid anyway)."""
    return str(message.get("sender_jid") or "").strip() or str(remote_jid or "").strip()


def _is_owner_message(
    *,
    channel_key: str,
    message: Dict[str, Any],
    sender_id: str,
    existing_state: Optional[Dict[str, Any]],
) -> bool:
    """True when this inbound message is from the OWNER's own identity:
    WhatsApp/Telegram/Signal self-chat (message.is_self_chat — computed
    gateway-side from the live connection's own account id, see
    empyralis-gateway/src/channels/whatsapp/message-mapper.ts for WhatsApp
    and empyralis-gateway/src/bridges/signal-cli-bridge.ts's
    mapSignalCliReceiveNotification for Signal's "Note to Self" equivalent),
    or a sender that matches this channel's previously-established linked
    owner identity (also correctly identifies the owner posting inside a
    WhatsApp group they're a member of, since a person's JID is the same in
    every context).

    Reuses triage_service.resolve_sender_identity for the actual identity
    resolution rather than re-implementing it — see that function's
    docstring. channel_bindings is built fresh here from the (clobber-fixed)
    linked identity rather than routing through triage's audience-registry
    concept, which models a different question (customer/audience senders
    on an audience-facing channel, not "is this sender the channel's own
    owner").
    """
    if bool(message.get("is_self_chat")):
        return True
    linked = _channel_owner_linked_id(channel_key=channel_key, state=existing_state)
    if not linked:
        return False
    from server_modules.triage_service import resolve_sender_identity

    identity = resolve_sender_identity(
        sender_id=sender_id,
        channel_origin=channel_key,
        channel_bindings=[{"channel_type": channel_key, "linked_user_id": linked}],
    )
    return identity == "owner"


def _generate_pairing_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _pairing_challenge_text(*, label: str) -> str:
    return (
        f"This {label} number is a private assistant line. It only replies to its "
        "owner and to contacts the owner has approved. The owner has been notified "
        "that you messaged, and can approve you to enable replies."
    )


async def _enforce_dm_policy(
    *,
    registration: Dict[str, Any],
    channel_key: str,
    agent_id: str,
    message: Dict[str, Any],
    remote_jid: str,
    existing_state: Optional[Dict[str, Any]],
    label: str,
) -> Dict[str, Any]:
    """THE gate. Must run before any reply — including a control-command
    reply — is generated for an inbound personal-channel message. Returns::

        {"allowed": bool, "mode": str, "sender_id": str, "is_owner": bool,
         "system_reply": Optional[str], "config_changed": bool}

    `system_reply`, when present, is a FIXED string (never LLM-generated)
    the caller should dispatch directly and then stop — a pairing challenge
    must never reach the model.
    """
    sender_id = _dm_policy_sender_id(message, remote_jid=remote_jid)
    is_owner = _is_owner_message(
        channel_key=channel_key, message=message, sender_id=sender_id, existing_state=existing_state,
    )
    if is_owner:
        return {
            "allowed": True, "mode": "owner", "sender_id": sender_id, "is_owner": True,
            "system_reply": None, "config_changed": False,
        }

    tenant_id = str(registration.get("tenant_id") or "default").strip() or "default"
    workspace_id = str(registration.get("workspace_id") or "default").strip() or "default"
    config = await _load_agent_dm_policy_config(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key,
    )
    mode = config["mode"]

    if mode == DM_POLICY_OPEN:
        return {
            "allowed": True, "mode": mode, "sender_id": sender_id, "is_owner": False,
            "system_reply": None, "config_changed": False,
        }

    if sender_id and sender_id in config["allowlist"]:
        return {
            "allowed": True, "mode": mode, "sender_id": sender_id, "is_owner": False,
            "system_reply": None, "config_changed": False,
        }

    if mode == DM_POLICY_PAIRING and sender_id:
        pending = dict(config["pending_pairing"])
        if sender_id in pending:
            # Already challenged once — stay silent on repeats (mirrors
            # OpenClaw's issuePairingChallenge: no reply when !created).
            return {
                "allowed": False, "mode": mode, "sender_id": sender_id, "is_owner": False,
                "system_reply": None, "config_changed": False,
            }
        pending[sender_id] = {
            "code": _generate_pairing_code(),
            "sender_name": str(message.get("push_name") or "").strip() or None,
            "requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        config["pending_pairing"] = pending
        persisted = await _persist_agent_dm_policy_config(
            tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key, config=config,
        )
        # Only send the challenge once we know it's actually recorded — an
        # unrecorded challenge would re-trigger (and re-message the
        # stranger) on every retry instead of going silent like a recorded
        # one does.
        reply = _pairing_challenge_text(label=label) if persisted else None
        return {
            "allowed": False, "mode": mode, "sender_id": sender_id, "is_owner": False,
            "system_reply": reply, "config_changed": persisted,
        }

    # owner_only (default) and allowlist-with-no-match both land here:
    # blocked, silent — no system reply, matching "record but do not reply."
    return {
        "allowed": False, "mode": mode, "sender_id": sender_id, "is_owner": False,
        "system_reply": None, "config_changed": False,
    }


async def _handle_dm_policy_blocked(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    inbound: Dict[str, Any],
    channel_key: str,
    provider: str,
    agent_id: str,
    external_message_id: str,
    remote_jid: str,
    duplicate: bool,
    decision: Dict[str, Any],
    trace_id: str = "",
) -> Dict[str, Any]:
    """Record the drop (and, for `pairing` mode's first contact, dispatch
    the one-time system challenge) — but never hand the message to the
    model, and never run the normal automatic-reply path."""
    no_reply_idempotency_key = f"{channel_key}:dmpolicy:{decision.get('mode')}:{external_message_id}"
    refreshed_inbound = personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        agent_id=agent_id,
        external_message_id=external_message_id,
        reply_idempotency_key=no_reply_idempotency_key,
    )
    system_reply = str(decision.get("system_reply") or "").strip()
    status = "pairing_challenge" if system_reply else "blocked"
    sender_id = str(decision.get("sender_id") or "")
    _emit_automatic_reply_audit(
        action=f"personal_channel.{channel_key.split('_', 1)[0]}.dm_policy",
        status=status,
        registration=registration,
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        detail=f"Inbound personal-channel message dropped by dmPolicy (mode={decision.get('mode')}).",
        metadata={
            "remote_jid": remote_jid,
            "inbound_external_message_id": external_message_id,
            "dm_policy_mode": decision.get("mode"),
            "sender_id_hash": hashlib.sha256(sender_id.encode("utf-8")).hexdigest()[:16] if sender_id else None,
        },
        trace_id=trace_id,
        idempotency_key=f"personal_channel.dm_policy.{status}:{gateway_id}:{channel_key}:{external_message_id}",
    )
    outbound: Optional[Dict[str, Any]] = None
    if system_reply:
        pairing_idempotency_key = f"{channel_key}:dmpolicy_pairing:{sender_id}"
        outbound, outbound_created = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=channel_key,
            agent_id=agent_id,
            idempotency_key=pairing_idempotency_key,
            remote_jid=remote_jid,
            text=system_reply,
            reply_to_external_message_id=external_message_id,
            metadata={"reply_source": "dm_policy_pairing_challenge"},
        )
        if outbound_created and str(outbound.get("status") or "").strip() != "delivered":
            try:
                _enforce_personal_channel_dispatch_decision(
                    gateway_id=str(gateway_id or "").strip(),
                    registration=registration,
                    capability_id=f"{channel_key}.send",
                    request_id=pairing_idempotency_key,
                )
                dispatch_result = await gateway_protocol_service.dispatch_channel_outbound(
                    gateway_id=str(gateway_id or "").strip(),
                    channel_key=channel_key,
                    provider=provider,
                    remote_jid=str(outbound.get("remote_jid") or remote_jid).strip(),
                    text=system_reply,
                    idempotency_key=pairing_idempotency_key,
                    reply_to_external_message_id=None,
                )
                delivered = personal_channels_repository.mark_outbound_delivered(
                    gateway_id=str(gateway_id or "").strip(),
                    channel_key=channel_key,
                    agent_id=agent_id,
                    idempotency_key=pairing_idempotency_key,
                    external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
                    metadata={"dispatch_result": dispatch_result},
                )
                outbound = delivered or outbound
            except Exception:
                _logger.warning(
                    "dm_policy: failed to dispatch pairing challenge channel=%s gateway=%s",
                    channel_key, gateway_id, exc_info=True,
                )
    return {
        "duplicate": duplicate,
        "inbound": refreshed_inbound or inbound,
        "outbound": outbound,
        "blocked": True,
        "policy": {"gate": "dm_policy", **decision},
    }


# ── groupPolicy: which groups the agent is even active in, + mention-gating ──
#
# TWO ORTHOGONAL AXES, both owner-configurable, neither ever an AI runtime
# decision:
#
#   1. group_policy (this section's storage, mirroring dm_policy's exact
#      install_metadata.<key>[channel_key] shape — see
#      _load_agent_dm_policy_config/_persist_agent_dm_policy_config above,
#      whose pattern this copies): open | allowlist | disabled. Is this
#      SPECIFIC group (keyed on the group's own chat id, not a sender id)
#      allowed to have the agent active at all. Parallels OpenClaw's
#      groupPolicy (dist/runtime-group-policy-BEjP88cf.js — see
#      docs/OpenClaw.md's GROUP/MENTION GATING section).
#   2. requireMention (also stored here, per-group-policy-config) — whether
#      an unaddressed message in an allowed group should be skipped.
#      Delegated to mention_gating_service.resolve_inbound_mention_decision,
#      the ONE shared resolver mirroring OpenClaw's
#      resolveInboundMentionDecision formula.
#
# DEFAULTS — read this before ever touching either constant below:
#
# Two standing rulings governed the MENTION-GATING axis specifically, and
# were in tension on their face:
#   Ruling A (2026-07-16): "Groups = see-and-decide, NOT mention-gated. The
#   agent should SEE every group message and decide to reply or stay silent
#   by its own judgment ... Do NOT build rigid gates."
#   Ruling B (2026-07-23): no filter may flag-and-withhold a message from
#   the reasoning model on CONTENT grounds; every message reaches the model
#   unconditionally (see sage_turn_adapter.py's removed Phase-P gate).
#
# The ACTUAL code shipped between those two dates (0fe9ada19 2026-07-18,
# c8b8fbed0 2026-07-19) added a hard, non-configurable gate to Telegram and
# the local-bridge channels (mirroring WhatsApp's pre-existing one): an
# unaddressed group message is silently dropped BEFORE ever reaching the
# model (see test_personal_channel_group_gate.py's pre-existing
# test_unaddressed_group_message_is_ignored_and_never_dispatched, which
# encodes exactly that as "today's" behavior). That gate is real, was
# deliberately built to fix a live "family group" spam incident, and is
# what a naive "preserve exact current behavior" reading of this section
# would keep as the default.
#
# The founder's resolution (2026-07-23) restored Ruling A as the default:
# Ruling A is explicitly "still in force" and the 2026-07-18/19 gate was a
# rigid gate of exactly the kind Ruling A says not to build. requireMention
# defaulted OFF — the agent sees every group message and can choose
# [SILENT] via its own judgment, given the group context this handler
# threads through regardless (is_group/chat_label — same as any other
# turn). test_personal_channel_group_gate.py's unaddressed-message tests
# were updated (not merely "no longer applicable") to reflect it, and tests
# proved requireMention=True (an explicit, owner-configured lever — never
# an AI runtime decision) reproduces the pre-2026-07-23 hard-gate behavior
# byte-for-byte, so nothing relying on the old strict gate lost the ability
# to have it.
#
# SUPERSEDED 2026-08-07 for the two DEFAULT_* constants below (see
# CHANNEL-GATEWAY-PLAN.md §4/§5 for the full incident writeup) — Ruling A's
# philosophy is NOT overturned by this, and neither is Ruling B: the agent
# still gets full group context on every turn it IS given, and this gate
# still keys ONLY on platform-computed addressing facts (was this message
# an @mention or a reply-to-agent; which chat is this) — NEVER on message
# content. What changed is that "unconfigured" stopped being a safe state
# to leave an owner in by construction. The founder's own personal
# Telegram account was added to a large public group; requireMention
# defaulted False AND group_policy's write path
# (_persist_agent_group_policy_config, below) had ZERO callers anywhere in
# the codebase — Gate 2 was unconditionally "open" for every agent,
# permanently, because nothing could ever change it. The agent replied to
# every message from every stranger in that group until the account got
# banned. An owner who never opened Settings (because there was nothing
# there to open) got "reply to literally everyone, everywhere" as their
# unconfigured starting point — that is the specific thing this change
# fixes, not Ruling A's see-and-decide philosophy itself. An owner who
# wants the original behavior back still can: set group_policy to "open"
# and require_mention to False explicitly, now that
# update_agent_group_policy_config (below) — wired to a real PATCH route,
# see routes_personal_channels.py — gives them a real lever to do it,
# which is the thing that didn't exist before this build.
#
# DEFAULT_GROUP_POLICY_MODE / DEFAULT_REQUIRE_MENTION below are the
# configured-but-unset default for a REAL, resolved agent identity. As of
# this build (see _resolve_local_bridge_agent_id) that now includes
# Signal/iMessage/WeChat-personal (LOCAL_BRIDGE_PERSONAL_CHANNELS) alongside
# Telegram Personal / WhatsApp Personal — all five channels resolve through
# _resolve_agent_id_for_inbound and land here once resolved, so a
# newly-claimed local-bridge binding gets the exact same safe
# allowlist/require-mention starting point WhatsApp/Telegram already do,
# configurable the same way (update_agent_group_policy_config,
# GROUP_POLICY_CHANNEL_KEYS already included the local-bridge keys before
# this build — only the identity resolution to ever reach that write path
# was missing).
#
# They do NOT govern the separate unresolved-identity fallback
# (_unresolved_identity_group_policy_config, below) — split out for the
# exact reason DEFAULT_DM_POLICY_MODE's own comment documents already
# having gone wrong once for dm_policy (ee3fca4f7c): a future change to the
# resolved-agent default must never silently reach into the
# unresolved-identity fallback too. That fallback is no longer the
# PERMANENT state for local-bridge channels (identity resolves for them
# now, in the common case), but it remains reachable — an ambiguous or
# not-yet-claimed preferred_gateway_id (see _resolve_local_bridge_agent_id),
# or any lookup failure — and per CHANNEL-GATEWAY-PLAN.md §"the last
# unscoped channels", any path that can still produce a genuinely unknown
# identity must fail CLOSED, not open. See
# _unresolved_identity_group_policy_config's own docstring for the fix
# (2026-08-07: flipped from GROUP_POLICY_OPEN to GROUP_POLICY_DISABLED).
GROUP_POLICY_OPEN = "open"
GROUP_POLICY_ALLOWLIST = "allowlist"
GROUP_POLICY_DISABLED = "disabled"
GROUP_POLICY_MODES = {GROUP_POLICY_OPEN, GROUP_POLICY_ALLOWLIST, GROUP_POLICY_DISABLED}
# Resolved-but-unconfigured default — SAFE, per CHANNEL-GATEWAY-PLAN.md §5
# step 2 (2026-08-07): matches OpenClaw's consistent default across every
# channel, not Telegram's legacy-open behavior. A brand-new agent binding,
# or an existing one whose owner has never touched group_policy, now
# starts allowlist/require-mention instead of open/no-mention-check. See
# scripts/backfill_group_policy_safe_defaults.py for the (not-yet-run,
# founder-approval-required) migration that pins EXISTING bindings to this
# same state explicitly rather than leaving them to it only implicitly.
DEFAULT_GROUP_POLICY_MODE = GROUP_POLICY_ALLOWLIST
DEFAULT_REQUIRE_MENTION = True

# Reason codes _enforce_group_policy returns when blocking — matches the
# pre-existing "group_no_mention" literal's style (a short, stable,
# machine-readable token every {"ignored": True, "reason": ...} caller
# already surfaces/traces, never a silent drop).
GROUP_GATE_REASON_NO_MENTION = "group_no_mention"
GROUP_GATE_REASON_POLICY_DENIED = "group_policy_denied"


def _normalize_group_policy_config(raw: Any) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in GROUP_POLICY_MODES:
        mode = DEFAULT_GROUP_POLICY_MODE
    allowlist = sorted({str(x).strip() for x in (data.get("allowlist") or []) if str(x or "").strip()})
    require_mention = _boolish(data.get("require_mention"), default=DEFAULT_REQUIRE_MENTION)
    return {"mode": mode, "allowlist": allowlist, "require_mention": require_mention}


def _unresolved_identity_group_policy_config(channel_key: str = "") -> Dict[str, Any]:
    """Fallback for _load_agent_group_policy_config when NO real per-agent
    install could even be identified (agent_id="" /
    personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID).

    CHANNEL-AWARE as of this build — two genuinely different situations
    both land on agent_id="", and conflating them would either reopen the
    local-bridge incident or silently regress a real, separately-decided
    WhatsApp/Telegram path:

    - LOCAL_BRIDGE_PERSONAL_CHANNELS (Signal/iMessage/WeChat-personal): until
      this build, agent_id="" was the PERMANENT case for these three —
      _resolve_agent_id_for_inbound had no lookup branch for them at all. It
      no longer is: _resolve_local_bridge_agent_id now resolves a real
      agent_id for them, either from an explicit owner action (iMessage's
      recheck/install routes) or a reverse preferred_gateway_id lookup. This
      fallback is still reachable whenever that resolution genuinely can't
      land (an ambiguous or not-yet-claimed preferred_gateway_id, or an
      install-lookup failure — see _resolve_local_bridge_agent_id's own
      docstring), and for THESE channels it now fails CLOSED
      (GROUP_POLICY_DISABLED/require_mention=True): this was literally the
      incident (CHANNEL-GATEWAY-PLAN.md's "the last unscoped channels") — an
      unresolved identity defaulting open, permanently and unconfigurably,
      let an agent reply unprompted in a large public group until the
      account got banned. A genuinely unknown local-bridge identity has no
      "the owner already configured this specific agent" story to lean on,
      so it must fail closed rather than open, matching dm_policy's own
      identity-less fallback (_unresolved_identity_dm_policy_config, already
      owner_only). Unlike the pre-this-build state, this is no longer a dead
      end: it means the gateway isn't (yet, or unambiguously) claimed by any
      agent's preferred_gateway_id — a real, fixable configuration gap.

    - Every other caller (WhatsApp/Telegram Personal's own agent_id=""
      case): a SEPARATE, pre-existing, deliberate product state — "Sage's
      own legacy Connect tab" binds a channel workspace-wide rather than to
      one specific agent (PersonalChannelConnectPanel.tsx's
      agentGatewayId===undefined path), which is not the incident this
      build fixes and is out of this build's scope to change. Stays
      GROUP_POLICY_OPEN/require_mention=False — today's actual, unchanged,
      separately-decided behavior for that path."""
    if str(channel_key or "").strip() in LOCAL_BRIDGE_PERSONAL_CHANNELS:
        return {"mode": GROUP_POLICY_DISABLED, "allowlist": [], "require_mention": True}
    return {"mode": GROUP_POLICY_OPEN, "allowlist": [], "require_mention": False}


async def _load_agent_group_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
) -> Dict[str, Any]:
    """Read-only — mirrors _load_agent_dm_policy_config's shape, persisted
    at install_metadata.group_policy[channel_key].

    An unresolved agent identity (see _unresolved_identity_group_policy_config
    above) returns that dedicated, channel-aware fallback, independent of
    DEFAULT_GROUP_POLICY_MODE. A RESOLVED agent_id whose install lookup
    fails, or who simply has no group_policy entry yet, gets
    DEFAULT_GROUP_POLICY_MODE/DEFAULT_REQUIRE_MENTION (via
    _normalize_group_policy_config(None) below) — the safe, owner-
    changeable default, now allowlist/require-mention as of 2026-08-07.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return _unresolved_identity_group_policy_config(channel_key)
    try:
        from server_modules import agent_registry_repository as _repo

        install = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id,
            tenant_id=str(tenant_id or "default").strip() or "default",
            workspace_id=str(workspace_id or "default").strip() or "default",
        )
    except Exception:
        _logger.warning("group_policy: install lookup failed for agent_id=%s — defaulting to DEFAULT_GROUP_POLICY_MODE", normalized_agent_id, exc_info=True)
        return _normalize_group_policy_config(None)
    if not isinstance(install, dict):
        return _normalize_group_policy_config(None)
    meta = dict(install.get("install_metadata") or install.get("metadata") or {})
    all_policies = meta.get("group_policy") if isinstance(meta.get("group_policy"), dict) else {}
    return _normalize_group_policy_config(all_policies.get(channel_key))


async def _persist_agent_group_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    config: Dict[str, Any],
) -> bool:
    """Write path. Had zero callers anywhere in the codebase until
    2026-08-07 (see CHANNEL-GATEWAY-PLAN.md §4) — wired to a real PATCH
    route via update_agent_group_policy_config below, the validating
    public wrapper routes_personal_channels.py calls; do not call this
    private function directly from a route, call the wrapper instead so a
    typo'd mode gets a real 400 rather than being silently coerced to the
    default by _normalize_group_policy_config. Mirrors
    _persist_agent_dm_policy_config's read-modify-write-the-whole-dict
    shape exactly (update_workspace_agent_install merges `metadata`
    shallowly at the TOP level only, so a naive per-channel write would
    clobber sibling channels' group_policy). Best-effort: never raises."""
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return False
    resolved_tenant_id = str(tenant_id or "default").strip() or "default"
    resolved_workspace_id = str(workspace_id or "default").strip() or "default"
    try:
        from server_modules import agent_registry_repository as _repo

        install = await _repo.get_workspace_agent_install_bundle(
            normalized_agent_id, tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id,
        )
        if not isinstance(install, dict):
            return False
        meta = dict(install.get("install_metadata") or install.get("metadata") or {})
        all_policies = dict(meta.get("group_policy")) if isinstance(meta.get("group_policy"), dict) else {}
        all_policies[channel_key] = _normalize_group_policy_config(config)
        updated = await _repo.update_workspace_agent_install(
            normalized_agent_id,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            metadata={"group_policy": all_policies},
        )
        return updated is not None
    except Exception:
        _logger.warning("group_policy: persist failed for agent_id=%s channel=%s", normalized_agent_id, channel_key, exc_info=True)
        return False


# Channel keys group_policy can ever apply to — WhatsApp/Telegram Personal
# plus the local-bridge set (Signal/iMessage/WeChat-personal). The WRITE
# path (this route/function) always worked for all five: it takes agent_id
# as an explicit, caller-supplied argument (the owner naming which agent's
# policy they're editing, from routes_personal_channels.py's own query
# param), never _resolve_agent_id_for_inbound. What was genuinely broken
# until this build was the READ side at INBOUND time: local-bridge messages
# always resolved to LEGACY_UNSCOPED_AGENT_ID, so whatever an owner
# configured here was persisted correctly but never actually consulted for
# a real incoming message (see _resolve_local_bridge_agent_id, which closes
# that gap).
GROUP_POLICY_CHANNEL_KEYS = frozenset(
    {WHATSAPP_PERSONAL_CHANNEL_KEY, TELEGRAM_PERSONAL_CHANNEL_KEY, *LOCAL_BRIDGE_PERSONAL_CHANNELS.keys()}
)


async def update_agent_group_policy_config(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    channel_key: str,
    mode: str,
    allowlist: Optional[List[str]] = None,
    require_mention: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Owner-facing write PRIMITIVE for the group_policy gate (Gate 2's
    allowlist axis + Gate 3's require_mention lever) — the validating,
    route-callable counterpart to _persist_agent_group_policy_config, which
    had zero callers anywhere before this build (see
    CHANNEL-GATEWAY-PLAN.md §4). routes_personal_channels.py's PATCH
    .../group-policy route is the only intended caller; call this instead
    of _persist_agent_group_policy_config directly so a bad `mode` gets a
    real error instead of being silently coerced to the default by
    _normalize_group_policy_config (correct behavior for a READ path facing
    possibly-stale stored data, wrong for a WRITE path facing a live
    caller).

    Raises ValueError for a missing/unresolvable agent_id, an unknown
    channel_key, or a mode outside GROUP_POLICY_MODES — the caller (the
    route) maps that to 400. Returns None if the agent install itself
    could not be found/updated (e.g. agent_id doesn't exist in this
    workspace) — the caller maps that to 404. This distinction mirrors
    every other config primitive in this module (see
    configure_whatsapp_personal_gateway's ValueError contract).

    require_mention=None means "use this build's safe default"
    (DEFAULT_REQUIRE_MENTION, currently True) rather than silently turning
    mention-gating off — an owner explicitly loosening it must pass
    require_mention=False themselves.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id or normalized_agent_id == personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        raise ValueError(
            "agent_id is required to configure a per-agent group policy — pass the specific "
            "agent install this policy applies to (group_policy is stored per agent+channel, "
            "not per gateway)."
        )
    normalized_channel_key = str(channel_key or "").strip().lower()
    if normalized_channel_key not in GROUP_POLICY_CHANNEL_KEYS:
        raise ValueError(f"channel_key must be one of {sorted(GROUP_POLICY_CHANNEL_KEYS)}.")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in GROUP_POLICY_MODES:
        raise ValueError(f"mode must be one of {sorted(GROUP_POLICY_MODES)}.")
    normalized_allowlist = sorted({str(x).strip() for x in (allowlist or []) if str(x or "").strip()})
    normalized_require_mention = DEFAULT_REQUIRE_MENTION if require_mention is None else bool(require_mention)
    config = {
        "mode": normalized_mode,
        "allowlist": normalized_allowlist,
        "require_mention": normalized_require_mention,
    }
    persisted = await _persist_agent_group_policy_config(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
        config=config,
    )
    if not persisted:
        return None
    return await _load_agent_group_policy_config(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=normalized_agent_id,
        channel_key=normalized_channel_key,
    )


def _group_policy_group_id(message: Dict[str, Any], *, remote_jid: str) -> str:
    """The identity group_policy's open|allowlist|disabled axis authorizes
    against: the group's own chat id (remote_jid — unlike dmPolicy's
    _dm_policy_sender_id, this is NEVER sender_jid; a group's membership
    list is irrelevant to "is this group itself allowed", only the group's
    own identity is)."""
    return str(remote_jid or "").strip()


async def _enforce_group_policy(
    *,
    registration: Dict[str, Any],
    channel_key: str,
    agent_id: str,
    message: Dict[str, Any],
    remote_jid: str,
) -> Dict[str, Any]:
    """THE group gate — replaces the inline `if is_group: if not
    is_mentioned and not is_reply_to_sage: skip` duplicated across the
    WhatsApp/Telegram/local-bridge/cloud handlers below. Combines the two
    axes documented above this function's constants.

    HARD CONSTRAINT: never reads message content/topic — only
    message["is_group"]/["is_mentioned"]/["is_reply_to_sage"] (platform-
    computed addressing facts a channel plugin derives from mention
    entities/reply-linkage, never from what the message says) and the
    group's own chat id.

    Self-chat bypass: self-chat (message["is_self_chat"]) is, by
    construction, never a group message on any channel (Saved
    Messages/Note-to-Self is always a private 1:1 with the owner) — the
    is_group short-circuit below already means self-chat is never gated.
    This explicit check is defense-in-depth so that remains true even if a
    future bridge ever mis-set both flags at once, matching the "owner's
    self-chat is NEVER gated by either policy" hard constraint literally
    rather than as an implied consequence.

    Returns {"allowed": bool, "reason": Optional[str], "mode": Optional[str],
    "require_mention": Optional[bool], "group_id": Optional[str],
    "was_addressed": Optional[bool]}. `reason` is GROUP_GATE_REASON_NO_MENTION
    or GROUP_GATE_REASON_POLICY_DENIED when blocked.

    was_addressed: the HONEST, real "was this message actually addressed"
    fact (explicit mention or reply-to-agent) — None for a DM/self-chat
    (not applicable; addressed is meaningless outside a group), otherwise
    the resolver's effective_was_mentioned, computed independently of
    require_mention. THIS MUST BE THREADED THROUGH to whatever builds the
    reply and feeds personal_channel_sage_bridge_service's InboundEnvelope
    (see _build_personal_channel_envelope's `addressed` field) — with
    requireMention defaulting OFF, allowed=True no longer implies "this was
    addressed" the way it used to when this gate hard-blocked every
    unaddressed message; callers that skip threading this through would
    silently tell the model "you were addressed directly" for a message
    that was not, breaking Ruling A's own "informed judgment" mechanism.
    """
    if bool(message.get("is_self_chat")) or not bool(message.get("is_group")):
        return {
            "allowed": True, "reason": None, "mode": None, "require_mention": None,
            "group_id": None, "was_addressed": None,
        }

    group_id = _group_policy_group_id(message, remote_jid=remote_jid)
    tenant_id = str(registration.get("tenant_id") or "default").strip() or "default"
    workspace_id = str(registration.get("workspace_id") or "default").strip() or "default"
    config = await _load_agent_group_policy_config(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_id=agent_id, channel_key=channel_key,
    )
    mode = config["mode"]
    require_mention = bool(config["require_mention"])

    # Computed regardless of require_mention (the formula doesn't depend on
    # it) so was_addressed is always the honest fact, whether or not this
    # policy would actually enforce it.
    mention_decision = mention_gating_service.resolve_inbound_mention_decision(
        facts=mention_gating_service.mention_facts_from_message(message),
        policy={"is_group": True, "require_mention": require_mention},
    )
    was_addressed = bool(mention_decision["effective_was_mentioned"])

    if mode == GROUP_POLICY_DISABLED:
        return {
            "allowed": False, "reason": GROUP_GATE_REASON_POLICY_DENIED, "mode": mode,
            "require_mention": require_mention, "group_id": group_id, "was_addressed": was_addressed,
        }
    if mode == GROUP_POLICY_ALLOWLIST and (not group_id or group_id not in config["allowlist"]):
        return {
            "allowed": False, "reason": GROUP_GATE_REASON_POLICY_DENIED, "mode": mode,
            "require_mention": require_mention, "group_id": group_id, "was_addressed": was_addressed,
        }
    if mention_decision["should_skip"]:
        return {
            "allowed": False, "reason": GROUP_GATE_REASON_NO_MENTION, "mode": mode,
            "require_mention": require_mention, "group_id": group_id, "was_addressed": was_addressed,
        }
    return {
        "allowed": True, "reason": None, "mode": mode,
        "require_mention": require_mention, "group_id": group_id, "was_addressed": was_addressed,
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


# Holds strong references to the fire-and-forget asyncio.Task objects
# _ensure_agent_channel_binding_enabled schedules below -- asyncio only
# weakly tracks a bare create_task() via the event loop, so without this
# set the task can be garbage-collected mid-run with no warning (the same
# footgun documented for the compaction background job,
# docs/PLATFORM-MAP.md Part 31.1). Entries remove themselves via
# add_done_callback the moment each task finishes, so this never grows
# unbounded. See docs/design/audit-silent-failures.md C2.
_CHANNEL_BINDING_ENABLE_TASKS: set[asyncio.Task] = set()


def _track_channel_binding_enable_task(task: asyncio.Task) -> None:
    _CHANNEL_BINDING_ENABLE_TASKS.add(task)

    def _on_done(finished: asyncio.Task) -> None:
        _CHANNEL_BINDING_ENABLE_TASKS.discard(finished)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            # Defense in depth: _enable()'s own try/except below already
            # logs+audits every failure it can see. This callback only
            # fires if something escapes that (e.g. a bug in the handler
            # itself, or a CancelledError-adjacent edge case) -- so a
            # background-task crash still can never vanish with zero trace.
            from server_modules import durability_signal

            durability_signal.capture_durability_failure(
                "channel binding enable task (uncaught)",
                exc=exc,
                event_class="channel_binding_enable_task_crashed",
            )

    task.add_done_callback(_on_done)


def _ensure_agent_channel_binding_enabled(
    *,
    agent_id: str,
    channel_key: str,
    registration: Dict[str, Any],
) -> None:
    """The Channels-tab pill's own gate (connection_catalog_service.
    agent_status_items) requires BOTH the underlying session state AND an
    ENABLED agent_channel_bindings row before it will ever show "connected"
    for a specific agent — that binding is how the UI distinguishes "this
    agent's channel" from "some other agent's, on the same catalog item."
    Other channel types (OAuth-based connectors) create that binding as
    part of their own connect flow; the full-account personal-channel wizard
    never did. Fire-and-forget from here, the moment a real (non-legacy)
    agent's session reaches "connected" — pairing an account for an agent
    should be sufficient to enable it for that agent, no separate manual
    toggle.

    Best-effort: a failure here must never break the state sync that's
    actually reporting the real session status -- but "best-effort" no
    longer means silent. `agent_bindings_repository.py` documents a live
    DB-level unique constraint (uq_agent_channel_bindings_inbound_owner_v2)
    that can genuinely fire here (re-pairing the same WhatsApp/Telegram
    account to a different agent is the ordinary way to trigger it); until
    this fix, that exception vanished into a bare `except Exception: pass`
    with zero logging, permanently stranding the Channels-tab pill in a
    not-connected state with no diagnostics and no manual recovery path
    (docs/design/audit-silent-failures.md C2). Every failure here is now
    logged with full context (exc_info=True) and raised as a durability
    signal (ERROR log + Sentry + an operator-visible activity-ledger
    dead-letter event via durability_signal.capture_durability_failure), and
    the scheduling task is tracked so it can't be GC'd mid-flight either.
    """
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — nothing to schedule onto (shouldn't happen; all callers are async)

    tenant_id = str(registration.get("tenant_id") or "").strip() or "default"
    workspace_id = str(registration.get("workspace_id") or "").strip() or "default"

    async def _enable() -> None:
        try:
            from server_modules import agent_bindings_repository as bindings
            await bindings.upsert_channel_binding(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                agent_install_id=normalized_agent_id,
                channel_key=channel_key,
                enabled=True,
            )
        except Exception as exc:
            from server_modules import durability_signal

            _logger.exception(
                "_ensure_agent_channel_binding_enabled: upsert_channel_binding failed "
                "agent_id=%s channel_key=%s tenant_id=%s workspace_id=%s -- the "
                "Channels-tab pill for this agent will misreport as not-connected "
                "until this binding is enabled.",
                normalized_agent_id, channel_key, tenant_id, workspace_id,
            )
            durability_signal.capture_durability_failure(
                f"channel binding enable for agent={normalized_agent_id} channel={channel_key}",
                exc=exc,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                channel=channel_key,
                event_class="channel_binding_enable_failed",
                action="upsert_channel_binding",
                summary=(
                    f"Session for agent {normalized_agent_id} on channel {channel_key} "
                    "connected, but enabling its agent_channel_bindings row failed "
                    f"({exc.__class__.__name__}) -- the Channels-tab pill will show "
                    "not-connected until this is manually retried."
                ),
            )

    task = loop.create_task(_enable())
    _track_channel_binding_enable_task(task)


def _claim_agent_channel_state(
    *,
    gateway_id: str,
    channel_key: str,
    agent_id: str,
    registration: Dict[str, Any],
) -> None:
    """Called at the START of configure_*_personal_gateway, before the
    Gateway is ever dispatched to. Seeds a row under the REAL agent_id if
    one doesn't already exist for this (gateway_id, channel_key, agent_id) —
    a no-op if this agent already has a row (don't clobber a connected
    session's status back to "connecting" on a retry/refresh call). This is
    what makes _resolve_agent_id_for_inbound correct even for a mid-pairing
    status (code_required, qr_required, ...): by the time ANY response
    comes back from the Gateway, the row already exists under the right
    agent_id, not the legacy/unscoped sentinel."""
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        return  # nothing to claim — caller is using the pre-existing unscoped path
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        existing = personal_channels_repository.get_whatsapp_state(
            str(gateway_id or "").strip(), channel_key=channel_key, agent_id=normalized_agent_id,
        )
        if existing is not None:
            return
        personal_channels_repository.upsert_whatsapp_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=channel_key,
            agent_id=normalized_agent_id,
            provider=WHATSAPP_PERSONAL_PROVIDER,
            status="connecting",
        )
    elif channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        existing = personal_channels_repository.get_telegram_state(
            str(gateway_id or "").strip(), channel_key=channel_key, agent_id=normalized_agent_id,
        )
        if existing is not None:
            return
        personal_channels_repository.upsert_telegram_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=channel_key,
            agent_id=normalized_agent_id,
            provider=TELEGRAM_PERSONAL_PROVIDER,
            status="connecting",
        )
    elif channel_key in LOCAL_BRIDGE_PERSONAL_CHANNELS:
        # Local-bridge twin — used by an explicit owner action that already
        # names a real agent_id (today: recheck_imessage_personal_gateway /
        # install_imessage_imsg_gateway, whose routes accept agent_id as a
        # query param). This is a MORE authoritative identity signal than
        # _resolve_local_bridge_agent_id's own preferred_gateway_id
        # fallback (an explicit action by the agent's own owner, not an
        # inference), so it's checked first by that function's fast path
        # via the same row.
        existing = personal_channels_repository.get_local_bridge_state(
            str(gateway_id or "").strip(), channel_key=channel_key, agent_id=normalized_agent_id,
        )
        if existing is not None:
            return
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=channel_key,
            agent_id=normalized_agent_id,
            provider=LOCAL_BRIDGE_PERSONAL_CHANNELS.get(channel_key, {}).get("provider", channel_key),
            status="connecting",
            metadata={"resolved_via": "explicit_owner_action"},
        )


def _resolve_agent_id_for_inbound(gateway_id: str, channel_key: str) -> str:
    """Which agent's session an inbound event belongs to. The Gateway's
    session runtime doesn't know about Empyralis agent ids (see
    personal_channels_repository.find_agent_id_for_telegram_session's
    docstring) so this is a reverse lookup by gateway_id+channel_key against
    whichever agent has a currently-connected session there. Returns
    LEGACY_UNSCOPED_AGENT_ID (empty string) when that's ambiguous or there
    is none — execute_sage_turn_for_channel already treats that as "run as
    Sage," the pre-existing behavior, so an ambiguous lookup never breaks a
    turn, it just doesn't get specialist-scoped.

    This is the FAST path only — an indexed row lookup, no I/O beyond
    SQLite. For Signal/iMessage/WeChat-personal (LOCAL_BRIDGE_PERSONAL_CHANNELS)
    it only finds a row once something has already claimed one; see
    _resolve_local_bridge_agent_id (async, does the actual claiming) for the
    real inbound-handler entry point for those three channels — this
    synchronous function alone is not enough for them the way it is for
    WhatsApp/Telegram, which get an explicit agent_id at configure time
    (_claim_agent_channel_state)."""
    normalized_gateway_id = str(gateway_id or "").strip()
    if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY:
        return personal_channels_repository.find_agent_id_for_whatsapp_session(
            normalized_gateway_id, channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        )
    if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY:
        return personal_channels_repository.find_agent_id_for_telegram_session(
            normalized_gateway_id, channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        )
    if channel_key in LOCAL_BRIDGE_PERSONAL_CHANNELS:
        return personal_channels_repository.find_agent_id_for_local_bridge_session(
            normalized_gateway_id, channel_key=channel_key,
        )
    return personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID


async def _resolve_local_bridge_agent_id(
    *,
    gateway_id: str,
    channel_key: str,
    registration: Dict[str, Any],
) -> str:
    """The real inbound-resolution entry point for Signal/iMessage/
    WeChat-personal — _resolve_agent_id_for_inbound's fast path alone isn't
    enough for these three, because unlike WhatsApp/Telegram there is no
    configure_*_personal_gateway step that names a real agent_id up front
    (_claim_agent_channel_state). These are OS-level bridges authenticated
    entirely outside Empyralis (signal-cli, Messages.app) — there is no
    "the owner just told us who this is" moment for most of them.

    What DOES already unambiguously answer "which agent owns this gateway's
    local-bridge channel" is the SAME mechanism the product already uses in
    the opposite direction for these exact channels: install_metadata.
    preferred_gateway_id. FleetAgentDetail.tsx's own doc comment on
    CHANNEL_DOORS states it directly — "'Full account' doors bind to THIS
    agent's own preferred_gateway_id ... never to a workspace-wide/
    Sage-routed session" — Signal/iMessage's full-account doors are already
    product-documented as belonging to whichever agent points its own
    preferred_gateway_id at this box. Reusing it here for inbound
    resolution is the existing convention, not a new one.

    Fast path: _resolve_agent_id_for_inbound's indexed row lookup, populated
    by a PRIOR call to this function (or, for iMessage specifically, by an
    explicit owner action — see recheck_imessage_personal_gateway/
    install_imessage_imsg_gateway, which call _claim_agent_channel_state
    directly with the agent_id their own route already receives, a more
    authoritative signal than this fallback and checked first via the same
    row).

    Slow path (only runs until the fast path has something to find):
    reverse-scans this gateway's own tenant/workspace fleet for agent
    installs whose preferred_gateway_id equals this gateway_id. Exactly one
    match claims and persists a row (personal_channel_local_bridge_states),
    so every later message on this gateway+channel hits the fast path
    instead. Zero or multiple matches is genuinely ambiguous (no agent has
    claimed this box, or more than one has — the "multi-agent-per-box" case
    noted in find_agent_id_for_telegram_session's own docstring as not yet
    resolvable) — returns LEGACY_UNSCOPED_AGENT_ID and claims nothing, so a
    later, unambiguous state doesn't have a wrong row to override.

    UNLIKE WhatsApp/Telegram's own ambiguous-lookup fallback (which only
    costs specialist-scoping — the turn still runs as Sage), an unresolved
    result here also denies the group/DM gates by construction (see
    _unresolved_identity_group_policy_config / _unresolved_identity_dm_policy_config)
    rather than defaulting open — there is no "the owner already configured
    this specific agent" story to lean on for a gateway nobody has claimed,
    or that more than one agent claims."""
    resolved = _resolve_agent_id_for_inbound(gateway_id, channel_key)
    if resolved and resolved != personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID:
        return resolved
    normalized_gateway_id = str(gateway_id or "").strip()
    if not normalized_gateway_id:
        return personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID
    tenant_id = str(registration.get("tenant_id") or "default").strip() or "default"
    workspace_id = str(registration.get("workspace_id") or "default").strip() or "default"
    try:
        from server_modules import agent_registry_repository as _repo

        installs = await _repo.list_workspace_agent_installs(tenant_id=tenant_id, workspace_id=workspace_id)
    except Exception:
        _logger.warning(
            "local_bridge: preferred_gateway_id lookup failed for gateway_id=%s channel=%s",
            normalized_gateway_id, channel_key, exc_info=True,
        )
        return personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID
    matches = sorted({
        str(install.get("id") or "").strip()
        for install in installs
        if isinstance(install, dict)
        and bool(install.get("enabled"))
        and str((install.get("metadata") or {}).get("preferred_gateway_id") or "").strip() == normalized_gateway_id
        and str(install.get("id") or "").strip()
    })
    if len(matches) != 1:
        if len(matches) > 1:
            _logger.warning(
                "local_bridge: gateway_id=%s is preferred_gateway_id for %d agents (%s) — "
                "ambiguous, leaving channel=%s identity unresolved (fails closed rather than "
                "guessing which agent owns it).",
                normalized_gateway_id, len(matches), matches, channel_key,
            )
        return personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID
    resolved_agent_id = matches[0]
    provider = LOCAL_BRIDGE_PERSONAL_CHANNELS.get(channel_key, {}).get("provider", channel_key)
    personal_channels_repository.upsert_local_bridge_state(
        gateway_id=normalized_gateway_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=str(registration.get("user_id") or "").strip(),
        channel_key=channel_key,
        agent_id=resolved_agent_id,
        provider=provider,
        status="linked",
        metadata={"resolved_via": "preferred_gateway_id"},
    )
    return resolved_agent_id


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
                # `safety` here is the GATEWAY's own static manifest claim,
                # passed through unmodified (see empyralis-gateway's
                # PersonalChannelCapabilityManifest.safety). `dm_policy` below
                # is what the SERVER actually enforces for this channel —
                # see _enforce_dm_policy. This endpoint has no agent_id
                # parameter (a gateway can host more than one agent's
                # session per channel), so this is the channel-level
                # default/enforcement description, not one specific agent's
                # live allowlist — fetch that per-agent via a future
                # settings surface once one exists (see this build's report).
                "safety": secret_redaction_service.sanitize_mapping(_as_mapping(manifest.get("safety"))),
                "dm_policy": {
                    "default_mode": DEFAULT_DM_POLICY_MODE,
                    "modes": sorted(DM_POLICY_MODES),
                    "enforced_server_side": True,
                },
                # group_policy: the SAME channel-level default/enforcement
                # description as dm_policy above, for the separate group
                # axes (see _enforce_group_policy). require_mention's
                # default is OFF (Ruling A, see-and-decide) — an owner can
                # opt any agent back into a hard mention-only gate per
                # channel; see that constant's own doc comment for why.
                "group_policy": {
                    "default_mode": DEFAULT_GROUP_POLICY_MODE,
                    "modes": sorted(GROUP_POLICY_MODES),
                    "default_require_mention": DEFAULT_REQUIRE_MENTION,
                    "enforced_server_side": True,
                },
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
    agent_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """agent_id: pass it explicitly when the caller already knows it (the
    inbound-message handlers below, which resolve it from the message
    itself). Leave it None for the raw gateway.state.update push (no
    message to resolve from) — this falls back to "whoever most recently
    touched this gateway+channel" via the repository's reverse lookup,
    which configure_telegram_personal_gateway/configure_whatsapp_personal_gateway
    keep correct by claiming a row up front, before the Gateway ever
    reports back (see personal_channels_repository.find_agent_id_for_telegram_session's
    docstring)."""
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
        resolved_whatsapp_agent_id = (
            str(agent_id).strip() if agent_id is not None
            else _resolve_agent_id_for_inbound(gateway_id, WHATSAPP_PERSONAL_CHANNEL_KEY)
        )
        synced_state = personal_channels_repository.upsert_whatsapp_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id=resolved_whatsapp_agent_id,
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
        if str((synced_state or {}).get("status") or "").strip() == "connected":
            _ensure_agent_channel_binding_enabled(
                agent_id=resolved_whatsapp_agent_id,
                channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
                registration=registration,
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
        resolved_telegram_agent_id = (
            str(agent_id).strip() if agent_id is not None
            else _resolve_agent_id_for_inbound(gateway_id, TELEGRAM_PERSONAL_CHANNEL_KEY)
        )
        synced_state = personal_channels_repository.upsert_telegram_state(
            gateway_id=str(gateway_id or "").strip(),
            tenant_id=str(registration.get("tenant_id") or "").strip(),
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            user_id=str(registration.get("user_id") or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id=resolved_telegram_agent_id,
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
        if str((synced_state or {}).get("status") or "").strip() == "connected":
            _ensure_agent_channel_binding_enabled(
                agent_id=resolved_telegram_agent_id,
                channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
                registration=registration,
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


# ── Media pipeline (Feature B): inbound fetch/store/transcribe ────────
#
# THE CONTRACT: a channel.inbound message MAY carry an optional `media`
# array; each item is {kind: "image"|"voice"|"audio"|"video"|"file",
# media_id: "<gateway-fetchable id>", mime_type, filename?, size_bytes?,
# duration_sec?}. See gateway_protocol_service.fetch_channel_media's
# docstring for the exact fetch RPC, and
# personal_channel_media_store_service's module docstring for the
# storage/attachment-context contract.


async def _process_inbound_media_for_turn(
    *,
    gateway_id: str,
    channel_key: str,
    provider: str,
    registration: Dict[str, Any],
    agent_id: str,
    text: str,
    media_items: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]]]:
    """Fetch/store any media on this inbound message, splice voice/audio
    transcripts into the text (`[Voice message]: <transcript>`, or the
    fixed "transcription unavailable" placeholder), and return image/file
    attachments ready for the turn context. Never raises: any failure here
    degrades to the original text with no attachments rather than dropping
    the turn — matching this feature's graceful-degradation contract.
    """
    if not media_items:
        return text, []

    workspace_id = str(registration.get("workspace_id") or "default").strip() or "default"
    try:
        from server_modules import personal_channel_media_store_service as _media_store

        result = await _media_store.process_inbound_media(
            gateway_id=gateway_id,
            channel_key=channel_key,
            provider=provider,
            workspace_id=workspace_id,
            agent_id=agent_id,
            media=media_items,
        )
    except Exception:
        _logger.warning(
            "media pipeline: process_inbound_media failed channel=%s gateway=%s",
            channel_key, gateway_id, exc_info=True,
        )
        return text, []

    effective_text = text
    for voice_record in result.get("voice_records") or []:
        try:
            from server_modules import personal_channel_transcription_service as _stt

            transcription = await _stt.transcribe_voice_bytes(
                workspace_id=workspace_id,
                audio_bytes=voice_record.get("raw_bytes") or b"",
                mime_type=str(voice_record.get("mime_type") or "audio/ogg"),
                filename=str(voice_record.get("filename") or "voice-message"),
                agent_id=agent_id,
            )
            voice_text = _stt.format_voice_message_text(transcription)
        except Exception:
            _logger.warning(
                "media pipeline: voice transcription failed channel=%s gateway=%s",
                channel_key, gateway_id, exc_info=True,
            )
            voice_text = "[Voice message — transcription unavailable]"
        effective_text = f"{effective_text}\n{voice_text}".strip() if effective_text else voice_text

    attachments = list(result.get("attachments") or [])
    if not effective_text.strip():
        # Media-only message with nothing text-like to anchor the turn on
        # (e.g. an image with no caption, or an unfetchable voice note) —
        # the attachment context block (if any) still carries the image.
        effective_text = "[Media message]"

    return effective_text, attachments


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
    agent_id: str = "",
    attachments: Optional[List[Dict[str, Any]]] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
    sender_id: str = "",
    was_addressed: Optional[bool] = None,
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
            agent_id=agent_id,
            idempotency_key=reply_idempotency_key,
        )
    if outbound and str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id=agent_id,
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
            agent_id=agent_id,
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
                agent_id=agent_id,
                idempotency_key=_cmd_key,
                remote_jid=remote_jid,
                text=_cmd_reply,
                reply_to_external_message_id=external_message_id,
            )
            personal_channels_repository.mark_inbound_processed(
                gateway_id=str(gateway_id or "").strip(),
                channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id=agent_id,
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
            sender_id=sender_id,
            source_event_id=external_message_id,
            linked_user_name=linked_user_name,
            agent_id=agent_id,
            attachments=attachments,
            is_owner=is_owner,
            is_group=is_group,
            chat_label=chat_label,
            was_addressed=was_addressed,
        )
        reply_media = list((reply or {}).get("media") or [])
        # ABSOLUTE RULE: no hardcoded platform status/error message may EVER
        # be sent into a channel (DM or group). filter_channel_outbound_reply()
        # is the backstop here regardless of what the bridge service returned
        # — it catches [SILENT] markers AND any text matching a known
        # platform status/error string, so a turn error/quota denial/timeout
        # can never masquerade as a deliverable reply.
        _raw_reply_text = str((reply or {}).get("text") or "").strip()
        _safe_reply_text = filter_channel_outbound_reply(_raw_reply_text) if _raw_reply_text else None
        # A media-only reply (send_image/generate_image queued an attachment
        # but the model had nothing more to say, or its text was filtered
        # above) still has something to deliver — only skip when there is
        # genuinely neither safe text nor media.
        if not reply or (not _safe_reply_text and not reply_media):
            no_reply_idempotency_key = f"{WHATSAPP_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX}{external_message_id}"
            refreshed_inbound = personal_channels_repository.mark_inbound_processed(
                gateway_id=str(gateway_id or "").strip(),
                channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id=agent_id,
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
                detail=(
                    "Automatic WhatsApp personal reply was skipped because the agent returned no reply."
                    if not _raw_reply_text
                    else "Automatic WhatsApp personal reply was suppressed: a hardcoded status/error message may never reach a channel."
                ),
                metadata={"remote_jid": remote_jid, "inbound_external_message_id": external_message_id},
                trace_id=trace_id,
                idempotency_key=f"personal_channel.whatsapp.automatic_reply.skipped:{gateway_id}:{external_message_id}",
            )
            return {"duplicate": duplicate, "inbound": refreshed_inbound or inbound, "outbound": None}

        outbound, _ = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id=agent_id,
            idempotency_key=idempotency_key,
            remote_jid=remote_jid,
            text=_safe_reply_text,
            reply_to_external_message_id=external_message_id,
            metadata={
                "reply_source": str(reply.get("source") or "").strip() or None,
                "media": reply_media or None,
            },
        )

    if str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            agent_id=agent_id,
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
        # Auto-replies dispatch as normal messages, not forced quote-reply
        # bubbles: always threading a reply to the triggering message reads
        # as robotic on Telegram/WhatsApp. An explicit "reply to X" send
        # (send_whatsapp_personal_message) still honors a caller-supplied id.
        reply_to_external_message_id=None,
        # Read back from the stored outbound row (not the `reply` var above)
        # so this also carries media on the idempotent-replay path, where
        # `outbound` came from get_outbound_message() and `reply` was never
        # rebuilt this call.
        media=list((outbound.get("metadata") or {}).get("media") or []),
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        idempotency_key=idempotency_key,
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
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
    media_items = [item for item in (message.get("media") or []) if isinstance(item, dict)]
    if not external_message_id or not remote_jid or (not text and not media_items):
        raise ValueError("channel.inbound requires external_message_id, remote_jid, and (text or media).")
    if bool(message.get("from_me")) and not bool(message.get("is_self_chat")):
        return {"ignored": True, "reason": "from_me", "channel_key": WHATSAPP_PERSONAL_CHANNEL_KEY}
    # Resolved BEFORE the sync below (not after): this reflects whichever
    # agent's configure() call (or a prior message) already owns this
    # gateway+channel, and the sync then updates THAT SAME row to
    # "connected" rather than risking a second, wrongly-scoped row. Also
    # needed by the group gate right below (group_policy is per-agent
    # config), which is why this now runs before that gate rather than
    # after it as it used to when the gate was a hardcoded inline check.
    agent_id = _resolve_agent_id_for_inbound(gateway_id, WHATSAPP_PERSONAL_CHANNEL_KEY)
    # Group gate: _enforce_group_policy — the ONE shared resolver (see its
    # own docstring above) replacing what used to be an inline `if is_group
    # and not is_mentioned and not is_reply_to_sage: skip` here. The
    # Gateway-side filter (runtime.ts) used to be a primary gate duplicating
    # this same decision; it now only computes/forwards the raw mention
    # facts (is_mentioned/is_reply_to_sage) and this is the ONE place the
    # shouldSkip decision is made.
    group_decision = await _enforce_group_policy(
        registration=registration,
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        message=message,
        remote_jid=remote_jid,
    )
    if not group_decision["allowed"]:
        return {
            "ignored": True,
            "reason": group_decision["reason"],
            "channel_key": WHATSAPP_PERSONAL_CHANNEL_KEY,
        }
    # Read BEFORE the sync below writes to it: dmPolicy's owner check (further
    # down) needs the identity established at login, not whatever the sync
    # payload is about to (re)write — see _resolve_linked_identity_for_sync's
    # docstring.
    existing_state = personal_channels_repository.get_whatsapp_state(
        str(gateway_id or "").strip(), channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY, agent_id=agent_id,
    )
    sync_gateway_personal_channel_state(
        gateway_id=gateway_id,
        registration=registration,
        agent_id=agent_id,
        payload={
            "personal_channels": {
                WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "provider": str(payload.get("provider") or WHATSAPP_PERSONAL_PROVIDER).strip() or WHATSAPP_PERSONAL_PROVIDER,
                    "status": "connected",
                    # Only a genuine self-chat event supplies a NEW linked_jid;
                    # every other inbound message preserves whatever is
                    # already persisted (see _resolve_linked_identity_for_sync).
                    "linked_jid": _resolve_linked_identity_for_sync(
                        current_value=str(message.get("sender_jid") or "").strip() if bool(message.get("is_self_chat")) else None,
                        preserved_value=_channel_owner_linked_id(channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY, state=existing_state),
                    ),
                }
            }
        },
    )
    inbound, created = personal_channels_repository.record_inbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        sender_jid=str(message.get("sender_jid") or "").strip() or None,
        push_name=str(message.get("push_name") or "").strip() or None,
        text=text,
        metadata={
            "provider": str(payload.get("provider") or WHATSAPP_PERSONAL_PROVIDER).strip() or WHATSAPP_PERSONAL_PROVIDER,
            "received_at": str(message.get("received_at") or "").strip() or None,
            "from_me": bool(message.get("from_me")),
            "media_kinds": [str(item.get("kind") or "").strip() for item in media_items] or None,
        },
    )
    # ── dmPolicy gate: MUST run before any reply (including a control-command
    # reply) is generated. See _enforce_dm_policy's docstring. ──
    dm_decision = await _enforce_dm_policy(
        registration=registration,
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        message=message,
        remote_jid=remote_jid,
        existing_state=existing_state,
        label="WhatsApp",
    )
    if not dm_decision["allowed"]:
        return await _handle_dm_policy_blocked(
            gateway_id=gateway_id,
            registration=registration,
            inbound=inbound,
            channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
            provider=WHATSAPP_PERSONAL_PROVIDER,
            agent_id=agent_id,
            external_message_id=external_message_id,
            remote_jid=remote_jid,
            duplicate=not created,
            decision=dm_decision,
            trace_id=trace_id,
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

    effective_text, attachments = await _process_inbound_media_for_turn(
        gateway_id=gateway_id,
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        provider=WHATSAPP_PERSONAL_PROVIDER,
        registration=registration,
        agent_id=agent_id,
        text=text,
        media_items=media_items,
    )
    return await _deliver_whatsapp_personal_reply(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        remote_jid=remote_jid,
        external_message_id=external_message_id,
        text=effective_text,
        push_name=str(message.get("push_name") or "").strip() or None,
        duplicate=not created,
        trace_id=trace_id,
        agent_id=agent_id,
        attachments=attachments,
        is_owner=bool(dm_decision.get("is_owner")),
        # is_group: same signal the group-mention gate above already reads
        # (message.get("is_group")) — threaded through so an owner-unified
        # memory turn is never mistaken for a private 1:1 with the owner
        # just because the owner happens to be a member of this group (see
        # _build_unified_sage_personal_reply_async's is_group contract).
        # chat_label: the group's human-readable subject when the Gateway
        # supplied one (mapWhatsAppInboundMessage / runtime.ts's
        # groupMetadata lookup — best-effort, may be absent), used only to
        # make the owner-unified activity feed's mirrored entries legible.
        is_group=bool(message.get("is_group")),
        chat_label=str(message.get("chat_title") or "").strip() or None,
        # The specific participant who sent this message — differs from
        # remote_jid inside a group. Feeds ONLY the canonical
        # InboundEnvelope's sender.id (see
        # personal_channel_sage_bridge_service._build_personal_channel_envelope);
        # every other identity/routing decision in this handler already keys
        # off remote_jid, unchanged.
        sender_id=str(message.get("sender_jid") or "").strip(),
        # The REAL "was this message actually addressed" fact from the
        # group gate above (None for a non-group turn) — see
        # _enforce_group_policy's own docstring for why this must be
        # threaded through rather than left to default: with
        # requireMention OFF by default, allowed=True no longer implies
        # "addressed" the way it used to.
        was_addressed=group_decision.get("was_addressed"),
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
    media_items = [item for item in (message.get("media") or []) if isinstance(item, dict)]
    if not external_message_id or not remote_jid or (not text and not media_items):
        raise ValueError("channel.inbound requires external_message_id, remote_jid, and (text or media).")
    # A self-chat message (the owner messaging their own Telegram "Saved
    # Messages" — the exact analog of WhatsApp's is_self_chat command
    # channel) is ALWAYS from_me too, since only the owner can post into
    # their own Saved Messages. Let it through here exactly like the
    # WhatsApp handler above (`from_me and not is_self_chat`) instead of
    # ignoring every from_me message unconditionally — the pre-fix
    # behavior, which was moot only because the Gateway (runtime.ts)
    # dropped self-chat messages before they ever reached this handler at
    # all. An ordinary outgoing message to someone else (or to a group)
    # still has is_self_chat=False and is still ignored here.
    if bool(message.get("from_me")) and not bool(message.get("is_self_chat")):
        return {"ignored": True, "reason": "from_me", "channel_key": TELEGRAM_PERSONAL_CHANNEL_KEY}
    # Resolved BEFORE the sync below — see the WhatsApp handler's identical
    # comment above for why the ordering matters. Also now needed by the
    # group gate right below (group_policy is per-agent config).
    agent_id = _resolve_agent_id_for_inbound(gateway_id, TELEGRAM_PERSONAL_CHANNEL_KEY)
    # Group gate: _enforce_group_policy — the ONE shared resolver, replacing
    # what used to be an inline `if is_group and not is_mentioned and not
    # is_reply_to_sage: skip` here — identical contract to the WhatsApp
    # handler above.
    group_decision = await _enforce_group_policy(
        registration=registration,
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        message=message,
        remote_jid=remote_jid,
    )
    if not group_decision["allowed"]:
        return {
            "ignored": True,
            "reason": group_decision["reason"],
            "channel_key": TELEGRAM_PERSONAL_CHANNEL_KEY,
        }
    # Read BEFORE the sync below writes to it — see the WhatsApp handler's
    # identical comment above (_resolve_linked_identity_for_sync's docstring).
    existing_state = personal_channels_repository.get_telegram_state(
        str(gateway_id or "").strip(), channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY, agent_id=agent_id,
    )
    sync_gateway_personal_channel_state(
        gateway_id=gateway_id,
        registration=registration,
        agent_id=agent_id,
        payload={
            "personal_channels": {
                TELEGRAM_PERSONAL_CHANNEL_KEY: {
                    "provider": str(payload.get("provider") or TELEGRAM_PERSONAL_PROVIDER).strip() or TELEGRAM_PERSONAL_PROVIDER,
                    "status": "connected",
                    # Mirrors the WhatsApp handler above exactly, now that
                    # Telegram's message payload also carries is_self_chat
                    # (see telegram/message-mapper.ts's
                    # mapTelegramInboundMessage): only a genuine self-chat
                    # event supplies a NEW linked_user_id; every other
                    # inbound message (including a stranger's, or the
                    # owner posting inside a group) preserves whatever is
                    # already persisted. See
                    # _resolve_linked_identity_for_sync's docstring for why
                    # deriving this from the message's own sender_jid
                    # unconditionally (the pre-fix behavior) silently
                    # clobbered the real owner identity with whichever
                    # stranger last texted in.
                    "linked_user_id": _resolve_linked_identity_for_sync(
                        current_value=str(message.get("sender_jid") or "").strip() if bool(message.get("is_self_chat")) else None,
                        preserved_value=_channel_owner_linked_id(channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY, state=existing_state),
                    ),
                    "linked_name": str((existing_state or {}).get("linked_name") or "").strip() or None,
                }
            }
        },
    )
    inbound, created = personal_channels_repository.record_inbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        external_message_id=external_message_id,
        remote_jid=remote_jid,
        sender_jid=str(message.get("sender_jid") or "").strip() or None,
        push_name=str(message.get("push_name") or "").strip() or None,
        text=text,
        metadata={
            "provider": str(payload.get("provider") or TELEGRAM_PERSONAL_PROVIDER).strip() or TELEGRAM_PERSONAL_PROVIDER,
            "received_at": str(message.get("received_at") or "").strip() or None,
            "from_me": bool(message.get("from_me")),
            "media_kinds": [str(item.get("kind") or "").strip() for item in media_items] or None,
        },
    )
    # ── dmPolicy gate: MUST run before any reply (including a control-command
    # reply) is generated. See _enforce_dm_policy's docstring. ──
    dm_decision = await _enforce_dm_policy(
        registration=registration,
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        message=message,
        remote_jid=remote_jid,
        existing_state=existing_state,
        label="Telegram",
    )
    if not dm_decision["allowed"]:
        return await _handle_dm_policy_blocked(
            gateway_id=gateway_id,
            registration=registration,
            inbound=inbound,
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            provider=TELEGRAM_PERSONAL_PROVIDER,
            agent_id=agent_id,
            external_message_id=external_message_id,
            remote_jid=remote_jid,
            duplicate=not created,
            decision=dm_decision,
            trace_id=trace_id,
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
            agent_id=agent_id,
            idempotency_key=reply_idempotency_key,
        )
    if outbound and str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id=agent_id,
            external_message_id=external_message_id,
            reply_idempotency_key=idempotency_key,
        )
        return {"duplicate": not created, "inbound": inbound, "outbound": outbound}

    if outbound is None:
        effective_text, attachments = await _process_inbound_media_for_turn(
            gateway_id=gateway_id,
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            provider=TELEGRAM_PERSONAL_PROVIDER,
            registration=registration,
            agent_id=agent_id,
            text=text,
            media_items=media_items,
        )
        reply = personal_channel_sage_bridge_service.build_telegram_personal_reply(
            workspace_id=str(registration.get("workspace_id") or "").strip(),
            gateway_id=str(gateway_id or "").strip(),
            remote_jid=remote_jid,
            text=effective_text,
            push_name=str(message.get("push_name") or "").strip() or None,
            # The specific participant who sent this message (differs from
            # remote_jid inside a group) — feeds ONLY the canonical
            # InboundEnvelope's sender.id, see
            # personal_channel_sage_bridge_service._build_personal_channel_envelope.
            sender_id=str(message.get("sender_jid") or "").strip(),
            source_event_id=external_message_id,
            agent_id=agent_id,
            attachments=attachments,
            is_owner=bool(dm_decision.get("is_owner")),
            # is_group/chat_title: resolved gateway-side from the already-
            # fetched GramJS chat entity (see telegram/runtime.ts — a
            # private chat has no .title, a group/supergroup/channel does;
            # no extra network call). Absent on any inbound predating that
            # Gateway upgrade, which safely reads as False/None here — never
            # a regression.
            is_group=bool(message.get("is_group")),
            chat_label=str(message.get("chat_title") or "").strip() or None,
            # The REAL "was this message actually addressed" fact from the
            # group gate above (None for a non-group turn) — see
            # _enforce_group_policy's own docstring for why this must be
            # threaded through rather than left to default.
            was_addressed=group_decision.get("was_addressed"),
        )
        reply_media = list((reply or {}).get("media") or [])
        # ABSOLUTE RULE: no hardcoded platform status/error message may EVER
        # be sent into a channel (DM or group — a group turn only reaches
        # this point after already passing the mention/reply gate above, but
        # that gate is about WHETHER to run a turn at all, not about what a
        # turn is allowed to reply with, so this backstop still applies to
        # every reply unconditionally). filter_channel_outbound_reply() is
        # the backstop here regardless of what the bridge service returned.
        _raw_reply_text = str((reply or {}).get("text") or "").strip()
        _safe_reply_text = filter_channel_outbound_reply(_raw_reply_text) if _raw_reply_text else None
        # A media-only reply (send_image/generate_image queued an attachment
        # but the model had nothing more to say, or its text was filtered
        # above) still has something to deliver — only skip when there is
        # genuinely neither safe text nor media.
        if not reply or (not _safe_reply_text and not reply_media):
            no_reply_idempotency_key = f"{TELEGRAM_PERSONAL_NO_REPLY_IDEMPOTENCY_PREFIX}{external_message_id}"
            refreshed_inbound = personal_channels_repository.mark_inbound_processed(
                gateway_id=str(gateway_id or "").strip(),
                channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
                agent_id=agent_id,
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
                detail=(
                    "Automatic Telegram personal reply was skipped because the agent returned no reply."
                    if not _raw_reply_text
                    else "Automatic Telegram personal reply was suppressed: a hardcoded status/error message may never reach a channel."
                ),
                metadata={"remote_jid": remote_jid, "inbound_external_message_id": external_message_id},
                trace_id=trace_id,
                idempotency_key=f"personal_channel.telegram.automatic_reply.skipped:{gateway_id}:{external_message_id}",
            )
            return {"duplicate": not created, "inbound": refreshed_inbound or inbound, "outbound": None}

        outbound, _ = personal_channels_repository.create_or_get_outbound_message(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id=agent_id,
            idempotency_key=idempotency_key,
            remote_jid=remote_jid,
            text=_safe_reply_text,
            reply_to_external_message_id=external_message_id,
            metadata={
                "reply_source": str(reply.get("source") or "").strip() or None,
                "media": reply_media or None,
            },
        )

    if str(outbound.get("status") or "").strip() == "delivered":
        personal_channels_repository.mark_inbound_processed(
            gateway_id=str(gateway_id or "").strip(),
            channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
            agent_id=agent_id,
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
        # Auto-replies dispatch as normal messages, not forced quote-reply
        # bubbles: always threading a reply to the triggering message reads
        # as robotic on Telegram/WhatsApp. An explicit "reply to X" send
        # (send_telegram_personal_message) still honors a caller-supplied id.
        reply_to_external_message_id=None,
        # Read back from the stored outbound row (not the `reply` var above)
        # so this also carries media on the idempotent-replay path, where
        # `outbound` came from get_outbound_message() and `reply` was never
        # rebuilt this call.
        media=list((outbound.get("metadata") or {}).get("media") or []),
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        idempotency_key=idempotency_key,
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    personal_channels_repository.mark_inbound_processed(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
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
    attachments: Optional[List[Dict[str, Any]]] = None,
    is_owner: bool = False,
    is_group: bool = False,
    chat_label: Optional[str] = None,
    sender_id: str = "",
    was_addressed: Optional[bool] = None,
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
            sender_id=sender_id,
            fallback_label=label,
            source_event_id=external_message_id,
            attachments=attachments,
            is_owner=is_owner,
            is_group=is_group,
            chat_label=chat_label,
            was_addressed=was_addressed,
        )
        reply_media = list((reply or {}).get("media") or [])
        # ABSOLUTE RULE: no hardcoded platform status/error message may EVER
        # be sent into a channel (DM or group).
        _raw_reply_text = str((reply or {}).get("text") or "").strip()
        _safe_reply_text = filter_channel_outbound_reply(_raw_reply_text) if _raw_reply_text else None
        # A media-only reply (send_image/generate_image queued an attachment
        # but the model had nothing more to say, or its text was filtered
        # above) still has something to deliver — only skip when there is
        # genuinely neither safe text nor media.
        if not reply or (not _safe_reply_text and not reply_media):
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
                detail=(
                    f"Automatic {label} personal reply was skipped because the agent returned no reply."
                    if not _raw_reply_text
                    else f"Automatic {label} personal reply was suppressed: a hardcoded status/error message may never reach a channel."
                ),
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
            text=_safe_reply_text,
            reply_to_external_message_id=external_message_id,
            metadata={
                "reply_source": str(reply.get("source") or "").strip() or None,
                "media": reply_media or None,
            },
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
        # Auto-replies dispatch as normal messages, not forced quote-reply
        # bubbles: always threading a reply to the triggering message reads
        # as robotic. An explicit "reply to X" send
        # (send_local_bridge_personal_message) still honors a caller-supplied id.
        reply_to_external_message_id=None,
        # Read back from the stored outbound row (not the `reply` var above)
        # so this also carries media on the idempotent-replay path, where
        # `outbound` came from get_outbound_message() and `reply` was never
        # rebuilt this call.
        media=list((outbound.get("metadata") or {}).get("media") or []),
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
    media_items = [item for item in (message.get("media") or []) if isinstance(item, dict)]
    if not external_message_id or not remote_jid or (not text and not media_items):
        raise ValueError("channel.inbound requires external_message_id, remote_jid, and (text or media).")
    # A self-chat message (the owner messaging their own Signal "Note to
    # Self" conversation — the local-bridge analog of WhatsApp's/Telegram's
    # is_self_chat command channel) is always from_me too, since only the
    # linked account can post into its own self-conversation. Let it through
    # here exactly like the WhatsApp/Telegram handlers (`from_me and not
    # is_self_chat`) instead of ignoring every from_me message
    # unconditionally — _is_owner_message's is_self_chat shortcut (above)
    # already treats this as the owner regardless of channel, so no other
    # plumbing is needed once a bridge sets it. An ordinary outgoing message
    # to someone else (or to a group) still has is_self_chat=False and is
    # still ignored here. Whether is_self_chat is ever true depends on the
    # specific bridge actually computing it — see signal-cli-bridge.ts's
    # mapSignalCliReceiveNotification for the reference implementation
    # (BlueBubbles/WeChat bridges don't compute it yet, so iMessage/WeChat
    # keep the previous from_me-always-ignored behavior until they do).
    if bool(message.get("from_me")) and not bool(message.get("is_self_chat")):
        return {"ignored": True, "reason": "from_me", "channel_key": channel_key}
    # Resolved BEFORE the group gate (needed by it — group_policy is
    # per-agent config) exactly like the WhatsApp/Telegram handlers resolve
    # agent_id before their own group gate. Unlike those two, there is no
    # in-app configure step that already claimed a row for most local-bridge
    # channels, so this can genuinely still come back unresolved (ambiguous
    # or unclaimed preferred_gateway_id) — see _resolve_local_bridge_agent_id's
    # own docstring for the full contract, including why that now fails
    # CLOSED (denies) rather than open.
    agent_id = await _resolve_local_bridge_agent_id(
        gateway_id=gateway_id, channel_key=channel_key, registration=registration,
    )
    # Group gate: _enforce_group_policy — the ONE shared resolver, replacing
    # what used to be an inline `if is_group and not is_mentioned and not
    # is_reply_to_sage: skip` here — identical contract to the WhatsApp/
    # Telegram handlers. Whether is_group/is_mentioned/is_reply_to_sage are
    # ever true here depends on the specific bridge (signal-cli-bridge.ts,
    # bluebubbles-bridge.ts, or a third-party WeChat bridge) actually
    # computing them — see local-bridge-runtime.ts's mapInboundEvent.
    group_decision = await _enforce_group_policy(
        registration=registration,
        channel_key=channel_key,
        agent_id=agent_id,
        message=message,
        remote_jid=remote_jid,
    )
    if not group_decision["allowed"]:
        return {
            "ignored": True,
            "reason": group_decision["reason"],
            "channel_key": channel_key,
        }
    inbound, created = personal_channels_repository.record_inbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        agent_id=agent_id,
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
            "media_kinds": [str(item.get("kind") or "").strip() for item in media_items] or None,
        },
    )
    # ── dmPolicy gate ── MUST run before any reply (including a
    # control-command reply) is generated. See _enforce_dm_policy's
    # docstring. existing_state stays None: local-bridge channels don't
    # populate an equivalent of WhatsApp's linked_jid, so
    # _channel_owner_linked_id already returns "" for them regardless — only
    # message["is_self_chat"] can establish is_owner here, exactly as
    # before. When agent_id is still unresolved (see the comment above),
    # this evaluates through _unresolved_identity_dm_policy_config's
    # hardcoded owner_only with no owner signal available, i.e. every
    # sender is blocked — strictly SAFER than the pre-dmPolicy behavior
    # (reply to everyone, unconditionally), never worse.
    dm_decision = await _enforce_dm_policy(
        registration=registration,
        channel_key=channel_key,
        agent_id=agent_id,
        message=message,
        remote_jid=remote_jid,
        existing_state=None,
        label=label,
    )
    if not dm_decision["allowed"]:
        return await _handle_dm_policy_blocked(
            gateway_id=gateway_id,
            registration=registration,
            inbound=inbound,
            channel_key=channel_key,
            provider=provider,
            agent_id=agent_id,
            external_message_id=external_message_id,
            remote_jid=remote_jid,
            duplicate=not created,
            decision=dm_decision,
            trace_id=trace_id,
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

    effective_text, attachments = await _process_inbound_media_for_turn(
        gateway_id=gateway_id,
        channel_key=channel_key,
        provider=provider,
        registration=registration,
        agent_id=agent_id,
        text=text,
        media_items=media_items,
    )
    return await _deliver_local_bridge_personal_reply(
        gateway_id=gateway_id,
        registration=registration,
        inbound=inbound,
        remote_jid=remote_jid,
        external_message_id=external_message_id,
        text=effective_text,
        push_name=str(message.get("push_name") or "").strip() or None,
        duplicate=not created,
        channel_key=channel_key,
        provider=provider,
        label=label,
        trace_id=trace_id,
        attachments=attachments,
        is_owner=bool(dm_decision.get("is_owner")),
        # Same forward-compatible defaults as Telegram above: the local
        # bridge (Signal/iMessage/WeChat on Agent Computer) doesn't resolve
        # is_group/chat_title today, so this reads as False/None until that
        # bridge is upgraded — a safe no-op today, not a regression.
        is_group=bool(message.get("is_group")),
        chat_label=str(message.get("chat_title") or "").strip() or None,
        # The specific participant who sent this message — differs from
        # remote_jid inside a group. Feeds ONLY the canonical
        # InboundEnvelope's sender.id (see
        # personal_channel_sage_bridge_service._build_personal_channel_envelope);
        # present on Signal (signal-cli-bridge.ts's `source`) and iMessage
        # (imsg-imessage-runtime.ts's `event.sender_jid`) alike.
        sender_id=str(message.get("sender_jid") or "").strip(),
        # The REAL "was this message actually addressed" fact from the
        # group gate above — see _enforce_group_policy's own docstring.
        was_addressed=group_decision.get("was_addressed"),
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
    media: Optional[List[Dict[str, Any]]] = None,
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
        metadata={"source": "manual_api", "agent_computer_bridge": True, "media": list(media) if media else None},
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
        media=media,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=channel_key,
        idempotency_key=str(idempotency_key or "").strip(),
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    return delivered or outbound


async def recheck_imessage_personal_gateway(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    agent_id: str = "",
) -> Dict[str, Any]:
    """Live, on-demand re-probe of the imsg bridge on a paired gateway.

    Unlike get_gateway_personal_channel_surfaces() (which reads the LAST
    health snapshot the gateway pushed on its own connect/disconnect —
    see sync path: ImsgIMessagePersonalChannelRuntime.getHealthSnapshot ->
    PersonalChannelRuntimeRegistry.publishRegistryState ->
    gateway_protocol_service's state_update handler ->
    metadata.personal_channel_health), this dispatches a live tool-invoke
    round trip to the gateway RIGHT NOW and returns its fresh per-stage
    probe result directly in the response. This is what the setup panel's
    "Re-check" button calls after the user grants Full Disk Access or
    installs imsg, so the panel doesn't have to wait for the next gateway
    heartbeat/reconnect to see the fix take effect.
    """
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        IMESSAGE_PERSONAL_CHANNEL_KEY,
        IMESSAGE_PERSONAL_PROVIDER,
    )
    # Claim identity for this (gateway_id, channel_key) under the real
    # agent_id the route already received — iMessage's one genuine in-app
    # action for a channel family that otherwise has none (see
    # _resolve_local_bridge_agent_id's docstring). Mirrors
    # configure_whatsapp_personal_gateway/configure_telegram_personal_gateway's
    # own _claim_agent_channel_state call: seed the row BEFORE the round
    # trip, so even a probe that reports back "not connected yet" still
    # resolves inbound messages to the right agent from this point on.
    _claim_agent_channel_state(
        gateway_id=gateway_id, channel_key=IMESSAGE_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id, registration=registration,
    )
    run_id = f"gateway-imessage-recheck-{uuid4().hex[:12]}"
    trace_id = f"gateway-imessage-recheck-{uuid4().hex[:12]}"
    _enforce_personal_gateway_config_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=IMESSAGE_PERSONAL_RECHECK_CAPABILITY,
        run_id=run_id,
        trace_id=trace_id,
    )
    execution = await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=str(gateway_id or "").strip(),
        capability_id=IMESSAGE_PERSONAL_RECHECK_CAPABILITY,
        arguments={},
        run_id=run_id,
        trace_id=trace_id,
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        agent_scope="sage",
    )
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": IMESSAGE_PERSONAL_CHANNEL_KEY,
        **secret_redaction_service.sanitize_mapping(result),
    }


async def install_imessage_imsg_gateway(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    agent_id: str = "",
) -> Dict[str, Any]:
    """Auto-install `imsg` on the paired Mac via Homebrew, run from the
    gateway process itself so the user never opens a terminal.

    Why this is safe to trigger from the gateway (per the setup panel's
    design doc): the gateway already runs as the signed-in Mac user and
    already spawns `imsg` subprocesses directly for the RPC bridge, so one
    more well-known Homebrew formula install (`brew install
    steipete/tap/imsg` — the exact command from OpenClaw's own imsg setup
    docs) under that same already-trusted local process does not cross into
    a new trust boundary. It is still gated behind an explicit button click
    in the panel (never run automatically), and the manual command is
    always ALSO shown in the UI (see IMessageSetupPanel.tsx) as a
    copy-pasteable fallback regardless of whether this succeeds — e.g. a Mac
    without Homebrew, where the gateway-side run reports brewFound: false.

    A slower timeout than the default tool-invoke window: a cold Homebrew
    tap clone (or a source build with no bottle for this macOS/arch) can
    run for a few minutes, longer than the platform's normal 120s
    interactive-tool budget.
    """
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        IMESSAGE_PERSONAL_CHANNEL_KEY,
        IMESSAGE_PERSONAL_PROVIDER,
    )
    # See recheck_imessage_personal_gateway's identical claim call above —
    # same reasoning applies to the install action.
    _claim_agent_channel_state(
        gateway_id=gateway_id, channel_key=IMESSAGE_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id, registration=registration,
    )
    run_id = f"gateway-imessage-install-{uuid4().hex[:12]}"
    trace_id = f"gateway-imessage-install-{uuid4().hex[:12]}"
    _enforce_personal_gateway_config_decision(
        gateway_id=str(gateway_id or "").strip(),
        registration=registration,
        capability_id=IMESSAGE_PERSONAL_INSTALL_CAPABILITY,
        run_id=run_id,
        trace_id=trace_id,
    )
    execution = await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=str(gateway_id or "").strip(),
        capability_id=IMESSAGE_PERSONAL_INSTALL_CAPABILITY,
        arguments={},
        run_id=run_id,
        trace_id=trace_id,
        workspace_id=str(registration.get("workspace_id") or "").strip(),
        agent_scope="sage",
        timeout_seconds=280,
    )
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return {
        "gateway_id": str(gateway_id or "").strip(),
        "channel_key": IMESSAGE_PERSONAL_CHANNEL_KEY,
        **secret_redaction_service.sanitize_mapping(result),
    }


async def send_whatsapp_personal_message(
    *,
    gateway_id: str,
    registration: Dict[str, Any],
    remote_jid: str,
    text: str,
    idempotency_key: str,
    reply_to_external_message_id: Optional[str] = None,
    agent_id: str = "",
    media: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        WHATSAPP_PERSONAL_CHANNEL_KEY,
        WHATSAPP_PERSONAL_PROVIDER,
    )
    outbound, _ = personal_channels_repository.create_or_get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        idempotency_key=str(idempotency_key or "").strip(),
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
        metadata={"source": "manual_api", "media": list(media) if media else None},
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
        media=media,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        idempotency_key=str(idempotency_key or "").strip(),
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    return delivered or outbound


def get_whatsapp_gateway_view(gateway_id: str, *, agent_id: str = "") -> Dict[str, Any]:
    state = personal_channels_repository.get_whatsapp_state(
        str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
    )
    recent = personal_channels_repository.list_recent_gateway_messages(
        str(gateway_id or "").strip(),
        channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
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
    agent_id: str = "",
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        WHATSAPP_PERSONAL_CHANNEL_KEY,
        WHATSAPP_PERSONAL_PROVIDER,
    )
    _claim_agent_channel_state(
        gateway_id=gateway_id, channel_key=WHATSAPP_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id, registration=registration,
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
    agent_id: str = "",
) -> Dict[str, Any]:
    """Full reset: tears down any live/stuck session and clears the entire
    persisted config (phone number, pairing state) so a subsequent setup
    call starts genuinely fresh rather than inheriting a stuck pending
    login — see WhatsAppPersonalRuntime.handleDisconnect()'s doc comment.

    agent_id: accepted for the route contract (see routes_personal_channels.py)
    even though the Gateway itself has exactly one session to tear down
    today, regardless of which agent owns it — the subsequent
    gateway.state.update ("disconnected"/"idle") is picked up by
    sync_gateway_personal_channel_state's own reverse lookup, which
    resolves to this same agent (the most recently touched row)."""
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
    agent_id: str = "",
    media: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        TELEGRAM_PERSONAL_CHANNEL_KEY,
        TELEGRAM_PERSONAL_PROVIDER,
    )
    outbound, _ = personal_channels_repository.create_or_get_outbound_message(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        idempotency_key=str(idempotency_key or "").strip(),
        remote_jid=str(remote_jid or "").strip(),
        text=str(text or "").strip(),
        reply_to_external_message_id=str(reply_to_external_message_id or "").strip() or None,
        metadata={"source": "manual_api", "media": list(media) if media else None},
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
        media=media,
    )
    delivered = personal_channels_repository.mark_outbound_delivered(
        gateway_id=str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
        idempotency_key=str(idempotency_key or "").strip(),
        external_message_id=str(dispatch_result.get("external_message_id") or "").strip() or None,
        metadata={"dispatch_result": dispatch_result},
    )
    return delivered or outbound


def get_telegram_gateway_view(gateway_id: str, *, agent_id: str = "") -> Dict[str, Any]:
    state = personal_channels_repository.get_telegram_state(
        str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
    )
    recent = personal_channels_repository.list_recent_gateway_messages(
        str(gateway_id or "").strip(),
        channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id,
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
    agent_id: str = "",
) -> Dict[str, Any]:
    kill_switch_gate.assert_not_killed(gateway_id=gateway_id)
    channel_lane_contract_service.assert_personal_gateway_channel(
        TELEGRAM_PERSONAL_CHANNEL_KEY,
        TELEGRAM_PERSONAL_PROVIDER,
    )
    _claim_agent_channel_state(
        gateway_id=gateway_id, channel_key=TELEGRAM_PERSONAL_CHANNEL_KEY,
        agent_id=agent_id, registration=registration,
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
    agent_id: str = "",
) -> Dict[str, Any]:
    """Full reset — see disconnect_whatsapp_personal_gateway()'s doc comment
    (including its note on why agent_id is accepted but not load-bearing
    here yet) and TelegramPersonalRuntime.handleDisconnect() for why this
    exists."""
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
        channel_key: "telegram_personal" or "whatsapp_personal" (accepted but
            not yet branched on below — every reply is built via the
            Telegram-specific bridge call regardless of this value, and
            cloud-session-manager/src/ has no whatsapp/ producer at all
            today, so whatsapp_personal never actually arrives here)
        message: {external_message_id, sender_id, sender_name, text,
            received_at} today; optionally is_group/is_mentioned/
            is_reply_to_sage/chat_title if a future upstream adds them (see
            the group gate below — those fields default to "not a group"
            when absent, so this stays backward compatible with the current
            wire shape)
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

    # ── Group/mention gate (backend safety net) ──
    # WIRE REALITY TODAY: the only live producer of this webhook —
    # cloud-session-manager/src/telegram/hmac.js::buildSignedInbound, called
    # from inbound-handler.js — never puts is_group / is_mentioned /
    # is_reply_to_sage on the wire. The signed `message` body is exactly
    # {external_message_id, sender_id, sender_name, linked_username, text,
    # received_at}. So message.get("is_group") is always falsy here in
    # production today and this block is presently a no-op.
    #
    # It is a no-op because the signal is stripped upstream, NOT because
    # groups can't reach this function. GramJS's NewMessage handler
    # (cloud-session-manager/src/telegram/client-factory.js) fires for
    # group/channel chats too and computes a real isGroup; inbound-
    # handler.js:79-123 already gates on it there (isGroup && !isMentioned
    # && !isReplyToSage -> drop the message before it is ever forwarded —
    # added in 927d2c7c "add Saved Messages support + group chat gating",
    # explicitly to prevent "credit drain and Telegram spam risk from
    # replying to every group message", i.e. the same prior incident this
    # backend gate exists for). It then omits is_group/entities/
    # reply_to_msg_id when building the HTTP body, so even an addressed
    # group message that passes that gate arrives here indistinguishable
    # from a DM — and its sender_id is the individual member's JID (not the
    # group's), so a reply would route to a 1:1 chat with that member, not
    # back into the group (a separate, pre-existing routing quirk, not a
    # group-gating one).
    #
    # This block exists as the same defense-in-depth backend safety net the
    # three Gateway handlers above already have — each states "the Gateway-
    # side filter is the primary gate; this is a backend safety net in case
    # the Gateway bypasses it for any reason" (see
    # _handle_telegram_gateway_channel_inbound /
    # _handle_whatsapp_gateway_channel_inbound /
    # _handle_local_bridge_gateway_channel_inbound). The cloud-session-
    # manager path should not be the one ingestion path in this file that
    # trusts a single upstream gate with zero redundancy: if
    # cloud-session-manager's JS gate ever regresses, or some future
    # producer of this same webhook doesn't replicate it, this is what
    # stops an unaddressed group message from reaching a live agent turn.
    #
    # For this to ever actually engage, the upstream payload must start
    # setting message.is_group (bool) and either message.is_mentioned
    # (bool, precomputed) or message.entities (raw, for this side to
    # compute it) plus message.is_reply_to_sage (bool) or
    # message.reply_to_msg_id paired with a sent-message-id set. Missing
    # is_group defaults to False deliberately — a DM (the only shape the
    # wire actually sends today) must never be silently dropped by this
    # gate.
    #
    # Now routed through _enforce_group_policy — the same ONE shared
    # resolver the three Gateway handlers use — instead of its own inline
    # copy, so this stays a genuine no-op today (agent_id="": no per-agent
    # identity resolves on this path, same as the local-bridge handler) but
    # picks up group_policy/requireMention consistently the moment the
    # upstream wire ever does start sending these fields.
    group_decision = await _enforce_group_policy(
        registration={"tenant_id": "default", "workspace_id": resolved_workspace_id},
        channel_key=channel_key,
        agent_id="",
        message=message,
        remote_jid=remote_jid,
    )
    if not group_decision["allowed"]:
        return {
            "ignored": True,
            "reason": group_decision["reason"],
            "channel_key": channel_key,
            "session_id": session_id,
        }

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

    # Build Sage reply using the existing bridge — same as Gateway path.
    # is_owner intentionally NOT passed here (stays at its safe default of
    # False/guarded): this Stage 2 cloud-session-manager path has no
    # dmPolicy/_is_owner_message equivalent that robustly resolves owner
    # identity the way the Gateway-based handlers below do — see
    # HARD CONSTRAINTS in fix/owner-aware-provenance: uncertain identity
    # must default to the guarded/external path, never to owner trust.
    #
    # is_group/chat_label: the SAME "family group" bug fix as the three
    # Gateway handlers (see _handle_telegram_gateway_channel_inbound's
    # matching build_telegram_personal_reply call) — this used to build the
    # reply with zero group signal even for a message that had ALREADY
    # passed the is_group/is_mentioned gate above, so the model was never
    # told an addressed group turn was a group turn at all (it reached
    # _personal_channel_guard_metadata's Chat-Type/Group-Name branch with
    # is_group hardcoded False, the exact class of bug this fix line
    # closes). Wired from the same message.get("is_group")/"chat_title"
    # fields the gate above reads; per this function's own docstring the
    # live wire never sets them today (cloud-session-manager strips them
    # upstream), so this is forward-compatible plumbing, not a behavior
    # change against current production traffic — identical in spirit to
    # the Gateway handlers' own "safe no-op today" comments on this same
    # field.
    reply = await personal_channel_sage_bridge_service.build_telegram_personal_reply_async(
        workspace_id=resolved_workspace_id,
        gateway_id=f"cloud:{session_id}",
        remote_jid=remote_jid,
        text=text,
        push_name=push_name,
        source_event_id=external_message_id,
        is_group=bool(message.get("is_group")),
        chat_label=str(message.get("chat_title") or "").strip() or None,
        was_addressed=group_decision.get("was_addressed"),
    )

    # ABSOLUTE RULE: no hardcoded platform status/error message may EVER be
    # sent into a channel (DM or group) — filter_channel_outbound_reply()
    # is the backstop regardless of what the bridge service returned.
    _raw_reply_text = str((reply or {}).get("text") or "").strip()
    reply_text = filter_channel_outbound_reply(_raw_reply_text) if _raw_reply_text else None
    if not reply_text:
        return {
            "status": "no_reply",
            "session_id": session_id,
            "external_message_id": external_message_id,
        }

    # Dispatch the reply to the cloud session manager
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

