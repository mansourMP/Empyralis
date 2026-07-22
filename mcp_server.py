"""Empyralis MCP server — exposes the platform as tools for external AI clients.

Two MCP surfaces in Empyralis:

1. **Empyralis AS an MCP server** (this file) — external AI clients (Claude Code,
   Claude Desktop, Claude web/mobile Connectors, ChatGPT) connect TO Empyralis
   at ``/mcp`` to call platform tools. Two auth paths, both resolved through
   ``_resolve_workspace`` below:
     - Per-workspace bearer API key (``server_modules/mcp_server_auth.py``) —
       the original path; Claude Code CLI depends on it.
     - OAuth 2.1 (``server_modules/mcp_oauth_provider.py``), opt-in via
       ``EMPYRALIS_MCP_OAUTH_ENABLED=true`` — lets Claude add Empyralis as a
       one-click Connector. The mcp SDK (``mcp.server.auth``) supplies PKCE,
       dynamic client registration (RFC 7591), and authorization/protected-
       resource metadata (RFC 8414/9728); ``mcp_oauth_provider.py`` supplies
       the storage and the consent screen tied to the existing dashboard
       session (``server_modules/auth.py``).

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
  - ``empyralis_chat`` → full turn through normal chat path (triage, ledger, AI)

Write (gated behind ``EMPYRALIS_MCP_WRITE_ENABLED=true`` + per-key writes_enabled):
  - ``empyralis_create_project`` → projects_repository.create_project
  - ``empyralis_create_agent`` → fleet_create_agent (+ projects_repository.assign_install_to_project)
  - ``empyralis_configure_agent`` → fleet_configure_agent
  - ``empyralis_message_agent`` → fleet_message_agent
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
    from mcp.types import ToolAnnotations
except Exception:
    FastMCP = None  # type: ignore[assignment]
    Context = None  # type: ignore[assignment]
    ToolAnnotations = None  # type: ignore[assignment]

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
    "empyralis_chat",
    # Write (gated behind EMPYRALIS_MCP_WRITE_ENABLED + per-key writes_enabled)
    "empyralis_create_project",
    "empyralis_create_agent",
    "empyralis_configure_agent",
    "empyralis_message_agent",
    "empyralis_assign_channel_bot",
    "empyralis_release_channel_bot",
    "empyralis_connect_connector",
    "empyralis_trigger_test_turn",
]

_WRITE_ENABLED_GLOBAL = os.getenv("EMPYRALIS_MCP_WRITE_ENABLED", "").strip().lower() in {
    "1", "true", "yes",
}  # Global emergency off-switch — when false, ALL write tools are blocked regardless of per-key settings.

_MCP_OAUTH_ENABLED = os.getenv("EMPYRALIS_MCP_OAUTH_ENABLED", "").strip().lower() in {
    "1", "true", "yes",
}  # Opt-in: an internet-facing OAuth authorization server is a bigger surface
   # than the existing bearer-key path, so it stays off until explicitly enabled
   # (and reviewed) per deployment, even though the legacy bearer-key path
   # always works regardless of this flag.

# Set by _build_mcp_server() when EMPYRALIS_MCP_OAUTH_ENABLED is on and a public
# base URL is configured. None means OAuth is not wired in — mount_empyralist_mcp
# then mounts only the plain (legacy-bearer-key-only) streamable HTTP app, same
# as before this feature existed.
oauth_provider: Any = None


def _resolve_public_base_url() -> str:
    for key in ("EMPYRALIS_PUBLIC_BASE_URL", "PUBLIC_BASE_URL"):
        base = str(os.getenv(key) or "").strip().rstrip("/")
        if base:
            return base
    return ""


# ── API key resolution ──────────────────────────────────────────────────


async def _resolve_workspace(ctx: Any) -> Dict[str, Any]:
    """Extract ``{workspace_id, writes_enabled}`` from the authenticated MCP request.

    Prefers the mcp SDK's verified access token (populated by its
    AuthContextMiddleware whenever ``_build_mcp_server`` wired an
    ``auth_server_provider`` in — see ``mcp_oauth_provider.py``). This single
    path serves BOTH real OAuth-issued tokens and legacy per-workspace bearer
    API keys, because ``EmpyralisOAuthProvider.load_access_token`` itself
    tries the OAuth tables first and falls back to
    ``mcp_server_auth.resolve_workspace_from_api_key`` — so a legacy key
    keeps working unchanged once OAuth is enabled.

    Falls back to manually parsing the Authorization header (this function's
    entire pre-OAuth behavior, unchanged) when no auth_server_provider is
    configured at all — e.g. ``EMPYRALIS_MCP_OAUTH_ENABLED`` unset.
    """
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        access_token = get_access_token()
    except Exception:
        access_token = None

    if access_token is not None:
        workspace_id = str(getattr(access_token, "workspace_id", "") or "").strip()
        if workspace_id:
            from server_modules.mcp_oauth_provider import SCOPE_WRITE

            scopes = set(getattr(access_token, "scopes", None) or [])
            return {
                "workspace_id": workspace_id,
                "writes_enabled": SCOPE_WRITE in scopes,
                "scopes": scopes,
            }

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
    """Write an mcp_inbound ledger event for an inbound external MCP call.

    Previously called runs_core.emit_log with kwargs that don't exist on it, so
    every call raised TypeError and was swallowed — the claimed mcp_inbound audit
    trail did not exist. Routed through the real activity ledger.
    """
    try:
        from server_modules import activity_ledger_service
        from server_modules import control_plane_repository as cpr

        tenant_id = await cpr.resolve_tenant_id_for_workspace(workspace_id, default="default")
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="external_mcp_client",
            actor_id="external_mcp_client",
            event_class="mcp_inbound",
            action=tool_name,
            title=f"MCP {tool_name}",
            summary=f"ok={ok}",
            metadata={"ok": ok, **extra},
        )
    except Exception:
        LOGGER.debug("Failed to ledger MCP call %s", tool_name, exc_info=True)


# ── Build server ──────────────────────────────────────────────────────────


def _build_mcp_server() -> FastMCP | None:
    global oauth_provider
    if FastMCP is None:
        return None

    auth_kwargs: Dict[str, Any] = {}
    if _MCP_OAUTH_ENABLED:
        base_url = _resolve_public_base_url()
        if not base_url:
            LOGGER.warning(
                "EMPYRALIS_MCP_OAUTH_ENABLED is set but no public base URL is configured "
                "(EMPYRALIS_PUBLIC_BASE_URL / PUBLIC_BASE_URL) — MCP OAuth connector stays "
                "disabled; the legacy bearer-key path still works."
            )
        else:
            try:
                from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

                from server_modules import mcp_oauth_provider

                resource_server_url = f"{base_url}{EMPYRALIST_MCP_PATH}"
                provider = mcp_oauth_provider.EmpyralisOAuthProvider(
                    issuer_url=base_url, resource_server_url=resource_server_url,
                )
                auth_kwargs["auth_server_provider"] = provider
                auth_kwargs["auth"] = AuthSettings(
                    issuer_url=base_url,
                    resource_server_url=resource_server_url,
                    client_registration_options=ClientRegistrationOptions(
                        enabled=True,
                        valid_scopes=list(mcp_oauth_provider.SCOPES),
                        default_scopes=list(mcp_oauth_provider.DEFAULT_SCOPES),
                    ),
                    revocation_options=RevocationOptions(enabled=True),
                )
                oauth_provider = provider
            except Exception:
                LOGGER.exception(
                    "Failed to configure MCP OAuth provider — falling back to legacy "
                    "bearer-key-only auth for this process."
                )
                oauth_provider = None
                auth_kwargs = {}

    # streamable_http_path="/": the SDK's default internal protocol path is
    # ALSO "/mcp", and mount_empyralist_mcp() mounts the sub-app under
    # EMPYRALIST_MCP_PATH ("/mcp") — so with the default, the only URL that
    # answered was /mcp/mcp (the documented /mcp returned 307→404; the
    # platform-MCP audit proved it empirically, broken since the first
    # mount). Root the protocol INSIDE the sub-app so the public path is
    # exactly EMPYRALIST_MCP_PATH.
    #
    # transport_security: the SDK's DNS-rebinding guard 421s any request
    # whose Host header isn't allow-listed, and its defaults only admit
    # localhost forms — nginx forwards `Host: empyralis.ai`, so every real
    # public request would be rejected even with the path fixed (proved with
    # an ASGI-transport handshake: 127.0.0.1 → 200, empyralis.ai → 421).
    # Allow the public host(s) + local dev/test forms explicitly; the guard
    # itself stays ON.
    from mcp.server.transport_security import TransportSecuritySettings

    _public_host = ""
    try:
        from urllib.parse import urlparse
        _public_host = urlparse(os.environ.get("EMPYRALIS_PUBLIC_BASE_URL", "")).netloc
    except Exception:
        _public_host = ""
    _allowed_hosts = [h for h in {
        "empyralis.ai", "www.empyralis.ai", _public_host,
        "127.0.0.1:8001", "localhost:8001", "127.0.0.1", "localhost", "testserver",
    } if h]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=[f"https://{h}" for h in _allowed_hosts] + [f"http://{h}" for h in _allowed_hosts],
    )
    return FastMCP(
        EMPYRALIST_MCP_NAME,
        streamable_http_path="/",
        transport_security=security,
        **auth_kwargs,
    )


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

    @empyralist_mcp.tool(
        title="List Projects",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
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

    @empyralist_mcp.tool(
        title="List Agents",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
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

    @empyralist_mcp.tool(
        title="Get Agent Activity",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
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

    @empyralist_mcp.tool(
        title="Get Agent Conversations",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
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

    # NOTE: empyralis_memory_read / _list / _write were removed here. They called
    # agent_memory_tools with a workspace-only signature the real functions do not
    # accept (they are agent-scoped and require agent_install_id + path/content),
    # so every call raised TypeError. Exposing agent-scoped memory over MCP needs
    # a deliberate agent-selection design; until then the dead tools are gone
    # rather than advertised-but-crashing.

    @empyralist_mcp.tool(
        title="Chat with Sage",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
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

    @empyralist_mcp.tool(
        title="Create Project",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
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

    @empyralist_mcp.tool(
        title="Create Agent",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
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

    @empyralist_mcp.tool(
        title="Configure Agent",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )
    async def empyralis_configure_agent(
        agent_id: str, patch: Dict[str, Any], ctx: Context = None,
    ) -> Dict[str, Any]:
        """Configure agent settings. Requires writes_enabled on the API key."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules.fleet_tools import fleet_configure_agent
        result = await fleet_configure_agent(
            workspace_id=ws, tenant_id=tenant, agent_id=agent_id, patch=patch, actor_id="external_mcp_client",
        )
        await _ledger_mcp_call(ws, "empyralis_configure_agent", result.get("ok", False), agent_id=agent_id)
        return result

    @empyralist_mcp.tool(
        title="Message Agent",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
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

    @empyralist_mcp.tool(
        title="Assign Channel Bot",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )
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

    @empyralist_mcp.tool(
        title="Release Channel Bot",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )
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

    @empyralist_mcp.tool(
        title="Connect Connector",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_connect_connector(
        provider: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Begin connecting an OAuth connector (e.g. 'gmail', 'github', 'slack').
        Returns an ``authorization_url`` the human opens to grant access. Requires writes_enabled."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from types import SimpleNamespace
        from server_modules import connection_oauth_service
        base = _resolve_public_base_url()
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

    @empyralist_mcp.tool(
        title="Trigger Test Turn",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
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
    sub_app = empyralist_mcp.streamable_http_app()

    if oauth_provider is None:
        app.mount(EMPYRALIST_MCP_PATH, sub_app)
        return

    # streamable_http_app() bundles the OAuth authorization-server routes
    # (/authorize, /token, /register, /revoke, /.well-known/oauth-authorization-server)
    # and the RFC 9728 protected-resource-metadata route into the SAME
    # Starlette app as the MCP protocol route (see
    # mcp.server.fastmcp.server.FastMCP.streamable_http_app). Two problems
    # with just mounting that whole app under EMPYRALIST_MCP_PATH:
    #   1. RFC 8414/9728 client discovery expects those routes at the public
    #      origin root — our issuer_url has no path component (see
    #      _build_mcp_server) — not nested under /mcp/authorize etc, which a
    #      spec-following client never requests.
    #   2. It would leave a second, *live* copy of /register reachable at
    #      /mcp/register that the POST /register rate limiter below — which
    #      only guards the literal path "/register" — would never see.
    # So: build a protocol-only Starlette app (same route + same bearer-auth
    # middleware the SDK built, needed for get_access_token() in tool
    # handlers) for the /mcp mount, and re-register everything else directly
    # on the app root, where discovery actually looks and the rate limiter
    # actually guards.
    from starlette.applications import Starlette

    from server_modules import mcp_oauth_provider

    protocol_path = empyralist_mcp.settings.streamable_http_path
    protocol_routes = [r for r in sub_app.routes if getattr(r, "path", None) == protocol_path]
    oauth_routes = [r for r in sub_app.routes if getattr(r, "path", None) != protocol_path]

    protocol_app = Starlette(routes=protocol_routes, middleware=sub_app.user_middleware)
    app.mount(EMPYRALIST_MCP_PATH, protocol_app)

    for route in oauth_routes:
        app.router.routes.append(route)

    mcp_oauth_provider.register_consent_routes(app, oauth_provider)
    mcp_oauth_provider.register_register_rate_limit_guard(app)


@asynccontextmanager
async def empyralist_mcp_lifespan() -> AsyncIterator[None]:
    if empyralist_mcp is None:
        yield
        return
    async with empyralist_mcp.session_manager.run():
        yield
