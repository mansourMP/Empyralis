from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Dict, Tuple


@dataclass(frozen=True)
class ParsedChatInput:
    kind: str
    command: str = ""
    argument: str = ""
    goal: str = ""


def input_prompt_label() -> str:
    return "›"


def _one_app_mode_enabled() -> bool:
    raw = str(os.getenv("ORION_CLI_ONE_APP_MODE", "1") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def commands_hint() -> str:
    return "Commands: type goal | /run <goal> | /ok <id> | /hold <id> <reason> | /session | /status | /help | /exit"


def help_text() -> str:
    lines = [
        "Slash commands:",
        "/help",
        "/status",
        "/engine <codex|orion>",
        "/agent <id> (or /agent, /agents)",
        "/session <key> (or /session)",
        "/model <provider/model> (or /model, /models)",
        "/think <off|low|medium|high|auto>",
        "/verbose <on|off>",
        "/reasoning <on|off>",
        "/usage <off|tokens|full>",
        "/elevated <on|off|ask|full>",
        "/activation <mention|always>",
        "/settings",
        "/run <goal>",
        "/preflight",
        "/approvals",
        "/approval-menu (interactive approval picker)",
        "/objective <add|list|board|get|tail|doctor|pause|resume|complete|run-now|resume-latest|tick|approvals|approve-latest|approve|reject>",
        "/approve-event <event_id_or_short> [note]",
        "/reject-event <event_id_or_short> <reason>",
        "/approve <run_id_or_short> [note]",
        "/reject <run_id_or_short> <reason>",
        "/ok <id_or_short> [note] (smart approve for objective/runtime approvals)",
        "/hold <id_or_short> <reason> (smart reject for objective/runtime approvals)",
        "/new or /reset",
        "/abort",
        "/mode",
        "/trust",
        "/target",
        "/exit",
    ]
    if not _one_app_mode_enabled():
        lines.insert(lines.index("/mode"), "/route <local_run|telegram_send>")
    return "\n".join(lines)


def _split_command(raw: str) -> Tuple[str, str]:
    text = str(raw or "").strip()
    if not text:
        return "", ""
    if not text.startswith("/"):
        return "", text
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    return command, argument


def parse_chat_input(raw: str, *, require_run_prefix: bool) -> ParsedChatInput:
    text = str(raw or "").strip()
    if not text:
        return ParsedChatInput(kind="empty")

    command, argument = _split_command(text)
    if command:
        if command == "/run":
            if argument:
                return ParsedChatInput(kind="run", command="run", goal=argument)
            return ParsedChatInput(kind="command", command="run", argument="")
        return ParsedChatInput(kind="command", command=command.lstrip("/"), argument=argument)

    if require_run_prefix:
        return ParsedChatInput(kind="command", command="prefix_required", argument=text)

    return ParsedChatInput(kind="run", command="run", goal=text)


def is_supported_command(command: str) -> bool:
    return command in {
        "help",
        "commands",
        "engine",
        "exit",
        "quit",
        "refresh",
        "route",
        "session",
        "sessions",
        "status",
        "agent",
        "agents",
        "model",
        "models",
        "think",
        "verbose",
        "reasoning",
        "usage",
        "elevated",
        "activation",
        "abort",
        "new",
        "reset",
        "settings",
        "mode",
        "trust",
        "target",
        "events",
        "dashboard",
        "doctor",
        "approvals",
        "objective",
        "approval-menu",
        "approve-event",
        "reject-event",
        "approve",
        "reject",
        "ok",
        "hold",
        "run",
        "preflight",
        "prefix_required",
    }


def parse_command_alias(command: str) -> str:
    aliases: Dict[str, str] = {
        "commands": "help",
        "quit": "exit",
        "agents": "agent",
        "models": "model",
        "sessions": "session",
        "elev": "elevated",
        "approval": "approvals",
        "pending": "approvals",
        "approvalmenu": "approval-menu",
        "approvalsmenu": "approval-menu",
        "obj": "objective",
        "approveevent": "approve-event",
        "rejectevent": "reject-event",
    }
    token = str(command or "").strip().lower()
    return aliases.get(token, token)
