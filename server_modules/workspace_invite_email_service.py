"""The workspace-invite email -- subject, body, accept link, and the send.

Until this module existed the invite path minted a token and sent NOTHING:
create_workspace_invite_route returned the token and the UI told the owner
"No email sender yet -- copy this link and share it yourself." Meanwhile
email_provider_service.py was a complete, working Resend integration with
exactly one caller (email_verification_service.py). This is the second.

Conventions are email_verification_service.py's, deliberately, rather than a
second style: build (subject, html, text) in one pure function so the copy is
readable and testable on its own, then hand it to
email_provider_service.send_email and let that module's failures RAISE. The
caller decides what a failure means -- and for invites it means "report it",
never "fail the invite": the row and its token are already created and
usable, so a dead mailbox must not cost the owner the link.

The accept link is built from the same public-frontend-origin resolution the
rest of the platform uses (cloud_cutover_config.resolve_public_frontend_origin
-- EMPYRALIS_PUBLIC_FRONTEND_ORIGIN / FRONTEND_PUBLIC_ORIGIN / the first entry
of FRONTEND_ORIGINS, with a loopback dev fallback). No domain is hardcoded
here, and /join/{token} is the only frontend route that can redeem a
workspace_invite_v1 token (see frontend/app/join/[token]/page.tsx -- NOT
/invite/[code], which is the unrelated pilot-signup surface).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, Optional
from urllib.parse import quote

from server_modules import email_provider_service
from server_modules.cloud_cutover_config import (
    CloudCutoverConfigError,
    resolve_public_frontend_origin,
)


LOGGER = logging.getLogger(__name__)

# The three honest outcomes of trying to email an invite. Collapsing
# "provider is not configured" into "the send failed" would tell an owner to
# retry something that can never work, and collapsing either into "sent"
# is the failure mode this whole change exists to remove.
DELIVERY_SENT = "sent"
DELIVERY_NOT_CONFIGURED = "not_configured"
DELIVERY_FAILED = "failed"


def build_invite_accept_url(token: str) -> str:
    """The /join/{token} link an invitee clicks.

    Raises CloudCutoverConfigError only in staging/production with no public
    frontend origin configured -- which is a real misconfiguration and should
    surface, not be papered over with a localhost link nobody can click.
    """
    origin = resolve_public_frontend_origin(os.environ).rstrip("/")
    return f"{origin}/join/{quote(str(token or '').strip(), safe='')}"


def build_invite_logo_url() -> str:
    """The Empyralis mark, as a PNG on the same public origin as the link.

    PNG and not the SVG the app itself uses: Gmail strips <img> pointing at
    SVG outright and several other clients do too, so an SVG logo is a mark
    that is simply absent for most recipients. Hosted over HTTPS and not
    inlined as a data: URI for the same reason -- Gmail drops data: image
    sources as well, and a CID attachment would turn a one-call Resend send
    into a multipart build for a decoration.

    Same origin resolution as build_invite_accept_url, so no domain is
    hardcoded and a deployment that cannot resolve one raises there first.
    """
    origin = resolve_public_frontend_origin(os.environ).rstrip("/")
    return f"{origin}/brand-assets/empyralis/empyralis-mark-email-128.png"


def _expiry_line(expires_at_epoch: Optional[int]) -> str:
    try:
        epoch = int(expires_at_epoch or 0)
    except (TypeError, ValueError):
        epoch = 0
    if epoch <= 0:
        return ""
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%d %B %Y")
    return f"This invite expires on {stamp}."


def build_invite_email_content(
    *,
    invitee_email: str,
    workspace_name: str,
    inviter_label: str,
    accept_url: str,
    expires_at_epoch: Optional[int] = None,
    logo_url: str = "",
) -> tuple[str, str, str]:
    """(subject, html, text).

    Labels, does not lecture (CLAUDE.md): who invited them, to what, one
    link, and the two facts they cannot guess -- which address the invite is
    bound to, and when it expires.

    logo_url is OPTIONAL and the email is complete without it. Mail clients
    block remote images by default, so the mark can never be load-bearing:
    every fact and the link itself live in text, the <img> carries alt="Empyralis"
    for the blocked case, and a caller with no resolvable public origin simply
    omits the tag rather than emitting a broken image. The plaintext part is
    unchanged -- a logo has no plaintext form.
    """
    workspace = str(workspace_name or "").strip() or "a workspace"
    inviter = str(inviter_label or "").strip() or "A teammate"
    address = str(invitee_email or "").strip()

    subject = f"{inviter} invited you to {workspace} on Empyralis"
    expiry = _expiry_line(expires_at_epoch)

    html_parts = []
    logo = str(logo_url or "").strip()
    if logo:
        # Inline styles and width/height attributes both: Outlook ignores the
        # style, everything else ignores the attributes, and a logo that
        # renders at its intrinsic 128px in one client and 64px in another is
        # the reason to state it twice.
        html_parts.append(
            f'<p style="margin:0 0 16px;"><img src="{escape(logo, quote=True)}" alt="Empyralis"'
            ' width="64" height="64" style="display:block;width:64px;height:64px;border:0;"></p>'
        )
    html_parts += [
        f"<p>{escape(inviter)} invited you to join <strong>{escape(workspace)}</strong> on Empyralis.</p>",
        f'<p><a href="{escape(accept_url, quote=True)}">Accept the invite</a></p>',
    ]
    if address:
        html_parts.append(
            f"<p>Sign in with {escape(address)} — the invite only works for that address."
            + (f" {escape(expiry)}" if expiry else "")
            + "</p>"
        )
    elif expiry:
        html_parts.append(f"<p>{escape(expiry)}</p>")
    html_parts.append(f'<p style="color:#666;font-size:13px;">{escape(accept_url)}</p>')
    html = "".join(html_parts)

    text_lines = [
        f"{inviter} invited you to join {workspace} on Empyralis.",
        "",
        "Accept the invite:",
        accept_url,
    ]
    if address:
        text_lines += ["", f"Sign in with {address} — the invite only works for that address."]
    if expiry:
        text_lines += ["", expiry]
    text = "\n".join(text_lines)

    return subject, html, text


def inviter_label_from_user(user: Optional[Dict[str, Any]]) -> str:
    """Whatever the inviter is actually called, in the order a person would
    recognise it. Never an id -- an email from "usr_8f21" names nobody."""
    if not isinstance(user, dict):
        return ""
    for key in ("display_name", "name", "full_name", "email"):
        value = str(user.get(key) or "").strip()
        if value:
            return value
    return ""


async def send_workspace_invite_email(
    *,
    invitee_email: str,
    workspace_name: str,
    inviter_label: str,
    token: str,
    expires_at_epoch: Optional[int] = None,
) -> None:
    """Send it. Raises email_provider_service.EmailProviderError subclasses
    (EmailProviderUnavailable when the provider isn't configured,
    EmailSendFailed when it rejects or is unreachable) rather than returning
    a status -- the caller catches and decides, matching
    email_verification_service.start_verification's own posture."""
    accept_url = build_invite_accept_url(token)
    subject, html, text = build_invite_email_content(
        invitee_email=invitee_email,
        workspace_name=workspace_name,
        inviter_label=inviter_label,
        accept_url=accept_url,
        expires_at_epoch=expires_at_epoch,
        logo_url=build_invite_logo_url(),
    )
    await email_provider_service.send_email(
        to=invitee_email,
        subject=subject,
        html=html,
        text=text,
    )


async def deliver_workspace_invite_email(
    *,
    invitee_email: str,
    workspace_name: str,
    inviter_label: str,
    token: str,
    expires_at_epoch: Optional[int] = None,
) -> Dict[str, Any]:
    """The call-site-friendly wrapper: never raises, always reports.

    An invite row and its token are already created by the time this runs.
    A send failure must therefore cost the owner nothing except the email --
    so every failure is converted into one of the three delivery statuses
    above and handed back for the response to carry. The UI reads that and
    tells the truth instead of a hardcoded assumption.
    """
    address = str(invitee_email or "").strip()
    if not email_provider_service.email_provider_configured():
        # The cheap check email_provider_configured() exists for: skip the
        # attempt (and its logged error) when there is provably no provider.
        return {"status": DELIVERY_NOT_CONFIGURED, "email": address}

    try:
        await send_workspace_invite_email(
            invitee_email=address,
            workspace_name=workspace_name,
            inviter_label=inviter_label,
            token=token,
            expires_at_epoch=expires_at_epoch,
        )
    except email_provider_service.EmailProviderUnavailable:
        return {"status": DELIVERY_NOT_CONFIGURED, "email": address}
    except (email_provider_service.EmailProviderError, CloudCutoverConfigError) as exc:
        LOGGER.error("workspace_invite_email_failed: %s: %s", address, exc)
        return {"status": DELIVERY_FAILED, "email": address}
    except Exception as exc:  # noqa: BLE001 -- an invite must survive any mailer defect
        LOGGER.exception("workspace_invite_email_unexpected_error: %s: %s", address, exc)
        return {"status": DELIVERY_FAILED, "email": address}

    return {"status": DELIVERY_SENT, "email": address}
