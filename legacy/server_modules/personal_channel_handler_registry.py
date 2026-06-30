"""
Registry for personal-channel handlers.

Replaces the hardcoded if/elif dispatch in personal_channels_service.py
with a pluggable registry keyed by channel_key.

Each live personal channel (WhatsApp, Telegram, future channels) registers
one PersonalChannelHandler instance.  The registry dispatches inbound
handling, reply delivery, state sync, and configuration to the correct
handler by channel_key.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class PersonalChannelHandler(ABC):
    """Interface for a single personal channel (WhatsApp, Telegram, etc.)."""

    @property
    @abstractmethod
    def channel_key(self) -> str:
        """e.g. 'whatsapp_personal'"""
        ...

    @property
    @abstractmethod
    def provider(self) -> str:
        """e.g. 'whatsapp_baileys'"""
        ...

    # --- State sync -------------------------------------------------

    def build_state_sync_payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Channel-specific state fields extracted from a gateway-heartbeat message."""
        return {}

    # --- Audit ------------------------------------------------------

    def audit_action_prefix(self) -> str:
        """Short label for audit events, e.g. 'whatsapp' or 'telegram'."""
        return str(self.channel_key).split("_", 1)[0]


class PersonalChannelHandlerRegistry:
    """Holds one handler per channel_key and dispatches operations."""

    def __init__(self) -> None:
        self._handlers: Dict[str, PersonalChannelHandler] = {}

    def register(self, handler: PersonalChannelHandler) -> None:
        key = handler.channel_key
        if key in self._handlers:
            raise ValueError(
                f"Handler already registered for channel_key={key!r}"
            )
        self._handlers[key] = handler

    def get(self, channel_key: str) -> PersonalChannelHandler:
        handler = self._handlers.get(channel_key)
        if handler is None:
            raise ValueError(
                f"No handler registered for channel_key={channel_key!r}"
            )
        return handler

    def has(self, channel_key: str) -> bool:
        return channel_key in self._handlers

    def registered_keys(self) -> tuple[str, ...]:
        return tuple(self._handlers.keys())

    def build_state_sync_payload(
        self, *, channel_key: str, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self.get(channel_key).build_state_sync_payload(message)
