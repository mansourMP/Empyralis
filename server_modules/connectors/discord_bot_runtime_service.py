from __future__ import annotations

import asyncio
import re
import threading
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
            if not str((credentials or {}).get("bot_token") or "").strip():
                self._statuses.append(
                    DiscordBotRuntimeStatus(connector_id, workspace_id, "failed", "missing_bot_token")
                )
                continue
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

            # Register slash commands with Discord (fire-and-forget)
            try:
                import asyncio as _asyncio_disc
                _token = str((credentials or {}).get("bot_token") or "").strip()
                _asyncio_disc.ensure_future(
                    _register_discord_slash_commands(bot_token=_token)
                )
            except Exception:
                pass

        return {"ok": True, "started": started, "statuses": self.statuses()}

    def stop(self) -> Dict[str, Any]:
        stopped = len(self._listeners)
        self._listeners.clear()
        self._threads.clear()
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

        # ── Personal DM routing through unified Sage ingress (Path A) ──
        # Mirrors the Telegram-hosted pattern:
        #   parse → dispatch_command() → execute_sage_turn() → filter_outbound_reply() → send_dm()
        # This guarantees identical safety rules, context loading, response envelope,
        # persistence, and audit as every other Main Agent channel.
        message_type = str(parsed.get("message_type") or "").strip().lower()
        if message_type == "direct_message":
            try:
                from server_modules.connectors.discord_connector import send_message as _send_msg

                _discord_user_id = str(parsed.get("user_id") or "").strip()
                _discord_channel_id = str(parsed.get("channel_id") or "").strip()
                _push_name = str(parsed.get("username") or "").strip()
                _text = str(parsed.get("text") or "").strip()

                if not _discord_user_id or not _text:
                    return {"ok": True, "handled": True, "triggered": False, "reason": "empty_dm"}

                # ── /pair <code> — link Discord user to workspace ──
                _pair_match = re.match(r"^/pair\s+(\S+)", _text)
                if _pair_match:
                    _code = _pair_match.group(1).strip()
                    from server_modules.sage_telegram_hosted_service import consume_pairing_code
                    from server_modules.discord_pairing_service import (
                        pair_discord_workspace,
                    )
                    _pair_wid = consume_pairing_code(_code)
                    if _pair_wid:
                        pair_discord_workspace(_discord_user_id, _pair_wid)
                        _send_msg(
                            credentials=dict(credentials),
                            channel_id=_discord_channel_id,
                            content="✅ Connected to Empyralis! Your workspace is now linked. Try sending a message.",
                        )
                    else:
                        _send_msg(
                            credentials=dict(credentials),
                            channel_id=_discord_channel_id,
                            content="Invalid or expired pairing code. Generate a new one at empyralis.ai → Connections → Discord.",
                        )
                    return {"ok": True, "handled": True, "triggered": True, "reason": "pair_command"}

                # ── Workspace lookup ──
                from server_modules.discord_pairing_service import (
                    get_workspace_for_discord_user,
                )
                _workspace_id = get_workspace_for_discord_user(_discord_user_id)
                if not _workspace_id:
                    _send_msg(
                        credentials=dict(credentials),
                        channel_id=_discord_channel_id,
                        content="Send `/pair CODE` to connect your workspace. Get a code at empyralis.ai → Connections → Discord.",
                    )
                    return {"ok": True, "handled": True, "triggered": True, "reason": "unpaired_discord_user"}

                # ── Shared command dispatcher ──
                from server_modules.sage_command_dispatcher import dispatch_command as _dispatch_cmd
                _cmd_reply = await _dispatch_cmd(
                    command=_text,
                    workspace_id=_workspace_id,
                    thread_id="sage-main",
                    channel_origin="discord_personal",
                    sender_id=_discord_user_id or None,
                )
                if _cmd_reply is not None:
                    _send_msg(
                        credentials=dict(credentials),
                        channel_id=_discord_channel_id,
                        content=_cmd_reply,
                    )
                    return {"ok": True, "handled": True, "triggered": True, "reason": "command_dispatched"}

                # ── Route through unified Sage ingress ──
                from server_modules.sage_turn_adapter import execute_sage_turn

                result = await execute_sage_turn(
                    workspace_id=_workspace_id,
                    message=_text,
                    channel_origin="discord_personal",
                    channel_sender_id=_discord_user_id,
                    channel_sender_name=_push_name or None,
                    thread_id="sage-main",
                )

                _sage_reply = str(result.message or "").strip()
                if _sage_reply:
                    from server_modules.channel_adapter import filter_outbound_reply
                    _filtered = filter_outbound_reply(_sage_reply)
                    if _filtered:
                        _send_msg(
                            credentials=dict(credentials),
                            channel_id=_discord_channel_id,
                            content=_filtered,
                        )
                        return {"ok": True, "handled": True, "triggered": True, "reason": "sage_ingress_dm_replied"}
                return {"ok": True, "handled": True, "triggered": True, "reason": "sage_ingress_dm_no_reply"}
            except Exception as _dm_exc:
                import logging as _logging2
                import traceback as _tb
                _logging2.getLogger(__name__).warning(
                    "Discord Sage ingress DM failed: %s\nTRACEBACK:\n%s",
                    _dm_exc, _tb.format_exc(),
                )
                return {"ok": True, "handled": True, "triggered": False, "reason": f"sage_ingress_dm_error: {_dm_exc}"}

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
    {"name": "compact", "description": "Summarize and clear conversation context"},
    {"name": "memory", "description": "Show what Sage remembers about you"},
    {"name": "help", "description": "Show available commands"},
    {"name": "new", "description": "Start a new task session"},
    {"name": "approve", "description": "Approve pending action"},
    {"name": "deny", "description": "Deny pending action"},
]


async def _register_discord_slash_commands(bot_token: str) -> None:
    """Register slash commands with Discord via REST API."""
    if not bot_token:
        return
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # Register globally (no guild_id = global commands)
            resp = await client.put(
                f"https://discord.com/api/v10/applications/@me/commands",
                headers={"Authorization": f"Bot {bot_token}"},
                json=DISCORD_SLASH_COMMANDS,
            )
            if resp.status_code == 200:
                import logging
                _log = logging.getLogger(__name__)
                _log.info("Discord slash commands registered: %d commands", len(DISCORD_SLASH_COMMANDS))
    except Exception:
        pass


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
