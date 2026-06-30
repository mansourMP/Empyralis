from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass(frozen=True)
class ChatCommandInput:
    raw: str
    command: str
    argument: str
    parsed_kind: str
    route_label: str
    session_label: str
    current_scope: str
    recent_events: List[Dict[str, Any]]


@dataclass(frozen=True)
class ChatCommandResult:
    handled: bool = False
    exit_chat: bool = False
    reset_frame: bool = False


ChatCommandHandler = Callable[[ChatCommandInput], ChatCommandResult | None]


class ChatCommandRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, ChatCommandHandler] = {}

    def register(self, command: str, handler: ChatCommandHandler) -> None:
        token = str(command or "").strip().lower()
        if not token:
            return
        self._handlers[token] = handler

    def dispatch(self, item: ChatCommandInput) -> ChatCommandResult:
        handler = self._handlers.get(str(item.command or "").strip().lower())
        if handler is None:
            return ChatCommandResult(handled=False)
        result = handler(item)
        if result is None:
            return ChatCommandResult(handled=True)
        return result

