"""GET /api/w/{workspace_id}/conversations[/{conversation_id}] -- the
workspace-level Conversations view added to routes_conversations.py.

Two concerns, mirroring the established patterns for owner-gated fleet
routes (test_routes_fleet_auth_guard.py, test_routes_fleet_delete_agent.py):

  1. Auth/isolation over a REAL ASGI request (not a direct Python call),
     proving the dependency wiring itself -- not just the handler body --
     rejects an unauthenticated caller, a caller from a different
     workspace, and a caller with a role below "owner" in the RIGHT
     workspace. This view is deliberately owner-gated (not "viewer", the
     level most read routes in routes_fleet.py use): it is the owner's own
     unified inbox, including their self-chat turns to Sage.

  2. Parsing correctness: given real files written through
     agent_conversation_memory.append_turn (the only writer in
     production, personal_channel_sage_bridge_service.py), the list/load
     endpoints must recover the right {agent, channel, sender} tags per
     conversation_key = f"{surface_channel}:{remote_jid}", and must never
     let a workspace read another workspace's conversation even when it
     knows -- or guesses -- the exact conversation_id.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from server_modules import agent_conversation_memory, routes_conversations


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_conversations.router)
    return app


def _owner_of_ws1() -> dict:
    return {
        "user_id": "owner-1",
        "email": "owner@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "owner",
                "tenant_role": "owner",
            }
        },
    }


def _viewer_of_ws1() -> dict:
    """A genuine ws-1 member, just with role below "owner" -- proves the
    gate is minimum_role="owner", not merely "any authenticated member"."""
    return {
        "user_id": "viewer-1",
        "email": "viewer@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-1": {
                "workspace_id": "ws-1",
                "tenant_id": "tenant-1",
                "role": "viewer",
                "tenant_role": "viewer",
            }
        },
    }


def _intruder_from_ws2() -> dict:
    """A real, authenticated user -- but whose only membership is a
    DIFFERENT workspace than the one being requested."""
    return {
        "user_id": "intruder-1",
        "email": "intruder@example.com",
        "auth_type": "bearer",
        "workspace_access": {
            "ws-2": {
                "workspace_id": "ws-2",
                "tenant_id": "tenant-2",
                "role": "owner",
                "tenant_role": "owner",
            }
        },
    }


def _seed_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point agent_conversation_memory at an isolated temp directory and
    write real turns through append_turn -- the exact write path
    personal_channel_sage_bridge_service.py uses in production -- so these
    tests exercise the real on-disk shape, not a hand-built fixture."""
    monkeypatch.setattr(agent_conversation_memory, "_CONVERSATIONS_ROOT", tmp_path / "conversations")

    # ws-1: owner's own self-chat to Sage (agent_id="") over Telegram --
    # MUST surface in the owner's unified view, same as any other channel.
    agent_conversation_memory.append_turn(
        workspace_id="ws-1", agent_id="", conversation_key="telegram_personal:owner-self",
        role="user", content="remind me to call mom at 5",
    )
    agent_conversation_memory.append_turn(
        workspace_id="ws-1", agent_id="", conversation_key="telegram_personal:owner-self",
        role="assistant", content="Will do -- reminder set for 5pm.",
    )

    # ws-1: a specialist agent's WhatsApp customer conversation.
    agent_conversation_memory.append_turn(
        workspace_id="ws-1", agent_id="agent-42",
        conversation_key="whatsapp_personal:15551230000@s.whatsapp.net",
        role="user", content="what are your hours?",
    )
    agent_conversation_memory.append_turn(
        workspace_id="ws-1", agent_id="agent-42",
        conversation_key="whatsapp_personal:15551230000@s.whatsapp.net",
        role="assistant", content="9-5 Monday to Friday!",
    )

    # ws-2: a DIFFERENT workspace's conversation -- must never appear in,
    # or be loadable from, ws-1's view.
    agent_conversation_memory.append_turn(
        workspace_id="ws-2", agent_id="agent-99", conversation_key="telegram_personal:999",
        role="user", content="ws-2's private message",
    )


# ── Auth / isolation ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_unauthenticated_list_request_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_AUTH_REQUIRED", "1")
    monkeypatch.delenv("EMPYRALIS_DEPLOY_ENV", raising=False)
    monkeypatch.delenv("ORION_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)

    app = _build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/conversations")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_wrong_workspace_list_request_is_rejected() -> None:
    """A real, authenticated session for a DIFFERENT workspace must not be
    able to list ws-1's conversations just by changing the URL segment."""
    app = _build_app()
    app.dependency_overrides[routes_conversations.auth_module.get_current_user] = _intruder_from_ws2

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/conversations")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_wrong_workspace_load_request_is_rejected() -> None:
    """Same for the single-conversation load route."""
    app = _build_app()
    app.dependency_overrides[routes_conversations.auth_module.get_current_user] = _intruder_from_ws2

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/conversations/agent-42:whatsapp_personal_15551230000")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_viewer_role_cannot_list_conversations() -> None:
    """This view is owner-gated, not viewer-gated: a genuine ws-1 member
    with role="viewer" must still get a real 403 -- the owner's unified
    inbox (including their own self-chat) is not a shared team surface."""
    app = _build_app()
    app.dependency_overrides[routes_conversations.auth_module.get_current_user] = _viewer_of_ws1

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/conversations")

    assert response.status_code == 403


# ── Positive path: real data, correct provenance tags ───────────────────


@pytest.mark.anyio
async def test_owner_lists_own_workspace_conversations_with_provenance_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_store(tmp_path, monkeypatch)
    app = _build_app()
    app.dependency_overrides[routes_conversations.auth_module.get_current_user] = _owner_of_ws1

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/conversations")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    conversations = body["conversations"]
    # Exactly ws-1's two conversations -- ws-2's is neither present nor
    # counted, proving the walk never crosses the workspace directory.
    assert len(conversations) == 2

    by_agent = {c["agent_id"]: c for c in conversations}

    sage_convo = by_agent[""]
    assert sage_convo["channel_label"] == "Telegram"
    assert sage_convo["surface_channel"] == "telegram_personal"
    assert sage_convo["sender"] == "owner-self"
    assert sage_convo["last_message_preview"] == "Will do -- reminder set for 5pm."
    assert sage_convo["turn_count"] == 2

    specialist_convo = by_agent["agent-42"]
    assert specialist_convo["channel_label"] == "WhatsApp"
    assert specialist_convo["surface_channel"] == "whatsapp_personal"
    # remote_jid recovery is a heuristic (the ":" delimiter is sanitized to
    # "_" on disk, same as the "@" inside the JID) -- assert the honest,
    # recoverable form rather than claiming a lossless reversal.
    assert specialist_convo["sender"] == "15551230000_s.whatsapp.net"
    assert specialist_convo["last_message_preview"] == "9-5 Monday to Friday!"


@pytest.mark.anyio
async def test_owner_loads_one_conversations_full_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_store(tmp_path, monkeypatch)
    app = _build_app()
    app.dependency_overrides[routes_conversations.auth_module.get_current_user] = _owner_of_ws1

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        list_response = await client.get("/api/w/ws-1/conversations")
        conversation_id = next(
            c["conversation_id"] for c in list_response.json()["conversations"] if c["agent_id"] == "agent-42"
        )
        response = await client.get(f"/api/w/ws-1/conversations/{conversation_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    conversation = body["conversation"]
    assert conversation["agent_id"] == "agent-42"
    assert conversation["channel_label"] == "WhatsApp"
    assert conversation["turns"] == [
        {"role": "user", "content": "what are your hours?"},
        {"role": "assistant", "content": "9-5 Monday to Friday!"},
    ]


@pytest.mark.anyio
async def test_owner_cannot_load_another_workspaces_conversation_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The critical isolation case: the OWNER of ws-1 (a legitimate,
    correctly-scoped caller) tries to load ws-2's conversation_id directly
    (as if guessed or leaked) -- must 404-shaped-false, never the other
    workspace's turns, because conversation_id alone carries no workspace
    scoping -- workspace_id comes from the authenticated URL segment, and
    the file it resolves to must live under THIS workspace's directory."""
    _seed_store(tmp_path, monkeypatch)
    app = _build_app()
    app.dependency_overrides[routes_conversations.auth_module.get_current_user] = _owner_of_ws1

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ws2_list = agent_conversation_memory.list_workspace_conversations(workspace_id="ws-2")
        ws2_conversation_id = ws2_list[0]["conversation_id"]
        response = await client.get(f"/api/w/ws-1/conversations/{ws2_conversation_id}")

    assert response.status_code == 200  # route itself succeeds -- the payload says "not found"
    body = response.json()
    assert body["ok"] is False
    assert body["conversation"] is None


@pytest.mark.anyio
async def test_owner_loading_unknown_conversation_id_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_store(tmp_path, monkeypatch)
    app = _build_app()
    app.dependency_overrides[routes_conversations.auth_module.get_current_user] = _owner_of_ws1

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/w/ws-1/conversations/agent-42:does_not_exist")

    body = response.json()
    assert body["ok"] is False
    assert body["conversation"] is None


@pytest.mark.anyio
async def test_owner_loading_path_traversal_conversation_id_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crafted conversation_id must never escape the workspace's
    conversation directory. A "/" in the id is rejected two ways depending
    on whether it is URL-encoded: Starlette's own path converter refuses
    to route an encoded "/" into a single {conversation_id} segment at
    all (404, before our handler runs); a literal "/" that DOES reach the
    handler is rejected by _parse_conversation_id's own validation (200,
    ok: false). Either is an acceptable "nothing leaked" outcome -- what
    must never happen is a 200 with real conversation data."""
    _seed_store(tmp_path, monkeypatch)
    app = _build_app()
    app.dependency_overrides[routes_conversations.auth_module.get_current_user] = _owner_of_ws1

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        encoded_slash_response = await client.get("/api/w/ws-1/conversations/..%2F..%2F..%2Fetc:passwd")
        literal_dots_response = await client.get("/api/w/ws-1/conversations/agent-42:..")

    assert encoded_slash_response.status_code == 404

    assert literal_dots_response.status_code == 200
    literal_dots_body = literal_dots_response.json()
    assert literal_dots_body["ok"] is False
    assert literal_dots_body["conversation"] is None
