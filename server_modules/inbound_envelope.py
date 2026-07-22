"""Canonical inbound envelope — WHO is talking, FROM WHERE, on every channel.

The founder's rule this module encodes: the agent must never have to *infer*
attribution — the platform *tells* it, structurally, on every inbound message.
The model receives a deterministic one-line header (render_envelope_header) it
cannot miss, and code-level gates (envelope_allows_owner_commands) enforce the
parts that must never depend on model reasoning at all.

Design doc: docs/design/inbound-envelope-design.md
Ground-truth audit that motivated this: docs/design/inbound-attribution-audit.md
(no canonical envelope existed; each channel collapsed or discarded its signals).

Construction happens in each channel's inbound path; transport happens on
NormalizedSageTurn.envelope (channel_adapter.py); injection happens ONCE, in
sage_turn_adapter.execute_sage_turn(), the single chokepoint every channel
routes through. This module deliberately imports nothing from the runtime so
every channel service can import it without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class SurfaceKind(str, Enum):
    """Where an inbound message physically came from — the surface taxonomy.

    OWNER_SELF_CHAT  the owner's own "message yourself" thread (Telegram Saved
                     Messages / WhatsApp self-chat / Signal Note-to-Self): the
                     owner's private command channel to the agent.
    DM               a 1:1 direct conversation with one human.
    GROUP            a multi-member group chat (family group, team group, ...).
    BROADCAST_CHANNEL a one-to-many broadcast surface (Telegram channel,
                     Slack/Discord announcement channels).
    CONSOLE          the product's own UI (web console / Ask AI / agent Chat
                     tab) — always the authenticated owner-side account.
    API              a programmatic caller (API key / webhook / ACP).
    """

    OWNER_SELF_CHAT = "owner_self_chat"
    DM = "dm"
    GROUP = "group"
    BROADCAST_CHANNEL = "broadcast_channel"
    CONSOLE = "console"
    API = "api"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, raw: Any) -> "SurfaceKind":
        cleaned = str(raw or "").strip().lower()
        for member in cls:
            if member.value == cleaned:
                return member
        return cls.UNKNOWN


# Human-readable platform labels for the rendered header. Keys are
# channel_adapter.ChannelOrigin values (kept as plain strings so this module
# stays import-cycle-free); unknown platforms render as their raw token.
_PLATFORM_LABELS: Dict[str, str] = {
    "telegram_hosted": "Telegram",
    "telegram_personal": "Telegram",
    "whatsapp_personal": "WhatsApp",
    "signal_personal": "Signal",
    "imessage_personal": "iMessage",
    "wechat_personal": "WeChat",
    "wechat_official": "WeChat",
    "discord_personal": "Discord",
    "discord_guild": "Discord",
    "slack": "Slack",
    "slack_guild": "Slack",
    "github": "GitHub",
    "sms": "SMS",
    "web": "Console",
    "console": "Console",
    "acp": "API",
    "api": "API",
}


@dataclass(frozen=True)
class EnvelopeSender:
    """The human (or bot) behind one inbound message.

    is_owner is tri-state on purpose: True (verified the workspace owner),
    False (verified NOT the owner), None (channel cannot verify — treated as
    NOT owner by every gate; rendered as "unverified").
    """

    id: str = ""
    display_name: str = ""
    is_owner: Optional[bool] = None
    is_bot: bool = False


@dataclass(frozen=True)
class EnvelopeChat:
    """The room the message arrived in (group/channel only; empty for DMs)."""

    id: str = ""
    title: str = ""


@dataclass(frozen=True)
class InboundEnvelope:
    """One inbound message's full attribution: platform + surface + sender.

    addressed: whether the agent was explicitly addressed (@-mention or a
    reply to one of its messages). None = the channel can't tell.
    """

    platform: str = "unknown"
    surface: SurfaceKind = SurfaceKind.UNKNOWN
    sender: EnvelopeSender = field(default_factory=EnvelopeSender)
    chat: EnvelopeChat = field(default_factory=EnvelopeChat)
    addressed: Optional[bool] = None

    # ── serialization (metadata persistence / session_ctx transport) ──────

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "surface": self.surface.value,
            "sender_id": self.sender.id,
            "sender_name": self.sender.display_name,
            "sender_is_owner": self.sender.is_owner,
            "sender_is_bot": self.sender.is_bot,
            "chat_id": self.chat.id,
            "chat_title": self.chat.title,
            "addressed": self.addressed,
        }

    @classmethod
    def from_metadata(cls, raw: Any) -> Optional["InboundEnvelope"]:
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                platform=str(raw.get("platform") or "unknown").strip() or "unknown",
                surface=SurfaceKind.coerce(raw.get("surface")),
                sender=EnvelopeSender(
                    id=str(raw.get("sender_id") or "").strip(),
                    display_name=str(raw.get("sender_name") or "").strip(),
                    is_owner=raw.get("sender_is_owner") if isinstance(raw.get("sender_is_owner"), bool) else None,
                    is_bot=bool(raw.get("sender_is_bot")),
                ),
                chat=EnvelopeChat(
                    id=str(raw.get("chat_id") or "").strip(),
                    title=str(raw.get("chat_title") or "").strip(),
                ),
                addressed=raw.get("addressed") if isinstance(raw.get("addressed"), bool) else None,
            )
        except Exception:
            return None


def platform_label(platform: str) -> str:
    token = str(platform or "").strip().lower()
    return _PLATFORM_LABELS.get(token, token or "unknown")


# ── The code-level gates (never model-reasoning-dependent) ─────────────────


def envelope_allows_owner_commands(envelope: Optional[InboundEnvelope]) -> bool:
    """May THIS message carry owner commands / owner authority?

    True ONLY when the sender is the VERIFIED owner AND the surface is a
    private one (self-chat, a DM, or the console). A group or broadcast
    channel can NEVER be an owner-command surface — even if the owner is the
    one typing there — which makes the recurring "family group treated as the
    owner" failure structurally impossible rather than prompt-discouraged.
    Unverified senders (is_owner=None) fail closed.
    """

    if envelope is None:
        return False
    if envelope.sender.is_owner is not True:
        return False
    return envelope.surface in (
        SurfaceKind.OWNER_SELF_CHAT,
        SurfaceKind.DM,
        SurfaceKind.CONSOLE,
    )


# ── The context-window header (what the model actually sees) ───────────────


def render_envelope_header(envelope: Optional[InboundEnvelope]) -> str:
    """One deterministic, token-lean line stating who/where — prepended to the
    inbound message content at the execute_sage_turn chokepoint.

    Shapes (keep stable — the model learns them):
      [Telegram · your owner Mansur · talking to you directly]
      [Slack · DM · from Dana — NOT your owner]
      [Telegram · group "Family" · from Aruzhan — NOT your owner · you were not
       addressed — observe; reply only if clearly addressed or truly helpful;
       otherwise reply exactly [SILENT]]
      [Console · your owner Mansur]
    Returns "" when there is nothing trustworthy to say (no envelope).
    """

    if envelope is None:
        return ""

    label = platform_label(envelope.platform)
    sender_name = envelope.sender.display_name or envelope.sender.id or "unknown sender"

    # Sender clause — owner status is ALWAYS stated explicitly.
    if envelope.sender.is_owner is True:
        sender_clause = f"your owner {sender_name}"
    elif envelope.sender.is_owner is False:
        bot_note = " (a bot)" if envelope.sender.is_bot else ""
        sender_clause = f"from {sender_name}{bot_note} — NOT your owner"
    else:
        sender_clause = f"from {sender_name} — unverified, treat as NOT your owner"

    parts = [label]

    if envelope.surface == SurfaceKind.CONSOLE:
        parts.append(sender_clause)
    elif envelope.surface == SurfaceKind.OWNER_SELF_CHAT:
        parts.append(sender_clause)
        parts.append("talking to you directly")
    elif envelope.surface == SurfaceKind.DM:
        parts.append("DM")
        parts.append(sender_clause)
        if envelope.sender.is_owner is True:
            parts.append("talking to you directly")
    elif envelope.surface in (SurfaceKind.GROUP, SurfaceKind.BROADCAST_CHANNEL):
        room = "group" if envelope.surface == SurfaceKind.GROUP else "channel"
        title = f' "{envelope.chat.title}"' if envelope.chat.title else ""
        parts.append(f"{room}{title}")
        parts.append(sender_clause)
        if envelope.addressed is True:
            parts.append("you were addressed directly")
        else:
            addressed_note = "you were not addressed — " if envelope.addressed is False else ""
            parts.append(
                f"{addressed_note}observe; reply only if clearly addressed or truly helpful; "
                "otherwise reply exactly [SILENT]"
            )
    elif envelope.surface == SurfaceKind.API:
        parts.append("API caller")
        parts.append(sender_clause)
    else:
        parts.append(sender_clause)

    return "[" + " · ".join(parts) + "]"


def prepend_envelope_header(message: str, envelope: Optional[InboundEnvelope]) -> str:
    """message content with the header line on top (idempotent: if the message
    already starts with this exact header, don't double it — protects against
    a retried turn re-running the chokepoint)."""

    header = render_envelope_header(envelope)
    body = str(message or "")
    if not header:
        return body
    if body.startswith(header):
        return body
    return f"{header}\n{body}" if body else header
