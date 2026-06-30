from __future__ import annotations

import logging

from server_modules import conversation_memory_facade_service, failure_policy_service
from server_modules.conversation_memory_policy import EXTERNAL_CHANNEL_CUSTOMER_PROFILE
from server_modules.channel_routing_models import ChannelRoutingContext
from server_modules.channel_turn_request_service import rebuild_turn_request
from server_modules.error_contracts import STORAGE_WRITE_FAILURE, SEVERITY_ERROR


logger = logging.getLogger(__name__)


async def apply_memory_overlay(
    *,
    context: ChannelRoutingContext,
    inbound_event_id: str | None,
) -> ChannelRoutingContext:
    memory_context = await conversation_memory_facade_service.aload_context(
        conversation_memory_facade_service.ConversationMemorySubject(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            surface_kind=conversation_memory_facade_service.DEPLOYED_AGENT_CHANNEL_SURFACE,
            responder_install_id=str(context.responder_install_id or "").strip(),
            thread_id=context.thread_id,
            session_key=context.session_key,
            deployed_agent_id=str(context.deployed_agent_id or "").strip(),
            channel_key=context.channel_key,
            external_user_id=context.actor_id,
        ),
        EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
        exclude_event_id=inbound_event_id,
        metadata={"deployed_agent": context.deployed_agent},
    )
    shared_metadata = dict(context.shared_metadata)
    if bool(memory_context.diagnostics.get("enabled")):
        shared_metadata.update(
            {
                "conversation_memory_enabled": True,
                "conversation_memory_summary_present": bool(memory_context.diagnostics.get("summary_present")),
                "conversation_memory_message_count": int(memory_context.diagnostics.get("message_count") or 0),
                "conversation_memory_compacted": bool(memory_context.compacted),
            }
        )
    business_plan = str(memory_context.business_plan or "").strip() or None
    return rebuild_turn_request(
        context,
        request_id=inbound_event_id,
        prior_messages=memory_context.prior_messages or None,
        business_plan=business_plan,
        shared_metadata=shared_metadata,
    )


async def persist_snapshot(
    *,
    context: ChannelRoutingContext,
    assistant_reply: str,
) -> dict[str, object] | None:
    try:
        await conversation_memory_facade_service.apersist_interaction(
            conversation_memory_facade_service.ConversationMemorySubject(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                surface_kind=conversation_memory_facade_service.DEPLOYED_AGENT_CHANNEL_SURFACE,
                responder_install_id=str(context.responder_install_id or "").strip(),
                thread_id=context.thread_id,
                session_key=context.session_key,
                deployed_agent_id=str(context.deployed_agent_id or "").strip(),
                channel_key=context.channel_key,
                external_user_id=context.actor_id,
            ),
            EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
            user_message=context.message,
            assistant_reply=assistant_reply,
            metadata={"deployed_agent": context.deployed_agent},
        )
        return None
    except Exception:
        return failure_policy_service.log_degraded_operation(
            logger=logger,
            code="channel_memory_snapshot_persist_failed",
            message="Failed to persist deployed-agent memory snapshot.",
            error_class=STORAGE_WRITE_FAILURE,
            degraded_component="channel_memory_snapshot",
            severity=SEVERITY_ERROR,
            retryable=False,
            status_code=500,
            request_id=str(context.shared_metadata.get("request_id") or "").strip() or None,
            metadata={
                "tenant_id": context.tenant_id,
                "workspace_id": context.workspace_id,
                "channel_key": context.channel_key,
                "deployed_agent_id": context.deployed_agent_id,
                "session_key": context.session_key,
                "thread_id": context.thread_id,
            },
        )
