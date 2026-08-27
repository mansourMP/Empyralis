"""
Channel transport interface — the thin contract every channel implements.

Each channel (Telegram, Discord, Slack, etc.) provides a ChannelTransport
implementation.  The shared-core SageReplyDispatcher owns the reliability
logic that is genuinely channel-agnostic — guaranteed response, error
classification, message splitting, typing lifecycle, and in-band retry on a
transient send failure.  Formatting is NOT on that list: only the channel
knows its own native markup dialect (MarkdownV2, mrkdwn, Discord markdown,
plain text) and what a rejected/malformed send should fall back to, so
send_message() owns formatting end to end — see its own docstring below.
The channel only needs to implement the transport primitives below.

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
        max_message_length. `text` is the ORIGINAL, UNFORMATTED chunk —
        the dispatcher does NOT call format_text() before invoking this
        method, and never has a good reason to: only the transport knows
        whether the string it is about to hand to the platform is meant to
        be rendered as markup or as literal plain text.

        This method therefore OWNS formatting end to end: call
        format_text() on `text` yourself if you want native markup, attempt
        that formatted send, and on a parse-mode rejection fall back to
        sending the ORIGINAL `text` (never the formatted string) as plain
        text with no parse mode. See TelegramHostedTransport.send_message
        (sage_telegram_hosted_service.py) and AgentBotTransport.send_message
        (hosted_bot_provisioning_service.py) for the reference shape.

        Do not call format_text() a second time on a string this method
        already formatted, and do not have a caller of send_message()
        pre-format `text` before passing it in — either one double-applies
        the transport's own formatting, which is not idempotent (Telegram's
        MarkdownV2 escaping is a documented example: escaping "\\(" a
        second time produces "\\\\(", invalid syntax that gets
        rejected and then delivered as literal, backslash-visible plain
        text). agent_reply_dispatcher._send_one_chunk's own docstring
        records the real incident this caused.

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
