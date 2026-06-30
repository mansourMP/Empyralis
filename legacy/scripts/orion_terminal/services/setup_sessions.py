from __future__ import annotations

from typing import Any, Dict, Optional

from ..clients.runtime import RuntimeClient


def create_setup_session(
    client: RuntimeClient,
    workspace_id: str,
    provider: Optional[str] = None,
    flow: Optional[str] = None,
) -> Dict[str, Any]:
    return client.create_setup_session(workspace_id=workspace_id, provider=provider, flow=flow)


def get_setup_session(
    client: RuntimeClient,
    session_id: str,
) -> Dict[str, Any]:
    return client.get_setup_session(session_id=session_id)


def setup_action(
    client: RuntimeClient,
    session_id: str,
    action: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return client.setup_action(session_id=session_id, action=action, payload=payload)


def complete_setup_session(client: RuntimeClient, session_id: str) -> Dict[str, Any]:
    return client.complete_setup_session(session_id)


def cancel_setup_session(client: RuntimeClient, session_id: str) -> Dict[str, Any]:
    return client.cancel_setup_session(session_id)


def resume_setup_session(client: RuntimeClient, session_id: str) -> Dict[str, Any]:
    return client.resume_setup_session(session_id)
