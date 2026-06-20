"""
Channel → Gateway bridge — the ONLY interface channel code may use to
communicate with the Agent Computer gateway.

This module keeps the channel plane cleanly separated from the execution
plane.  Channel files (routes_*, agent_channel_router, personal_channels_*)
call these functions instead of reaching into gateway_protocol_service or
gateway_execution_service directly.

Architecture contract:
  Channel layer → channel_gateway_bridge → gateway layer → Agent Computer

No channel file should import gateway_protocol_service, gateway_execution_service,
or hardware_action_broker_service directly.  Use this bridge instead.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


async def dispatch_channel_message(
    *,
    gateway_id: str,
    channel_key: str,
    message_payload: Dict[str, Any],
    run_id: str = "",
    trace_id: str = "",
    request_id: str = "",
    workspace_id: str = "",
    ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Send an outbound channel message through the gateway.

    This is the delivery mechanism for personal gateway channels
    (WhatsApp Personal, Telegram Personal, iMessage, Signal, WeChat).
    Equivalent to the Telegram Bot API's sendMessage for the hosted bot.
    """
    from server_modules import gateway_protocol_service

    return await gateway_protocol_service.dispatch_channel_outbound(
        gateway_id=gateway_id,
        channel_key=channel_key,
        message_payload=message_payload,
        run_id=run_id,
        trace_id=trace_id,
        request_id=request_id,
        workspace_id=workspace_id,
        ttl_seconds=ttl_seconds,
    )


async def execute_channel_configuration(
    *,
    gateway_id: str,
    capability_id: str,
    arguments: Dict[str, Any],
    run_id: str = "",
    trace_id: str = "",
    workspace_id: str = "",
    registration: Optional[Dict[str, Any]] = None,
    agent_scope: str = "sage",
) -> Dict[str, Any]:
    """Execute a channel configuration operation on the gateway.

    Used for pairing, QR-code generation, session setup, and other
    channel infrastructure operations.  NOT for message delivery —
    use dispatch_channel_message() for that.
    """
    from server_modules import gateway_execution_service

    return await gateway_execution_service.execute_tool_via_gateway(
        gateway_id=gateway_id,
        capability_id=capability_id,
        arguments=arguments,
        run_id=run_id,
        trace_id=trace_id,
        workspace_id=workspace_id,
        agent_scope=agent_scope,
    )


def gateway_connection_is_live(gateway_id: str) -> bool:
    """Check if the gateway has an active WebSocket connection."""
    from server_modules import gateway_protocol_service

    return gateway_protocol_service.gateway_connection_is_live(gateway_id)


async def enforce_gateway_service_decision(
    *,
    operation: str,
    gateway_id: str,
    workspace_id: str,
    tenant_id: str = "",
    actor_id: str = "",
    quota_profile: str = "",
    capability_id: str = "",
    run_id: str = "",
    trace_id: str = "",
    request_id: str = "",
    approval_provided: bool = False,
    approval_memory_hit: bool = False,
    risk_decision: str = "",
    browser_session_id: str = "",
) -> Dict[str, Any]:
    """Enforce a gateway service decision through the Rust kernel."""
    from server_modules.routes_gateway import _enforce_gateway_service_decision

    return _enforce_gateway_service_decision(
        operation=operation,
        gateway_id=gateway_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        quota_profile=quota_profile,
        capability_id=capability_id,
        run_id=run_id,
        trace_id=trace_id,
        request_id=request_id,
        approval_provided=approval_provided,
        approval_memory_hit=approval_memory_hit,
        risk_decision=risk_decision,
        browser_session_id=browser_session_id,
    )
