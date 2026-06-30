"""
Unified error notification system — single source of truth for every channel.

One file.  Every error path (route handlers, channel bridges, reply dispatcher,
generation service) flows through :func:`classify_error_notification`.  That
function maps a raw error string to a fully-formed `ErrorNotification` with the
correct tone, title, body, and actions.

Channel adapters receive the SAME notification and degrade gracefully:

- **Web chat** → renders as a rich inline card (colored, dismissible, buttons)
- **Telegram**       → markdown text + inline keyboard (native buttons)
- **Discord**        → embed object + message components
- **Slack**          → Block Kit message
- **WhatsApp / Signal / WeChat / iMessage** → plain text via
  :func:`render_error_notification_text`

``classify_error_notification`` shares the identical 5-bucket keyword logic
with :func:`sage_command_dispatcher.classify_error`.  The two functions must
stay in sync — any keyword change in one must be mirrored in the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── Notification action (button / link) ────────────────────────────────────


@dataclass(slots=True)
class NotificationAction:
    """A single action button shown in an error notification card.

    Channels with native controls (Telegram inline keyboard, Discord
    components, Slack Block Kit buttons) map this to their platform
    primitives.  Plain-text channels skip actions entirely.
    """

    label: str
    """User-visible button text (``"Try Again"``, ``"Open AI Setup"``)."""

    type: str = "command"
    """Action kind: ``"command"`` (slash command), ``"url"`` (external link)."""

    value: str = ""
    """The slash command or URL to execute."""

    style: str = "primary"
    """Visual hint: ``"primary"``, ``"secondary"``, ``"danger"``."""


# ── Error notification envelope ─────────────────────────────────────────────


@dataclass(slots=True)
class ErrorNotification:
    """Universal error notification — one envelope, every channel.

    This is the SINGLE structure every error path returns.  Channel
    renderers degrade what they cannot support.
    """

    tone: str
    """Visual tone: ``"info"`` | ``"warning"`` | ``"danger"``."""

    title: str
    """Short heading (``"Rate Limited"``, ``"Auth Failed"``, …)."""

    body: str
    """Human-readable message — the output of ``classify_error()``."""

    raw_detail: str = ""
    """Raw exception text, surfaced as a secondary detail line or
    collapsible ``<details>`` block in web chat."""

    actions: list[NotificationAction] = field(default_factory=list)
    """Ordered action buttons.  First action is the primary CTA."""

    dismissible: bool = True
    """Whether the user can dismiss the notification card."""

    def as_dict(self) -> dict:
        """JSON-serializable representation for API responses."""
        return {
            "tone": self.tone,
            "title": self.title,
            "body": self.body,
            "raw_detail": self.raw_detail,
            "actions": [
                {
                    "label": a.label,
                    "type": a.type,
                    "value": a.value,
                    "style": a.style,
                }
                for a in self.actions
            ],
            "dismissible": self.dismissible,
        }


# ── Plain-text fallback renderer ────────────────────────────────────────────


def render_error_notification_text(notification: ErrorNotification) -> str:
    """Render an ErrorNotification as plain text for channels without rich UI.

    WhatsApp, Signal, WeChat, and iMessage use this path.  Actions are
    rendered as readable prompts (e.g. ``"Try /retry to attempt again."``)
    since these channels cannot show inline buttons.
    """
    lines: list[str] = []

    # Emoji prefix per tone
    _tone_emoji: dict[str, str] = {
        "info": "ℹ️",
        "warning": "⚠️",
        "danger": "❌",
    }
    emoji = _tone_emoji.get(notification.tone, "⚠️")

    lines.append(f"{emoji}  **{notification.title}**")
    lines.append(notification.body)

    if notification.raw_detail:
        lines.append(f"↳ {notification.raw_detail}")

    # Render actions as text prompts
    for action in notification.actions:
        if action.type == "command":
            lines.append(f"\nTry `{action.value}` to {action.label.lower()}.")
        elif action.type == "url":
            lines.append(f"\n{action.label}: {action.value}")

    return "\n".join(lines)


# ── Bucket classification → notification ────────────────────────────────────

# Every error type gets the same Try Again action.
# The body text (the user's constant) already tells them what to do —
# "Add your own API key or top up to continue", "Check your API key", etc.
_TRY_AGAIN = NotificationAction(
    label="Try Again",
    type="command",
    value="/retry",
    style="primary",
)


def classify_error_notification(
    error_text: str | None,
    *,
    raw_error: str = "",
) -> ErrorNotification:
    """Map an error string to a fully-formed ErrorNotification.

    Uses the SAME 5-bucket keyword logic as
    :func:`sage_command_dispatcher.classify_error`.  Keep them in sync.
    """
    from server_modules.sage_command_dispatcher import (
        SAGE_AI_LIMIT_REPLY,
        SAGE_AI_NEEDS_ATTENTION_REPLY,
        SAGE_ERROR_REPLY,
        SAGE_PROVIDER_UNREACHABLE_REPLY,
        SAGE_RATE_LIMITED_REPLY,
    )

    if not error_text:
        return ErrorNotification(
            tone="danger",
            title="Something Went Wrong",
            body=SAGE_ERROR_REPLY,
            raw_detail=str(raw_error or ""),
            actions=[_TRY_AGAIN],
        )

    msg = str(error_text).lower().strip()

    # 1) Credits exhausted
    if any(kw in msg for kw in (
        "reached your ai limit", "ai limit", "cap_reached",
    )):
        return ErrorNotification(
            tone="danger",
            title="Credit Exhausted",
            body=SAGE_AI_LIMIT_REPLY,
            raw_detail=str(raw_error or ""),
            actions=[_TRY_AGAIN],
        )

    # 2) Rate limited
    if any(kw in msg for kw in (
        "provider_rate_limited", "429", "rate limit", "too many requests",
    )):
        return ErrorNotification(
            tone="warning",
            title="Rate Limited",
            body=SAGE_RATE_LIMITED_REPLY,
            raw_detail=str(raw_error or ""),
            actions=[_TRY_AGAIN],
        )

    # 3) Auth / key failed
    if any(kw in msg for kw in (
        "provider_generation_failed", "401", "403", "auth", "api key",
        "invalid key", "unauthorized",
    )):
        return ErrorNotification(
            tone="danger",
            title="Auth Failed",
            body=SAGE_AI_NEEDS_ATTENTION_REPLY,
            raw_detail=str(raw_error or ""),
            actions=[_TRY_AGAIN],
        )

    # 4) Provider unreachable
    if any(kw in msg for kw in (
        "provider_transport_unavailable", "transport", "connection",
        "timeout", "unreachable",
    )):
        return ErrorNotification(
            tone="warning",
            title="Service Unreachable",
            body=SAGE_PROVIDER_UNREACHABLE_REPLY,
            raw_detail=str(raw_error or ""),
            actions=[_TRY_AGAIN],
        )

    # 5) Catch-all
    return ErrorNotification(
        tone="danger",
        title="Something Went Wrong",
        body=SAGE_ERROR_REPLY,
        raw_detail=str(raw_error or ""),
        actions=[_TRY_AGAIN],
    )
