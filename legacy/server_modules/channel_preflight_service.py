from __future__ import annotations

from typing import Any, Tuple

from server_modules import safe_mode_service
from server_modules.channel_errors import ChannelIngressValidationError, ChannelSecurityDeniedError
from server_modules.channel_identity_service import SUPPORTED_CHANNEL_KEYS, normalize_channel_key, normalize_endpoint_key


def assert_inbound_allowed(
    *,
    tenant_id: str,
    workspace_id: str,
    channel_key: str,
    endpoint_key: Any,
    customer_message: str,
) -> Tuple[str, str, str]:
    resolved_channel_key = normalize_channel_key(channel_key)
    if resolved_channel_key not in SUPPORTED_CHANNEL_KEYS:
        raise ChannelIngressValidationError("Unsupported channel.")
    resolved_endpoint_key = normalize_endpoint_key(resolved_channel_key, endpoint_key)
    if not resolved_endpoint_key:
        raise ChannelIngressValidationError("endpoint_key is required.")
    resolved_message = str(customer_message or "").strip()
    if not resolved_message:
        raise ChannelIngressValidationError("customer_message is required.")
    workspace_policy = safe_mode_service.resolve_machine_policy_status(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if bool((workspace_policy.get("kill_switch") or {}).get("active")):
        raise ChannelSecurityDeniedError("This workspace is temporarily disabled by a security kill switch.")
    if safe_mode_service.is_channel_disabled(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        channel_key=resolved_channel_key,
        endpoint_key=resolved_endpoint_key,
    ):
        raise ChannelSecurityDeniedError("This channel is temporarily disabled by a security control.")
    return resolved_channel_key, resolved_endpoint_key, resolved_message


def assert_agent_allowed(
    *,
    tenant_id: str,
    workspace_id: str,
    responder_install_id: str | None,
) -> None:
    if responder_install_id and safe_mode_service.is_agent_disabled(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_install_id=responder_install_id,
    ):
        raise ChannelSecurityDeniedError("This agent is temporarily disabled by a security control.")
