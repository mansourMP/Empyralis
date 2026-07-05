from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from server_modules import runtime_common
from server_modules.connectors.discord_connector import (
    DiscordGatewayListener,
    build_run_goal_from_event,
    event_matches_connector,
    should_trigger_agent_run,
)


LoadVaultFn = Callable[[], Dict[str, Any]]
ResolveCredentialFn = Callable[[str, Optional[str]], Dict[str, Any]]
ListenerFactory = Callable[..., Any]
AppendEventFn = Callable[..., Any]
RouteMessageFn = Callable[..., Awaitable[Dict[str, Any]]]
ResolveTenantFn = Callable[[Dict[str, Any], str], Awaitable[str]]


@dataclass(frozen=True)
class DiscordBotRuntimeStatus:
    connector_id: str
    workspace_id: str
    status: str
    reason: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "connector_id": self.connector_id,
            "workspace_id": self.workspace_id,
            "status": self.status,
            "reason": self.reason,
        }


def _normalize_workspace_id(value: Any) -> str:
    return str(value or "default").strip() or "default"


def _connector_rows(vault: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = vault.get("credentials") if isinstance(vault, dict) else []
    if not isinstance(rows, list):
        return []
    return [
        dict(row)
        for row in rows
        if isinstance(row, dict) and str(row.get("provider") or "").strip().lower() == "discord_bot"
    ]


def _endpoint_key(entry: Dict[str, Any], parsed: Dict[str, Any]) -> str:
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    bindings = metadata.get("channel_registry_bindings") if isinstance(metadata.get("channel_registry_bindings"), dict) else {}
    discord_binding = bindings.get("discord") if isinstance(bindings.get("discord"), dict) else {}
    for candidate in (
        discord_binding.get("endpoint_key"),
        metadata.get("discord_endpoint_key"),
        parsed.get("channel_id"),
        parsed.get("guild_id"),
        entry.get("id"),
        "discord",
    ):
        token = str(candidate or "").strip()
        if token:
            return token
    return "discord"


async def _default_resolve_tenant(entry: Dict[str, Any], workspace_id: str) -> str:
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    for candidate in (entry.get("tenant_id"), metadata.get("tenant_id")):
        token = str(candidate or "").strip()
        if token:
            return token
    from server_modules import control_plane_repository

    workspace = await control_plane_repository.get_workspace_by_id(str(workspace_id or "").strip())
    tenant_id = str((workspace or {}).get("tenant_id") or "").strip()
    if tenant_id:
        return tenant_id
    raise RuntimeError("Discord bot connector is not scoped to a tenant.")


async def _default_route_message(**kwargs: Any) -> Dict[str, Any]:
    from server_modules import agent_channel_router

    return await agent_channel_router.route_inbound_channel_message(**kwargs)


def _default_append_event(**kwargs: Any) -> Any:
    from server_modules import runtime_config

    append_fn = getattr(runtime_config, "_append_channel_event", None)
    if callable(append_fn):
        return append_fn(**kwargs)
    return None


class DiscordBotRuntimeService:
    def __init__(
        self,
        *,
        load_vault: LoadVaultFn = runtime_common.load_vault,
        resolve_vault_credential: ResolveCredentialFn = runtime_common.resolve_vault_credential,
        listener_factory: ListenerFactory = DiscordGatewayListener,
        append_event: Optional[AppendEventFn] = _default_append_event,
        route_message: RouteMessageFn = _default_route_message,
        resolve_tenant: ResolveTenantFn = _default_resolve_tenant,
    ) -> None:
        self.load_vault = load_vault
        self.resolve_vault_credential = resolve_vault_credential
        self.listener_factory = listener_factory
        self.append_event = append_event
        self.route_message = route_message
        self.resolve_tenant = resolve_tenant
        self._listeners: List[Any] = []
        self._threads: List[threading.Thread] = []
        # Phase 3A: machine-local credential locks held for the lifetime of each
        # bot listener, so two gateway processes on one host can't consume the
        # same Discord bot token's inbound stream. Released in stop().
        self._locked_credentials: List[tuple] = []
        self._statuses: List[DiscordBotRuntimeStatus] = []

    def connector_rows(self) -> List[Dict[str, Any]]:
        return _connector_rows(self.load_vault())

    def statuses(self) -> List[Dict[str, str]]:
        return [status.as_dict() for status in self._statuses]

    def preflight(self) -> Dict[str, Any]:
        rows = self.connector_rows()
        statuses: List[DiscordBotRuntimeStatus] = []
        ready = 0
        for row in rows:
            connector_id = str(row.get("id") or "").strip()
            workspace_id = _normalize_workspace_id(row.get("workspace_id"))
            if not connector_id:
                statuses.append(DiscordBotRuntimeStatus("", workspace_id, "fail", "missing_connector_id"))
                continue
            try:
                credentials = self.resolve_vault_credential(connector_id, workspace_id)
            except Exception as exc:
                statuses.append(
                    DiscordBotRuntimeStatus(connector_id, workspace_id, "fail", f"credential_resolution_failed: {exc}")
                )
                continue
            if not str((credentials or {}).get("bot_token") or "").strip():
                statuses.append(DiscordBotRuntimeStatus(connector_id, workspace_id, "fail", "missing_bot_token"))
                continue
            statuses.append(DiscordBotRuntimeStatus(connector_id, workspace_id, "pass", "ready_to_start"))
            ready += 1
        if not rows:
            statuses.append(
                DiscordBotRuntimeStatus("", "default", "warn", "no_registered_discord_bot_connectors")
            )
        return {
            "ok": ready == len(rows) and bool(rows),
            "connector_count": len(rows),
            "ready_count": ready,
            "statuses": [status.as_dict() for status in statuses],
        }

    def start(self, *, block: bool = False) -> Dict[str, Any]:
        rows = self.connector_rows()
        if not rows:
            # ── Discord chatbot v1 fallback: use DISCORD_BOT_TOKEN from env ──
            # No vault connector needed — the bot just listens for DMs and
            # routes them to Sage via execute_sage_turn(channel_origin="discord_personal").
            import os as _os
            _env_token = str(_os.getenv("DISCORD_BOT_TOKEN") or "").strip()
            if not _env_token:
                self._statuses = [
                    DiscordBotRuntimeStatus(
                        connector_id="",
                        workspace_id="default",
                        status="idle",
                        reason="no_registered_discord_bot_connectors",
                    )
                ]
                return {"ok": True, "started": 0, "statuses": self.statuses()}
            # Synthesize a virtual connector entry so the listener loop below works.
            rows = [
                {
                    "id": "discord_env_bot",
                    "workspace_id": "default",
                    "provider": "discord_bot",
                    "metadata": {},
                }
            ]
            # Patch credential resolution for this synthetic row.
            _orig_resolve = self.resolve_vault_credential
            def _env_credential(_cid: str, _wid: str, _orig=_orig_resolve, _tok=_env_token) -> dict:
                if _cid == "discord_env_bot":
                    return {"bot_token": _tok}
                return _orig(_cid, _wid)
            self.resolve_vault_credential = _env_credential

        started = 0
        self._statuses = []
        for row in rows:
            connector_id = str(row.get("id") or "").strip()
            workspace_id = _normalize_workspace_id(row.get("workspace_id"))
            if not connector_id:
                self._statuses.append(
                    DiscordBotRuntimeStatus("", workspace_id, "failed", "missing_connector_id")
                )
                continue
            try:
                credentials = self.resolve_vault_credential(connector_id, workspace_id)
            except Exception as exc:
                self._statuses.append(
                    DiscordBotRuntimeStatus(connector_id, workspace_id, "failed", f"credential_resolution_failed: {exc}")
                )
                continue
            bot_token = str((credentials or {}).get("bot_token") or "").strip()
            if not bot_token:
                self._statuses.append(
                    DiscordBotRuntimeStatus(connector_id, workspace_id, "failed", "missing_bot_token")
                )
                continue
            # Phase 3A: on-prem single-process guarantee — one host process per
            # bot token. The Postgres inbound-owner unique index is the cloud-side
            # guarantee; both layers run together.
            from server_modules import gateway_credential_lock as _cred_lock
            _acquired, _existing = _cred_lock.acquire_scoped_lock(
                "discord_bot", bot_token, metadata={"connector_id": connector_id, "workspace_id": workspace_id}
            )
            if not _acquired:
                _owner_pid = _existing.get("pid") if isinstance(_existing, dict) else None
                self._statuses.append(
                    DiscordBotRuntimeStatus(
                        connector_id, workspace_id, "failed",
                        f"credential_in_use_on_host{f' (PID {_owner_pid})' if _owner_pid else ''}",
                    )
                )
                continue
            self._locked_credentials.append(("discord_bot", bot_token))
            allowed_channel_ids = self._allowed_channel_ids(row, credentials)
            listener = self.listener_factory(
                credentials,
                allowed_channel_ids=allowed_channel_ids,
                on_event=lambda parsed, entry=dict(row), secret=dict(credentials): self.handle_parsed_event_sync(
                    parsed,
                    connector_entry=entry,
                    credentials=secret,
                ),
            )
            self._listeners.append(listener)
            if block:
                listener.run_forever()
            else:
                thread = threading.Thread(target=listener.run_forever, daemon=True)
                thread.start()
                self._threads.append(thread)
            started += 1
            self._statuses.append(DiscordBotRuntimeStatus(connector_id, workspace_id, "online"))

            # Register slash commands with Discord — must happen AFTER the
            # client is connected so we can read the bot's application ID
            # (the client's user.id) instead of the invalid "@me" placeholder.
            _token = str((credentials or {}).get("bot_token") or "").strip()
            _app_id = ""
            _deadline = time.time() + 30  # 30 s for client to finish login
            while time.time() < _deadline:
                _client_user = getattr(listener, "_client", None)
                if _client_user is not None:
                    _user_obj = getattr(_client_user, "user", None)
                    if _user_obj is not None:
                        _app_id = str(getattr(_user_obj, "id", "") or "").strip()
                        if _app_id:
                            break
                time.sleep(0.5)
            if _app_id:
                try:
                    import asyncio as _asyncio_disc
                    _asyncio_disc.ensure_future(
                        _register_discord_slash_commands(bot_token=_token, application_id=_app_id)
                    )
                except Exception:
                    pass
            else:
                import logging as _dc_log
                _dc_log.getLogger(__name__).warning(
                    "Discord slash commands NOT registered: client did not become ready within 30 s"
                )

        return {"ok": True, "started": started, "statuses": self.statuses()}

    def stop(self) -> Dict[str, Any]:
        stopped = len(self._listeners)
        self._listeners.clear()
        self._threads.clear()
        # Phase 3A: release the per-bot-token credential locks we hold.
        if self._locked_credentials:
            from server_modules import gateway_credential_lock as _cred_lock
            for scope, identity in self._locked_credentials:
                try:
                    _cred_lock.release_scoped_lock(scope, identity)
                except Exception:
                    pass
            self._locked_credentials.clear()
        self._statuses = [
            DiscordBotRuntimeStatus("", "default", "offline", "stopped")
        ]
        return {"ok": True, "stopped": stopped}

    def handle_parsed_event_sync(
        self,
        parsed: Dict[str, Any],
        *,
        connector_entry: Dict[str, Any],
        credentials: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(
                self.handle_parsed_event(
                    parsed,
                    connector_entry=connector_entry,
                    credentials=credentials,
                )
            )
            return {"ok": True, "scheduled": True}
        return asyncio.run(
            self.handle_parsed_event(
                parsed,
                connector_entry=connector_entry,
                credentials=credentials,
            )
        )

    async def handle_parsed_event(
        self,
        parsed: Dict[str, Any],
        *,
        connector_entry: Dict[str, Any],
        credentials: Dict[str, Any],
    ) -> Dict[str, Any]:
        raw_event = parsed.get("raw_event") if isinstance(parsed.get("raw_event"), dict) else {}
        raw_author = raw_event.get("author") if isinstance(raw_event.get("author"), dict) else {}
        if bool(raw_author.get("bot")):
            return {"ok": True, "handled": True, "triggered": False, "reason": "bot_authored"}
        metadata = connector_entry.get("metadata") if isinstance(connector_entry.get("metadata"), dict) else {}
        workspace_id = _normalize_workspace_id(connector_entry.get("workspace_id"))
        if not event_matches_connector(parsed, credentials, metadata):
            return {"ok": True, "handled": False, "triggered": False, "reason": "connector_mismatch"}
        if not should_trigger_agent_run(parsed, credentials, metadata=metadata):
            return {"ok": True, "handled": True, "triggered": False, "reason": "not_triggered"}
        # ── Slash command (INTERACTION_CREATE) handling ──
        event_type_raw = str(parsed.get("event_type") or "").strip().lower()
        if event_type_raw == "interaction_create":
            return await _handle_discord_interaction(
                parsed=parsed,
                connector_entry=connector_entry,
            )

        # ── DM events do NOT reach this handler ──
        # DiscordGatewayListener.on_message (discord_connector.py:1056-1058)
        # intercepts DMs and routes them through _handle_dm_via_gateway()
        # (Path C) BEFORE calling self._on_event.  Consequently,
        # message_type="direct_message" is unreachable here.
        # The canonical DM path is _handle_dm_via_gateway() in
        # discord_connector.py.  Guild messages continue below.

        goal = build_run_goal_from_event(parsed)
        if not goal:
            return {"ok": True, "handled": True, "triggered": False, "reason": "empty_goal"}

        trace_id = str(parsed.get("message_id") or parsed.get("interaction_id") or uuid.uuid4().hex).strip()
        session_key = str(parsed.get("channel_id") or parsed.get("guild_id") or "discord").strip() or "discord"
        if callable(self.append_event):
            self.append_event(
                channel="discord",
                direction="inbound",
                event_type=str(parsed.get("event_type") or "message_create"),
                text=str(parsed.get("text") or "").strip() or None,
                workspace_id=workspace_id,
                session_key=session_key,
                message_id=str(parsed.get("message_id") or "").strip() or None,
                trace_id=f"discord:{trace_id}",
                metadata=parsed,
            )
        route_result = await self.route_message(
            tenant_id=await self.resolve_tenant(connector_entry, workspace_id),
            workspace_id=workspace_id,
            channel_key="discord",
            endpoint_key=_endpoint_key(connector_entry, parsed),
            customer_message=goal,
            session_key=session_key,
            message_id=str(parsed.get("message_id") or parsed.get("interaction_id") or "").strip() or None,
            actor_id=str(parsed.get("user_id") or "").strip() or None,
            actor_display_name=str(parsed.get("username") or "").strip() or None,
            metadata={
                "connector_id": str(connector_entry.get("id") or "").strip() or None,
                "delivery_source": "discord_gateway",
                "discord_channel_id": str(parsed.get("channel_id") or "").strip() or None,
                "discord_guild_id": str(parsed.get("guild_id") or "").strip() or None,
                "discord_message_id": str(parsed.get("message_id") or "").strip() or None,
                "source_event_id": str(parsed.get("message_id") or parsed.get("interaction_id") or "").strip() or None,
            },
            allow_master_fallback=False,
        )
        payload = route_result if isinstance(route_result, dict) else {}
        return {
            "ok": True,
            "handled": True,
            "triggered": bool(str(payload.get("run_id") or "").strip() or payload.get("triggered")),
            "run_id": str(payload.get("run_id") or "").strip() or None,
        }

    @staticmethod
    def _allowed_channel_ids(row: Dict[str, Any], credentials: Dict[str, Any]) -> Sequence[str]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        raw = str(
            metadata.get("allowed_channel_ids")
            or credentials.get("allowed_channel_ids")
            or credentials.get("channel_id")
            or ""
        ).strip()
        return [item.strip() for item in raw.split(",") if item.strip()]


__all__ = ["DiscordBotRuntimeService", "DiscordBotRuntimeStatus"]


# ── Discord slash command registration ──

DISCORD_SLASH_COMMANDS = [
    # Sessions & runs
    {"name": "new",      "type": 1, "description": "Start a new task session"},
    {"name": "compact",  "type": 1, "description": "Summarize and clear old context"},
    {"name": "stop",     "type": 1, "description": "Abort the current run"},
    # Model
    {"name": "model",    "type": 1, "description": "Show available models or set the active one"},
    # Discovery
    {"name": "help",     "type": 1, "description": "Show available commands"},
    {"name": "commands", "type": 1, "description": "Show full command catalog"},
    {"name": "tools",    "type": 1, "description": "Show what the agent can use right now"},
    {"name": "status",   "type": 1, "description": "Report AI readiness and providers"},
    {"name": "whoami",   "type": 1, "description": "Show your sender ID"},
    {"name": "usage",    "type": 1, "description": "Show token and cost summary"},
    # Memory
    {"name": "memory",   "type": 1, "description": "Show what I remember about you"},
    # Tasks & agents
    {"name": "tasks",    "type": 1, "description": "List background tasks"},
    {"name": "agents",   "type": 1, "description": "List sub-agents for this session"},
    {"name": "skills",   "type": 1, "description": "List or run available skills"},
    # Admin
    {"name": "config",   "type": 1, "description": "Read or write configuration"},
    {"name": "mcp",      "type": 1, "description": "Manage MCP server configuration"},
    {"name": "plugins",  "type": 1, "description": "Manage plugins"},
    {"name": "debug",    "type": 1, "description": "Runtime-only config overrides"},
    # Channel
    {"name": "tts",      "type": 1, "description": "Text-to-speech control"},
    {"name": "bash",     "type": 1, "description": "Execute a host shell command"},
]


async def _register_discord_slash_commands(bot_token: str, application_id: str) -> None:
    """Register slash commands with Discord via REST API.

    Must be called AFTER the Discord gateway client has connected and
    ``application_id`` (the bot's user ID) is known.
    """
    import logging
    _log = logging.getLogger(__name__)
    if not bot_token:
        _log.warning("Discord slash commands NOT registered: no bot_token")
        return
    if not application_id:
        _log.error("Discord slash commands NOT registered: no application_id")
        return
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"https://discord.com/api/v10/applications/{application_id}/commands",
                headers={"Authorization": f"Bot {bot_token}"},
                json=DISCORD_SLASH_COMMANDS,
            )
            if resp.status_code == 200:
                _log.info("Discord slash commands registered: %d commands", len(DISCORD_SLASH_COMMANDS))
            else:
                _log.error(
                    "Discord slash command registration failed: HTTP %s — %s",
                    resp.status_code,
                    (resp.text or "")[:500],
                )
    except Exception as exc:
        _log.exception("Discord slash command registration error: %s", exc)


async def _handle_discord_interaction(
    *,
    parsed: dict,
    connector_entry: dict,
) -> dict:
    """Handle Discord INTERACTION_CREATE (slash command) events."""
    interaction = parsed.get("raw_event") if isinstance(parsed.get("raw_event"), dict) else {}
    interaction_id = str(interaction.get("id") or "").strip()
    interaction_token = str(interaction.get("token") or "").strip()
    command_data = interaction.get("data") if isinstance(interaction.get("data"), dict) else {}
    command_name = str(command_data.get("name") or "").strip().lower()

    if not command_name or not interaction_id or not interaction_token:
        return {"ok": True, "handled": False, "reason": "invalid_interaction"}

    workspace_id = _normalize_workspace_id(connector_entry.get("workspace_id"))
    user_id = str((interaction.get("user") or interaction.get("member", {}).get("user") or {}).get("id") or "").strip()

    # Dispatch via shared command dispatcher
    from server_modules.sage_command_dispatcher import dispatch_command as _dispatch_cmd
    reply = await _dispatch_cmd(
        command=f"/{command_name}",
        workspace_id=workspace_id,
        thread_id="sage-main",
        channel_origin="discord_personal",
        sender_id=user_id or None,
    )

    if reply is None:
        reply = "Command not recognized."

    # Respond to interaction via Discord REST API
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://discord.com/api/v10/interactions/{interaction_id}/{interaction_token}/callback",
                json={
                    "type": 4,  # CHANNEL_MESSAGE_WITH_SOURCE
                    "data": {"content": str(reply)[:2000]},
                },
            )
    except Exception:
        pass

    return {"ok": True, "handled": True, "triggered": True, "reason": "interaction_handled"}
