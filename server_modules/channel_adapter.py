from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from server_modules.inbound_envelope import InboundEnvelope


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
    # "Public deployed agent" mode: a workspace's own WhatsApp Business
    # number (Twilio-backed — see whatsapp_ingress_service.py's account_sid/
    # message_sid handling) replying to any customer who messages it, the
    # WhatsApp analog of TELEGRAM_HOSTED. Named to match the existing
    # connection-id this same surface already uses in
    # connection_readiness_service.py's _EXTERNAL_ACCOUNT_CONNECTION_IDS /
    # _CERTIFICATION_REQUIREMENTS ("whatsapp_twilio"), not a new coinage.
    WHATSAPP_TWILIO = "whatsapp_twilio"
    DISCORD_PERSONAL = "discord_personal"
    IMESSAGE_PERSONAL = "imessage_personal"
    WECHAT_PERSONAL = "wechat_personal"
    SIGNAL_PERSONAL = "signal_personal"
    SLACK = "slack"
    SLACK_GUILD = "slack_guild"
    DISCORD_GUILD = "discord_guild"
    GITHUB = "github"
    SMS = "sms"
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

    # Canonical inbound attribution (inbound_envelope.py) — WHO sent this and
    # FROM WHERE (owner-self-chat / DM / group / channel / console), verified
    # by the channel, never inferred by the model. Unlike the fields above,
    # this DOES reach the prompt: execute_sage_turn() renders it into a
    # one-line header on the message content, and code-level gates
    # (envelope_allows_owner_commands) consult it. None = legacy caller that
    # hasn't been wired yet; everything then behaves exactly as before.
    envelope: InboundEnvelope | None = None


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
    envelope: InboundEnvelope | None = None,
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
        envelope=envelope if isinstance(envelope, InboundEnvelope) else None,
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


def filter_channel_outbound_reply(reply: str | None) -> str | None:
    """Filter an outbound reply bound for a CHANNEL — Telegram, WhatsApp,
    Discord, Signal, iMessage, WeChat, Slack; DM or GROUP. Returns None if
    the reply should be suppressed.

    ABSOLUTE RULE: no hardcoded platform status/error/failure message may
    EVER be sent into a channel. This layers platform-event suppression on
    top of filter_outbound_reply()'s [SILENT]/NO_REPLY marker filter: any
    text that matches a known platform status/error string (quota denial,
    entitlement block, provider/timeout failure, "nothing to say" fallback,
    etc. — see platform_event.CHANNEL_SUPPRESSED_TEXTS) is ALSO suppressed,
    regardless of whether it arrived via a raised exception, a result.error
    field, or embedded directly in the reply text. The failure should be
    logged and surfaced on the dashboard/activity feed by the caller — the
    channel itself gets nothing.

    Every channel send point (dispatch_sage_reply, the personal-channel
    gateway bridge, business-connector guild/DM routing) must call this —
    not the bare filter_outbound_reply() — before treating text as
    deliverable. Web chat / dashboard call sites are exempt and must keep
    calling filter_outbound_reply() directly so errors stay visible there.
    """
    text = filter_outbound_reply(reply)
    if text is None:
        return None
    from server_modules.platform_event import is_channel_suppressed_text

    if is_channel_suppressed_text(text):
        return None
    return text


# ──────────────────────────────────────────────────────────────────────────
# Reply outcome — "nothing to send" is THREE different facts, not one
# ──────────────────────────────────────────────────────────────────────────

# Keys a reply-dict producer sets to say "this turn did not complete".
# personal_channel_sage_bridge_service._build_error_reply_dict is the only
# producer today.
DELIVERY_FAILED_KEY = "delivery_failed"
DELIVERY_FAILED_CODE_KEY = "delivery_failed_code"

DELIVER = "deliver"
SILENT = "silent"
UNDELIVERED = "undelivered"


@dataclass(frozen=True)
class ChannelReplyOutcome:
    """What a channel delivery seam should actually DO with a reply dict.

    Every personal-channel delivery seam used to collapse three completely
    different facts into one `if not reply or not safe_text:` branch, and
    then record ALL of them with the `<channel>:noreply:` idempotency
    marker — the durable "the agent was asked and DELIBERATELY said nothing"
    record. Two of the three are not that:

        kind=deliver      the agent produced something. Send it.
        kind=silent       the agent ran to completion and chose to say
                          nothing (a group message that wasn't for it, a
                          [SILENT] sentinel). A real decision — record the
                          no-reply marker, do not retry.
        kind=undelivered  the turn did NOT complete: it raised, or a
                          platform status string reached the send boundary
                          pretending to be a reply. The agent never decided
                          anything. Recording the no-reply marker here is a
                          LIE that also disarms the only retry this message
                          will ever get, because `channel.inbound` is
                          at-least-once on every leg and that marker is what
                          the redelivery guard reads.

    text is populated for `deliver`, and for `undelivered` ONLY when the
    recipient is the workspace OWNER and the failure code is in
    platform_event.CHANNEL_OWNER_SAFE_CODES — a frozen literal selected by
    code, never caller-supplied text. It is always None for `silent`.
    """

    kind: str
    text: str | None = None
    media: tuple[dict, ...] = ()
    status_code: str = ""

    @property
    def is_undelivered(self) -> bool:
        return self.kind == UNDELIVERED


def resolve_channel_reply_outcome(
    reply: Any,
    *,
    is_owner: bool = False,
) -> ChannelReplyOutcome:
    """Classify a channel reply dict into deliver / silent / undelivered.

    The single place that decides which of the three a "nothing to send"
    turn actually was. Put here, beside filter_channel_outbound_reply(),
    because every seam that calls that filter must make this decision too —
    a per-seam `if` is a rule the next author has to know, and there are
    already three near-identical copies of that `if` in
    personal_channels_service.py.

    *is_owner* must come from a robust ownership signal
    (personal_channels_service._is_owner_message), never a claimed name.
    False (the default) means "treat as a stranger" — the conservative side.
    """
    from server_modules import platform_event

    data = reply if isinstance(reply, dict) else {}
    media = tuple(item for item in (data.get("media") or []) if isinstance(item, dict))
    raw_text = str(data.get("text") or "").strip()

    # Two DIFFERENT filters, and the difference is the whole point:
    #   filter_outbound_reply        strips the [SILENT]/NO_REPLY sentinels —
    #                                the model's own deliberate "say nothing".
    #   is_channel_suppressed_text   catches a hardcoded PLATFORM status/error
    #                                string — the model said nothing of the
    #                                kind; the platform did.
    # Collapsing them (as `filter_channel_outbound_reply` alone does, correctly,
    # for its own job of deciding deliverability) is what made a failure
    # indistinguishable from a silence decision at every delivery seam.
    unmarked_text = filter_outbound_reply(raw_text) if raw_text else None
    safe_text = (
        None
        if unmarked_text is None or platform_event.is_channel_suppressed_text(unmarked_text)
        else unmarked_text
    )

    failed = bool(data.get(DELIVERY_FAILED_KEY))
    status_code = str(data.get(DELIVERY_FAILED_CODE_KEY) or "").strip()

    # A producer returned NON-EMPTY, NON-SENTINEL text that the suppression
    # allowlist ate: a hardcoded platform status/error string wearing a
    # reply's clothes (the 2026-07-18 shape). The turn failed; it did not
    # choose silence. Suppression still holds — safe_text is already None and
    # raw_text is never read again.
    if not failed and unmarked_text is not None and safe_text is None:
        failed = True

    if failed:
        if not status_code:
            status_code = platform_event.CHANNEL_EXECUTION_FAILED.code
        owner_text = (
            platform_event.owner_channel_text_for_code(status_code) if is_owner else None
        )
        # Media queued before the failure is real agent output (send_image
        # already ran), so it still ships — but the turn is still recorded
        # as undelivered, never as deliberate silence.
        return ChannelReplyOutcome(
            kind=UNDELIVERED,
            text=owner_text,
            media=media,
            status_code=status_code,
        )

    if safe_text or media:
        return ChannelReplyOutcome(kind=DELIVER, text=safe_text or "", media=media)

    return ChannelReplyOutcome(kind=SILENT)
