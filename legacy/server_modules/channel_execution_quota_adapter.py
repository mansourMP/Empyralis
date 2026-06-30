from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from server_modules import channel_concurrency_service

ChannelExecutionLimitError = channel_concurrency_service.ChannelExecutionLimitError


@asynccontextmanager
async def channel_execution_slot(
    *,
    tenant_id: str,
    workspace_id: str,
    responder_install_id: Optional[str],
    thread_id: str,
    session_key: str,
    channel_key: str,
    endpoint_key: str,
    workspace: Optional[Dict[str, Any]] = None,
    install: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    async with channel_concurrency_service.channel_execution_slot(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        responder_install_id=responder_install_id,
        thread_id=thread_id,
        session_key=session_key,
        channel_key=channel_key,
        endpoint_key=endpoint_key,
        workspace=workspace,
        install=install,
        metadata=metadata,
    ) as slot:
        yield slot


def quota_snapshot_as_dict(snapshot: Any) -> Optional[Dict[str, Any]]:
    if snapshot is None:
        return None
    if hasattr(snapshot, "as_dict"):
        return snapshot.as_dict()
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return None
