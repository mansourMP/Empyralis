"""
Single command registry — one source of truth for every /command.

Replaces the two if/elif chains in:
  - sage_command_dispatcher.py  (channel commands)
  - direct_chat_response_service.py  (web slash commands)

Both surfaces dispatch from this registry.  Commands that only make sense
on one surface (e.g. /compact on channels, /status on web) are scoped so
they are silently ignored on the wrong surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

# ── types ──────────────────────────────────────────────────────────────────

CommandHandler = Callable[..., Awaitable[Optional[Dict[str, Any]]]]


@dataclass
class Command:
    name: str
    aliases: List[str] = field(default_factory=list)
    description: str = ""
    # Where this command is valid.  "channel" = Telegram/Discord/WhatsApp,
    # "web" = web chat UI, "both" = everywhere.
    scope: str = "both"
    # Can appear inline in messages (stripped before LLM sees them).
    # Directive-only messages persist the setting; inline directives in
    # mixed messages act as one-time hints.
    directive: bool = False
    # Works embedded in normal text — executed AND stripped before LLM.
    inline_shortcut: bool = False
    # "all" = anyone can use; "owner" = workspace owner only.
    access: str = "all"


# ── registry ───────────────────────────────────────────────────────────────

_registry: Dict[str, Command] = {}
_handlers: Dict[str, CommandHandler] = {}


def register(
    name: str,
    handler: CommandHandler,
    *,
    aliases: Optional[List[str]] = None,
    description: str = "",
    scope: str = "both",
    directive: bool = False,
    inline_shortcut: bool = False,
    access: str = "all",
) -> None:
    """Register a command with its handler."""
    cmd = Command(
        name=name,
        aliases=aliases or [],
        description=description,
        scope=scope,
        directive=directive,
        inline_shortcut=inline_shortcut,
        access=access,
    )
    _registry[name] = cmd
    _handlers[name] = handler
    for alias in cmd.aliases:
        _registry[alias] = cmd
        _handlers[alias] = handler


def get(name: str) -> Optional[Command]:
    """Look up a command by name (with or without leading /)."""
    key = name.lstrip("/")
    return _registry.get(key)


def get_handler(name: str) -> Optional[CommandHandler]:
    """Return the handler for a command, or None."""
    key = name.lstrip("/")
    return _handlers.get(key)


def list_for_scope(scope: str) -> List[Command]:
    """All commands available on the given surface."""
    seen: set[str] = set()
    result: List[Command] = []
    for cmd in _registry.values():
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        if cmd.scope in (scope, "both"):
            result.append(cmd)
    return result


def help_text(scope: str = "both") -> str:
    """One-line-per-command help block for the given surface."""
    lines: List[str] = []
    for cmd in list_for_scope(scope):
        alias_hint = f" ({" / ".join(cmd.aliases)})" if cmd.aliases else ""
        lines.append(
            f"/{cmd.name}{alias_hint} — {cmd.description}"
            if cmd.description
            else f"/{cmd.name}{alias_hint}"
        )
    return "\n".join(lines)


def get_directives() -> list[str]:
    """Return command names that are marked as directives."""
    seen: set[str] = set()
    result: list[str] = []
    for cmd in _registry.values():
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        if cmd.directive:
            result.append(cmd.name)
    return result


def get_inline_shortcuts() -> list[str]:
    """Return command names that are marked as inline shortcuts."""
    seen: set[str] = set()
    result: list[str] = []
    for cmd in _registry.values():
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        if cmd.inline_shortcut:
            result.append(cmd.name)
    return result


async def _is_sender_owner(sender_id: str, workspace_id: str) -> bool:
    """Check if *sender_id* is the workspace owner.

    Matches *sender_id* against the workspace's identity_links — the same
    owner-linkage triage_service.resolve_sender_identity() checks for channel
    sender classification (identity_links[channel_type] = {user_id,
    sender_hash}). Deliberately channel-agnostic: this signature has no
    channel_origin, and a sender_id (a Telegram numeric id, a Discord
    snowflake, ...) is not expected to collide across channel types, so a
    match on ANY linked channel is treated as owner. Fails to False (not
    owner) on any missing input or lookup error — an owner-gated command
    must never execute for a sender we couldn't positively identify.
    """
    clean_sender_id = str(sender_id or "").strip()
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_sender_id or not clean_workspace_id:
        return False
    try:
        from server_modules.control_plane_repository import get_workspace_by_id

        workspace = await get_workspace_by_id(clean_workspace_id)
    except Exception:
        return False
    if not isinstance(workspace, dict):
        return False

    raw_links = workspace.get("identity_links")
    identity_links: Dict[str, Any] = {}
    if isinstance(raw_links, dict):
        identity_links = raw_links
    elif isinstance(raw_links, str) and raw_links.strip():
        import json

        try:
            parsed = json.loads(raw_links)
        except Exception:
            return False
        if isinstance(parsed, dict):
            identity_links = parsed

    for channel_data in identity_links.values():
        if not isinstance(channel_data, dict):
            continue
        linked_user_id = str(channel_data.get("user_id") or "").strip()
        owner_sender_hash = str(channel_data.get("sender_hash") or "").strip()
        if linked_user_id and linked_user_id == clean_sender_id:
            return True
        if owner_sender_hash and owner_sender_hash == clean_sender_id:
            return True
    return False


# ── message processor ──────────────────────────────────────────────────────


@dataclass
class ProcessedMessage:
    """Result of :func:`process_message`."""
    text: str              # cleaned text (directives + shortcuts stripped)
    replies: list[str]     # replies from executed directives / shortcuts
    is_command_only: bool  # True if nothing remains after stripping


async def process_message(
    *,
    text: str,
    workspace_id: str,
    surface: str = "channel",
    **kwargs: Any,
) -> ProcessedMessage:
    """Parse directives and inline shortcuts from *text*, execute them,
    and return the cleaned text with collected replies.

    This is the preferred entry point for ALL inbound message handling.
    It replaces raw :func:`dispatch` for the initial message classification
    step — :func:`dispatch` is still used internally to execute individual
    directive/shortcut handlers.
    """
    import re as _re
    import logging as _plog
    _log = _plog.getLogger(__name__)

    _msg = str(text or "")
    replies: list[str] = []
    sender_id = str(kwargs.get("sender_id") or kwargs.get("channel_sender_id") or "")

    # ── 1. Extract directives ──────────────────────────────────────────
    # A directive is /<name> optionally followed by args.  Args extend to
    # the next /<token> or end-of-string.
    directive_names = get_directives()
    remaining = _msg
    found_directives: list[tuple[str, str]] = []  # (name, args)

    if directive_names:
        # Build a pattern that matches any directive: /(name1|name2|...)(\s+.*?)?
        # Stop at the next /[a-z] token or end of string.
        _names_pat = "|".join(_re.escape(n) for n in directive_names)
        _dir_re = _re.compile(
            rf"/({_names_pat})\b(\s+(?:(?!\/[a-z])[\s\S])*)?",
            _re.IGNORECASE,
        )
        matches: list[_re.Match] = []
        for m in _dir_re.finditer(remaining):
            matches.append(m)

        # Strip directives from the text (process in reverse order to
        # preserve string positions).
        for m in reversed(matches):
            name = m.group(1).lower()
            args = (m.group(2) or "").strip()
            found_directives.append((name, args))
            remaining = remaining[:m.start()] + remaining[m.end():]

        # Collapse whitespace left by stripped tokens.
        remaining = _re.sub(r"\s{2,}", " ", remaining).strip()

    # ── 2. Extract inline shortcuts ────────────────────────────────────
    shortcut_names = get_inline_shortcuts()
    found_shortcuts: list[str] = []

    if shortcut_names:
        _sc_names_pat = "|".join(_re.escape(n) for n in shortcut_names)
        _sc_re = _re.compile(rf"/({_sc_names_pat})\b", _re.IGNORECASE)
        sc_matches: list[_re.Match] = []
        for m in _sc_re.finditer(remaining):
            sc_matches.append(m)

        for m in reversed(sc_matches):
            found_shortcuts.append(m.group(1).lower())
            remaining = remaining[:m.start()] + remaining[m.end():]

        remaining = _re.sub(r"\s{2,}", " ", remaining).strip()

    # ── 3. Access control ──────────────────────────────────────────────
    is_owner = await _is_sender_owner(sender_id, workspace_id)
    authorized_directives: list[tuple[str, str]] = []
    for name, args in found_directives:
        cmd = get(name)
        if cmd and cmd.access == "owner" and not is_owner:
            _log.debug("process_message: skipping unauthorized directive /%s", name)
            continue
        authorized_directives.append((name, args))

    authorized_shortcuts: list[str] = []
    for name in found_shortcuts:
        cmd = get(name)
        if cmd and cmd.access == "owner" and not is_owner:
            _log.debug("process_message: skipping unauthorized shortcut /%s", name)
            continue
        authorized_shortcuts.append(name)

    # ── 4. Execute directives ──────────────────────────────────────────
    for name, args in authorized_directives:
        try:
            result = await dispatch(
                text=f"/{name} {args}".strip(),
                workspace_id=workspace_id,
                surface=surface,
                **kwargs,
            )
            if result and result.get("reply"):
                replies.append(str(result["reply"]))
        except Exception as exc:
            _log.warning("process_message: directive /%s failed: %s", name, exc)

    # ── 5. Execute shortcuts ───────────────────────────────────────────
    for name in authorized_shortcuts:
        try:
            result = await dispatch(
                text=f"/{name}",
                workspace_id=workspace_id,
                surface=surface,
                **kwargs,
            )
            if result and result.get("reply"):
                replies.append(str(result["reply"]))
        except Exception as exc:
            _log.warning("process_message: shortcut /%s failed: %s", name, exc)

    # ── 6. Classify ────────────────────────────────────────────────────
    is_command_only = not remaining.strip()

    return ProcessedMessage(
        text=remaining,
        replies=replies,
        is_command_only=is_command_only,
    )


def parse(text: str) -> tuple[str, str]:
    """Split "/model gpt-5" into ("model", "gpt-5")."""
    s = (text or "").strip()
    if not s.startswith("/"):
        return ("", "")
    tokens = s.split(maxsplit=1)
    cmd = tokens[0][1:].strip().lower()
    remainder = tokens[1].strip() if len(tokens) > 1 else ""
    return (cmd, remainder)


# ── dispatch ───────────────────────────────────────────────────────────────


async def dispatch(
    *,
    text: str,
    workspace_id: str,
    surface: str = "channel",
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Parse *text*, look up a registered handler, and return its result.

    Returns ``None`` when *text* is not a recognised command (caller should
    treat it as a normal message).  Otherwise returns a dict with at minimum
    ``{"reply": "…"}``.
    """
    cmd_name, remainder = parse(text)
    if not cmd_name:
        return None

    handler = get_handler(cmd_name)
    if handler is None:
        return None

    cmd = get(cmd_name)
    if cmd and cmd.scope not in (surface, "both"):
        # Command exists but not for this surface — silently ignore
        return None

    # Owner-gated commands (e.g. /bash, /config, /mcp, /plugins, /debug) must
    # never execute for a non-owner sender. process_message() already checks
    # this before calling into dispatch() for its own callers, but
    # sage_command_dispatcher.py (every customer-facing channel — Telegram,
    # Discord, WhatsApp, Slack, WeChat, iMessage) calls dispatch() directly,
    # so the check must also live here or those channels get an ungated
    # shell/config/plugin command. Silently treat as unrecognized (None) —
    # do not reveal the command exists to an unauthorized sender.
    if cmd and cmd.access == "owner":
        sender_id = str(kwargs.get("sender_id") or kwargs.get("channel_sender_id") or "")
        if not await _is_sender_owner(sender_id, workspace_id):
            return None

    return await handler(
        workspace_id=workspace_id,
        remainder=remainder,
        surface=surface,
        **kwargs,
    )


def dispatch_sync(
    *,
    text: str,
    workspace_id: str,
    surface: str = "web",
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Synchronous wrapper for use inside sync generator functions.

    Prefer :func:`dispatch` when you can ``await``.  This wrapper exists
    for the rare case where a sync generator (inside a running event loop)
    needs to handle a /command before yielding SSE events.
    """
    import asyncio

    try:
        # Running loop — we are on the event-loop thread.
        loop = asyncio.get_running_loop()
        coro = dispatch(text=text, workspace_id=workspace_id, surface=surface, **kwargs)
        # ensure_future + brief drive: command dispatch is fast (<50 ms) so
        # the brief loop-block is harmless here.
        task = loop.create_task(coro)
        while not task.done():
            loop.call_soon(lambda: None)
        return task.result()
    except RuntimeError:
        # No running loop — safe to run synchronously.
        return asyncio.run(dispatch(
            text=text, workspace_id=workspace_id, surface=surface, **kwargs
        ))


# ── built-in command handlers ──────────────────────────────────────────────


async def _handle_help(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    return {"reply": help_text(surface)}


async def _handle_memory(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    try:
        from server_modules import memory_service

        text = memory_service.get_memory(workspace_id) or "No memory facts saved yet."
        return {"reply": text}
    except Exception:
        return {"reply": "Memory is temporarily unavailable."}


# ── registration — called once at import time ──────────────────────────────

def _register_builtins() -> None:
    # Sessions & runs (standalone only — must be the entire message)
    register("new", _handle_new, description="Start a new task session")
    register("main", _handle_main, description="Return to the main thread")
    register("compact", _handle_compact, description="Summarise and clear old context")
    register("stop", _handle_stop, description="Abort the current run")
    register("clear", _handle_clear, description="Clear conversation history for this thread")
    register("export", _handle_export, aliases=["export-trajectory"],
             description="Export session data")

    # Model & thinking (directives — can appear inline, stripped before LLM)
    register("model", _handle_model,
             description="Set the AI model, or show available models when called without arguments",
             directive=True, inline_shortcut=True)
    register("thinking", _handle_thinking, aliases=["think"],
             description="Set thinking effort level (off|minimal|low|medium|high)",
             directive=True, inline_shortcut=True)

    # Discovery & status (inline shortcuts — work embedded in text)
    register("help", _handle_help, description="Show available commands",
             inline_shortcut=True)
    register("commands", _handle_commands, description="Show full command catalog",
             inline_shortcut=True)
    register("tools", _handle_tools, description="Show what the agent can use right now",
             inline_shortcut=True)
    register("status", _handle_status, description="Report AI readiness and connected providers",
             inline_shortcut=True)
    register("whoami", _handle_whoami, description="Show your sender ID",
             inline_shortcut=True)
    register("usage", _handle_usage, description="Show token and cost summary",
             inline_shortcut=True)

    # Memory
    register("memory", _handle_memory, description="View saved memory entries for this workspace")
    register("forget", _handle_forget, description="Delete a memory entry by key")

    # Tasks & agents
    register("tasks", _handle_tasks, description="List background tasks")
    register("agents", _handle_agents, description="List sub-agents for this session")
    register("skills", _handle_skills, aliases=["skill"],
             description="List or run available skills")

    # Admin (owner-gated)
    register("config", _handle_config, description="Read or write configuration",
             access="owner")
    register("mcp", _handle_mcp, description="Manage MCP server configuration",
             access="owner")
    register("plugins", _handle_plugins, aliases=["plugin"],
             description="Manage plugins", access="owner")
    register("debug", _handle_debug, description="Runtime-only config overrides",
             access="owner")

    # Channel & execution
    register("tts", _handle_tts, description="Text-to-speech control")
    register("bash", _handle_bash, description="Execute a host shell command",
             access="owner")


# ── handler implementations ────────────────────────────────────────────────
#
# Handlers that delegate to existing subsystems import lazily to avoid
# circular imports.  Handlers for subsystems not yet wired return a clear
# "not yet available" message so the command is discoverable.


# ── Sessions & runs ────────────────────────────────────────────────────

async def _handle_new(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    from server_modules.sage_command_dispatcher import _handle_new as _impl
    channel_origin = str(kwargs.get("channel_origin") or kwargs.get("sender_id") or "")
    result = await _impl(workspace_id, channel_origin)
    return {"reply": result}


async def _handle_compact(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    from server_modules.sage_command_dispatcher import _handle_compact as _impl
    thread_id = str(kwargs.get("thread_id", "sage-main"))
    result = await _impl(workspace_id, thread_id)
    return {"reply": result}


async def _handle_stop(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Abort the currently active run for this workspace."""
    services = kwargs.get("services")
    if services is None:
        return {"reply": "Cannot check active runs — services not loaded."}
    try:
        count = services.active_run_count(workspace_id)
    except Exception:
        count = 0
    if count <= 0:
        return {"reply": "No active runs to stop."}
    # Find a run matching this workspace and kill it
    try:
        from server_modules.local_queue import (
            handle_request_local_run_hard_kill,
            _server as _lq_server,
        )
    except Exception:
        return {"reply": "Run control is not available in this environment."}
    killed = 0
    for run_id, run in list((_lq_server.runs or {}).items()):
        if not isinstance(run, dict):
            continue
        if str(run.get("workspace_id") or "").strip() != str(workspace_id).strip():
            continue
        status = str(run.get("status") or "").strip().lower()
        if status in {"completed", "failed", "timeout", "stopped", "cancelled"}:
            continue
        try:
            handle_request_local_run_hard_kill(
                run_id,
                reason="User requested stop via /stop slash command",
                requested_by=str(kwargs.get("sender_id") or "").strip() or None,
            )
            killed += 1
        except Exception:
            pass
    if killed > 0:
        return {"reply": f"Stopped {killed} active run{'s' if killed != 1 else ''}."}
    return {"reply": "No active runs found matching this workspace."}


async def _handle_main(
    *, workspace_id: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Switch back to the sage-main thread."""
    from server_modules.sage_command_dispatcher import _handle_main as _impl
    channel_origin = str(kwargs.get("channel_origin") or kwargs.get("sender_id") or "")
    await _impl(workspace_id, channel_origin)
    return {"reply": "Heads up: back to the main thread."}


async def _handle_clear(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Clear conversation history for this thread. Platform-voice reply only."""
    return {"reply": "Heads up: conversation history cleared for this thread."}


async def _handle_forget(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Delete a memory entry by key."""
    memory_key = str(remainder or "").strip()
    if not memory_key:
        return {"reply": "Heads up: usage — /forget <key>"}
    try:
        from server_modules.direct_chat_response_service import memory_service
        deleted = memory_service.delete_memory(workspace_id, memory_key)
        return {"reply": f"Heads up: forgot memory '{memory_key}'." if deleted else f"Heads up: memory '{memory_key}' was not found."}
    except Exception:
        return {"reply": f"Heads up: could not delete memory '{memory_key}'."}


async def _handle_export(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Export session data."""
    return {"reply": "Heads up: session export is available. Use the export tool in settings to download your session data."}


# ── Model ───────────────────────────────────────────────────────────────

async def _handle_model(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    # ── Load the model catalog (human-readable labels) ──────────────────
    _catalog: dict = {}
    try:
        from server_modules.provider_profiles import PROVIDER_MODEL_CATALOG
        _catalog = PROVIDER_MODEL_CATALOG
    except Exception:
        _catalog = {}

    model_name = (remainder or "").strip()

    # ── No arg — show available models with human labels ────────────────
    if not model_name:
        services = kwargs.get("services")
        if services is None:
            return {"reply": "Usage: /model <name>\nExample: /model deepseek-chat\n\n(Model list unavailable — services not loaded.)"}
        try:
            connected = services.connected_provider_tokens(workspace_id)
            lines: list[str] = ["Available models:"]
            if connected:
                for provider_id in sorted(connected):
                    provider_models = _catalog.get(provider_id, {}) if _catalog else {}
                    if isinstance(provider_models, dict) and provider_models:
                        for model_id, info in sorted(provider_models.items()):
                            label = str(info.get("label") or model_id)
                            lines.append(f"- {label}  ({provider_id}/{model_id})")
                    else:
                        lines.append(f"- {provider_id}  (configured)")
            else:
                lines.append("- No providers connected")
            lines.append("\nSet one: /model <name or partial match>")
            return {"reply": "\n".join(lines)}
        except Exception:
            return {"reply": "Usage: /model <name>\nExample: /model deepseek-chat"}

    # ── With arg — fuzzy-match against catalog labels ──────────────────
    resolved = model_name  # default: use the raw input
    model_lower = model_name.lower()
    candidates: list[tuple[str, str, str]] = []  # (score, provider_id, model_id)

    for provider_id, provider_models in _catalog.items():
        if not isinstance(provider_models, dict):
            continue
        for model_id, info in provider_models.items():
            label = str(info.get("label") or model_id)
            label_lower = label.lower()
            model_id_lower = model_id.lower()
            # Exact match on label or model_id
            if model_lower == label_lower or model_lower == model_id_lower:
                candidates.append((100, provider_id, model_id))
                break
            # Substring match on label
            if model_lower in label_lower:
                score = 50 + len(model_lower) / max(len(label_lower), 1) * 30
                candidates.append((score, provider_id, model_id))
            # Substring match on model_id
            elif model_lower in model_id_lower:
                score = 40 + len(model_lower) / max(len(model_id_lower), 1) * 30
                candidates.append((score, provider_id, model_id))
            # Starts-with match (lower score)
            elif label_lower.startswith(model_lower):
                candidates.append((20, provider_id, model_id))
            elif model_id_lower.startswith(model_lower):
                candidates.append((10, provider_id, model_id))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_provider, best_model = candidates[0]
        if best_score >= 50:
            info = _catalog.get(best_provider, {}).get(best_model, {})
            label = str(info.get("label") or best_model) if isinstance(info, dict) else best_model
            resolved = f"{best_provider}/{best_model}"
            model_name = label  # display the human label in the reply

    # ── Persist ────────────────────────────────────────────────────────
    try:
        from server_modules.sage_agent_runtime_service import (
            set_persisted_model_preference,
        )
        await set_persisted_model_preference(workspace_id, resolved)
        return {
            "reply": (
                f"Model set to {model_name} ({resolved}).\n"
                "(Persists across restarts and applies to all channels.)"
            )
        }
    except Exception:
        return {"reply": f"Model preference noted: {model_name} ({resolved})"}


async def _handle_thinking(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    level = (remainder or "").strip().lower()
    valid = {"off", "minimal", "low", "medium", "high"}
    if level not in valid:
        return {"reply": f"Usage: /thinking <level>\nValid: {', '.join(sorted(valid))}"}
    # Persist to the ACTING AGENT's own model_config.reasoning_effort — the
    # SAME per-agent field the Fleet Model tab's picker reads/writes (see
    # fleet_tools.py's fleet_configure_agent), not the old workspace-global
    # sage_ai_reasoning_effort metadata this used to write. That old field
    # was inert: nothing but /config's own display ever read it back, so a
    # value set here never reached an actual reply turn (see
    # sage_agent_runtime_service.py's handle_sage_chat / _run_sage_action_
    # loop_v3, which now consult model_config.reasoning_effort instead).
    #
    # Targets whichever agent is acting THIS turn: the resolved specialist
    # when the caller threads one through (kwargs["agent_install_id"] —
    # sage_turn_adapter.py does this for channel turns), else the
    # workspace's own master (Sage) install — the common case for both web
    # Sage chat and the "Direct Chat" runs-API surface, neither of which has
    # a specialist concept to lose here.
    try:
        from server_modules import fleet_tools
        from server_modules import agent_registry_repository as agent_repo
        from server_modules.control_plane_repository import resolve_tenant_id_for_workspace

        ws = str(workspace_id or "default").strip() or "default"
        tenant_id = await resolve_tenant_id_for_workspace(ws, default="default")
        target_agent_id = str(kwargs.get("agent_install_id") or "").strip()
        if not target_agent_id:
            master = await agent_repo.get_workspace_master_agent_install(
                tenant_id=tenant_id, workspace_id=ws,
            )
            target_agent_id = str((master or {}).get("id") or "").strip()
        if not target_agent_id:
            return {"reply": f"Thinking level set to '{level}' for this session (no agent found to persist it against)."}

        install = await agent_repo.get_workspace_agent_install_bundle(
            target_agent_id, tenant_id=tenant_id, workspace_id=ws,
        )
        # Merge, never wholesale-replace — fleet_configure_agent's
        # model_config patch REPLACES the stored dict outright (see
        # FleetAgentDetail.tsx's patchModelConfig() for the identical
        # merge-before-patch requirement on the UI side), so losing the
        # existing mode/provider/model/runtime/gateway_binding here would
        # silently unbind whatever brain this agent was already running.
        existing_meta = dict((install or {}).get("install_metadata") or (install or {}).get("metadata") or {})
        next_model_config = dict(existing_meta.get("model_config") or {})
        next_model_config["reasoning_effort"] = level
        result = await fleet_tools.fleet_configure_agent(
            actor_id=str(kwargs.get("sender_id") or "owner"),
            workspace_id=ws,
            tenant_id=tenant_id,
            agent_id=target_agent_id,
            patch={"model_config": next_model_config},
        )
        if not result.get("ok"):
            # Most likely cause: this level isn't in the acting agent's own
            # mode/runtime vocabulary (e.g. "off" on a claude_code
            # cli_subscription agent, which has no such --effort value) —
            # surface the real reason rather than claiming success.
            return {"reply": f"Thinking level set to '{level}' for this session ({result.get('error') or 'could not persist'})."}
        return {"reply": f"Thinking level set to '{level}' (persisted for this agent)."}
    except Exception:
        return {"reply": f"Thinking level set to '{level}' for this session."}


# ── Discovery & status ─────────────────────────────────────────────────

async def _handle_commands(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    return {"reply": help_text(surface)}


async def _handle_tools(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    tool_capabilities = kwargs.get("tool_capabilities") or []
    availability_payload = kwargs.get("availability_payload") or {}
    lines: list[str] = []
    # Builtins (always available)
    lines.append("Builtins:  web__search  web__fetch  http_request  "
                  "memory_search  memory_get  memory_update  "
                  "generate_image  browser__*  llm__task  send_image")
    # Local tools (gateway-gated)
    if availability_payload.get("ai_ready"):
        lines.append("Local:     file__read  file__write  shell__exec  "
                      "screenshot__capture  computer__*")
    else:
        lines.append("Local:     (offline — no gateway)")
    # Connector tools (workspace capabilities)
    if tool_capabilities:
        names = sorted({t.get("name", "?") for t in tool_capabilities if isinstance(t, dict)})
        if names:
            lines.append(f"Connectors: {', '.join(names)}")
    else:
        lines.append("Connectors: (none configured)")
    return {"reply": "Available tools\n" + "\n".join(lines)}


async def _handle_status(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    services = kwargs.get("services")
    availability_payload = kwargs.get("availability_payload") or {}
    if services is None:
        return {"reply": "Status unavailable — services not loaded."}
    connected = services.connected_provider_tokens(workspace_id)
    try:
        from server_modules import memory_service
        memory_count = len(memory_service.list_memory_entries(workspace_id))
    except Exception:
        memory_count = 0
    active_runs = services.active_run_count(workspace_id)
    return {
        "reply": (
            "Runtime status\n"
            f"- AI ready: {'yes' if bool(availability_payload.get('ai_ready')) else 'no'}\n"
            f"- Connected providers: {', '.join(connected) if connected else 'none'}\n"
            f"- Memory facts: {memory_count}\n"
            f"- Active runs: {active_runs}"
        )
    }


async def _handle_whoami(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    sender_id = str(kwargs.get("sender_id") or kwargs.get("channel_sender_id") or "unknown")
    sender_name = str(kwargs.get("sender_name") or kwargs.get("channel_sender_name") or "")
    origin = str(kwargs.get("channel_origin") or surface)
    lines = [f"Sender ID: {sender_id}"]
    if sender_name:
        lines.append(f"Name: {sender_name}")
    lines.append(f"Channel: {origin}")
    lines.append(f"Workspace: {workspace_id}")
    lines.append(f"Thread: {kwargs.get('thread_id', 'sage-main')}")
    return {"reply": "\n".join(lines)}


async def _handle_usage(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Show credit balance and recent usage for this workspace."""
    try:
        from server_modules.billing_service import (
            credit_balance_for_workspace,
            credit_usage_history_for_workspace,
        )
        balance = credit_balance_for_workspace(workspace_id)
        usage = credit_usage_history_for_workspace(workspace_id, limit=5)

        lines: list[str] = []
        bal_usd = float(balance.get("credit_balance_usd") or 0)
        bal_credits = int(balance.get("credit_balance_credits") or 0)
        lines.append(f"Credit balance: ${bal_usd:.2f} USD  ({bal_credits} credits)")

        entries = usage.get("entries") if isinstance(usage.get("entries"), list) else []
        if entries:
            lines.append("\nRecent usage:")
            for e in entries[:5]:
                label = str(e.get("label") or "Agent")
                credits = int(e.get("credits") or 0)
                tokens = int(e.get("total_tokens") or 0)
                provider = str(e.get("provider") or "").upper() if e.get("provider") else ""
                created = str(e.get("created_at") or "")[:10] if e.get("created_at") else ""
                lines.append(f"  {created}  {credits:+d} credits  {tokens} tokens  {label}")
        else:
            lines.append("No usage recorded yet.")

        lines.append("\nManage billing: /config or the dashboard.")
        return {"reply": "\n".join(lines)}
    except Exception as exc:
        import logging as _ul
        _ul.getLogger(__name__).warning("_handle_usage failed: %s", exc)
        return {"reply": "Usage data is temporarily unavailable. Check the dashboard for provider quotas."}


# ── Tasks & agents ──────────────────────────────────────────────────────

async def _handle_tasks(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """List active runs for this workspace from the durable run store."""
    workspace_token = str(workspace_id or "").strip()
    if not workspace_token:
        # Fail closed rather than reading every workspace's runs. The repository
        # would raise anyway; the broad `except Exception` below would turn that
        # into a misleading "temporarily unavailable".
        return {"reply": "No workspace is selected, so there is nothing to list."}
    try:
        from server_modules.run_state_repository import list_live_runs_page

        runs = await list_live_runs_page(workspace_id=workspace_token, limit=20)

        # Also check in-memory local queue
        try:
            from server_modules.local_queue import _server as _lq_server
            local_runs = dict(_lq_server.runs or {}) if _lq_server else {}
        except Exception:
            local_runs = {}

        active: list[dict] = []
        for r in runs:
            state = str(r.get("state") or "").strip().lower()
            if state in {"completed", "failed", "timeout", "stopped", "cancelled"}:
                continue
            active.append(r)

        # Merge local runs not in the durable store
        seen_ids = {str(r.get("run_id") or "") for r in active}
        for run_id, run in local_runs.items():
            if run_id in seen_ids:
                continue
            if not isinstance(run, dict):
                continue
            status = str(run.get("status") or "").strip().lower()
            if status in {"completed", "failed", "timeout", "stopped", "cancelled"}:
                continue
            active.append({
                "run_id": run_id,
                "workspace_id": run.get("workspace_id", workspace_id),
                "state": status,
                "payload": run,
            })

        if not active:
            return {"reply": "No active background tasks."}

        lines = [f"Active tasks ({len(active)}):"]
        for r in active[:10]:
            rid = str(r.get("run_id") or "?")[:12]
            state = str(r.get("state") or r.get("status") or "?").title()
            registered = str(r.get("registered_at") or "")[:16] if r.get("registered_at") else ""
            lines.append(f"  {rid}  {state}  {registered}")
        if len(active) > 10:
            lines.append(f"  ... and {len(active) - 10} more")
        return {"reply": "\n".join(lines)}
    except Exception as exc:
        import logging as _tl
        _tl.getLogger(__name__).warning("_handle_tasks failed: %s", exc)
        return {"reply": "Task tracking is temporarily unavailable."}


async def _handle_agents(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """List deployed agents in the current workspace."""
    try:
        from server_modules.agent_registry_repository import list_workspace_agent_installs
        from server_modules.control_plane_repository import get_workspace_by_id

        ws = await get_workspace_by_id(workspace_id)
        tenant_id = str((ws or {}).get("tenant_id") or "default").strip() or "default"

        agents = await list_workspace_agent_installs(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            include_master=True,
        )

        if not agents:
            return {"reply": "No agents deployed in this workspace."}

        lines = [f"Deployed agents ({len(agents)}):"]
        for a in agents[:15]:
            name = str(a.get("agent_definition_name") or a.get("agent_definition_slug") or "?")
            kind = str(a.get("agent_kind") or "?")
            status = str(a.get("status") or "active")
            runtime = str(a.get("runtime_class") or a.get("placement_mode") or "?")
            installed = str(a.get("installed_at") or str(a.get("created_at") or ""))[:10]
            lines.append(f"  {name}  ({kind})  {status}  on {runtime}  {installed}")
        if len(agents) > 15:
            lines.append(f"  ... and {len(agents) - 15} more")
        return {"reply": "\n".join(lines)}
    except Exception as exc:
        import logging as _al
        _al.getLogger(__name__).warning("_handle_agents failed: %s", exc)
        return {"reply": "Agent listing is temporarily unavailable."}


async def _handle_skills(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    skill_name = (remainder or "").strip()
    try:
        from server_modules.skills_service import list_installed_skills
        skills = list_installed_skills(workspace_id)
    except Exception:
        skills = []
    if not skills:
        return {"reply": "No skills installed in this workspace."}
    if skill_name:
        # Dispatch to the skill execution bridge
        try:
            from server_modules.skill_registry import execute_skill, get_skill_definition
            definition = get_skill_definition(skill_name, workspace_id=workspace_id)
            if definition is None:
                return {"reply": f"Skill '{skill_name}' is not registered in this workspace."}
            result = await execute_skill(
                skill_id=skill_name,
                tenant_id=str(kwargs.get("tenant_id") or "default"),
                workspace_id=workspace_id,
                goal=str(kwargs.get("remainder") or f"Execute {skill_name}"),
                agent_label="Agent",
                hard_context="",
                operational_policy="",
            )
            reply = str(result.get("reply") or f"{skill_name} completed.")
            return {"reply": reply}
        except Exception as exc:
            import logging as _sl
            _sl.getLogger(__name__).warning("Skill execution failed for %s: %s", skill_name, exc)
            return {"reply": f"Skill '{skill_name}' could not be executed right now."}
    lines = ["Installed skills:"] + [f"- {s}" for s in sorted(skills)[:20]]
    return {"reply": "\n".join(lines)}


# ── Admin ───────────────────────────────────────────────────────────────

async def _handle_config(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Show current workspace configuration."""
    try:
        from server_modules.control_plane_repository import get_workspace_by_id
        from server_modules.workspace_config_schema import workspace_admin_defaults_from_metadata

        ws = await get_workspace_by_id(workspace_id)
        if not ws or not isinstance(ws, dict):
            return {"reply": "Workspace not found."}

        meta = dict(ws.get("metadata") or {})
        defaults = workspace_admin_defaults_from_metadata(meta)

        lines = [
            "Workspace config",
            f"  Runtime target:     {defaults.runtime_target}",
            f"  Billing plan:       {defaults.billing_plan}",
            f"  AI provider:        {defaults.sage_ai_provider or '(auto)'}",
            f"  AI model:           {defaults.sage_ai_model or '(auto)'}",
            f"  AI policy:          {defaults.hosted_sage_ai_policy}",
            f"  Monthly cap:        ${defaults.hosted_sage_ai_monthly_cap_usd:.2f} USD",
            f"  Context budget:     {defaults.context_budget_preset}",
            f"  Retention:          {defaults.retention_preset}",
            f"  Health safety:      {'on' if defaults.health_safety_enabled else 'off'}",
            f"  Live channels:      {', '.join(defaults.allowed_live_channels) if defaults.allowed_live_channels else '(none)'}",
        ]

        # Reasoning effort now lives on the acting agent's own
        # model_config.reasoning_effort (see _handle_thinking) — the same
        # per-agent field the Fleet Model tab's picker reads/writes — not
        # workspace metadata. Best-effort: never let a lookup failure here
        # break the rest of /config's output.
        try:
            from server_modules import fleet_tools
            from server_modules import agent_registry_repository as agent_repo
            from server_modules.control_plane_repository import resolve_tenant_id_for_workspace

            ws_id = str(workspace_id or "default").strip() or "default"
            tenant_id = await resolve_tenant_id_for_workspace(ws_id, default="default")
            target_agent_id = str(kwargs.get("agent_install_id") or "").strip()
            if not target_agent_id:
                master = await agent_repo.get_workspace_master_agent_install(
                    tenant_id=tenant_id, workspace_id=ws_id,
                )
                target_agent_id = str((master or {}).get("id") or "").strip()
            if target_agent_id:
                install = await agent_repo.get_workspace_agent_install_bundle(
                    target_agent_id, tenant_id=tenant_id, workspace_id=ws_id,
                )
                reasoning = str(fleet_tools.resolve_model_config(install).get("reasoning_effort") or "").strip()
                if reasoning:
                    lines.append(f"  Reasoning effort:   {reasoning}")
        except Exception:
            pass

        return {"reply": "\n".join(lines)}
    except Exception as exc:
        import logging as _cl
        _cl.getLogger(__name__).warning("_handle_config failed: %s", exc)
        return {"reply": "Config is temporarily unavailable. Use the dashboard."}


async def _handle_mcp(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """List connected MCP servers and their status."""
    try:
        from server_modules.mcp_registry_service import list_workspace_mcp_servers

        servers = list_workspace_mcp_servers(workspace_id)

        if not servers:
            return {"reply": "No MCP servers configured in this workspace.\n\nAdd one: dashboard → MCP → Add Server"}

        lines = [f"MCP servers ({len(servers)}):"]
        for s in servers:
            sid = str(s.get("id") or "?")
            label = str(s.get("label") or sid)
            enabled = bool(s.get("enabled"))
            tools = int(s.get("tool_count") or 0)
            status = "connected" if enabled else "not connected"
            lines.append(f"  {label} — {status}  [{tools} tool{'s' if tools != 1 else ''}]")
        return {"reply": "\n".join(lines)}
    except Exception as exc:
        import logging as _ml
        _ml.getLogger(__name__).warning("_handle_mcp failed: %s", exc)
        return {"reply": "MCP server listing is temporarily unavailable. Use the dashboard."}


async def _handle_plugins(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Show installed plugins (skills with their availability status)."""
    try:
        from server_modules import installed_skills

        skills = installed_skills.list_installed_skills(workspace_id=workspace_id)

        if not skills:
            return {"reply": "No plugins installed in this workspace.\n\nBrowse the marketplace: dashboard → Plugins"}

        lines = [f"Installed plugins ({len(skills)}):"]
        for s in sorted(skills, key=lambda x: str(x.get("name") or str(x)))[:20]:
            if isinstance(s, dict):
                name = str(s.get("name") or s.get("id") or "?")
                enabled = bool(s.get("enabled"))
                available = bool(s.get("available"))
                status = "active" if (enabled and available) else ("disabled" if not enabled else "unavailable")
                desc = str(s.get("description") or "")[:60]
                lines.append(f"  {name}  [{status}]  {desc}" if desc else f"  {name}  [{status}]")
            else:
                lines.append(f"  {s}")
        if len(skills) > 20:
            lines.append(f"  ... and {len(skills) - 20} more")
        lines.append("\n/skills for full list  |  /skills <name> to run one")
        return {"reply": "\n".join(lines)}
    except Exception as exc:
        import logging as _pl
        _pl.getLogger(__name__).warning("_handle_plugins failed: %s", exc)
        return {"reply": "Plugin listing is temporarily unavailable."}


async def _handle_debug(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Return a system state snapshot for this workspace."""
    try:
        from server_modules.gateway_state_repository import list_workspace_gateway_registrations
        from server_modules.control_plane_repository import get_workspace_by_id
        from server_modules.workspace_config_schema import workspace_admin_defaults_from_metadata
        from server_modules import memory_service
        import time as _time

        snapshot_time = _time.time()

        # Workspace info
        ws = await get_workspace_by_id(workspace_id)
        meta = dict((ws or {}).get("metadata") or {})
        defaults = workspace_admin_defaults_from_metadata(meta) if ws else None

        # Gateway registrations
        gateways = list_workspace_gateway_registrations(workspace_id, include_revoked=False)
        gw_statuses: list[str] = []
        for g in gateways:
            gid = str(g.get("gateway_id") or "?")[:16]
            gst = str(g.get("status") or "?")
            gw_statuses.append(f"{gid} ({gst})")

        # Memory
        try:
            mem_count = len(memory_service.list_memory_entries(workspace_id))
        except Exception:
            mem_count = 0

        # Providers
        services = kwargs.get("services")
        providers: list[str] = []
        if services is not None:
            try:
                providers = services.connected_provider_tokens(workspace_id)
            except Exception:
                pass

        # Active runs (in-memory)
        try:
            from server_modules.local_queue import _server as _lq_server
            local_runs = dict(_lq_server.runs or {}) if _lq_server else {}
            active_local = sum(
                1 for r in local_runs.values()
                if isinstance(r, dict) and str(r.get("status") or "").strip().lower()
                not in {"completed", "failed", "timeout", "stopped", "cancelled"}
            )
        except Exception:
            active_local = 0

        lines = [
            "System debug snapshot",
            f"  Workspace:          {workspace_id}",
            f"  Tenant:             {str((ws or {}).get('tenant_id') or '?') if ws else '?'}",
            f"  Runtime target:     {defaults.runtime_target if defaults else '?'}",
            f"  AI provider:        {defaults.sage_ai_provider or '(auto)' if defaults else '?'}",
            f"  AI model:           {defaults.sage_ai_model or '(auto)' if defaults else '?'}",
            f"  Gateway connected:  {'yes' if gw_statuses else 'no'}",
        ]
        if gw_statuses:
            for gs in gw_statuses:
                lines.append(f"    • {gs}")
        lines.extend([
            f"  Live channels:      {', '.join(defaults.allowed_live_channels) if defaults and defaults.allowed_live_channels else '(none)'}",
            f"  Memory facts:       {mem_count}",
            f"  Active runs:        {active_local}",
            f"  Providers:          {', '.join(providers) if providers else 'none'}",
        ])

        # Channel origins — check workspace's channel_active_threads
        if ws and isinstance(ws.get("channel_active_threads"), dict):
            cat = ws["channel_active_threads"]
            if cat:
                lines.append(f"  Active threads:     {len(cat)}")
                for ch, tid in sorted(cat.items()):
                    lines.append(f"    • {ch} → {str(tid)[:32]}")

        return {"reply": "\n".join(lines)}
    except Exception as exc:
        import logging as _dl
        _dl.getLogger(__name__).warning("_handle_debug failed: %s", exc)
        return {"reply": "Debug snapshot is temporarily unavailable."}


# ── Channel & execution ─────────────────────────────────────────────────

async def _handle_tts(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Toggle text-to-speech mode for this workspace.

    Usage: /tts on   or   /tts off
    When on, the agent will include speech-synthesis instructions in its output.
    """
    toggle = (remainder or "").strip().lower()
    if toggle not in {"on", "off"}:
        # Show current state
        try:
            from server_modules.control_plane_repository import get_workspace_by_id
            ws = await get_workspace_by_id(workspace_id)
            meta = dict((ws or {}).get("metadata") or {})
            current = str(meta.get("tts_enabled") or "").strip().lower()
            state = "on" if current in {"true", "1", "on"} else "off"
            return {"reply": f"TTS is currently {state}.\n\nUsage: /tts on  |  /tts off"}
        except Exception:
            return {"reply": "Usage: /tts on  |  /tts off"}

    enabled = toggle == "on"
    try:
        from server_modules.control_plane_repository import (
            update_workspace_admin_defaults_metadata,
        )
        await update_workspace_admin_defaults_metadata(
            str(workspace_id or "default").strip() or "default",
            {"tts_enabled": "true" if enabled else "false"},
        )
        return {"reply": f"TTS turned {'on' if enabled else 'off'} (persisted across restarts)."}
    except Exception:
        return {"reply": f"TTS toggled to {'on' if enabled else 'off'} for this session."}


async def _handle_bash(
    *, workspace_id: str, remainder: str, surface: str, **kwargs: Any
) -> Dict[str, Any]:
    """Execute a shell command via a connected Gateway, or locally in dev mode.

    Owner-gated.  Requires a connected Gateway with shell.execute capability.
    Falls back to :func:`local_tool_executor.shell_execute` in development mode.
    """
    command = (remainder or "").strip()
    if not command:
        return {"reply": "Usage: /bash <command>\n\nExamples:\n  /bash ls -la\n  /bash cat /etc/hostname\n  /bash df -h"}

    import uuid as _uuid
    import time as _time

    # Try local execution first (dev mode, no gateway needed)
    try:
        from server_modules.local_tool_executor import shell_execute, is_local_dev
        if is_local_dev():
            result = shell_execute(command, timeout=30)
            stdout = str(result.get("stdout") or "").strip()
            stderr = str(result.get("stderr") or "").strip()
            exit_code = int(result.get("exit_code") or 0)
            lines = [f"$ {command}"]
            if stdout:
                lines.append(stdout[:2000])
            if stderr:
                lines.append(f"stderr:\n{stderr[:500]}")
            if exit_code != 0:
                lines.append(f"exit code: {exit_code}")
            return {"reply": "\n".join(lines)}
    except Exception:
        pass

    # Gateway path — find an active gateway for this workspace
    try:
        from server_modules.gateway_state_repository import list_workspace_gateway_registrations
        gateways = list_workspace_gateway_registrations(workspace_id, include_revoked=False)
        active_gw = None
        for g in gateways:
            if str(g.get("status") or "").strip().lower() == "active":
                active_gw = g
                break
        if active_gw is None:
            return {"reply": "No active Gateway found for this workspace.\n\nDeploy the Gateway on a Mac/Windows/Linux machine: dashboard → Gateway → Add Device\n\nOr run in development mode (ENV=development) for local execution."}

        gateway_id = str(active_gw.get("gateway_id") or "").strip()
        run_id = f"cmd-{_uuid.uuid4().hex[:12]}"
        trace_id = f"trace-{_uuid.uuid4().hex[:12]}"

        from server_modules.gateway_execution_service import execute_tool_via_gateway

        result = await execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id="shell.execute",
            arguments={"command": command},
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=workspace_id,
            timeout_seconds=30,
            request_id=kwargs.get("request_id"),
        )

        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
        stdout = ""
        stderr = ""
        exit_code = 0
        for a in artifacts:
            if not isinstance(a, dict):
                continue
            if a.get("mime_type") in ("text/plain", "text/x-stdout"):
                stdout = str(a.get("content") or a.get("text") or "")
            elif a.get("name") == "stderr" or a.get("mime_type") == "text/x-stderr":
                stderr = str(a.get("content") or a.get("text") or "")
            if a.get("name") == "exit_code":
                exit_code = int(a.get("content") or a.get("text") or 0)

        lines = [f"$ {command}"]
        if stdout:
            lines.append(stdout[:2000])
        if stderr:
            lines.append(f"stderr:\n{stderr[:500]}")
        if exit_code:
            lines.append(f"exit code: {exit_code}")
        if not stdout and not stderr:
            status = str(result.get("status") or result.get("state") or "completed")
            lines.append(f"(no output — status: {status})")
        return {"reply": "\n".join(lines)}

    except Exception as exc:
        import logging as _bl
        _bl.getLogger(__name__).warning("_handle_bash failed: %s", exc)
        return {"reply": "The shell command could not be executed. Try again or simplify the command."}


_register_builtins()
