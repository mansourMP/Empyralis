from __future__ import annotations

from typing import Any, Dict, Optional

from server_modules import control_plane_repository
from server_modules.channel_routing_models import ChannelExecutionResult, ChannelRoutingContext
from server_modules.channel_turn_request_service import coerce_dict


async def _ensure_channel_thread(context: ChannelRoutingContext) -> None:
    thread_id = str(context.thread_id or "").strip()
    if not thread_id:
        return
    await control_plane_repository.ensure_agent_thread(
        thread_id=thread_id,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        owner_user_id=str(context.execution_owner.get("user_id") or "").strip() or None,
        master_agent_install_id=str(context.master_install_id or context.responder_install_id or "").strip() or None,
        channel=context.channel_key,
        title=control_plane_repository.build_default_thread_title(context.message),
        metadata={
            "source": "external_channel_ingress",
            "channel_key": context.channel_key,
            "endpoint_key": context.endpoint_key,
            "session_key": context.session_key,
            "responder_install_id": context.responder_install_id,
            "deployed_agent_id": context.deployed_agent_id,
        },
    )


def inbound_event_id(event: Optional[Dict[str, Any]]) -> Optional[str]:
    return str(coerce_dict(event).get("id") or "").strip() or None


def is_duplicate_inbound(event: Optional[Dict[str, Any]]) -> bool:
    return bool(coerce_dict(event).get("_duplicate_hit"))


async def record_inbound_message(
    *,
    context: ChannelRoutingContext,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    await _ensure_channel_thread(context)
    actor = {
        "type": "user",
        "id": context.actor_id,
        "display_name": context.actor_display_name,
    }
    return await control_plane_repository.append_agent_channel_event(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        channel_key=context.channel_key,
        endpoint_key=context.endpoint_key,
        session_key=context.session_key,
        thread_id=context.thread_id,
        responder_install_id=context.responder_install_id,
        direction="inbound",
        event_type="message",
        message_id=str(message_id or "").strip() or None,
        actor=actor,
        text=context.message,
        payload={"message": context.message},
        metadata=context.shared_metadata,
        deployed_agent_id=context.deployed_agent_id,
        status="received",
    )


async def record_outbound_result(
    *,
    context: ChannelRoutingContext,
    result: ChannelExecutionResult,
    parent_event_id: Optional[str],
) -> Dict[str, Any]:
    actor = {
        "type": "assistant",
        "id": context.responder_install_id or getattr(context.manifest, "manifest_id", "agent"),
        "display_name": context.responder_label,
    }
    payload = result.payload or {
        "artifact": result.artifact,
        "steps": result.steps,
        "critic": result.critic,
        "status": result.status,
        "limit_reason": result.limit_reason,
        "retry_after_seconds": result.retry_after_seconds,
        "quota_snapshot": result.quota_snapshot,
        "run_id": result.run_id,
        "metadata": result.metadata,
        "health_safety": result.health_safety,
        "error": result.error,
        "degraded_operations": result.degraded_operations,
    }
    metadata = result.event_metadata or context.shared_metadata
    return await control_plane_repository.append_agent_channel_event(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        channel_key=context.channel_key,
        endpoint_key=context.endpoint_key,
        session_key=context.session_key,
        thread_id=context.thread_id,
        responder_install_id=context.responder_install_id,
        direction="outbound",
        event_type=result.event_type,
        message_id=None,
        parent_event_id=parent_event_id,
        run_id=result.run_id,
        actor=actor,
        text=result.reply,
        payload=payload,
        metadata=metadata,
        deployed_agent_id=context.deployed_agent_id,
        status=result.status,
    )
