"""Empyralis MCP server — exposes the platform as tools for external AI clients.

Two MCP surfaces in Empyralis:

1. **Empyralis AS an MCP server** (this file) — external AI clients (Claude Code,
   Claude Desktop, ChatGPT) connect TO Empyralis at ``/mcp`` to call platform tools.
   Auth: per-workspace API key (bearer token).

2. **Empyralis as an MCP client** — Empyralis connects TO 30+ external MCP
   services (Gmail, GitHub, Slack, Notion, etc.) via ``mcp_registry_service.py``.
   Auth: OAuth → credential vault → MCP server registration.

Both surfaces are cloud-to-cloud.  No user hardware needed for either.

Tools (Phase 3D)
----------------
Read + chat (always live):
  - ``empyralis_list_projects`` → projects_repository.list_projects
  - ``empyralis_list_agents`` → fleet_list_agents (+ project, channel, connector status)
  - ``empyralis_get_agent_activity`` → fleet_get_agent_activity
  - ``empyralis_get_agent_conversations`` → deployed_agent_service.list_deployed_agent_conversations
  - ``empyralis_memory_read`` → memory_read
  - ``empyralis_memory_list`` → memory_list
  - ``empyralis_chat`` → full turn through normal chat path (triage, ledger, AI)

Write (gated behind ``EMPYRALIS_MCP_WRITE_ENABLED=true`` + per-key writes_enabled):
  - ``empyralis_create_project`` → projects_repository.create_project
  - ``empyralis_create_agent`` → fleet_create_agent (+ projects_repository.assign_install_to_project)
  - ``empyralis_configure_agent`` → fleet_configure_agent
  - ``empyralis_message_agent`` → fleet_message_agent
  - ``empyralis_memory_write`` → memory_write
  - ``empyralis_assign_channel_bot`` → hosted_bot_provisioning_service / discord_bot_provisioning_service
  - ``empyralis_release_channel_bot`` → hosted_bot_provisioning_service / discord_bot_provisioning_service
  - ``empyralis_connect_connector`` → connection_oauth_service.start_oauth (returns authorization_url)
  - ``empyralis_trigger_test_turn`` → deployed_agent_test_turn_service.execute_test_turn

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
    # Read (always live)
    "empyralis_list_projects",
    "empyralis_list_agents",
    "empyralis_get_agent_activity",
    "empyralis_get_agent_conversations",
    "empyralis_memory_read",
    "empyralis_memory_list",
    "empyralis_chat",
    # Write (gated behind EMPYRALIS_MCP_WRITE_ENABLED + per-key writes_enabled)
    "empyralis_create_project",
    "empyralis_create_agent",
    "empyralis_configure_agent",
    "empyralis_message_agent",
    "empyralis_memory_write",
    "empyralis_assign_channel_bot",
    "empyralis_release_channel_bot",
    "empyralis_connect_connector",
    "empyralis_trigger_test_turn",
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

    async def _tenant(ws: str) -> str:
        """Resolve the tenant for a workspace — same derivation the fleet routes use."""
        from server_modules import control_plane_repository as cpr
        return await cpr.resolve_tenant_id_for_workspace(ws, default="default")

    # ── Read + chat tools (always live) ──────────────────────────────

    @empyralist_mcp.tool()
    async def empyralis_list_projects(ctx: Context) -> Dict[str, Any]:
        """List the projects (client/company groupings of agents) in your workspace."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import projects_repository as _p
        projects = await _p.list_projects(tenant_id=tenant, workspace_id=ws)
        counts = await _p.count_agents_by_project(tenant_id=tenant, workspace_id=ws)
        for p in projects:
            p["agent_count"] = int(counts.get(p.get("id"), 0))
        await _ledger_mcp_call(ws, "empyralis_list_projects", True, project_count=len(projects))
        return {"ok": True, "projects": projects}

    @empyralist_mcp.tool()
    async def empyralis_list_agents(ctx: Context) -> Dict[str, Any]:
        """List agents in your workspace, each enriched with its project name,
        connected channels, and connected connectors."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules.fleet_tools import fleet_list_agents
        from server_modules import agent_bindings_repository as _b
        from server_modules import projects_repository as _p

        result = await fleet_list_agents(workspace_id=ws, tenant_id=tenant, actor_id="external_mcp_client")
        agents = result.get("agents", []) if result.get("ok") else []

        chan = await _b.list_workspace_channel_bindings(tenant_id=tenant, workspace_id=ws, enabled_only=True)
        conn = await _b.list_workspace_connector_bindings(tenant_id=tenant, workspace_id=ws, enabled_only=True)
        chan_by_agent: Dict[str, list] = {}
        for row in chan:
            chan_by_agent.setdefault(str(row.get("agent_install_id")), []).append(str(row.get("key")))
        conn_by_agent: Dict[str, list] = {}
        for row in conn:
            conn_by_agent.setdefault(str(row.get("agent_install_id")), []).append(str(row.get("key")))
        projects = {p.get("id"): p for p in await _p.list_projects(tenant_id=tenant, workspace_id=ws)}

        for a in agents:
            aid = str(a.get("agent_id"))
            a["channels"] = sorted(set(chan_by_agent.get(aid, [])))
            a["connectors"] = sorted(set(conn_by_agent.get(aid, [])))
            a["project_name"] = (projects.get(str(a.get("project_id") or "")) or {}).get("name", "")
        await _ledger_mcp_call(ws, "empyralis_list_agents", True, agent_count=len(agents))
        return {"ok": True, "agents": agents}

    @empyralist_mcp.tool()
    async def empyralis_get_agent_activity(
        agent_id: str, limit: int = 20, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Get recent ledger activity for a specific agent."""
        r = await _resolve(ctx); ws = _ws(r)
        from server_modules.fleet_tools import fleet_get_agent_activity
        result = await fleet_get_agent_activity(
            workspace_id=ws, agent_id=agent_id, actor_id="external_mcp_client",
        )
        if isinstance(result, dict) and isinstance(result.get("events"), list) and limit and limit > 0:
            result = {**result, "events": result["events"][: int(limit)]}
        await _ledger_mcp_call(ws, "empyralis_get_agent_activity", True, agent_id=agent_id)
        return {"ok": True, **result}

    @empyralist_mcp.tool()
    async def empyralis_get_agent_conversations(
        agent_id: str, limit: int = 20, ctx: Context = None,
    ) -> Dict[str, Any]:
        """List a deployed agent's recent conversations with its end customers.

        ``agent_id`` is a deployed-agent id. Returns ``ok: False`` with a clear
        reason when the agent is not a customer-facing deployed agent.
        """
        r = await _resolve(ctx); ws = _ws(r)
        from server_modules import deployed_agent_service
        current_user = {"user_id": "external_mcp_client", "email": "", "mcp_workspace_id": ws}
        try:
            payload = await deployed_agent_service.list_deployed_agent_conversations(
                deployed_agent_id=agent_id, current_user=current_user,
                owner_workspace_id=ws, limit=limit, offset=0,
            )
        except Exception as exc:  # noqa: BLE001 — surface a clean reason to the client
            await _ledger_mcp_call(ws, "empyralis_get_agent_conversations", False, agent_id=agent_id)
            return {"ok": False, "error": str(exc), "agent_id": agent_id}
        await _ledger_mcp_call(ws, "empyralis_get_agent_conversations", True, agent_id=agent_id)
        return {"ok": True, "agent_id": agent_id, **(payload if isinstance(payload, dict) else {})}

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
    async def empyralis_create_project(
        name: str, description: str = "", ctx: Context = None,
    ) -> Dict[str, Any]:
        """Create a project (a client/company grouping of agents). Requires writes_enabled."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import projects_repository as _p
        try:
            project = await _p.create_project(
                tenant_id=tenant, workspace_id=ws, name=name, description=description,
            )
        except Exception as exc:  # noqa: BLE001
            await _ledger_mcp_call(ws, "empyralis_create_project", False, name=name)
            return {"ok": False, "error": str(exc)}
        await _ledger_mcp_call(ws, "empyralis_create_project", True, project_id=project.get("id"))
        return {"ok": True, "project": project}

    @empyralist_mcp.tool()
    async def empyralis_create_agent(
        name: str, project_id: str = "", instructions: str = "",
        purpose_preset: str = "", ctx: Context = None,
    ) -> Dict[str, Any]:
        """Create a new specialist agent, optionally inside a project.
        Requires writes_enabled on the API key."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules.fleet_tools import fleet_create_agent
        from server_modules import projects_repository as _p

        result = await fleet_create_agent(
            actor_id="external_mcp_client", workspace_id=ws, tenant_id=tenant,
            name=name, instructions=instructions, purpose_preset=purpose_preset,
        )
        agent_id = str(result.get("agent_id") or "").strip()
        assigned_project = ""
        if result.get("ok") and agent_id and str(project_id or "").strip():
            try:
                if await _p.assign_install_to_project(
                    tenant_id=tenant, workspace_id=ws,
                    install_id=agent_id, project_id=str(project_id).strip(),
                ):
                    assigned_project = str(project_id).strip()
            except Exception as exc:  # noqa: BLE001 — agent still created; report the linkage failure
                result["project_assignment_error"] = str(exc)
        result["project_id"] = assigned_project
        await _ledger_mcp_call(ws, "empyralis_create_agent", result.get("ok", False), agent_id=agent_id, project_id=assigned_project)
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

    @empyralist_mcp.tool()
    async def empyralis_assign_channel_bot(
        agent_id: str, channel: str, token: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Bind a BYO bot to an agent so it owns that channel. ``channel`` is
        'telegram' or 'discord'. One bot binds to exactly one agent. Requires writes_enabled."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r); tenant = await _tenant(ws)
        ch = str(channel or "").strip().lower()
        try:
            if ch in ("discord", "discord_bot"):
                from server_modules import discord_bot_provisioning_service as prov
                result = await prov.assign_agent_discord(
                    agent_install_id=agent_id, workspace_id=ws, tenant_id=tenant, token=token,
                )
            elif ch in ("telegram", "telegram_bot"):
                from server_modules import hosted_bot_provisioning_service as prov
                result = await prov.assign_byo_bot(
                    agent_install_id=agent_id, workspace_id=ws, tenant_id=tenant, token=token,
                )
            else:
                return {"ok": False, "error": f"Unsupported channel '{channel}'. Use 'telegram' or 'discord'."}
        except Exception as exc:  # noqa: BLE001 — includes the one-bot-one-agent guarantee
            await _ledger_mcp_call(ws, "empyralis_assign_channel_bot", False, agent_id=agent_id, channel=ch)
            return {"ok": False, "error": str(exc), "channel": ch}
        await _ledger_mcp_call(ws, "empyralis_assign_channel_bot", True, agent_id=agent_id, channel=ch)
        return {"ok": True, "channel": ch, "binding": result}

    @empyralist_mcp.tool()
    async def empyralis_release_channel_bot(
        agent_id: str, channel: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Release an agent's bot for a channel ('telegram' or 'discord'). Requires writes_enabled."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r); tenant = await _tenant(ws)
        ch = str(channel or "").strip().lower()
        try:
            if ch in ("discord", "discord_bot"):
                from server_modules import discord_bot_provisioning_service as prov
                result = await prov.release_agent_discord(
                    agent_install_id=agent_id, workspace_id=ws, tenant_id=tenant,
                )
            elif ch in ("telegram", "telegram_bot"):
                from server_modules import hosted_bot_provisioning_service as prov
                result = await prov.release_agent_telegram(
                    agent_install_id=agent_id, workspace_id=ws, tenant_id=tenant,
                )
            else:
                return {"ok": False, "error": f"Unsupported channel '{channel}'. Use 'telegram' or 'discord'."}
        except Exception as exc:  # noqa: BLE001
            await _ledger_mcp_call(ws, "empyralis_release_channel_bot", False, agent_id=agent_id, channel=ch)
            return {"ok": False, "error": str(exc), "channel": ch}
        await _ledger_mcp_call(ws, "empyralis_release_channel_bot", True, agent_id=agent_id, channel=ch)
        return {"ok": True, "channel": ch, **(result if isinstance(result, dict) else {})}

    @empyralist_mcp.tool()
    async def empyralis_connect_connector(
        provider: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Begin connecting an OAuth connector (e.g. 'gmail', 'github', 'slack').
        Returns an ``authorization_url`` the human opens to grant access. Requires writes_enabled."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from types import SimpleNamespace
        from server_modules import connection_oauth_service
        base = ""
        for key in ("EMPYRALIS_PUBLIC_BASE_URL", "PUBLIC_BASE_URL"):
            base = str(os.getenv(key) or "").strip().rstrip("/")
            if base:
                break
        shim_request = SimpleNamespace(base_url=(base + "/") if base else "http://localhost:8001/")
        try:
            started = connection_oauth_service.start_oauth(
                provider=str(provider or "").strip().lower(),
                workspace_id=ws, surface="sage", request=shim_request, user_id="external_mcp_client",
            )
        except Exception as exc:  # noqa: BLE001 — e.g. provider not OAuth-configured on this server
            await _ledger_mcp_call(ws, "empyralis_connect_connector", False, provider=provider)
            return {"ok": False, "error": str(exc), "provider": provider}
        url = started.get("authorization_url") if isinstance(started, dict) else None
        await _ledger_mcp_call(ws, "empyralis_connect_connector", True, provider=provider)
        return {"ok": True, "provider": provider, "authorization_url": url,
                "instructions": "Open authorization_url in a browser to grant access."}

    @empyralist_mcp.tool()
    async def empyralis_trigger_test_turn(
        agent_id: str, message: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Send a test message to a deployed agent and get its reply, without a
        real customer. ``agent_id`` is a deployed-agent id. Requires writes_enabled."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from types import SimpleNamespace
        from server_modules import deployed_agent_test_turn_service
        current_user = {"user_id": "external_mcp_client", "email": "", "mcp_workspace_id": ws}
        request = SimpleNamespace(message=message, channel="test")
        try:
            result = await deployed_agent_test_turn_service.execute_test_turn(
                deployed_agent_id=agent_id, workspace_id=ws, request=request, current_user=current_user,
            )
        except Exception as exc:  # noqa: BLE001 — readiness / not-a-deployed-agent surfaces cleanly
            await _ledger_mcp_call(ws, "empyralis_trigger_test_turn", False, agent_id=agent_id)
            return {"ok": False, "error": str(exc), "agent_id": agent_id}
        await _ledger_mcp_call(ws, "empyralis_trigger_test_turn", True, agent_id=agent_id)
        return {"ok": True, "agent_id": agent_id, **(result if isinstance(result, dict) else {"result": result})}


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
