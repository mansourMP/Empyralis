from __future__ import annotations

from typing import Any, Dict, Optional


async def load_deployed_channel_context(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Optional[Dict[str, Any]],
    channel_key: str,
    external_user_id: str,
    session_key: str,
    responder_install_id: Optional[str],
    exclude_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    from server_modules import deployed_agent_memory_service

    return await deployed_agent_memory_service.load_deployed_agent_memory_context(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent=deployed_agent,
        channel_key=channel_key,
        external_user_id=external_user_id,
        session_key=session_key,
        responder_install_id=responder_install_id,
        exclude_event_id=exclude_event_id,
    )


async def persist_deployed_channel_interaction(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent: Optional[Dict[str, Any]],
    channel_key: str,
    external_user_id: str,
    session_key: str,
    thread_id: str,
    responder_install_id: Optional[str],
    user_message: str,
    assistant_reply: str,
) -> Optional[Dict[str, Any]]:
    from server_modules import deployed_agent_memory_service

    return await deployed_agent_memory_service.persist_deployed_agent_memory_snapshot(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        deployed_agent=deployed_agent,
        channel_key=channel_key,
        external_user_id=external_user_id,
        session_key=session_key,
        thread_id=thread_id,
        responder_install_id=responder_install_id,
        user_message=user_message,
        assistant_reply=assistant_reply,
    )
