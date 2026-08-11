"""Signup reports whether the verification email actually went out.

The bug this covers: register_user() sends the 6-digit code best-effort (an
email provider must never cost someone their account -- that part is correct
and stays), but it reported the OUTCOME to nobody. The server logged the
failure and the client redirected to /verify-email, which said "We sent a
6-digit code to the address you signed up with" unconditionally. So while
EMAIL_PROVIDER_FROM_ADDRESS pointed at a domain that was never registered
with Resend, every single signup got a 403, no email, and a screen insisting
one was on its way. The person waits forever on a code that does not exist.

Three states, asserted separately, because collapsing them IS the failure
mode -- and using the same three names the invite path already settled on
(sent / failed / not_configured, see test_workspace_invite_email.py):

    provider sends        status == "sent"             account still usable
    provider refuses      status == "failed"           account still usable
    provider unconfigured status == "not_configured"   account still usable

The "account still usable" column is asserted every time on purpose: the
whole reason this path is best-effort is that a dead mailer must never undo
a signup, so a fix that reports the failure by FAILING the signup would be a
worse bug than the one it replaces.

email_provider_service.send_email is mocked throughout -- no test may reach a
real provider (conftest's socket guard would fail it anyway). Call COUNTS are
asserted rather than "a send happened": "an email was sent" is satisfied by
two emails as happily as by one, and the not-configured case only means
something if nothing was attempted at all.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from server_modules import auth
from server_modules import email_provider_service


def _signup() -> dict:
    """A real registration through the real entry point."""
    clean_email = f"signup-{uuid.uuid4().hex[:10]}@example.com"
    payload = auth.register_user(clean_email, "S1gnup-Secret-Pass!")
    assert isinstance(payload, dict)
    payload["_email"] = clean_email
    return payload


def _assert_account_really_exists(payload: dict) -> None:
    """The account must survive whatever the mailer did."""
    user = auth._find_user_by_email(str(payload["_email"]))
    assert isinstance(user, dict) and str(user.get("id") or "").strip(), (
        "the signup did not create a usable account"
    )
    memberships = auth._list_workspace_memberships(str(user["id"]).strip())
    assert memberships, "the signup did not create a bootstrap workspace"


def test_successful_send_reports_sent_and_sends_exactly_one_email():
    with patch.object(email_provider_service, "send_email", new=AsyncMock()) as send_email:
        payload = _signup()

    assert payload["email_verification"] == {"status": "sent"}
    # Exactly one: a double-send is a different bug that "an email was sent"
    # would happily pass.
    assert send_email.await_count == 1
    _assert_account_really_exists(payload)


def test_provider_refusal_reports_failed_and_still_creates_the_account():
    """What actually happened in production: Resend answered 403 because the
    sending domain was not verified. Reached the provider, got refused."""
    refusal = email_provider_service.EmailSendFailed(
        "Email provider rejected the send (status 403)."
    )
    with patch.object(email_provider_service, "send_email", new=AsyncMock(side_effect=refusal)) as send_email:
        payload = _signup()

    assert payload["email_verification"] == {"status": "failed"}
    assert send_email.await_count == 1
    _assert_account_really_exists(payload)


def test_unconfigured_provider_reports_not_configured_distinctly_from_failed():
    """`not_configured` can never succeed on a retry; `failed` often can. A UI
    that cannot tell them apart tells someone to retry the impossible."""
    unavailable = email_provider_service.EmailProviderUnavailable(
        "EMAIL_PROVIDER_API_KEY is not set."
    )
    with patch.object(
        email_provider_service, "send_email", new=AsyncMock(side_effect=unavailable)
    ):
        payload = _signup()

    assert payload["email_verification"] == {"status": "not_configured"}
    _assert_account_really_exists(payload)


def test_the_three_outcomes_are_actually_distinguishable():
    """The point of the whole change: a caller can tell the three apart.

    Asserted as a set rather than one-by-one so that collapsing any two of
    them -- the exact regression this file exists to prevent -- fails here
    even if each individual test above were somehow still satisfied."""
    outcomes = []

    with patch.object(email_provider_service, "send_email", new=AsyncMock()):
        outcomes.append(_signup()["email_verification"]["status"])
    with patch.object(
        email_provider_service,
        "send_email",
        new=AsyncMock(side_effect=email_provider_service.EmailSendFailed("refused")),
    ):
        outcomes.append(_signup()["email_verification"]["status"])
    with patch.object(
        email_provider_service,
        "send_email",
        new=AsyncMock(side_effect=email_provider_service.EmailProviderUnavailable("unset")),
    ):
        outcomes.append(_signup()["email_verification"]["status"])

    assert outcomes == ["sent", "failed", "not_configured"]
    assert len(set(outcomes)) == 3
