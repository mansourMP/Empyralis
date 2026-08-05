"""MAN-293: POST /auth/verify-email/resend must never put an internal
config detail (env var name, vendor name) in the HTTP response an end user
reads.

Reproduced on production: a new user on /verify-email clicks "Resend", the
email provider isn't configured, and the 503 body was:

    {"detail":"EMAIL_PROVIDER_API_KEY is not configured. Set it to a Resend
    API key to enable transactional email sending.", ...}

routes_auth.auth_resend_verify_email used to relay `str(exc)` straight from
email_provider_service's EmailProviderUnavailable/EmailSendFailed into the
HTTPException `detail` it raises -- and that exception's message is
deliberately detailed (see email_provider_service.py's own docstring: "a
missing or blank EMAIL_PROVIDER_API_KEY raises EmailProviderUnavailable with
a clear message"), because it's meant for the server log, not the browser.
The fix keeps that detailed message -- test_email_provider_service.py still
asserts on it at the service layer -- but stops the route from forwarding it
verbatim to the client; the client gets a plain, honest, vendor-and-env-var-
free sentence, and the real detail goes to LOGGER.error instead.

This is a NEW file (server_modules/tests/conftest.py and most existing test
files are owned by other concurrently-running agents per the collision
protocol in docs/AGENT-OPERATING-RULES.md).
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from server_modules import email_provider_service
from server_modules import routes_auth


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_auth.router)
    app.dependency_overrides[routes_auth.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
    }
    return app


@pytest.fixture(autouse=True)
def _fake_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sidesteps the real SQLite-backed user lookup
    # (auth.get_authenticated_user_record) -- this suite only cares about
    # what routes_auth.auth_resend_verify_email does with the exception
    # email_verification_service.resend_verification raises, not about
    # authentication plumbing.
    monkeypatch.setattr(
        routes_auth,
        "get_authenticated_user_record",
        lambda current_user: {"id": "user-1", "email": "new-user@example.com"},
    )


async def _post_resend() -> httpx.Response:
    app = _build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # No auth cookies on the request -> validate_csrf's "no live
        # cookie session" branch returns True with no CSRF header required
        # (see auth.validate_csrf), matching how a bearer-authenticated
        # request actually looks.
        return await client.post("/auth/verify-email/resend")


@pytest.mark.anyio
async def test_provider_unavailable_503_does_not_leak_env_var_or_vendor_name(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_detail = (
        "EMAIL_PROVIDER_API_KEY is not configured. Set it to a Resend API "
        "key to enable transactional email sending."
    )

    async def _raise_unavailable(*, user_id: str, email: str) -> None:
        raise email_provider_service.EmailProviderUnavailable(real_detail)

    monkeypatch.setattr(
        routes_auth.email_verification_service, "resend_verification", _raise_unavailable
    )

    with caplog.at_level(logging.ERROR, logger="server_modules.routes_auth"):
        response = await _post_resend()

    assert response.status_code == 503
    body = response.json()
    detail = str(body.get("detail") or "")

    # The actual bug: the raw internal message reaching the client.
    assert "EMAIL_PROVIDER_API_KEY" not in detail
    assert "Resend" not in detail
    assert "EMAIL_PROVIDER_API_KEY" not in response.text
    assert "Resend" not in response.text
    # Still an honest, non-empty message -- not just blanked out.
    assert detail.strip()
    assert detail == routes_auth.EMAIL_SEND_UNAVAILABLE_MESSAGE

    # The operator-facing detail must not simply vanish -- it belongs in the
    # server log instead of the response body.
    assert "EMAIL_PROVIDER_API_KEY" in caplog.text
    assert "user-1" in caplog.text


@pytest.mark.anyio
async def test_send_failed_502_does_not_leak_provider_error_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_detail = "Could not reach the email provider: [Errno 61] Connection refused to api.resend.com"

    async def _raise_send_failed(*, user_id: str, email: str) -> None:
        raise email_provider_service.EmailSendFailed(real_detail)

    monkeypatch.setattr(
        routes_auth.email_verification_service, "resend_verification", _raise_send_failed
    )

    with caplog.at_level(logging.ERROR, logger="server_modules.routes_auth"):
        response = await _post_resend()

    assert response.status_code == 502
    body = response.json()
    detail = str(body.get("detail") or "")

    assert "api.resend.com" not in detail
    assert "api.resend.com" not in response.text
    assert detail.strip()
    assert detail == routes_auth.EMAIL_SEND_UNAVAILABLE_MESSAGE

    assert "api.resend.com" in caplog.text
