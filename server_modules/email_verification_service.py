"""Signup email verification -- code generation, storage, and enforcement.

See docs/design/email-verification-plan.md for the full design writeup.
Short version: `pilot_invite_service.py` is the established convention this
mirrors -- generate a short-lived secret code, store it via
control_plane_repository.py with an expiry, expose create/verify/resend
helpers. This module does the same for a 6-digit numeric email verification
code, tied to a user_id rather than being a standalone shareable invite.

Key differences from the pilot-invite pattern, deliberately:
- The code is stored hashed (SHA-256, peppered with the JWT secret and bound
  to the user_id), never in plaintext. A 6-digit space is small enough that a
  raw DB read should not be a free bypass the way a 12-char base62 pilot
  invite code effectively already is when stored in the clear.
- Verification failures increment a per-code attempt counter and lock the
  code out after EMAIL_VERIFICATION_MAX_ATTEMPTS wrong guesses, independent
  of the HTTP-layer rate limit applied in routes_auth.py.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from typing import Optional

from fastapi import HTTPException

from server_modules import control_plane_repository
from server_modules import email_provider_service
from server_modules.jwt_secret import resolve_jwt_secret


LOGGER = logging.getLogger(__name__)

CODE_LENGTH = 6
DEFAULT_TTL_MINUTES = 20
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_MIN_RESEND_INTERVAL_SECONDS = 60


def _code_ttl_seconds() -> int:
    raw = os.environ.get("EMAIL_VERIFICATION_CODE_TTL_MINUTES")
    try:
        minutes = int(raw) if raw else DEFAULT_TTL_MINUTES
    except (TypeError, ValueError):
        minutes = DEFAULT_TTL_MINUTES
    return max(1, minutes) * 60


def _max_attempts() -> int:
    raw = os.environ.get("EMAIL_VERIFICATION_MAX_ATTEMPTS")
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_ATTEMPTS
    except (TypeError, ValueError):
        return DEFAULT_MAX_ATTEMPTS


def _min_resend_interval_seconds() -> int:
    raw = os.environ.get("EMAIL_VERIFICATION_MIN_RESEND_INTERVAL_SECONDS")
    try:
        return max(0, int(raw)) if raw else DEFAULT_MIN_RESEND_INTERVAL_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_MIN_RESEND_INTERVAL_SECONDS


def generate_code() -> str:
    """Cryptographically random 6-digit numeric code, zero-padded.

    Uses secrets.randbelow (CSPRNG), not random -- a predictable code would
    make this whole feature pointless.
    """
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def _hash_code(code: str, *, user_id: str) -> str:
    # Peppered with the JWT secret (the platform's existing secret-material
    # source -- see jwt_secret.py -- rather than inventing a new secret
    # store) and bound to user_id so the same 6-digit code for two different
    # users never hashes identically.
    pepper = resolve_jwt_secret()
    return hashlib.sha256(f"{pepper}:{user_id}:{code}".encode("utf-8")).hexdigest()


def _verify_code_hash(code: str, *, user_id: str, code_hash: str) -> bool:
    candidate = _hash_code(code, user_id=user_id)
    return secrets.compare_digest(candidate, str(code_hash or ""))


def _verification_email_content(code: str) -> tuple[str, str, str]:
    minutes = _code_ttl_seconds() // 60
    subject = "Your Empyralis verification code"
    html = (
        "<p>Your Empyralis verification code is:</p>"
        f'<p style="font-size:28px;font-weight:700;letter-spacing:4px;">{code}</p>'
        f"<p>This code expires in {minutes} minutes. If you didn't request this, "
        "you can ignore this email.</p>"
    )
    text = f"Your Empyralis verification code is {code}. It expires in {minutes} minutes."
    return subject, html, text


async def start_verification(*, user_id: str, email: str) -> None:
    """Create a fresh code and email it.

    Raises (EmailProviderUnavailable / EmailSendFailed) rather than silently
    no-op'ing when the send fails -- callers decide what that should mean for
    them. register_user()'s signup path catches and logs rather than failing
    the whole signup, since account creation must not block on email
    provider latency/config (see the plan doc); the resend endpoint instead
    lets the failure surface to the caller as a clear HTTP error.
    """
    clean_user_id = str(user_id or "").strip()
    clean_email = str(email or "").strip().lower()
    if not clean_user_id or not clean_email:
        raise HTTPException(status_code=400, detail="user_id and email are required to start verification.")

    code = generate_code()
    code_hash = _hash_code(code, user_id=clean_user_id)
    expires_at_epoch = int(time.time()) + _code_ttl_seconds()
    record = await control_plane_repository.create_email_verification_code(
        user_id=clean_user_id,
        email=clean_email,
        code_hash=code_hash,
        expires_at_epoch=expires_at_epoch,
        max_attempts=_max_attempts(),
    )
    if record is None:
        raise HTTPException(status_code=500, detail="Could not create an email verification code.")

    subject, html, text = _verification_email_content(code)
    await email_provider_service.send_email(to=clean_email, subject=subject, html=html, text=text)


async def resend_verification(*, user_id: str, email: str) -> None:
    """Issue a new code, subject to a minimum interval since the last one."""
    clean_user_id = str(user_id or "").strip()
    latest = await control_plane_repository.get_latest_email_verification_code(clean_user_id)
    if isinstance(latest, dict) and latest.get("status") == "pending":
        created_at = int(latest.get("created_at") or 0)
        min_interval = _min_resend_interval_seconds()
        elapsed = int(time.time()) - created_at
        if elapsed < min_interval:
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {min_interval - elapsed} more seconds before requesting another code.",
            )
    await start_verification(user_id=clean_user_id, email=email)


async def verification_status(user_id: str) -> str:
    """'verified' | 'pending' | 'none'.

    'none' means no verification code was ever issued for this user --
    treated as verified for backward compatibility (accounts created before
    this feature shipped, or created through a path that doesn't call
    start_verification, must not be retroactively locked out).
    """
    latest = await control_plane_repository.get_latest_email_verification_code(str(user_id or "").strip())
    if not isinstance(latest, dict):
        return "none"
    return "verified" if latest.get("status") == "verified" else "pending"


async def is_verified(user_id: str) -> bool:
    status = await verification_status(user_id)
    return status in {"verified", "none"}


async def verify_code(*, user_id: str, code: str) -> None:
    clean_user_id = str(user_id or "").strip()
    clean_code = str(code or "").strip()
    if not clean_code:
        raise HTTPException(status_code=400, detail="Verification code is required.")

    latest = await control_plane_repository.get_latest_email_verification_code(clean_user_id)
    if not isinstance(latest, dict):
        raise HTTPException(
            status_code=404,
            detail="No verification code was found for this account. Request a new one.",
        )
    if latest.get("status") == "verified":
        return
    if latest.get("status") != "pending":
        raise HTTPException(status_code=410, detail="This verification code is no longer valid. Request a new one.")

    expires_at = latest.get("expires_at")
    if expires_at is not None and int(expires_at) < int(time.time()):
        raise HTTPException(status_code=410, detail="This verification code has expired. Request a new one.")

    attempts = int(latest.get("attempts") or 0)
    max_attempts = int(latest.get("max_attempts") or _max_attempts())
    if attempts >= max_attempts:
        raise HTTPException(status_code=429, detail="Too many incorrect attempts. Request a new code.")

    record_id = str(latest.get("id") or "")
    if not _verify_code_hash(clean_code, user_id=clean_user_id, code_hash=str(latest.get("code_hash") or "")):
        await control_plane_repository.record_email_verification_attempt(record_id)
        raise HTTPException(status_code=400, detail="That code is incorrect.")

    await control_plane_repository.mark_email_verification_code_verified(record_id)
