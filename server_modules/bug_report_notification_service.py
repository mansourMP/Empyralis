"""Emails the operator when a bug report lands (MAN-106 follow-up).

bug_report_service.create_report already persists every submission, durably,
into `bug_reports` -- but that table has no dedicated ops UI and its own read
route (GET /api/w/{workspace_id}/fleet/bug-reports) is scoped per-WORKSPACE
and gated owner-of-that-workspace, so a report filed in a customer's own
workspace was reachable by no one at Empyralis without a direct database
query. The founder, preparing to launch: *"we have to make sure that it's
landing in the correct page -- possibly just my email, or maybe something
that is in GitHub."* The repo is private, so a GitHub-issue link is a dead
end for anyone who isn't a collaborator (404/login-wall) -- see CLAUDE.md's
own confirmation (`gh api repos/mansourMP/Empyralis` -> "visibility":
"private"). Email is the one door that is actually open.

Conventions are workspace_invite_email_service.py's, deliberately: build
(subject, html, text) in one pure function, hand it to
email_provider_service.send_email, and never let a mailer defect cost the
thing that already happened -- the report row is committed before this ever
runs, so every failure here is swallowed into a status and logged, never
raised back into the request. "The report was saved" and "the operator was
emailed about it" are two different facts and this module only ever
speaks to the second one.
"""

from __future__ import annotations

import logging
import os
from html import escape
from typing import Any, Dict

from server_modules import email_provider_service

LOGGER = logging.getLogger(__name__)

# Same three honest outcomes workspace_invite_email_service already uses --
# "not configured" and "failed" are different facts (one is fixable by
# setting an env var, the other by checking the mailer), and both are
# different from "sent". Never collapsed into one.
DELIVERY_SENT = "sent"
DELIVERY_NOT_CONFIGURED = "not_configured"
DELIVERY_FAILED = "failed"

DEFAULT_NOTIFY_EMAIL = "mansurao886@gmail.com"


def _notify_email() -> str:
    """Where a bug-report notification goes. Overridable per-deployment
    (EMPYRALIS_BUG_REPORT_NOTIFY_EMAIL) so a future self-hosted operator
    is not silently emailing this platform's own founder; defaults to the
    one address that exists today."""
    return str(os.environ.get("EMPYRALIS_BUG_REPORT_NOTIFY_EMAIL") or "").strip() or DEFAULT_NOTIFY_EMAIL


def build_bug_report_notification_content(
    *,
    report: Dict[str, Any],
    workspace_name: str,
    reporter_label: str,
) -> tuple[str, str, str]:
    """(subject, html, text). Labels, does not lecture: what was reported,
    where, and by whom -- the three facts a reporter should never have to
    type themselves because the platform already knows them."""
    title = str(report.get("title") or "").strip() or "(no title)"
    description = str(report.get("description") or "").strip()
    page_path = str(report.get("page_path") or "").strip()
    report_id = str(report.get("id") or "").strip()
    workspace = str(workspace_name or "").strip() or str(report.get("workspace_id") or "").strip() or "a workspace"
    reporter = str(reporter_label or "").strip() or "someone"

    subject = f"Bug report: {title}"

    html_parts = [
        f"<p><strong>{escape(title)}</strong></p>",
        f"<p>Reported by {escape(reporter)} in <strong>{escape(workspace)}</strong>"
        + (f" on <code>{escape(page_path)}</code>" if page_path else "")
        + ".</p>",
    ]
    if description:
        # <pre> so newlines the reporter typed survive; escaped, so nothing
        # they typed can inject markup into an email a human will read.
        html_parts.append(f"<pre style=\"white-space:pre-wrap;font-family:inherit;\">{escape(description)}</pre>")
    if report_id:
        html_parts.append(f'<p style="color:#666;font-size:13px;">{escape(report_id)}</p>')
    html = "".join(html_parts)

    text_lines = [title, "", f"Reported by {reporter} in {workspace}" + (f" on {page_path}" if page_path else "") + "."]
    if description:
        text_lines += ["", description]
    if report_id:
        text_lines += ["", report_id]
    text = "\n".join(text_lines)

    return subject, html, text


async def deliver_bug_report_notification(
    *,
    report: Dict[str, Any],
    workspace_name: str = "",
    reporter_label: str = "",
) -> Dict[str, Any]:
    """The call-site-friendly wrapper: never raises, always reports.

    The report row already exists by the time this runs (create_report
    commits before this is ever called), so a send failure here must cost
    nothing but the notification itself."""
    to = _notify_email()
    if not email_provider_service.email_provider_configured():
        return {"status": DELIVERY_NOT_CONFIGURED, "to": to}

    try:
        subject, html, text = build_bug_report_notification_content(
            report=report,
            workspace_name=workspace_name,
            reporter_label=reporter_label,
        )
        await email_provider_service.send_email(to=to, subject=subject, html=html, text=text)
    except email_provider_service.EmailProviderUnavailable:
        return {"status": DELIVERY_NOT_CONFIGURED, "to": to}
    except email_provider_service.EmailProviderError as exc:
        LOGGER.error("bug_report_notification_failed: %s: %s", to, exc)
        return {"status": DELIVERY_FAILED, "to": to}
    except Exception as exc:  # noqa: BLE001 -- a bug report must survive any mailer defect
        LOGGER.exception("bug_report_notification_unexpected_error: %s: %s", to, exc)
        return {"status": DELIVERY_FAILED, "to": to}

    return {"status": DELIVERY_SENT, "to": to}
