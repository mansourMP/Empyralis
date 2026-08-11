"""Invites are actually sent now -- and a dead mailer never costs the link.

The bug this covers: create_workspace_invite_route created the row, returned
a token and sent NOTHING, while the UI said "No email sender yet". The
founder invited real people, including himself on a second account, and
nobody received anything -- for a product whose pitch is agents working
alongside a team, multiplayer never started.

Three states, asserted separately because collapsing them is the whole
failure mode:

    configured + sends      email_delivery.status == "sent",   token usable
    configured + fails      email_delivery.status == "failed", token usable
    not configured          status == "not_configured",        token usable
                            and send_email is never called at all

email_provider_service.send_email is MOCKED throughout -- no test may reach a
real provider (conftest's socket guard would fail it anyway, and a test that
mails a real address is worse than a failing one). Call COUNTS are asserted,
not just "a send happened": "an email was sent" is satisfied by two emails
just as happily as by one, and the not-configured case is only meaningful if
nothing was attempted.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from server_modules import auth
from server_modules import control_plane_repository
from server_modules import email_provider_service
from server_modules import routes_workspaces
from server_modules import workspace_invite_email_service


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_workspaces.router)
    return app


def _register_owner() -> dict:
    """Real registration -- auth.register_user's own bootstrap workspace is
    the target workspace, exactly as it would be for a real first user. Same
    shape test_routes_workspaces_invites.py uses."""
    clean_email = f"owner-{uuid.uuid4().hex[:10]}@example.com"
    auth.register_user(clean_email, "Own3r-Secret-Pass!")
    user = auth._find_user_by_email(clean_email)
    assert isinstance(user, dict) and user.get("id")
    user_id = str(user["id"]).strip()

    memberships = auth._list_workspace_memberships(user_id)
    assert memberships, "registration did not create a bootstrap workspace membership"
    workspace_id = str(memberships[0]["workspace_id"]).strip()

    workspace_access = auth._effective_workspace_access(
        user_id=user_id,
        email=clean_email,
        role="owner",
        auth_type="bearer",
        is_admin=False,
        workspace_ids=[workspace_id],
    )
    return {
        "user_id": user_id,
        "email": clean_email,
        "workspace_id": workspace_id,
        "current_user": {
            "user_id": user_id,
            "auth_type": "bearer",
            "email": clean_email,
            "workspace_ids": list(workspace_access.keys()),
            "workspace_access": workspace_access,
            "role": "owner",
            "is_admin": False,
            "auth_admin": False,
        },
    }


async def _create_invite(app: FastAPI, caller: dict, workspace_id: str, *, email: str) -> httpx.Response:
    app.dependency_overrides[routes_workspaces.get_current_user] = lambda: caller
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            f"/workspaces/{workspace_id}/invites",
            json={"email": email, "role": "member"},
        )


def _assert_token_is_usable(payload: dict, *, expected_email: str) -> None:
    """The point of "best effort": whatever the mailer did, the owner still
    has a link that works. Verified against the real token verifier, not by
    eyeballing a non-empty string."""
    token = payload["token"]
    assert isinstance(token, str) and token.count(".") == 2
    claims = control_plane_repository.verify_workspace_invite_token(token)
    assert str(claims.get("email") or "").lower() == expected_email


@pytest.mark.anyio
async def test_configured_provider_sends_the_invite_email_exactly_once():
    app = _build_app()
    owner = _register_owner()
    invitee = "invitee-sent@example.com"

    send_email = AsyncMock(return_value={"id": "resend-1"})
    with (
        patch.dict(os.environ, {"EMAIL_PROVIDER_API_KEY": "re_test_key"}, clear=False),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee)

    assert response.status_code == 200
    payload = response.json()
    assert payload["email_delivery"] == {"status": "sent", "email": invitee}
    _assert_token_is_usable(payload, expected_email=invitee)

    # == 1, never ">= 1": a double send is a real defect this assertion is
    # the only thing standing in front of.
    assert send_email.await_count == 1
    kwargs = send_email.await_args.kwargs
    assert kwargs["to"] == invitee
    assert payload["token"] in kwargs["html"]
    assert payload["token"] in kwargs["text"]


@pytest.mark.anyio
async def test_a_failed_send_still_returns_a_usable_invite():
    app = _build_app()
    owner = _register_owner()
    invitee = "invitee-failed@example.com"

    send_email = AsyncMock(side_effect=email_provider_service.EmailSendFailed("provider rejected"))
    with (
        patch.dict(os.environ, {"EMAIL_PROVIDER_API_KEY": "re_test_key"}, clear=False),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee)

    assert response.status_code == 200, "a mailer failure must never fail invite creation"
    payload = response.json()
    assert payload["email_delivery"]["status"] == "failed"
    _assert_token_is_usable(payload, expected_email=invitee)
    assert send_email.await_count == 1

    # The row exists and is pending -- the invite is real, only the email
    # is not.
    pending = await control_plane_repository.list_pending_workspace_invites(owner["workspace_id"])
    assert invitee in [str(item.get("email") or "") for item in pending]


@pytest.mark.anyio
async def test_unconfigured_provider_reports_not_configured_and_attempts_nothing():
    app = _build_app()
    owner = _register_owner()
    invitee = "invitee-unconfigured@example.com"

    send_email = AsyncMock(return_value={"id": "should-not-happen"})
    env_without_key = {k: v for k, v in os.environ.items() if k != "EMAIL_PROVIDER_API_KEY"}
    with (
        patch.dict(os.environ, env_without_key, clear=True),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee)

    assert response.status_code == 200
    payload = response.json()
    assert payload["email_delivery"]["status"] == "not_configured"
    _assert_token_is_usable(payload, expected_email=invitee)
    assert send_email.await_count == 0, "email_provider_configured() exists to avoid this attempt entirely"


@pytest.mark.anyio
async def test_an_unexpected_mailer_defect_never_fails_the_invite():
    """Not defensive padding: the invite row is already written by the time
    the send runs, so ANY exception out of the mailer would otherwise
    convert a created invite into a 500 the owner reads as "it didn't
    work"."""
    app = _build_app()
    owner = _register_owner()
    invitee = "invitee-crash@example.com"

    send_email = AsyncMock(side_effect=RuntimeError("mailer exploded"))
    with (
        patch.dict(os.environ, {"EMAIL_PROVIDER_API_KEY": "re_test_key"}, clear=False),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee)

    assert response.status_code == 200
    assert response.json()["email_delivery"]["status"] == "failed"


@pytest.mark.anyio
async def test_a_workspace_lookup_failure_never_fails_the_invite():
    """The workspace NAME is part of the email, not part of the invite. It is
    read after the row is written, so a control-plane read that raises there
    would otherwise turn a created invite into a 500 -- the row would exist
    and the owner would be told it did not."""
    app = _build_app()
    owner = _register_owner()
    invitee = "invitee-nolookup@example.com"

    send_email = AsyncMock(return_value={"id": "resend-2"})
    with (
        patch.dict(os.environ, {"EMAIL_PROVIDER_API_KEY": "re_test_key"}, clear=False),
        patch.object(email_provider_service, "send_email", send_email),
        patch.object(
            control_plane_repository,
            "get_workspace_by_id",
            AsyncMock(side_effect=RuntimeError("control plane unreachable")),
        ),
    ):
        response = await _create_invite(app, owner["current_user"], owner["workspace_id"], email=invitee)

    assert response.status_code == 200
    payload = response.json()
    assert payload["email_delivery"]["status"] == "sent"
    _assert_token_is_usable(payload, expected_email=invitee)
    # Still sent, and still names something a person can read.
    assert send_email.await_count == 1
    assert "a workspace" in send_email.await_args.kwargs["subject"]


class TestInviteEmailCopy:
    """The email itself. Content, not delivery."""

    def test_accept_url_uses_the_configured_public_origin(self):
        with patch.dict(
            os.environ,
            {"EMPYRALIS_PUBLIC_FRONTEND_ORIGIN": "https://app.example.com"},
            clear=False,
        ):
            url = workspace_invite_email_service.build_invite_accept_url("abc.def.ghi")
        assert url == "https://app.example.com/join/abc.def.ghi"

    def test_accept_url_is_never_a_hardcoded_domain(self):
        """Two different configured origins must produce two different
        links -- the only assertion that catches a literal baked into the
        builder."""
        with patch.dict(os.environ, {"EMPYRALIS_PUBLIC_FRONTEND_ORIGIN": "https://one.example"}, clear=False):
            first = workspace_invite_email_service.build_invite_accept_url("t")
        with patch.dict(os.environ, {"EMPYRALIS_PUBLIC_FRONTEND_ORIGIN": "https://two.example"}, clear=False):
            second = workspace_invite_email_service.build_invite_accept_url("t")
        assert first != second
        assert first.startswith("https://one.example/")
        assert second.startswith("https://two.example/")

    def test_copy_names_the_inviter_the_workspace_and_the_link(self):
        subject, html, text = workspace_invite_email_service.build_invite_email_content(
            invitee_email="teammate@example.com",
            workspace_name="Acme Ops",
            inviter_label="Mansur",
            accept_url="https://app.example.com/join/tok",
            expires_at_epoch=1_755_000_000,
        )
        assert subject == "Mansur invited you to Acme Ops on Empyralis"
        for body in (html, text):
            assert "Mansur" in body
            assert "Acme Ops" in body
            assert "https://app.example.com/join/tok" in body
            assert "teammate@example.com" in body
        assert "expires on" in text

    def test_inviter_label_prefers_a_name_over_an_id(self):
        assert (
            workspace_invite_email_service.inviter_label_from_user(
                {"id": "usr_8f21", "display_name": "Mansur", "email": "m@example.com"}
            )
            == "Mansur"
        )
        assert (
            workspace_invite_email_service.inviter_label_from_user({"id": "usr_8f21", "email": "m@example.com"})
            == "m@example.com"
        )
        assert workspace_invite_email_service.inviter_label_from_user({"id": "usr_8f21"}) == ""
