"""
Channel transport interface — the thin contract every channel implements.

Each channel (Telegram, Discord, Slack, etc.) provides a ChannelTransport
implementation.  The shared-core SageReplyDispatcher owns ALL reliability
logic — guaranteed response, error classification, message splitting,
typing lifecycle, formatting.  The channel only needs to implement the
transport primitives below.

Adding a new channel = implement this class (~30 lines) → inherit all R1
guarantees automatically.  No reliability logic lives in the channel.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class ChannelTransport(ABC):
    """Thin contract that each channel implements.

    The core owns the reliability LOGIC.  The channel owns the transport
    PRIMITIVES (send, typing, max-length, formatting).
    """

    # ── Class-level constants (override in subclass) ──

    max_message_length: int = 4000
    """Maximum characters per message chunk.  The dispatcher auto-splits."""

    supports_typing_indicator: bool = False
    """Whether this channel can show a typing/activity indicator."""

    # ── Required primitives ──

    @abstractmethod
    async def send_message(
        self,
        text: str,
        *,
        reply_to_id: Optional[str] = None,
    ) -> bool:
        """Deliver a text chunk to the user.  NEVER raise — return bool.

        The dispatcher has already split the message to fit within
        max_message_length and applied format_text().  This method is
        a pure transport concern: deliver the bytes.

        Returns True if the message was acknowledged by the platform.
        """
        ...

    # ── Optional primitives (override for channels that support them) ──

    async def start_typing(self) -> None:
        """Begin showing a typing/activity indicator.  No-op by default.

        Called before the AI turn starts.  Pair with stop_typing() in a
        try/finally block.  Must be safe to call multiple times (idempotent).
        """
        return

    async def stop_typing(self) -> None:
        """Stop the typing/activity indicator.  No-op by default.

        Must be safe to call even if start_typing() was never called.
        """
        return

    def format_text(self, text: str) -> str:
        """Convert LLM output to this channel's native markup format.

        Default is identity (plain text).  Override for channels that use
        MarkdownV2 (Telegram), mrkdwn (Slack), or Discord-flavored Markdown.
        """
        return text
