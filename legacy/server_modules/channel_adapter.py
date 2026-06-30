from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChannelOrigin(str, Enum):
    """Canonical channel origin identifiers. Every inbound message must declare one.

    Use ChannelOrigin.coerce(raw) to parse a free-text string — unknown values
    are stored as ChannelOrigin.UNKNOWN rather than raising, so existing
    channels don't break during transition.
    """
    WEB = "web"
    TELEGRAM_HOSTED = "telegram_hosted"
    TELEGRAM_PERSONAL = "telegram_personal"
    WHATSAPP_PERSONAL = "whatsapp_personal"
    DISCORD_PERSONAL = "discord_personal"
    IMESSAGE_PERSONAL = "imessage_personal"
    WECHAT_PERSONAL = "wechat_personal"
    SIGNAL_PERSONAL = "signal_personal"
    SLACK = "slack"
    SLACK_GUILD = "slack_guild"
    DISCORD_GUILD = "discord_guild"
    GITHUB = "github"
    ACP = "acp"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, raw: str | None) -> ChannelOrigin:
        """Parse a free-text channel_origin string into the enum.

        Returns the matching member, or ChannelOrigin.UNKNOWN for unrecognized
        values. Never raises — this is a transition-safe coercion.
        """
        if not raw:
            return cls.UNKNOWN
        cleaned = str(raw).strip().lower()
        for member in cls:
            if member.value == cleaned:
                return member
        return cls.UNKNOWN


@dataclass
class NormalizedSageTurn:
    """Canonical input shape for ALL channels before handle_sage_chat()."""
    workspace_id: str
    tenant_id: str
    message: str
    surface: str                     # "chat" | "acp"
    mode: str                        # always "owner_sage" — pass through
    current_user: dict | None = None
    attachments: list[dict] = field(default_factory=list)

    # Routing metadata only — NEVER reaches the prompt
    channel_origin: ChannelOrigin = ChannelOrigin.UNKNOWN
    channel_sender_id: str = ""
    channel_sender_name: str = ""
    channel_message_id: str = ""


_SILENCE_MARKERS = (
    "[SILENT]",
    "NO_REPLY",
)


def normalize_sage_inbound(
    *,
    workspace_id: str,
    message: str,
    channel_origin: str,
    surface: str = "chat",
    mode: str = "owner_sage",
    tenant_id: str = "",
    current_user: dict | None = None,
    attachments: list[dict] | None = None,
    channel_sender_id: str = "",
    channel_sender_name: str = "",
    channel_message_id: str = "",
) -> NormalizedSageTurn:
    """Normalize any channel's inbound message into canonical form.

    channel_origin is REQUIRED — no default. Every caller must declare
    which channel surface this message arrived through. The value is
    stored as routing metadata ONLY and never injected into the prompt.
    """
    origin = ChannelOrigin.coerce(channel_origin)
    if origin == ChannelOrigin.UNKNOWN:
        raw = str(channel_origin or "").strip()
        if not raw:
            raise ValueError("channel_origin is required for normalize_sage_inbound()")

    normalized_message = str(message or "").strip()

    return NormalizedSageTurn(
        workspace_id=str(workspace_id or "").strip(),
        tenant_id=str(tenant_id or "").strip(),
        message=normalized_message,
        surface=str(surface or "chat").strip() or "chat",
        mode=str(mode or "owner_sage").strip() or "owner_sage",
        current_user=dict(current_user) if isinstance(current_user, dict) else None,
        attachments=list(attachments) if isinstance(attachments, list) else [],
        channel_origin=origin,
        channel_sender_id=str(channel_sender_id or "").strip(),
        channel_sender_name=str(channel_sender_name or "").strip(),
        channel_message_id=str(channel_message_id or "").strip(),
    )


def filter_outbound_reply(
    reply: str | None,
    silence_marker: str = "[SILENT]",
) -> str | None:
    """Filter outbound reply. Returns None if reply should be suppressed.

    Suppressed cases: None, empty/whitespace-only, exactly [SILENT],
    starts with [SILENT], or matches known NO_REPLY conventions.

    Single shared implementation — all channel send points call this.
    """
    if reply is None:
        return None
    text = str(reply).strip()
    if not text:
        return None
    text_upper = text.upper()
    for marker in _SILENCE_MARKERS:
        if text_upper == marker.upper() or text_upper.startswith(marker.upper()):
            return None
    return text
