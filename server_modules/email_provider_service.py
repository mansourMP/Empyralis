"""Transactional email sending via Resend.

Zero email-sending capability existed on this platform before this module --
confirmed by grep across server_modules/ and frontend/ before building this
(workspace invites are link-only for exactly this reason: there was never
anywhere to send a link *to*). This is the first integration point. See
docs/design/email-verification-plan.md for why Resend was chosen over
Postmark/SES.

Anti-silent-failure discipline, matching multimodal_provider_service.py's
handling of OPENAI_API_KEY/ELEVENLABS_API_KEY: a missing or blank
EMAIL_PROVIDER_API_KEY raises EmailProviderUnavailable with a clear message
and a LOGGER.error line -- never a silent no-op. Do not add a fallback that
swallows this and pretends the email sent.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx


LOGGER = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_FROM_ADDRESS = "Empyralis <onboarding@empyralis.ai>"


class EmailProviderError(RuntimeError):
    """Base class for email-provider failures."""


class EmailProviderUnavailable(EmailProviderError):
    """Raised when the provider isn't configured (missing API key)."""


class EmailSendFailed(EmailProviderError):
    """Raised when the provider was reachable but rejected/failed the send."""


def _env_text(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


def _api_key() -> str:
    return _env_text("EMAIL_PROVIDER_API_KEY")


def _from_address() -> str:
    return _env_text("EMAIL_PROVIDER_FROM_ADDRESS", DEFAULT_FROM_ADDRESS)


def email_provider_configured() -> bool:
    """Cheap configuration check callers can use before attempting a send
    (e.g. to short-circuit with a clearer error) without triggering the
    logged failure path that send_email raises on."""
    return bool(_api_key())


async def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a transactional email via Resend's HTTP API.

    Raises EmailProviderUnavailable if EMAIL_PROVIDER_API_KEY is not set --
    this never silently no-ops, per this platform's anti-silent-failure
    discipline. Raises EmailSendFailed if Resend rejects the request or is
    unreachable. Returns Resend's parsed JSON response on success.
    """
    clean_to = str(to or "").strip()
    if not clean_to:
        raise EmailProviderError("send_email requires a non-empty 'to' address.")
    clean_subject = str(subject or "").strip()
    if not clean_subject:
        raise EmailProviderError("send_email requires a non-empty 'subject'.")

    api_key = _api_key()
    if not api_key:
        LOGGER.error(
            "email_provider_unavailable: EMAIL_PROVIDER_API_KEY is not set; "
            "cannot send email to %s (subject=%r). Set EMAIL_PROVIDER_API_KEY "
            "to a Resend API key to enable transactional email sending.",
            clean_to,
            clean_subject,
        )
        raise EmailProviderUnavailable(
            "EMAIL_PROVIDER_API_KEY is not configured. Set it to a Resend API "
            "key to enable transactional email sending."
        )

    payload: Dict[str, Any] = {
        "from": _from_address(),
        "to": [clean_to],
        "subject": clean_subject,
        "html": html,
    }
    if text:
        payload["text"] = text

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            response = await client.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        LOGGER.error(
            "email_provider_request_failed: could not reach Resend for %s: %s",
            clean_to,
            exc,
        )
        raise EmailSendFailed(f"Could not reach the email provider: {exc}") from exc

    if response.status_code >= 400:
        LOGGER.error(
            "email_provider_rejected: Resend returned status %s for %s: %s",
            response.status_code,
            clean_to,
            response.text[:500],
        )
        raise EmailSendFailed(
            f"Email provider rejected the send (status {response.status_code})."
        )

    try:
        return response.json()
    except Exception:
        return {}
