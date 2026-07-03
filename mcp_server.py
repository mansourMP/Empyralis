"""Empyralis MCP server — exposes the platform as tools for external AI clients.

Two MCP surfaces in Empyralis:

1. **Empyralis AS an MCP server** (this file) — external AI clients (Claude Code,
   Claude Desktop, ChatGPT) connect TO Empyralis at ``/mcp`` to call platform tools.
   Auth: per-workspace API key (bearer token).

2. **Empyralis as an MCP client** — Empyralis connects TO 30+ external MCP
   services (Gmail, GitHub, Slack, Notion, etc.) via ``mcp_registry_service.py``.
   Auth: OAuth → credential vault → MCP server registration.

Both surfaces are cloud-to-cloud.  No user hardware needed for either.

Tools (Phase U2)
----------------
Read + chat (always live):
  - ``empyralis_list_agents`` → fleet_list_agents
  - ``empyralis_get_agent_activity`` → fleet_get_agent_activity
  - ``empyralis_memory_read`` → memory_read
  - ``empyralis_memory_list`` → memory_list
  - ``empyralis_chat`` → full turn through normal chat path (triage, ledger, AI)

Write (gated behind ``EMPYRALIS_MCP_WRITE_ENABLED=true``):
  - ``empyralis_create_agent`` → fleet_create_agent
  - ``empyralis_configure_agent`` → fleet_configure_agent
  - ``empyralis_message_agent`` → fleet_message_agent
  - ``empyralis_memory_write`` → memory_write

All calls are ledgered with ``event_class="mcp_inbound"`` and
``actor="external_mcp_client"``.  Workspace is resolved from the API
key — never from tool arguments.  Operator-role rules apply.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI

try:
    from mcp.server.fastmcp import Context, FastMCP
except Exception:
    FastMCP = None  # type: ignore[assignment]
    Context = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

EMPYRALIST_MCP_PATH = "/mcp"
EMPYRALIST_MCP_NAME = "empyralist"
EMPYRALIST_MCP_ENDPOINT = "http://127.0.0.1:8001/mcp"
EMPYRALIST_MCP_TOOLS = [
    "empyralis_list_agents",
    "empyralis_get_agent_activity",
    "empyralis_memory_read",
    "empyralis_memory_list",
    "empyralis_chat",
    "empyralis_create_agent",
    "empyralis_configure_agent",
    "empyralis_message_agent",
    "empyralis_memory_write",
]

_WRITE_ENABLED_GLOBAL = os.getenv("EMPYRALIS_MCP_WRITE_ENABLED", "").strip().lower() in {
    "1", "true", "yes",
}  # Global emergency off-switch — when false, ALL write tools are blocked regardless of per-key settings.


# ── API key resolution ──────────────────────────────────────────────────


async def _resolve_workspace(ctx: Any) -> Dict[str, Any]:
    """Extract ``{workspace_id, writes_enabled}`` from the MCP request's Authorization header."""
    auth = ""
    try:
        headers = getattr(getattr(ctx, "request_context", None), "request", None)
        if headers is not None:
            scope = getattr(headers, "scope", {})
            for h in scope.get("headers", []):
                if h[0] == b"authorization":
                    auth = h[1].decode()
                    break
    except Exception:
        pass

    if not auth:
        raise RuntimeError(
            "Missing MCP API key. Add an Authorization header: "
            '"Bearer empyralis_mcp_..." — create a key at POST /api/connections/mcp-keys.'
        )

    from server_modules.mcp_server_auth import resolve_workspace_from_api_key

    resolved = await resolve_workspace_from_api_key(auth)
    if not resolved:
        raise RuntimeError(
            "Invalid or revoked MCP API key. Create a new key at POST /api/connections/mcp-keys."
        )
    return resolved


async def _ledger_mcp_call(workspace_id: str, tool_name: str, ok: bool, **extra: Any) -> None:
    """Write an mcp_inbound ledger event."""
    try:
        from server_modules.runs_core import emit_log
        emit_log(
            workspace_id=workspace_id,
            event_class="mcp_inbound",
            action=tool_name,
            actor_id="external_mcp_client",
            metadata={"ok": ok, **extra},
        )
    except Exception:
        LOGGER.debug("Failed to ledger MCP call %s", tool_name, exc_info=True)


# ── Build server ──────────────────────────────────────────────────────────


def _build_mcp_server() -> FastMCP | None:
    if FastMCP is None:
        return None
    return FastMCP(EMPYRALIST_MCP_NAME)


empyralist_mcp = _build_mcp_server()


# ── Register tools ────────────────────────────────────────────────────────

if empyralist_mcp is not None:

    # ── Helpers ──────────────────────────────────────────────────────

    async def _resolve(ctx: Context) -> Dict[str, Any]:
        """Resolve ``{workspace_id, writes_enabled}`` from the request."""
        return await _resolve_workspace(ctx)

    def _ws(resolved: Dict[str, Any]) -> str:
        """Extract workspace_id from resolved auth data."""
        return str(resolved["workspace_id"])

    def _check_write(resolved: Dict[str, Any]) -> None:
        """Raise if writes are not permitted for this key."""
        if not _WRITE_ENABLED_GLOBAL:
            raise RuntimeError(
                "MCP write tools are globally disabled. "
                "Set EMPYRALIS_MCP_WRITE_ENABLED=true on the server."
            )
        if not resolved.get("writes_enabled"):
            raise RuntimeError(
                "This API key does not have write access. "
                "Create a new key with writes_enabled=true at POST /api/connections/mcp-keys."
            )

    # ── Read + chat tools (always live) ──────────────────────────────

    @empyralist_mcp.tool()
    async def empyralis_list_agents(ctx: Context) -> Dict[str, Any]:
        """List all agents in your Empyralis workspace."""
        r = await _resolve(ctx); ws = _ws(r)
        from server_modules.fleet_tools import fleet_list_agents
        result = await fleet_list_agents(workspace_id=ws, actor_id="external_mcp_client")
        await _ledger_mcp_call(ws, "empyralis_list_agents", True, agent_count=len(result.get("agents", [])))
        return {"ok": True, **result}

    @empyralist_mcp.tool()
    async def empyralis_get_agent_activity(
        agent_id: str, limit: int = 20, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get recent ledger activity for a specific agent."""
        r = await _resolve(ctx); ws = _ws(r)
        from server_modules.fleet_tools import fleet_get_agent_activity
        result = await fleet_get_agent_activity(
            workspace_id=ws, agent_id=agent_id, limit=limit, actor_id="external_mcp_client",
        )
        await _ledger_mcp_call(ws, "empyralis_get_agent_activity", True, agent_id=agent_id)
        return {"ok": True, **result}

    @empyralist_mcp.tool()
    async def empyralis_memory_read(key: str, ctx: Context = None) -> Dict[str, Any]:
        """Read a memory entry by key from your workspace."""
        r = await _resolve(ctx); ws = _ws(r)
        from server_modules.agent_memory_tools import memory_read
        result = await memory_read(workspace_id=ws, key=key)
        await _ledger_mcp_call(ws, "empyralis_memory_read", True, key=key)
        return {"ok": True, "key": key, "value": result}

    @empyralist_mcp.tool()
    async def empyralis_memory_list(ctx: Context = None) -> Dict[str, Any]:
        """List all memory entries in your workspace."""
        r = await _resolve(ctx); ws = _ws(r)
        from server_modules.agent_memory_tools import memory_list
        entries = await memory_list(workspace_id=ws)
        await _ledger_mcp_call(ws, "empyralis_memory_list", True, entry_count=len(entries or []))
        return {"ok": True, "entries": entries or []}

    @empyralist_mcp.tool()
    async def empyralis_chat(message: str, agent_id: str = "", ctx: Context = None) -> Dict[str, Any]:
        """Send a message through the full Empyralis turn (triage, ledger, AI)."""
        r = await _resolve(ctx); ws = _ws(r)
        from server_modules.direct_chat_runtime_exports import build_operator_namespace
        from server_modules.direct_chat_operator_binding_service import build_direct_chat_module_export_map_from_namespace

        namespace = build_operator_namespace(workspace_id=ws)
        export_map = build_direct_chat_module_export_map_from_namespace(namespace=namespace)
        collect_fn = export_map.get("collect_direct_operator_reply")
        if collect_fn is None:
            raise RuntimeError("Chat runtime not available.")

        payload = await collect_fn(message=message)
        reply = str(payload.get("reply") or "").strip()
        await _ledger_mcp_call(ws, "empyralis_chat", True, message_len=len(message), reply_len=len(reply))
        return {"ok": True, "reply": reply, "agent_id": agent_id or "(sage)"}

    # ── Write tools (gated per-key + global off-switch) ──────────────

    @empyralist_mcp.tool()
    async def empyralis_create_agent(name: str, ctx: Context = None) -> Dict[str, Any]:
        """Create a new specialist agent. Requires writes_enabled on the API key."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from server_modules.fleet_tools import fleet_create_agent
        result = await fleet_create_agent(workspace_id=ws, agent_label=name, actor_id="external_mcp_client")
        await _ledger_mcp_call(ws, "empyralis_create_agent", result.get("ok", False), agent_label=name)
        return result

    @empyralist_mcp.tool()
    async def empyralis_configure_agent(
        agent_id: str, patch: Dict[str, Any], ctx: Context = None,
    ) -> Dict[str, Any]:
        """Configure agent settings. Requires writes_enabled on the API key."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from server_modules.fleet_tools import fleet_configure_agent
        result = await fleet_configure_agent(
            workspace_id=ws, agent_id=agent_id, patch_dict=patch, actor_id="external_mcp_client",
        )
        await _ledger_mcp_call(ws, "empyralis_configure_agent", result.get("ok", False), agent_id=agent_id)
        return result

    @empyralist_mcp.tool()
    async def empyralis_message_agent(
        agent_id: str, message: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Send message to agent's fleet inbox. Requires writes_enabled on the API key."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from server_modules.fleet_tools import fleet_message_agent
        result = await fleet_message_agent(
            workspace_id=ws, agent_id=agent_id, message=message, actor_id="external_mcp_client",
        )
        await _ledger_mcp_call(ws, "empyralis_message_agent", result.get("ok", False), agent_id=agent_id)
        return result

    @empyralist_mcp.tool()
    async def empyralis_memory_write(key: str, value: str, ctx: Context = None) -> Dict[str, Any]:
        """Write a memory entry. Requires writes_enabled on the API key."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from server_modules.agent_memory_tools import memory_write
        result = await memory_write(workspace_id=ws, key=key, value=value)
        await _ledger_mcp_call(ws, "empyralis_memory_write", True, key=key)
        return {"ok": True, "key": key, "result": result}


# ── Mount + lifespan ─────────────────────────────────────────────────────


def mount_empyralist_mcp(app: FastAPI) -> None:
    if empyralist_mcp is None:
        return
    app.mount(EMPYRALIST_MCP_PATH, empyralist_mcp.streamable_http_app())


@asynccontextmanager
async def empyralist_mcp_lifespan() -> AsyncIterator[None]:
    if empyralist_mcp is None:
        yield
        return
    async with empyralist_mcp.session_manager.run():
        yield
