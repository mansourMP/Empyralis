from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


_THREAD_COMMAND_RE = re.compile(
    r"^\s*/(?P<command>new|threads|use|status|help)(?:\s+(?P<args>.*?))?\s*$",
    re.IGNORECASE,
)
_THREAD_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class PersonalChannelThreadCommand:
    command: str
    action: str
    args: str = ""
    thread_id: str = ""
    title: str = ""
    dispatch_allowed: bool = False
    requires_owner_context: bool = True

    def as_dict(self) -> Dict[str, object]:
        return {
            "command": self.command,
            "action": self.action,
            "args": self.args,
            "thread_id": self.thread_id,
            "title": self.title,
            "dispatch_allowed": self.dispatch_allowed,
            "requires_owner_context": self.requires_owner_context,
        }


def parse_thread_command(text: str) -> Optional[PersonalChannelThreadCommand]:
    match = _THREAD_COMMAND_RE.match(str(text or ""))
    if not match:
        return None
    command = str(match.group("command") or "").strip().lower()
    args = str(match.group("args") or "").strip()

    if command == "new":
        title = args[:80].strip()
        return PersonalChannelThreadCommand(
            command=command,
            action="start_new_thread",
            args=args,
            title=title,
        )
    if command == "threads":
        return PersonalChannelThreadCommand(command=command, action="list_threads", args=args)
    if command == "use":
        thread_id = args.split()[0].strip() if args else ""
        if not thread_id or not _THREAD_ID_RE.match(thread_id):
            return PersonalChannelThreadCommand(
                command=command,
                action="invalid_thread_reference",
                args=args,
            )
        return PersonalChannelThreadCommand(
            command=command,
            action="switch_thread",
            args=args,
            thread_id=thread_id,
        )
    if command == "status":
        return PersonalChannelThreadCommand(command=command, action="show_status", args=args)
    return PersonalChannelThreadCommand(command=command, action="show_help", args=args)


def canonical_channel_thread_key(
    *,
    channel_key: str,
    gateway_id: str,
    remote_jid: str,
    thread_alias: str = "default",
) -> str:
    channel = _safe_key_part(channel_key, fallback="channel")
    gateway = _safe_key_part(gateway_id, fallback="gateway")
    remote = _safe_key_part(remote_jid, fallback="remote")
    alias = _safe_key_part(thread_alias, fallback="default")
    return f"{channel}:{gateway}:{remote}:{alias}"


def build_thread_command_reply(
    command: PersonalChannelThreadCommand,
    *,
    active_thread_id: str = "",
    available_threads: Iterable[Dict[str, object]] = (),
) -> str:
    if command.action == "start_new_thread":
        title = command.title or "new thread"
        return f"Thread switch requested: start {title}. Empyralis will persist this as a new Sage thread."
    if command.action == "list_threads":
        rows = _thread_rows(available_threads)
        if not rows:
            return "No Sage threads are available for this messaging lane yet."
        return "Available Sage threads:\n" + "\n".join(rows)
    if command.action == "switch_thread":
        return f"Thread switch requested: use {command.thread_id} for this messaging lane."
    if command.action == "invalid_thread_reference":
        return "Use `/use <thread-id>` with a valid Sage thread id."
    if command.action == "show_status":
        active = active_thread_id.strip() or "default"
        return f"This messaging lane is mapped to Sage thread `{active}`."
    return "Messaging commands: `/new [title]`, `/threads`, `/use <thread-id>`, `/status`, `/help`."


def _thread_rows(available_threads: Iterable[Dict[str, object]]) -> List[str]:
    rows: List[str] = []
    for item in available_threads:
        thread_id = str(item.get("id") or item.get("thread_id") or "").strip()
        if not thread_id:
            continue
        title = str(item.get("title") or "Untitled").strip() or "Untitled"
        rows.append(f"- {thread_id}: {title}")
    return rows[:10]


def _safe_key_part(value: str, *, fallback: str) -> str:
    raw = str(value or "").strip()
    cleaned = re.sub(r"[^a-zA-Z0-9._:-]+", "_", raw).strip("_")
    return cleaned[:160] or fallback
