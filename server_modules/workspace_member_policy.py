"""How many people one workspace may hold, and the sentence said when it is
full.

Sibling of ``upload_content_policy``, and deliberately shaped like it: the
NUMBER lives in ``billing_credit_config`` (one place for every plan dial),
the DECISION and the customer-facing sentence live here, and the routes
call in rather than each growing their own copy of "is there room".

WHY A SEAT CAP AND NOT A PROJECT/DOCUMENT/TASK CAP: a seat has a real
marginal cost -- another person's turns, another person's storage, another
mailbox to deliver to. A project row does not. CLAUDE.md's positioning is
explicit and has been restated by the founder four times: context is the
product and is never the paywall. Capping rows would be charging for the
thing being sold; capping seats is charging for the thing being consumed.

THE COUNT INCLUDES THE OWNER. A "10 members" limit that actually admits
eleven people is a number nobody can reconcile against the roster on screen.

ENFORCED ON THE NARROW WAIST, NOT PER BRANCH. There are several ways to
accept an invite (the /join/{token} link, the pending-invite list's own
join route) and they all funnel through
``routes_workspaces._finalize_workspace_invite_acceptance``. The refusal
goes there -- the same "put the guard on the seam every branch must cross"
rule this codebase already applies to ``_guard_sage_visible_reply``.
Creating an invite ALSO checks, but that check is a courtesy rather than
the guard: it tells an owner before an email goes out, and it can go stale
between the invite and its acceptance, which is exactly why it cannot be
the only one.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from server_modules import billing_credit_config


class WorkspaceMemberLimitReached(ValueError):
    """This workspace is full. Carries a sentence that names the limit and
    the way out -- a refusal that says only "denied" sends the person back
    to guess, which is the same product-law violation as an upload
    rejection with no accepted-types line."""


def member_limit() -> int:
    """One read-through, so no call site holds its own copy of the number."""
    return billing_credit_config.workspace_member_limit()


def active_member_count(members: Optional[Iterable[Dict[str, Any]]]) -> int:
    """Count of ACTIVE memberships in a roster payload.

    ``control_plane_repository.list_workspace_members`` already filters to
    active rows, but this re-checks rather than trusting the caller's query:
    the number this function returns is the one a person is refused on, and
    it must mean the same thing as the roster they can see.
    """
    total = 0
    for member in members or []:
        if not isinstance(member, dict):
            continue
        status = str(member.get("status") or "active").strip().lower()
        if status and status != "active":
            continue
        total += 1
    return total


def is_already_a_member(members: Optional[Iterable[Dict[str, Any]]], user_id: str) -> bool:
    """Someone already inside does not consume a NEW seat.

    Without this, a person re-clicking their own invite link -- or an
    already-accepted invite replaying through the compatibility branch --
    would be refused for a seat they are already occupying, on a workspace
    that is merely full rather than over-full.
    """
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return False
    for member in members or []:
        if not isinstance(member, dict):
            continue
        if str(member.get("user_id") or "").strip() != clean_user_id:
            continue
        status = str(member.get("status") or "active").strip().lower()
        if not status or status == "active":
            return True
    return False


def member_limit_sentence(*, used_seats: int, limit: int) -> str:
    """The one line every seat refusal says. Names the limit, what is in
    use, and the two things that actually change the outcome."""
    used = max(0, int(used_seats or 0))
    total = max(0, int(limit or 0))
    return (
        f"This workspace is full: {used} of {total} member seats are in use. "
        "Remove a member you no longer work with to free a seat, or ask us "
        "to raise the limit."
    )


def assert_seat_available(
    *,
    members: Optional[List[Dict[str, Any]]],
    joining_user_id: str = "",
    limit: Optional[int] = None,
) -> int:
    """Raise WorkspaceMemberLimitReached unless one more person fits.
    Returns the seat count the workspace would then be at.

    ``limit <= 0`` means "no limit configured" and admits everyone -- an
    unset or misconfigured dial must never lock every workspace out of
    adding a teammate. The env override in billing_credit_config clamps to
    >= 1, so reaching that branch takes a deliberate call.
    """
    resolved_limit = member_limit() if limit is None else int(limit)
    used = active_member_count(members)
    if joining_user_id and is_already_a_member(members, joining_user_id):
        return used
    if resolved_limit <= 0:
        return used + 1
    if used >= resolved_limit:
        raise WorkspaceMemberLimitReached(
            member_limit_sentence(used_seats=used, limit=resolved_limit)
        )
    return used + 1
