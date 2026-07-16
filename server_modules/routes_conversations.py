"""Workspace-level Conversations view: every channel conversation across
every agent in one workspace, in ONE place, tagged with provenance --
which agent, which channel (Telegram/WhatsApp/...), and which person/group.

This was promised ("everything goes to the same place, tagged by where it
came from") but never built: the frontend had zero reader of the durable
channel-conversation store. Reads server_modules/agent_conversation_memory.py
directly -- the per-(workspace, agent, conversation) JSONL store that
actually survives a backend restart. /api/threads (control_plane_repository's
agent_turns table, via thread_service.py) is NOT used here: that SQL path
is dead under SQLite-fallback prod, which is exactly why this reader never
existed before now (see agent_conversation_memory.py's own docstring).

Owner-gated (minimum_role="owner", not the "viewer" most read routes in
routes_fleet.py use): this is the owner's OWN unified inbox across every
agent -- including their own self-chat/command-channel turns to Sage, not
a shared team-support surface.

Routes:
  GET /api/w/{workspace_id}/conversations
  GET /api/w/{workspace_id}/conversations/{conversation_id}
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query, Request

from server_modules import auth as auth_module

router = APIRouter(tags=["conversations"])


@router.get("/api/w/{workspace_id}/conversations")
async def list_workspace_conversations_route(
    request: Request,
    workspace_id: str,
    limit: int = Query(200, ge=1, le=1000, description="Max conversations to return"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """Every conversation across every agent in this workspace, newest
    activity first -- the reader this durable store never had."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import agent_conversation_memory

    try:
        conversations = agent_conversation_memory.list_workspace_conversations(workspace_id=resolved_workspace_id)
        return {"ok": True, "conversations": conversations[: max(1, limit)]}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "conversations": []}


@router.get("/api/w/{workspace_id}/conversations/{conversation_id}")
async def load_workspace_conversation_route(
    request: Request,
    workspace_id: str,
    conversation_id: str,
    limit: int = Query(200, ge=1, le=400, description="Max turns to return (store caps at 400)"),
    current_user: Dict[str, Any] = Depends(auth_module.get_current_user),
) -> Dict[str, Any]:
    """One conversation's turns plus its provenance tags, for the
    Conversations view's transcript pane."""
    resolved_workspace_id = auth_module.enforce_workspace_access(current_user, workspace_id, minimum_role="owner")
    from server_modules import agent_conversation_memory

    try:
        conversation = agent_conversation_memory.load_conversation(
            workspace_id=resolved_workspace_id, conversation_id=conversation_id, limit=limit,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "conversation": None}
    if conversation is None:
        return {"ok": False, "error": "Conversation not found.", "conversation": None}
    return {"ok": True, "conversation": conversation}
