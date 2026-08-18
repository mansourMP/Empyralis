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
  - ``empyralis_chat`` → sage_turn_adapter.execute_sage_turn, the same
    chokepoint every real channel (web, Telegram, Discord, WhatsApp...)
    routes a turn through -- full turn, named agent, real reply or a real
    error (MAN-205)

Tasks (always live — see "Write-gate decision" below):
  - ``empyralis_list_my_tasks`` → project_tasks_service.list_my_tasks (assigned to
    this key's external_agent_id, OR unassigned/backlog)
  - ``empyralis_list_tasks`` → project_tasks_service.list_tasks (the WHOLE
    board, whoever it is assigned to — list_my_tasks is the narrow slice;
    an agent that cannot see the board cannot triage it)
  - ``empyralis_assign_task`` → project_tasks_service.assign_task (agent) /
    assign_task_to_user (person). THE step that makes filed work happen:
    ``create_task`` fires no wakeup and no notification, so an unassigned
    task reaches nobody. Exactly one of agent_id/user_id; agent -> a
    scheduled wakeup, user -> a notification, and the response says which.
  - ``empyralis_get_task`` → project_tasks_service.get_task
  - ``empyralis_update_task_status`` → project_tasks_service.update_task (status only)
  - ``empyralis_set_task_priority`` → project_tasks_service.update_task (priority only;
    a separate tool from status because priority and status are independent
    facts and setting one must never quietly change the other)
  - ``empyralis_comment_on_task`` → project_tasks_service.add_task_comment
  - ``empyralis_create_task`` → project_tasks_service.create_task (accepts
    ``parent_task_id`` to create a SUB-TASK; exactly one level of nesting)
  - ``empyralis_set_task_parent`` → project_tasks_service.set_task_parent
    (re-file an existing task under a parent, or detach it back to top-level)
  - ``empyralis_list_labels`` → workspace_labels_service.list_labels
  - ``empyralis_add_task_label`` → workspace_labels_service.attach_label
  - ``empyralis_remove_task_label`` → workspace_labels_service.detach_label

Sub-tasks and labels (2026-07-29): a sub-task is a plain task carrying
``parent_task_id``, so every tool above already works on one unchanged, and
every task returned by any tool here carries ``subtask_count`` /
``subtask_done_count`` (the "1/3 done" rollup) and its ``labels``. Labels are
a per-WORKSPACE vocabulary: an external agent can list it and attach/detach
its entries, but deliberately cannot CREATE labels — that stays a human
decision, so a guessed word cannot fill the vocabulary with near-duplicates.

Documents (always live, feat/document-mcp-tools-and-revisions — same
write-gate reasoning as tasks, see below): a teammate's own Claude/ChatGPT
is the intended caller here — MCP is for the FOUNDER'S USERS, not for
Empyralis's own agents (those already have document__* native tools, see
skills_service.py). "Edit this document" / "update this document" said to an
external AI client should just work.
  - ``empyralis_create_document`` → project_documents_repository.create_document
  - ``empyralis_edit_document`` → project_documents_repository.
    edit_document_by_replace — the PRIMARY way to change a document: a
    targeted old_string/new_string patch, matched EXACTLY ONCE in the
    current body, never a whole-document rewrite. The founder's own words
    for this shape: "to upgrade one line or one word or one sentence...
    just like git — write a line and push it." A zero-match or
    multi-match old_string fails loudly with NO mutation.
  - ``empyralis_update_document`` → project_documents_repository.update_document
    — the FALLBACK for a genuine full rewrite (title and/or body, whole
    values). Reach for empyralis_edit_document first for anything smaller
    than "replace most of the document."
  - ``empyralis_list_documents`` → project_documents_repository.list_documents
    (one project at a time — project_id is REQUIRED and resolved server-side
    against the caller's own workspace, exactly like empyralis_create_task's
    own project_id, never trusted to widen scope past it)
  - ``empyralis_get_document`` → project_documents_repository.get_document
  - ``empyralis_list_document_revisions`` → project_documents_repository.
    list_document_revisions — read-only history (no restore/rollback tool;
    CLAUDE.md: "a surface must earn its place"). Every write above already
    records one revision automatically — a full snapshot AND a
    human-readable diff against the prior state ("this line changed", not
    "here is the whole document again") — nothing else to call to get
    tracked history, including with no hardware connected: `document`
    writes go straight to Postgres, in-process — skills_service.py's own
    hardware-required connector check
    (``connector_id not in {"hardware", "file", "shell", "screenshot",
    "computer"}``) deliberately excludes ``document``, so this whole
    surface needs no paired hardware, unlike a channel or shell tool.

Write-gate decision (task AND document tools): NOT behind
``EMPYRALIS_MCP_WRITE_ENABLED``. The 8 gated tools below are workspace-wide
configuration mutations (create/reconfigure an agent, take over a channel,
start an OAuth grant) — exactly what a read-only key must never be able to
do by accident. Task status/comments/documents are bounded to a project
already visible through this same key (``empyralis_list_projects`` and,
for tasks, ``empyralis_list_my_tasks``/``empyralis_get_task``) and are the
founder's core loop itself ("check Empyralis → pull task → work → comment
back", now extended to "tell your agent to edit the doc"). Gating them
would force operators to grant the SAME ``writes_enabled=true`` that also
unlocks channel takeover and agent creation just to let a teammate's own AI
client edit a project document — there is no granular per-tool scope today,
so that coupling is a worse privilege trade than leaving them ungated. It's
also consistent with the existing precedent: ``empyralis_chat`` already runs
a full AI turn (with whatever side effects Sage's own tools cause) without
being writes_enabled-gated; document writes are a narrower, more bounded
mutation than that, not a broader one.

Write (gated behind ``EMPYRALIS_MCP_WRITE_ENABLED=true`` + per-key writes_enabled):
  - ``empyralis_create_project`` → projects_repository.create_project
  - ``empyralis_create_agent`` → fleet_create_agent (+ projects_repository.assign_install_to_project)
  - ``empyralis_configure_agent`` → fleet_configure_agent
  - ``empyralis_message_agent`` → fleet_message_agent (ALWAYS returns
    ``ok: false`` -- agent-to-agent messaging has no delivery path yet;
    see docs/design/audit-silent-failures.md C1 and the tool's own
    docstring below)
  - ``empyralis_assign_channel_bot`` → hosted_bot_provisioning_service / discord_bot_provisioning_service
  - ``empyralis_release_channel_bot`` → hosted_bot_provisioning_service / discord_bot_provisioning_service
  - ``empyralis_connect_connector`` → connection_oauth_service.start_oauth (returns authorization_url)
  - ``empyralis_trigger_test_turn`` → deployed_agent_test_turn_service.execute_test_turn

All calls are ledgered with ``event_class="mcp_inbound"`` and
``actor_id`` = this key's own ``external_agent_id`` (``ext_agent_<hex16>``),
so two different keys are two distinguishable actors in the audit trail —
it was a hardcoded ``"external_mcp_client"`` constant for every caller until
2026-08-01, which made them indistinguishable.  Workspace is resolved from
the API key — never from tool arguments.  Operator-role rules apply.

IDENTITY ON WRITES: the bearer-key path resolves both ``external_agent_id``
AND ``external_agent_display_name`` (see ``_resolve_workspace``), and both
reach the write path. The id is what gets stored as the author/creator; the
display name rides along as a snapshot (``comment.author_display_name`` /
``task.metadata.created_by_display_name``) so a board can name the agent
that acted even if its roster row later disappears. The live name lives in
``mcp_external_agent_roster`` and is served to the UI by
``GET /api/w/{workspace_id}/fleet/roster`` (routes_fleet.py), which is what
the frontend prefers — the snapshot is the fallback, not the source of truth.
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
    # Tasks (always live — bounded to tasks already visible through this key;
    # see the write-gate rationale in the module docstring)
    "empyralis_create_task",
    "empyralis_list_my_tasks",
    "empyralis_list_tasks",
    "empyralis_assign_task",
    "empyralis_get_task",
    "empyralis_update_task_status",
    "empyralis_set_task_priority",
    "empyralis_comment_on_task",
    "empyralis_set_task_parent",
    # Labels (always live, same bound: the workspace vocabulary is readable
    # and attachable, but NOT creatable — see the module docstring)
    "empyralis_list_labels",
    "empyralis_add_task_label",
    "empyralis_remove_task_label",
    # Documents (always live, same bound as tasks — see the module docstring)
    "empyralis_create_document",
    "empyralis_edit_document",
    "empyralis_update_document",
    "empyralis_list_documents",
    "empyralis_get_document",
    "empyralis_list_document_revisions",
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

try:
    _CHAT_TURN_TIMEOUT_SECONDS = float(os.getenv("EMPYRALIS_MCP_CHAT_TIMEOUT_SECONDS", "") or 240)
except (TypeError, ValueError):
    _CHAT_TURN_TIMEOUT_SECONDS = 240.0
# empyralis_chat runs a full agent turn synchronously (tool calls, hardware
# dispatch) -- production p90 is ~120s. Bounded well above that so the tool
# does not hang forever with no diagnosis on a slow turn, without cutting off
# a merely-slow-but-healthy one. nginx's own timeout for /mcp is 86400s (see
# deploy/nginx-empyralis.conf), so this in-process bound is the real ceiling.

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


def _public_origin_request() -> Any:
    """A real ``starlette.requests.Request`` whose origin is this deployment's
    own public base URL.

    Exists because ``connection_oauth_service.start_oauth`` takes a ``Request``
    and derives the OAuth callback URL from it (``request_origin`` reads
    ``request.headers`` then falls back to ``request.base_url``). MCP tool
    calls have no HTTP request with the right origin to hand it — the live one
    is mounted under ``/mcp`` — so this constructs one from a genuine ASGI
    scope.

    Genuine is the point (MAN-205): the previous
    ``SimpleNamespace(base_url=...)`` satisfied exactly the one attribute its
    author knew about and raised ``AttributeError`` on the first one they did
    not, which is why the connector tool never worked. A real ``Request``
    answers every attribute the callee reaches for, today and after it grows
    another.
    """
    from urllib import parse as _urlparse

    from starlette.requests import Request as _StarletteRequest

    base = _resolve_public_base_url() or "http://localhost:8001"
    parts = _urlparse.urlsplit(base)
    scheme = parts.scheme or "http"
    netloc = parts.netloc or "localhost:8001"
    return _StarletteRequest(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": "/",
            "raw_path": b"/",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", netloc.encode("latin-1", "ignore"))],
            "server": (parts.hostname or "localhost", parts.port or (443 if scheme == "https" else 80)),
            "client": None,
        }
    )


# ── API key resolution ──────────────────────────────────────────────────


async def _resolve_workspace(ctx: Any) -> Dict[str, Any]:
    """Extract ``{workspace_id, writes_enabled, external_agent_id,
    external_agent_display_name}`` from the authenticated MCP request.

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

    ``external_agent_id``: the bearer-key path always carries one (Step 2 of
    "Mentions + identity for platform AND external agents" mints/backfills it
    in ``resolve_workspace_from_api_key`` itself). The OAuth path does NOT
    mint one yet — an OAuth-issued Connector session has its own client
    identity in the OAuth tables that Step 2 deliberately did not touch (out
    of scope: OAuth is opt-in, disabled by default, and needs its own
    integration pass) — so it explicitly returns ``None`` here rather than
    silently omitting the key, and every task tool that reads it must treat
    ``None`` as a real, traceable state ("no identity yet"), not an error.
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
                # Not minted for the OAuth path yet — see docstring above.
                "external_agent_id": None,
                "external_agent_display_name": None,
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


async def _ledger_mcp_call(resolved: Dict[str, Any], tool_name: str, ok: bool, **extra: Any) -> None:
    """Write an mcp_inbound ledger event for an inbound external MCP call.

    Previously called runs_core.emit_log with kwargs that don't exist on it, so
    every call raised TypeError and was swallowed — the claimed mcp_inbound audit
    trail did not exist. Routed through the real activity ledger.

    Takes the WHOLE resolved-auth dict (``_resolve_workspace``'s return), not
    just a workspace id, because ``actor_id`` used to be the literal constant
    ``"external_mcp_client"`` for every caller — which made two different
    bearer keys, i.e. two genuinely different external agents, indistinguishable
    in the audit trail. The actor is now this key's own
    ``external_agent_id`` (``ext_agent_<hex16>``, minted at key creation by
    ``mcp_external_agent_roster_service``), with its ``display_name`` carried in
    the event metadata — the ledger row itself has no name column, and adding
    one to satisfy a display concern would be the wrong shape when the roster
    is already the name's home.

    The old constant remains the fallback for exactly one case that is real and
    must stay traceable rather than crash: an OAuth Connector session, which
    ``_resolve_workspace`` documents as minting no external-agent identity yet.
    """
    workspace_id = str(resolved.get("workspace_id") or "").strip()
    external_agent_id = str(resolved.get("external_agent_id") or "").strip()
    display_name = str(resolved.get("external_agent_display_name") or "").strip()
    try:
        from server_modules import activity_ledger_service
        from server_modules import control_plane_repository as cpr

        tenant_id = await cpr.resolve_tenant_id_for_workspace(workspace_id, default="default")
        await activity_ledger_service.append_activity_event(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_type="external_agent" if external_agent_id else "external_mcp_client",
            actor_id=external_agent_id or "external_mcp_client",
            event_class="mcp_inbound",
            action=tool_name,
            title=f"MCP {tool_name}",
            summary=f"ok={ok}",
            metadata={
                "ok": ok,
                **({"external_agent_id": external_agent_id} if external_agent_id else {}),
                **({"external_agent_display_name": display_name} if display_name else {}),
                **extra,
            },
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

    def _mcp_current_user(
        resolved: Dict[str, Any], ws: str, tenant: str,
    ) -> Dict[str, Any]:
        """The acting identity for a service that runs ``auth.enforce_workspace_access``
        on its ``current_user`` rather than trusting a bare ``workspace_id``.

        MAN-205. Two tools used to hand those services an ad-hoc dict carrying
        an INVENTED key::

            {"user_id": "external_mcp_client", "email": "", "mcp_workspace_id": ws}
                                                ^^^^^^^^^^^^^^^^^ read NOWHERE in
                                                auth.py — grep it: the only two
                                                occurrences in the repo were the
                                                two dicts that wrote it.

        So ``allowed_workspace_ids()`` saw a user with no workspace grant at
        all and returned the EMPTY SET, and every call died 403 before it
        reached any real logic. The check was never wrong — it was handed an
        identity it could only reject.

        This grants exactly the workspace the API key already resolved to, at
        owner role, and nothing else. Deliberately NOT ``is_admin`` /
        ``auth_admin``: either of those makes ``allowed_workspace_ids`` and
        ``allowed_tenant_ids`` return ``None`` — i.e. every workspace of every
        tenant — which would quietly turn a single-workspace bearer key into a
        cross-tenant one. The real check still runs; it can now evaluate.
        """
        return {
            "user_id": str(resolved.get("external_agent_id") or "").strip() or "external_mcp_client",
            "email": "",
            "auth_type": "api_key",
            "role": "owner",
            "workspace_access": {
                ws: {
                    "workspace_id": ws,
                    "tenant_id": tenant,
                    "role": "owner",
                    "tenant_role": "owner",
                },
            },
        }

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
        await _ledger_mcp_call(r, "empyralis_list_projects", True, project_count=len(projects))
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
        await _ledger_mcp_call(r, "empyralis_list_agents", True, agent_count=len(agents))
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
        await _ledger_mcp_call(r, "empyralis_get_agent_activity", True, agent_id=agent_id)
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
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import deployed_agent_service
        current_user = _mcp_current_user(r, ws, tenant)
        try:
            payload = await deployed_agent_service.list_deployed_agent_conversations(
                deployed_agent_id=agent_id, current_user=current_user,
                owner_workspace_id=ws, limit=limit, offset=0,
            )
        except Exception as exc:  # noqa: BLE001 — surface a clean reason to the client
            await _ledger_mcp_call(r, "empyralis_get_agent_conversations", False, agent_id=agent_id)
            return {"ok": False, "error": str(exc), "agent_id": agent_id}
        await _ledger_mcp_call(r, "empyralis_get_agent_conversations", True, agent_id=agent_id)
        return {"ok": True, "agent_id": agent_id, **(payload if isinstance(payload, dict) else {})}

    # NOTE: empyralis_memory_read / _list / _write were removed here. They called
    # agent_memory_tools with a workspace-only signature the real functions do not
    # accept (they are agent-scoped and require agent_install_id + path/content),
    # so every call raised TypeError. Exposing agent-scoped memory over MCP needs
    # a deliberate agent-selection design; until then the dead tools are gone
    # rather than advertised-but-crashing.

    @empyralist_mcp.tool(
        title="Chat with an Agent",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_chat(message: str, agent_id: str = "", ctx: Context = None) -> Dict[str, Any]:
        """Send a message to a platform agent and get its real reply.

        Routes through ``sage_turn_adapter.execute_sage_turn`` -- the same
        chokepoint every real channel (web console, Telegram, Discord,
        WhatsApp...) runs a turn through -- so this exercises the full turn:
        tools, hardware dispatch, memory, and the activity ledger. Never a
        summary or a canned reply; a failed turn comes back as ``ok: false``
        with the real error, not a fabricated success.

        ``agent_id`` is an agent install id from ``empyralis_list_agents``
        (its ``agent_id`` field). It is validated against YOUR workspace
        (resolved from your API key) before anything runs -- an id from
        another workspace, or one that doesn't exist, is rejected with
        ``ok: false`` rather than silently falling back to any default
        agent. Omit ``agent_id`` to talk to the workspace's default agent;
        the response always names the real agent that answered.

        This can be slow -- production p90 is ~120s from tool calls inside
        the turn. Bounded to a hard timeout (default 240s, override with
        EMPYRALIS_MCP_CHAT_TIMEOUT_SECONDS) so a stuck turn surfaces an
        honest timeout instead of hanging forever.
        """
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import agent_registry_repository as reg

        requested_agent_id = str(agent_id or "").strip()
        if requested_agent_id:
            # Workspace-scoped lookup -- get_workspace_agent_install_bundle's
            # WHERE clause includes wai.workspace_id = ws, so an install id
            # belonging to a DIFFERENT workspace returns None here exactly
            # like a nonexistent one. This is the check MAN-206
            # (empyralis_assign_channel_bot) skipped; do it before anything
            # else touches agent_id.
            bundle = await reg.get_workspace_agent_install_bundle(
                requested_agent_id, tenant_id=tenant, workspace_id=ws,
            )
            if not isinstance(bundle, dict):
                await _ledger_mcp_call(r, "empyralis_chat", False, agent_id=requested_agent_id)
                return {
                    "ok": False,
                    "error": (
                        f"Agent '{requested_agent_id}' was not found in your workspace. "
                        "Call empyralis_list_agents to see the agent_id values you can use."
                    ),
                    "agent_id": requested_agent_id,
                }
            resolved_agent_id = requested_agent_id
            resolved_agent_label = str(bundle.get("label") or requested_agent_id)
        else:
            master = await reg.get_workspace_master_agent_install(tenant_id=tenant, workspace_id=ws)
            resolved_agent_id = str((master or {}).get("id") or "").strip()
            resolved_agent_label = str((master or {}).get("label") or resolved_agent_id)
            if not resolved_agent_id:
                await _ledger_mcp_call(r, "empyralis_chat", False, agent_id="")
                return {
                    "ok": False,
                    "error": "No agent_id given and this workspace has no default agent yet.",
                    "agent_id": "",
                }

        import asyncio

        from server_modules.sage_turn_adapter import execute_sage_turn
        from server_modules.specialist_runtime_context import resolve_specialist_runtime_context

        try:
            # None when resolved_agent_id IS the workspace master -- the turn
            # then runs Sage's own unchanged runtime, matching every other
            # channel's contract. resolved_agent_id was already validated
            # against this workspace above, so a None here from a lookup
            # failure (not a master match) is only a narrow race against a
            # delete between the two calls, not a fresh silent fallback.
            specialist_context = await resolve_specialist_runtime_context(
                workspace_id=ws, tenant_id=tenant, active_agent_install_id=resolved_agent_id,
            )
        except Exception:
            specialist_context = None

        current_user = {
            "user_id": str(r.get("external_agent_id") or "").strip() or "mcp_client",
            "email": "",
            "auth_type": "api_key",
        }

        try:
            sage_result = await asyncio.wait_for(
                execute_sage_turn(
                    workspace_id=ws,
                    tenant_id=tenant,
                    message=message,
                    surface="chat",
                    # channel_origin is routing/audit metadata only (never
                    # reaches the prompt). channel_sender_id is deliberately
                    # left unset: sage_agent_runtime_service.py defaults
                    # sender_class to "owner" for web/API sessions exactly
                    # when no per-message sender id is given -- setting one
                    # here would downgrade this call to "audience" tool
                    # access, which is the opposite of "the full turn."
                    channel_origin="mcp",
                    channel_sender_name=str(r.get("external_agent_display_name") or ""),
                    current_user=current_user,
                    specialist_context=specialist_context,
                ),
                timeout=_CHAT_TURN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await _ledger_mcp_call(r, "empyralis_chat", False, agent_id=resolved_agent_id, timed_out=True)
            return {
                "ok": False,
                "error": (
                    f"Timed out after {int(_CHAT_TURN_TIMEOUT_SECONDS)}s waiting for "
                    f"{resolved_agent_label}'s reply. The turn may still be running "
                    "server-side -- check empyralis_get_agent_activity."
                ),
                "agent_id": resolved_agent_id,
            }
        except Exception as exc:  # noqa: BLE001 -- surface the real failure, never a synthesized reply
            await _ledger_mcp_call(r, "empyralis_chat", False, agent_id=resolved_agent_id)
            return {"ok": False, "error": str(exc), "agent_id": resolved_agent_id}

        result = sage_result.as_dict() if hasattr(sage_result, "as_dict") else dict(sage_result or {})
        reply = str(result.get("message") or "").strip()
        error_text = str(result.get("error") or "").strip()
        if error_text and not reply:
            await _ledger_mcp_call(r, "empyralis_chat", False, agent_id=resolved_agent_id)
            return {"ok": False, "error": error_text, "agent_id": resolved_agent_id}

        await _ledger_mcp_call(
            r, "empyralis_chat", True,
            agent_id=resolved_agent_id, message_len=len(message), reply_len=len(reply),
        )
        return {
            "ok": True,
            "reply": reply,
            "agent_id": resolved_agent_id,
            "agent_label": resolved_agent_label,
            "tool_calls": result.get("tool_calls", []),
            "provider": result.get("provider", ""),
            "model": result.get("model"),
        }

    # ── Task tools (always live — see module docstring for the write-gate
    # decision: bounded to tasks already visible through this key, not a
    # workspace-wide configuration mutation, so not behind
    # EMPYRALIS_MCP_WRITE_ENABLED) ─────────────────────────────────────

    @empyralist_mcp.tool(
        title="Create Task",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_create_task(
        project_id: str, title: str, description: str = "", due_at: str = "",
        priority: int = 0, parent_task_id: str = "", ctx: Context = None,
    ) -> Dict[str, Any]:
        """Create a task on a project's shared board — the same board a human
        sees in the Tasks view and any platform agent in that project works
        off of. Created unassigned/'todo'; use empyralis_update_task_status
        (once assigned) to move it through its lifecycle, or ask the owner
        to assign it. project_id must be a project in this workspace.

        priority uses Linear's scale: 0 = none (default, untriaged),
        1 = urgent, 2 = high, 3 = medium, 4 = low. NOTE the direction —
        1 is the MOST urgent and 4 the least, so a LOWER number means MORE
        urgent. An out-of-range value is rejected with a clear error rather
        than silently becoming 'none'.

        parent_task_id files this as a SUB-TASK of an existing task — use it
        when you are breaking a big piece of work into steps, so the parent
        card shows real progress ("1/3 done") instead of the steps scattering
        across the board as unrelated cards. IMPORTANT: this board allows
        exactly ONE level of nesting. The parent must be a top-level task; a
        parent that is itself already a sub-task is rejected with a clear
        error (attach it to that sub-task's own parent instead). Parent and
        sub-task must be in the same project."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        author_id = r.get("external_agent_id") or "external_mcp_client"
        author_name = str(r.get("external_agent_display_name") or "").strip()
        from server_modules import project_tasks_service as tasks
        try:
            task = await tasks.create_task(
                tenant_id=tenant, workspace_id=ws, project_id=project_id,
                title=title, description=description, due_at=due_at or None,
                priority=priority,
                parent_task_id=parent_task_id or None,
                created_by=author_id,
                created_by_display_name=author_name,
            )
        except Exception as exc:  # noqa: BLE001 — includes an invalid/foreign project_id (FK violation)
            await _ledger_mcp_call(r, "empyralis_create_task", False, project_id=project_id, error=str(exc))
            return {"ok": False, "error": str(exc)}
        await _ledger_mcp_call(r, "empyralis_create_task", True, project_id=project_id, task_id=task.get("id"))
        return {"ok": True, "task": task}

    @empyralist_mcp.tool(
        title="List My Tasks",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
    async def empyralis_list_my_tasks(
        project_id: str = "", status: str = "", sort: str = "", ctx: Context = None,
    ) -> Dict[str, Any]:
        """List tasks assigned to you (this key's external-agent identity) or
        unassigned tasks still open for anyone in the workspace.
        Optionally filter to one project_id or one status
        (backlog|todo|in_progress|awaiting_input|blocked|in_review|done).

        Every task comes back with `priority` (0 = none, 1 = urgent, 2 = high,
        3 = medium, 4 = low — a LOWER number is MORE urgent) and a
        plain-English `priority_label`. Pass sort='priority' to get the most
        urgent work first and untriaged work last — that is how you decide
        what to pick up next; the default ordering is newest-first."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        external_agent_id = r.get("external_agent_id") or None
        from server_modules import project_tasks_service as tasks
        try:
            rows = await tasks.list_my_tasks(
                tenant_id=tenant, workspace_id=ws,
                external_agent_id=external_agent_id,
                project_id=project_id or None,
                status=status or None,
                sort=sort or None,
            )
        except Exception as exc:  # noqa: BLE001
            await _ledger_mcp_call(r, "empyralis_list_my_tasks", False, error=str(exc))
            return {"ok": False, "error": str(exc), "tasks": []}
        await _ledger_mcp_call(
            r, "empyralis_list_my_tasks", True,
            task_count=len(rows), external_agent_id=external_agent_id,
        )
        result: Dict[str, Any] = {"ok": True, "tasks": rows, "external_agent_id": external_agent_id}
        if not external_agent_id:
            result["note"] = (
                "This session has no external-agent identity yet (an OAuth Connector "
                "session, which does not mint one — see mcp_server.py's module "
                "docstring), so only unassigned/backlog tasks are shown, not "
                "anything specifically assigned to you."
            )
        return result

    @empyralist_mcp.tool(
        title="List Tasks",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
    async def empyralis_list_tasks(
        project_id: str = "", status: str = "", sort: str = "",
        top_level_only: bool = False, ctx: Context = None,
    ) -> Dict[str, Any]:
        """List the whole board for your workspace — every task, whoever it is
        assigned to. ``empyralis_list_my_tasks`` is the narrow slice (yours,
        plus unassigned); this is the board an agent needs to actually TRIAGE
        it: see what is already in flight, what is blocked, and what nobody
        has picked up, before filing or claiming anything.

        Optionally filter to one project_id (verified against YOUR workspace
        before anything is read, so "not your project" and "no tasks" stay
        different answers), one status (backlog|todo|in_progress|
        awaiting_input|blocked|in_review|done), or top_level_only=True to
        hide sub-tasks and see just the parent cards.

        sort='priority' returns it triage-ordered — urgent first, untriaged
        last. Priority is Linear's scale, where a LOWER number is MORE
        urgent: 1 = urgent, 4 = low, 0 = none.
        """
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import project_tasks_service as tasks
        if project_id:
            from server_modules import projects_repository as _p
            project = await _p.get_project(
                tenant_id=tenant, workspace_id=ws, project_id=project_id,
            )
            if not isinstance(project, dict):
                await _ledger_mcp_call(r, "empyralis_list_tasks", False, project_id=project_id)
                return {
                    "ok": False,
                    "error": (
                        f"Project '{project_id}' was not found in your workspace. "
                        "Call empyralis_list_projects to see the project_id values you can use."
                    ),
                    "tasks": [],
                }
        try:
            rows = await tasks.list_tasks(
                tenant_id=tenant, workspace_id=ws,
                project_id=project_id or None,
                status=status or None,
                sort=sort or None,
                top_level_only=bool(top_level_only),
            )
        except Exception as exc:  # noqa: BLE001
            await _ledger_mcp_call(r, "empyralis_list_tasks", False, error=str(exc))
            return {"ok": False, "error": str(exc), "tasks": []}
        await _ledger_mcp_call(r, "empyralis_list_tasks", True, task_count=len(rows), project_id=project_id)
        return {"ok": True, "tasks": rows}

    @empyralist_mcp.tool(
        title="Assign Task",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_assign_task(
        task_id: str, agent_id: str = "", user_id: str = "", ctx: Context = None,
    ) -> Dict[str, Any]:
        """Assign a task to a platform agent, or to a person in this workspace.

        This is what makes filed work actually HAPPEN. ``empyralis_create_task``
        fires nothing at all — no wakeup, no notification — so a task created
        and left unassigned sits on the board with nobody woken for it.
        Assignment is the step that reaches someone.

        Pass EXACTLY ONE of ``agent_id`` (a platform agent, from
        empyralis_list_agents) or ``user_id`` (a person in this workspace).
        Passing both, or neither, is refused rather than guessed at — an
        assignment sent to the wrong kind of teammate is worse than one that
        did not happen.

        The two do genuinely different things downstream, and the response
        says which happened rather than making you assume:
          agent_id -> schedules a wakeup, so the agent picks the work up
          user_id  -> creates a notification; people are not woken by schedulers

        The wakeup can fail on its own (quiet hours, a rate cap) while the
        assignment itself commits — those are two facts, so both come back:
        ``wake_request`` when one was scheduled, ``wake_error`` when the
        assignment stuck but nothing was woken. "Assigned" and "assigned and
        someone is on it" are not the same claim.
        """
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        clean_agent_id = str(agent_id or "").strip()
        clean_user_id = str(user_id or "").strip()
        if bool(clean_agent_id) == bool(clean_user_id):
            detail = (
                "Pass exactly one of agent_id or user_id."
                if clean_agent_id
                else "Pass agent_id (a platform agent) or user_id (a person in this workspace)."
            )
            await _ledger_mcp_call(r, "empyralis_assign_task", False, task_id=task_id)
            return {"ok": False, "error": detail, "task_id": task_id}

        from server_modules import project_tasks_service as tasks
        assignee_kind = "agent" if clean_agent_id else "user"
        try:
            if clean_agent_id:
                result = await tasks.assign_task(
                    tenant_id=tenant, workspace_id=ws, task_id=task_id,
                    agent_id=clean_agent_id, triggered_by="agent",
                )
            else:
                result = await tasks.assign_task_to_user(
                    tenant_id=tenant, workspace_id=ws, task_id=task_id,
                    user_id=clean_user_id, triggered_by="agent",
                )
        except Exception as exc:  # noqa: BLE001 — unknown task, or an assignee outside this workspace
            await _ledger_mcp_call(
                r, "empyralis_assign_task", False, task_id=task_id, assignee_kind=assignee_kind,
            )
            return {"ok": False, "error": str(exc), "task_id": task_id}

        result = result if isinstance(result, dict) else {}
        wake_error = str(result.get("wake_error") or "").strip()
        await _ledger_mcp_call(
            r, "empyralis_assign_task", True,
            task_id=task_id, assignee_kind=assignee_kind, woken=bool(result.get("wake_request")),
        )
        payload: Dict[str, Any] = {
            "ok": True,
            "task": result.get("task"),
            "assignee_kind": assignee_kind,
            "wake_request": result.get("wake_request"),
        }
        if wake_error:
            # The assignment committed; the wakeup did not. Saying only
            # "assigned" here would be the same collapse CLAUDE.md's
            # outcome-honesty rule exists for.
            payload["wake_error"] = wake_error
            payload["note"] = (
                "The task is assigned, but nothing was woken for it "
                f"({wake_error}). It will be picked up on the next wake."
            )
        return payload

    @empyralist_mcp.tool(
        title="Get Task",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
    async def empyralis_get_task(task_id: str, ctx: Context = None) -> Dict[str, Any]:
        """Get one task by id. Scoped like every other tool here — any task in
        your workspace, not only ones assigned to you (same precedent as
        empyralis_configure_agent: any agent in the workspace, not only yours).
        Comments live under task.metadata.comments; `priority` (0 = none,
        1 = urgent, 2 = high, 3 = medium, 4 = low — lower is more urgent) and
        its plain-English `priority_label` are returned on the task itself.

        Also returns the SUB-TASK ROLLUP — `task.subtask_count` and
        `task.subtask_done_count`, which together are the "1/3 done" progress
        on the card — plus the full `subtasks` list, `task.parent_task_id` if
        this task is itself a sub-task, and `task.labels`. Check the rollup
        before reporting a parent complete: a task whose sub-tasks are not all
        done is not done."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import project_tasks_service as tasks
        task = await tasks.get_task(tenant_id=tenant, workspace_id=ws, task_id=task_id)
        await _ledger_mcp_call(r, "empyralis_get_task", task is not None, task_id=task_id)
        if task is None:
            return {"ok": False, "error": f"Task '{task_id}' not found in this workspace.", "task_id": task_id}
        # The counts ride on the task itself (same query). The CHILDREN are a
        # second read, done only on this single-task path -- never on
        # empyralis_list_my_tasks, where it would be one extra query per card.
        subtasks = await tasks.list_subtasks(
            tenant_id=tenant, workspace_id=ws, parent_task_id=task_id,
        )
        return {"ok": True, "task": task, "subtasks": subtasks}

    @empyralist_mcp.tool(
        title="Set Task Parent",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_set_task_parent(
        task_id: str, parent_task_id: str = "", ctx: Context = None,
    ) -> Dict[str, Any]:
        """File an EXISTING task under another as a sub-task, or detach it back
        to top-level by leaving parent_task_id empty. Use when you realize a
        task already on the board is really a step of a bigger one.

        IMPORTANT: this board allows exactly ONE level of nesting. The parent
        must be a top-level task, and a task that already has sub-tasks of its
        own cannot itself become one — both are rejected with a clear error
        naming the reason, never silently applied. Parent and sub-task must be
        in the same project.

        Detaching is safe and non-destructive: the task keeps everything else
        (status, assignee, comments, labels) and simply returns to the
        top-level board. That is also what happens on its own if a parent task
        is ever deleted — sub-tasks are promoted, never deleted with it.

        Workspace-scoped like empyralis_get_task."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import project_tasks_service as tasks
        try:
            task = await tasks.set_task_parent(
                tenant_id=tenant, workspace_id=ws, task_id=task_id,
                parent_task_id=parent_task_id or None,
            )
        except ValueError as exc:
            await _ledger_mcp_call(r, "empyralis_set_task_parent", False, task_id=task_id)
            return {"ok": False, "error": str(exc), "task_id": task_id}
        if task is None:
            await _ledger_mcp_call(r, "empyralis_set_task_parent", False, task_id=task_id)
            return {"ok": False, "error": f"Task '{task_id}' not found in this workspace.", "task_id": task_id}
        await _ledger_mcp_call(
            r, "empyralis_set_task_parent", True,
            task_id=task_id, parent_task_id=parent_task_id or None,
        )
        return {"ok": True, "task": task}

    @empyralist_mcp.tool(
        title="Update Task Status",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_update_task_status(
        task_id: str, status: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Update a task's status: backlog | todo | in_progress | awaiting_input
        | blocked | in_review | done. Set 'in_review' when you have finished the
        work and a human should check it before the task is closed — that is the
        normal way to hand work back; reserve 'done' for work that needs no
        sign-off. ('open' is still accepted as the old name for 'todo'.)
        Workspace-scoped like empyralis_get_task — any task in your workspace.
        An invalid status is rejected with a clear, agent-facing error naming
        the valid set; it is never silently coerced."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import project_tasks_service as tasks
        try:
            task = await tasks.update_task(tenant_id=tenant, workspace_id=ws, task_id=task_id, status=status)
        except ValueError as exc:
            await _ledger_mcp_call(r, "empyralis_update_task_status", False, task_id=task_id, status=status)
            return {"ok": False, "error": str(exc), "task_id": task_id}
        if task is None:
            await _ledger_mcp_call(r, "empyralis_update_task_status", False, task_id=task_id, status=status)
            return {"ok": False, "error": f"Task '{task_id}' not found in this workspace.", "task_id": task_id}
        await _ledger_mcp_call(r, "empyralis_update_task_status", True, task_id=task_id, status=status)
        return {"ok": True, "task": task}

    @empyralist_mcp.tool(
        title="Set Task Priority",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_set_task_priority(
        task_id: str, priority: int, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Set a task's priority on Linear's scale: 0 = none (untriaged),
        1 = urgent, 2 = high, 3 = medium, 4 = low. NOTE the direction —
        1 is the MOST urgent and 4 the least, so a LOWER number means MORE
        urgent. Pass 0 to clear a priority back to untriaged.

        Triage is yours to do, not only the owner's: if you can tell that a
        task is more or less urgent than the board currently says, set it.
        A separate tool from empyralis_update_task_status on purpose —
        priority (how urgent) and status (where it is in the workflow) are
        independent, and changing one should never quietly change the other.

        Workspace-scoped like empyralis_get_task — any task in your
        workspace. An out-of-range priority is rejected with a clear,
        agent-facing error naming the valid set; it is never silently
        coerced to 'none'."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import project_tasks_service as tasks
        try:
            task = await tasks.update_task(
                tenant_id=tenant, workspace_id=ws, task_id=task_id, priority=priority,
            )
        except ValueError as exc:
            await _ledger_mcp_call(r, "empyralis_set_task_priority", False, task_id=task_id, priority=priority)
            return {"ok": False, "error": str(exc), "task_id": task_id}
        if task is None:
            await _ledger_mcp_call(r, "empyralis_set_task_priority", False, task_id=task_id, priority=priority)
            return {"ok": False, "error": f"Task '{task_id}' not found in this workspace.", "task_id": task_id}
        await _ledger_mcp_call(r, "empyralis_set_task_priority", True, task_id=task_id, priority=priority)
        return {"ok": True, "task": task}

    @empyralist_mcp.tool(
        title="Comment On Task",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_comment_on_task(
        task_id: str, body: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Post a progress note/comment on a task — visible to the owner and
        any other agent that reads the task afterward (task.metadata.comments).
        Workspace-scoped like empyralis_get_task — any task in your workspace."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        author_id = r.get("external_agent_id") or "external_mcp_client"
        author_name = str(r.get("external_agent_display_name") or "").strip()
        from server_modules import project_tasks_service as tasks
        try:
            task = await tasks.add_task_comment(
                tenant_id=tenant, workspace_id=ws, task_id=task_id,
                author_type="external_agent", author_id=author_id, body=body,
                author_display_name=author_name,
            )
        except ValueError as exc:
            await _ledger_mcp_call(r, "empyralis_comment_on_task", False, task_id=task_id)
            return {"ok": False, "error": str(exc), "task_id": task_id}
        if task is None:
            await _ledger_mcp_call(r, "empyralis_comment_on_task", False, task_id=task_id)
            return {"ok": False, "error": f"Task '{task_id}' not found in this workspace.", "task_id": task_id}
        await _ledger_mcp_call(r, "empyralis_comment_on_task", True, task_id=task_id)
        return {"ok": True, "task": task}

    # ── Labels. Ungated for the same reason the task tools above are (see
    # the module docstring's write-gate decision): attaching a chip to a task
    # you can already see is a narrower mutation than empyralis_chat, which
    # runs a whole AI turn ungated. Note what is MISSING here on purpose --
    # there is no empyralis_create_label. The workspace's label vocabulary is
    # a small curated human-owned thing, and an external agent that can mint
    # a label on a guessed word fills it with "bug"/"Bugs"/"bugfix" within a
    # week. empyralis_list_labels exists so attaching is a choice from a real
    # list rather than a guess. ──────────────────────────────────────────

    @empyralist_mcp.tool(
        title="List Labels",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
    async def empyralis_list_labels(ctx: Context = None) -> Dict[str, Any]:
        """List every label available in this workspace, with its colour and
        how many tasks currently carry it. Labels are shared across ALL
        projects in the workspace — a label like 'bug' means the same thing on
        every board. Call this before empyralis_add_task_label so you attach a
        label that actually exists; you cannot create new ones."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import workspace_labels_service as labels
        try:
            rows = await labels.list_labels(tenant_id=tenant, workspace_id=ws)
        except Exception as exc:  # noqa: BLE001
            await _ledger_mcp_call(r, "empyralis_list_labels", False, error=str(exc))
            return {"ok": False, "error": str(exc), "labels": []}
        await _ledger_mcp_call(r, "empyralis_list_labels", True, label_count=len(rows))
        return {"ok": True, "labels": rows}

    @empyralist_mcp.tool(
        title="Add Task Label",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_add_task_label(
        task_id: str, label: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Attach an existing workspace label to a task — how you categorize
        work so a human can filter for it later (e.g. tagging something you hit
        as 'bug'). `label` may be the label's NAME (matched case-insensitively,
        so 'bug' finds a label a human created as 'Bug') or its id.

        Idempotent: attaching a label the task already carries succeeds and
        changes nothing. Does NOT create unknown labels — if the one you want
        does not exist the error lists the ones that do, and the right move is
        to pick one or tell the owner what is missing.

        Workspace-scoped like empyralis_get_task — any task in your
        workspace."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        author_id = r.get("external_agent_id") or "external_mcp_client"
        from server_modules import workspace_labels_service as labels
        try:
            attached = await labels.attach_label(
                tenant_id=tenant, workspace_id=ws, task_id=task_id,
                label=label, added_by=author_id,
            )
        except Exception as exc:  # noqa: BLE001 — unknown label / unknown task both land here
            await _ledger_mcp_call(r, "empyralis_add_task_label", False, task_id=task_id, label=label)
            return {"ok": False, "error": str(exc), "task_id": task_id}
        await _ledger_mcp_call(r, "empyralis_add_task_label", True, task_id=task_id, label=label)
        return {"ok": True, "task_id": task_id, "labels": attached}

    @empyralist_mcp.tool(
        title="Remove Task Label",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_remove_task_label(
        task_id: str, label: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Take a label off a task. `label` may be the label's name
        (case-insensitive) or its id. Removes only the LINK — the label itself
        stays in the workspace vocabulary for every other task. Idempotent:
        detaching a label the task does not carry succeeds and changes nothing.

        Workspace-scoped like empyralis_get_task — any task in your
        workspace."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import workspace_labels_service as labels
        try:
            remaining = await labels.detach_label(
                tenant_id=tenant, workspace_id=ws, task_id=task_id, label=label,
            )
        except Exception as exc:  # noqa: BLE001
            await _ledger_mcp_call(r, "empyralis_remove_task_label", False, task_id=task_id, label=label)
            return {"ok": False, "error": str(exc), "task_id": task_id}
        await _ledger_mcp_call(r, "empyralis_remove_task_label", True, task_id=task_id, label=label)
        return {"ok": True, "task_id": task_id, "labels": remaining}

    # ── Document tools (always live — see the module docstring's write-gate
    # decision: bounded to a project already visible through this key, not a
    # workspace-wide configuration mutation, so not behind
    # EMPYRALIS_MCP_WRITE_ENABLED). MCP is for the FOUNDER'S USERS — a
    # teammate's own Claude/ChatGPT saying "edit this document" — not for
    # Empyralis's own platform agents, which already have document__* native
    # tools (skills_service.py). Needs no hardware: `document` writes go
    # straight to Postgres in-process, same as the task tools above. ──────

    @empyralist_mcp.tool(
        title="Create Document",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_create_document(
        project_id: str, title: str, body: str = "", ctx: Context = None,
    ) -> Dict[str, Any]:
        """Create a markdown document inside one of your workspace's projects
        -- a project's shared knowledge, the same set a human sees in the
        Projects view and any platform agent in that project reads via its
        own document__* tools. Title + markdown body only -- no file path,
        no attachments; there is no upload endpoint here (project documents
        are text by construction).

        project_id must be a project in YOUR workspace -- resolved and
        VERIFIED server-side (never trusted from the argument alone to widen
        scope) before anything is written, the same posture
        skills_service.py's document__* dispatch already enforces for
        platform agents (there the project is derived from the calling
        agent's own identity instead, since a platform agent has no
        project_id argument to trust).

        Every create is automatically recorded as revision 1 in this
        document's history — see empyralis_list_document_revisions. Nothing
        else to call to get tracked history, including with no hardware
        connected."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import projects_repository as _p

        project = await _p.get_project(tenant_id=tenant, workspace_id=ws, project_id=project_id)
        if project is None:
            await _ledger_mcp_call(r, "empyralis_create_document", False, project_id=project_id)
            return {
                "ok": False,
                "error": (
                    f"Project '{project_id}' was not found in your workspace. "
                    "Call empyralis_list_projects to see the project_id values you can use."
                ),
                "project_id": project_id,
            }
        author_id = r.get("external_agent_id") or "external_mcp_client"
        author_name = str(r.get("external_agent_display_name") or "").strip()
        from server_modules import project_documents_repository as documents
        try:
            document = await documents.create_document(
                tenant_id=tenant, workspace_id=ws, project_id=project_id,
                title=title, body=body,
                created_by=author_id,
                changed_by_type="external_agent",
                changed_by_display_name=author_name,
            )
        except Exception as exc:  # noqa: BLE001
            await _ledger_mcp_call(r, "empyralis_create_document", False, project_id=project_id, error=str(exc))
            return {"ok": False, "error": str(exc), "project_id": project_id}
        await _ledger_mcp_call(
            r, "empyralis_create_document", True, project_id=project_id, document_id=document.get("id"),
        )
        return {"ok": True, "document": document}

    @empyralist_mcp.tool(
        title="Edit Document",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_edit_document(
        document_id: str, old_string: str, new_string: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """THE PREFERRED WAY to change a document -- a targeted,
        line/sentence-level patch, never a whole-document rewrite. Use this
        for "fix this typo" / "update this sentence" / "change this line" —
        the normal case for "edit this document" / "update this document."

        `old_string` must match the document's CURRENT body EXACTLY ONCE:
        zero matches and multiple matches both FAIL LOUDLY with NO changes
        made (never guessed, never silently applied, never silently
        widened into a whole-body rewrite). If it fails, call
        empyralis_get_document to re-read the current content and narrow
        old_string (include a nearby heading or line) so the match is
        unique.

        Reach for empyralis_update_document only when you are replacing
        most or all of a document (a genuine full rewrite) -- that tool
        takes the whole new body at once and is the fallback, not the
        default.

        Workspace-scoped like empyralis_get_task -- any document in any
        project in your workspace, not only ones you created.

        Every successful edit is automatically recorded in this document's
        revision history AS A DIFF ("this line changed") — see
        empyralis_list_document_revisions."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        author_id = r.get("external_agent_id") or "external_mcp_client"
        author_name = str(r.get("external_agent_display_name") or "").strip()
        from server_modules import project_documents_repository as documents
        try:
            document = await documents.edit_document_by_replace(
                tenant_id=tenant, workspace_id=ws, document_id=document_id,
                old_string=old_string, new_string=new_string,
                updated_by=author_id,
                changed_by_type="external_agent",
                changed_by_display_name=author_name,
            )
        except documents.DocumentPreconditionFailed as exc:
            # "somebody else changed it" is not the same fact as "your
            # old_string did not match" -- both leave the document
            # untouched, but only one of them is fixed by re-reading and
            # reapplying the SAME edit. Hand back the current body so that
            # retry costs no extra round trip.
            await _ledger_mcp_call(r, "empyralis_edit_document", False, document_id=document_id)
            return {
                "ok": False,
                "conflict": True,
                "error": str(exc),
                "document_id": document_id,
                "current_document": exc.current_document,
            }
        except Exception as exc:  # noqa: BLE001 -- includes the "must match exactly once" failure
            await _ledger_mcp_call(r, "empyralis_edit_document", False, document_id=document_id)
            return {"ok": False, "error": str(exc), "document_id": document_id}
        if document is None:
            await _ledger_mcp_call(r, "empyralis_edit_document", False, document_id=document_id)
            return {
                "ok": False,
                "error": f"Document '{document_id}' not found in your workspace.",
                "document_id": document_id,
            }
        await _ledger_mcp_call(r, "empyralis_edit_document", True, document_id=document_id)
        return {"ok": True, "document": document}

    @empyralist_mcp.tool(
        title="Update Document",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_update_document(
        document_id: str, title: str = "", body: str = "", base_sha256: str = "", ctx: Context = None,
    ) -> Dict[str, Any]:
        """Replace a document's title and/or WHOLE body -- the FALLBACK
        path for a genuine full rewrite. Prefer empyralis_edit_document for
        anything smaller than "replace most of the document" (a typo, a
        sentence, a line): sending a full new body here for a one-line
        change is exactly the whole-blob-rewrite behavior the founder asked
        this surface to avoid.

        Partial update: omit whichever field you are NOT changing (an empty
        title/body means "leave it as is", the same "" -> omitted convention
        empyralis_set_task_parent's own parent_task_id already uses in this
        file). At least one of title/body must be given.

        Workspace-scoped like empyralis_get_task -- any document in any
        project in your workspace, not only ones you created.

        `base_sha256`: PASS THIS. Every empyralis_get_document response
        carries the document's `state_sha256`; sending it back here makes
        this write a compare-and-swap -- it lands only if nothing changed in
        between, and otherwise fails with the current content so you can
        reapply on top of it. Omitting it makes this an UNCONDITIONAL
        overwrite that will silently destroy a teammate's unsaved edit or
        another agent's change if one landed while you were composing this
        body. Prefer empyralis_edit_document, which carries its own
        precondition and needs nothing from you.

        Every update is automatically recorded in this document's revision
        history — see empyralis_list_document_revisions."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        clean_title = str(title or "").strip()
        clean_body = body if body else ""
        if not clean_title and not clean_body:
            await _ledger_mcp_call(r, "empyralis_update_document", False, document_id=document_id)
            return {
                "ok": False,
                "error": "Provide title and/or body to change — both were empty, nothing to update.",
                "document_id": document_id,
            }
        author_id = r.get("external_agent_id") or "external_mcp_client"
        author_name = str(r.get("external_agent_display_name") or "").strip()
        from server_modules import project_documents_repository as documents
        try:
            document = await documents.update_document(
                tenant_id=tenant, workspace_id=ws, document_id=document_id,
                # Explicit None when the caller supplied no base: this tool
                # is documented as the whole-body fallback and a model that
                # never read the document has no base to offer, so the
                # unconditional write stays REACHABLE but has to be asked
                # for -- it is not what happens when a parameter is
                # forgotten in this file.
                expected_sha256=str(base_sha256 or "").strip() or None,
                title=clean_title or None, body=clean_body or None,
                updated_by=author_id,
                changed_by_type="external_agent",
                changed_by_display_name=author_name,
            )
        except documents.DocumentPreconditionFailed as exc:
            await _ledger_mcp_call(r, "empyralis_update_document", False, document_id=document_id)
            return {
                "ok": False,
                "conflict": True,
                "error": str(exc),
                "document_id": document_id,
                "current_document": exc.current_document,
            }
        except Exception as exc:  # noqa: BLE001
            await _ledger_mcp_call(r, "empyralis_update_document", False, document_id=document_id)
            return {"ok": False, "error": str(exc), "document_id": document_id}
        if document is None:
            await _ledger_mcp_call(r, "empyralis_update_document", False, document_id=document_id)
            return {
                "ok": False,
                "error": f"Document '{document_id}' not found in your workspace.",
                "document_id": document_id,
            }
        await _ledger_mcp_call(r, "empyralis_update_document", True, document_id=document_id)
        return {"ok": True, "document": document}

    @empyralist_mcp.tool(
        title="List Documents",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
    async def empyralis_list_documents(project_id: str, ctx: Context = None) -> Dict[str, Any]:
        """List a project's documents, alphabetically by title -- a
        table-of-contents read, not a content dump (bodies are omitted;
        fetch one via empyralis_get_document). project_id must be a project
        in YOUR workspace -- resolved and VERIFIED server-side before
        anything is read, same as empyralis_create_document."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import projects_repository as _p

        project = await _p.get_project(tenant_id=tenant, workspace_id=ws, project_id=project_id)
        if project is None:
            await _ledger_mcp_call(r, "empyralis_list_documents", False, project_id=project_id)
            return {
                "ok": False,
                "error": (
                    f"Project '{project_id}' was not found in your workspace. "
                    "Call empyralis_list_projects to see the project_id values you can use."
                ),
                "project_id": project_id,
                "documents": [],
            }
        from server_modules import project_documents_repository as documents
        rows = await documents.list_documents(tenant_id=tenant, workspace_id=ws, project_id=project_id)
        await _ledger_mcp_call(r, "empyralis_list_documents", True, project_id=project_id, document_count=len(rows))
        return {"ok": True, "project_id": project_id, "documents": rows}

    @empyralist_mcp.tool(
        title="Get Document",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
    async def empyralis_get_document(document_id: str, ctx: Context = None) -> Dict[str, Any]:
        """Get one document by id, including its full markdown body.
        Workspace-scoped like empyralis_get_task -- any document in any
        project in your workspace, not only ones you created."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import project_documents_repository as documents
        document = await documents.get_document(tenant_id=tenant, workspace_id=ws, document_id=document_id)
        await _ledger_mcp_call(r, "empyralis_get_document", document is not None, document_id=document_id)
        if document is None:
            return {
                "ok": False,
                "error": f"Document '{document_id}' not found in your workspace.",
                "document_id": document_id,
            }
        return {"ok": True, "document": document}

    @empyralist_mcp.tool(
        title="List Document Revisions",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )
    async def empyralis_list_document_revisions(
        document_id: str, include_body: bool = False, ctx: Context = None,
    ) -> Dict[str, Any]:
        """List a document's revision history, newest first -- who changed
        it and when, distinguishing a human dashboard edit from a platform
        agent's own document__edit tool from an external MCP caller like
        this one (`changed_by_type`: human / agent / external_agent).
        Read-only: there is no restore/rollback tool. Pass
        include_body=True to read a specific past version's full markdown;
        omitted by default (a history read is normally "who touched this
        and when," not a content dump).

        Every empyralis_create_document / empyralis_update_document call
        (and every human/agent edit) already records one revision
        automatically — this is how you see it, including with no
        hardware connected.

        Workspace-scoped like empyralis_get_task -- any document in any
        project in your workspace."""
        r = await _resolve(ctx); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import project_documents_repository as documents
        document = await documents.get_document(tenant_id=tenant, workspace_id=ws, document_id=document_id)
        if document is None:
            await _ledger_mcp_call(r, "empyralis_list_document_revisions", False, document_id=document_id)
            return {
                "ok": False,
                "error": f"Document '{document_id}' not found in your workspace.",
                "document_id": document_id,
                "revisions": [],
            }
        revisions = await documents.list_document_revisions(
            tenant_id=tenant, workspace_id=ws, document_id=document_id, include_body=include_body,
        )
        await _ledger_mcp_call(
            r, "empyralis_list_document_revisions", True, document_id=document_id, revision_count=len(revisions),
        )
        return {"ok": True, "document_id": document_id, "revisions": revisions}

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
            await _ledger_mcp_call(r, "empyralis_create_project", False, name=name)
            return {"ok": False, "error": str(exc)}
        await _ledger_mcp_call(r, "empyralis_create_project", True, project_id=project.get("id"))
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
        await _ledger_mcp_call(r, "empyralis_create_agent", result.get("ok", False), agent_id=agent_id, project_id=assigned_project)
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
        await _ledger_mcp_call(r, "empyralis_configure_agent", result.get("ok", False), agent_id=agent_id)
        return result

    @empyralist_mcp.tool(
        title="Message Agent",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_message_agent(
        agent_id: str, message: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Not implemented -- always returns ok: false. Agent-to-agent
        messaging has no delivery path today (nothing ever reads it back);
        the error explains this and tells you to create/assign a task to
        the target agent instead. Do not retry this tool."""
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from server_modules.fleet_tools import fleet_message_agent
        result = await fleet_message_agent(
            workspace_id=ws, agent_id=agent_id, message=message, actor_id="external_mcp_client",
        )
        await _ledger_mcp_call(r, "empyralis_message_agent", result.get("ok", False), agent_id=agent_id)
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
            await _ledger_mcp_call(r, "empyralis_assign_channel_bot", False, agent_id=agent_id, channel=ch)
            return {"ok": False, "error": str(exc), "channel": ch}
        await _ledger_mcp_call(r, "empyralis_assign_channel_bot", True, agent_id=agent_id, channel=ch)
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
            await _ledger_mcp_call(r, "empyralis_release_channel_bot", False, agent_id=agent_id, channel=ch)
            return {"ok": False, "error": str(exc), "channel": ch}
        await _ledger_mcp_call(r, "empyralis_release_channel_bot", True, agent_id=agent_id, channel=ch)
        return {"ok": True, "channel": ch, **(result if isinstance(result, dict) else {})}

    @empyralist_mcp.tool(
        title="Connect Connector",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    async def empyralis_connect_connector(
        provider: str, ctx: Context = None,
    ) -> Dict[str, Any]:
        """Begin connecting an OAuth connector (e.g. 'gmail', 'github', 'slack').
        Returns an ``authorization_url`` the human opens to grant access. Requires writes_enabled.

        MAN-205: this used to pass ``SimpleNamespace(base_url=...)`` as the
        ``request``. ``start_oauth`` -> ``callback_url`` -> ``request_origin``
        reads ``request.headers`` first, so every call raised
        ``AttributeError`` before an authorization_url could exist — the tool
        was advertised and had never once succeeded.

        The replacement is a REAL ``starlette.requests.Request`` built from a
        real ASGI scope, not a wider stand-in: a stand-in only covers the
        attributes whoever wrote it happened to think of, which is exactly
        how this broke. The live MCP request is deliberately NOT reused —
        this app is mounted at ``/mcp``, so its ``base_url`` carries that
        root path and the derived callback URL would be
        ``/mcp/api/connections/oauth/...``, i.e. a 404 the customer only
        discovers after granting access.
        """
        r = await _resolve(ctx); _check_write(r); ws = _ws(r)
        from server_modules import connection_oauth_service
        try:
            started = connection_oauth_service.start_oauth(
                provider=str(provider or "").strip().lower(),
                workspace_id=ws, surface="sage", request=_public_origin_request(),
                user_id=str(r.get("external_agent_id") or "").strip() or "external_mcp_client",
            )
        except Exception as exc:  # noqa: BLE001 — e.g. provider not OAuth-configured on this server
            await _ledger_mcp_call(r, "empyralis_connect_connector", False, provider=provider)
            return {"ok": False, "error": str(exc), "provider": provider}
        url = started.get("authorization_url") if isinstance(started, dict) else None
        await _ledger_mcp_call(r, "empyralis_connect_connector", True, provider=provider)
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
        real customer. ``agent_id`` is a deployed-agent id. Requires writes_enabled.

        MAN-205: this tool could never once have run. ``execute_test_turn``
        takes ``tenant_id`` as a keyword-only argument with NO default, and
        this call site never passed it -> ``TypeError`` on every invocation,
        every time, since the tool was registered. The request was also a
        ``SimpleNamespace(message, channel)`` while the callee reads
        ``request.runtime_mode`` and ``request.customer_profile`` — so even
        with the TypeError fixed it would have died on ``AttributeError``
        two lines later.

        Both are fixed by building the call the way the REAL producer builds
        it (``routes_deployed_agents.test_turn_deployed_agent``): the actual
        ``DeployedAgentTestTurnRequest`` pydantic model rather than a
        hand-rolled stand-in that cannot notice a field it is missing, and
        ``result.model_dump()`` rather than an ``isinstance(dict)`` branch
        that would have quietly wrapped a pydantic object under ``"result"``.
        """
        r = await _resolve(ctx); _check_write(r); ws = _ws(r); tenant = await _tenant(ws)
        from server_modules import deployed_agent_test_turn_service
        from server_modules.schemas import DeployedAgentTestTurnRequest
        current_user = _mcp_current_user(r, ws, tenant)
        request = DeployedAgentTestTurnRequest(
            workspace_id=ws, message=message, channel="test",
        )
        try:
            result = await deployed_agent_test_turn_service.execute_test_turn(
                deployed_agent_id=agent_id, workspace_id=ws, tenant_id=tenant,
                request=request, current_user=current_user,
            )
        except Exception as exc:  # noqa: BLE001 — readiness / not-a-deployed-agent surfaces cleanly
            await _ledger_mcp_call(r, "empyralis_trigger_test_turn", False, agent_id=agent_id)
            return {"ok": False, "error": str(exc), "agent_id": agent_id}
        payload = result.model_dump() if hasattr(result, "model_dump") else (
            result if isinstance(result, dict) else {"result": result}
        )
        await _ledger_mcp_call(r, "empyralis_trigger_test_turn", True, agent_id=agent_id)
        return {"ok": True, "agent_id": agent_id, **payload}


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
