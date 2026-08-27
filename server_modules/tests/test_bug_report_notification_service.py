"""bug_report_notification_service -- the email that closes the loop on a
submitted bug report.

Before this module, POST /api/w/{workspace_id}/fleet/bug-reports persisted a
row into `bug_reports` and told nobody. Its own GET route is scoped per
WORKSPACE and gated owner-of-that-workspace, so a report filed in a
customer's own workspace was reachable by no one at Empyralis without a
direct database query -- the founder's own words, preparing to launch:
"we have to make sure that it's landing in the correct page -- possibly
just my email". The repo is private, so a GitHub-issue link would be a dead
end for anyone who isn't a collaborator; email is the door that is open.

Same three states workspace_invite_email_service already established, and
collapsing any two of them is the whole failure mode this file exists to
catch:

    configured + sends      status == "sent"
    configured + fails      status == "failed"
    not configured          status == "not_configured", send_email untouched

email_provider_service.send_email is MOCKED throughout -- no test may reach
a real provider (conftest's socket guard would fail it anyway).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from server_modules import bug_report_notification_service, email_provider_service


def _report(**overrides) -> dict:
    base = {
        "id": "bugreport_abc123",
        "title": "Placement step shows two computers both named 'This Device'",
        "description": "Both entries render the identical label.",
        "page_path": "/w/ws-1/agents?new=1",
        "workspace_id": "ws-1",
    }
    base.update(overrides)
    return base


@pytest.mark.anyio
async def test_configured_provider_sends_exactly_once() -> None:
    send_email = AsyncMock(return_value={"id": "resend-1"})
    with (
        patch.dict(os.environ, {"EMAIL_PROVIDER_API_KEY": "re_test_key"}, clear=False),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        result = await bug_report_notification_service.deliver_bug_report_notification(
            report=_report(),
            workspace_name="Mobile App",
            reporter_label="Mansur",
        )

    assert result["status"] == bug_report_notification_service.DELIVERY_SENT
    assert send_email.await_count == 1
    kwargs = send_email.await_args.kwargs
    assert kwargs["to"] == bug_report_notification_service.DEFAULT_NOTIFY_EMAIL
    assert "This Device" in kwargs["subject"]
    assert "Mansur" in kwargs["html"] and "Mobile App" in kwargs["html"]
    assert "Mansur" in kwargs["text"] and "Mobile App" in kwargs["text"]


@pytest.mark.anyio
async def test_a_failed_send_is_reported_and_never_raises() -> None:
    send_email = AsyncMock(side_effect=email_provider_service.EmailSendFailed("provider rejected"))
    with (
        patch.dict(os.environ, {"EMAIL_PROVIDER_API_KEY": "re_test_key"}, clear=False),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        result = await bug_report_notification_service.deliver_bug_report_notification(report=_report())

    assert result["status"] == bug_report_notification_service.DELIVERY_FAILED
    assert send_email.await_count == 1


@pytest.mark.anyio
async def test_unconfigured_provider_attempts_nothing() -> None:
    send_email = AsyncMock(return_value={"id": "should-not-happen"})
    env_without_key = {k: v for k, v in os.environ.items() if k != "EMAIL_PROVIDER_API_KEY"}
    with (
        patch.dict(os.environ, env_without_key, clear=True),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        result = await bug_report_notification_service.deliver_bug_report_notification(report=_report())

    assert result["status"] == bug_report_notification_service.DELIVERY_NOT_CONFIGURED
    assert send_email.await_count == 0, "email_provider_configured() exists to avoid this attempt entirely"


@pytest.mark.anyio
async def test_an_unexpected_mailer_defect_still_returns_a_status() -> None:
    send_email = AsyncMock(side_effect=RuntimeError("mailer exploded"))
    with (
        patch.dict(os.environ, {"EMAIL_PROVIDER_API_KEY": "re_test_key"}, clear=False),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        result = await bug_report_notification_service.deliver_bug_report_notification(report=_report())

    assert result["status"] == bug_report_notification_service.DELIVERY_FAILED


@pytest.mark.anyio
async def test_notify_destination_is_overridable_per_deployment() -> None:
    """The founder's own address is the built-in default, not the only
    possible one -- a future self-hosted operator must not be silently
    emailing this platform's own founder."""
    send_email = AsyncMock(return_value={"id": "resend-1"})
    with (
        patch.dict(
            os.environ,
            {"EMAIL_PROVIDER_API_KEY": "re_test_key", "EMPYRALIS_BUG_REPORT_NOTIFY_EMAIL": "ops@example.com"},
            clear=False,
        ),
        patch.object(email_provider_service, "send_email", send_email),
    ):
        result = await bug_report_notification_service.deliver_bug_report_notification(report=_report())

    assert result["to"] == "ops@example.com"
    assert send_email.await_args.kwargs["to"] == "ops@example.com"


def test_default_notify_email_is_the_founders_own_address() -> None:
    assert bug_report_notification_service.DEFAULT_NOTIFY_EMAIL == "mansurao886@gmail.com"


class TestContent:
    def test_subject_names_the_title(self) -> None:
        subject, _html, _text = bug_report_notification_service.build_bug_report_notification_content(
            report=_report(title="Login button does nothing"),
            workspace_name="Acme",
            reporter_label="Jane",
        )
        assert "Login button does nothing" in subject

    def test_html_carries_description_page_path_and_report_id(self) -> None:
        _subject, html, text = bug_report_notification_service.build_bug_report_notification_content(
            report=_report(),
            workspace_name="Mobile App",
            reporter_label="Mansur",
        )
        for fact in ("Both entries render the identical label.", "/w/ws-1/agents?new=1", "bugreport_abc123", "Mobile App", "Mansur"):
            assert fact in html
            assert fact in text

    def test_a_blank_workspace_name_and_reporter_still_render_readable_prose(self) -> None:
        """No name to show falls back to a real word ("a workspace" /
        "someone"), the same "never an empty label" discipline
        workspace_invite_email_service already uses -- never blank prose."""
        subject, html, text = bug_report_notification_service.build_bug_report_notification_content(
            report=_report(workspace_id=""),
            workspace_name="",
            reporter_label="",
        )
        assert subject
        assert "a workspace" in html and "a workspace" in text
        assert "someone" in html and "someone" in text

    def test_html_escapes_reporter_supplied_text(self) -> None:
        """A report title/description a person typed must never inject
        markup into an email a human is going to open and read -- the raw
        `<script>`/`<img>` tags must never survive as real tags, only as
        inert escaped text."""
        _subject, html, _text = bug_report_notification_service.build_bug_report_notification_content(
            report=_report(title="<img src=x onerror=alert(1)>", description="<script>evil()</script>"),
            workspace_name="Acme",
            reporter_label="Jane",
        )
        assert "<script>" not in html
        assert "<img " not in html
        assert "&lt;script&gt;" in html and "&lt;img" in html
