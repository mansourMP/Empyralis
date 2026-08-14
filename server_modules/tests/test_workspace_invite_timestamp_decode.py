"""MAN-343 follow-up: workspace_member_invites timestamp fields must decode
on real Postgres, not just SQLite.

THE BUG THIS FILE EXISTS FOR
-----------------------------
_workspace_invite_record_from_row (control_plane_repository.py) cast every
timestamp field with a bare ``int(row["created_at"])``. That is only
correct for the SQLite fallback branch, which stores these columns as
Python-generated epoch integers. Every real Postgres caller stores them as
TIMESTAMPTZ, which asyncpg decodes to a native ``datetime.datetime`` --
``int(a_datetime)`` raises TypeError unconditionally, on every row, on
every real Postgres database, every time.

Found while verifying MAN-70/MAN-335 (a separate ticket) against a
genuinely fresh throwaway Postgres that had not already been hand-patched
by an earlier debugging session: record_workspace_invite_email_delivery's
Postgres branch calls this function directly on a raw ``SELECT *`` row.
Its only caller (create_workspace_invite_route) wraps the call in a bare
try/except that logs and swallows the exception, so invite CREATION never
failed -- but the invite's email-delivery status
(sent/not_configured/failed/withheld -- workspace_invite_email_service's
own three/four-state doctrine, the entire point of that feature) has
never once been successfully PERSISTED on real Postgres. This was
invisible in every environment that has a real EMAIL_PROVIDER_API_KEY
configured (nothing there ever surfaced the swallowed exception to a
human), and every test of this path used a mocked/SQLite connection.

A second, uncaught call site exists too:
list_workspace_invites_for_project's Postgres branch calls this same
function with no try/except at all -- the owner-facing "who's been
invited" panel (ProjectMemberAdd.tsx) 500s outright on real Postgres the
moment a workspace has any invite. Not covered by a dedicated test here
(it needs a fuller project + invite fixture); the fix is the same one
function, so proving record_workspace_invite_email_delivery round-trips
proves both call sites are fixed.

Real Postgres required (skipped otherwise), same opt-in pattern as
test_workspace_invite_project_access_man70.py. Run with:

    DATABASE_URL=postgresql://...@localhost:5432/<disposable-db> \
        python3 -m pytest server_modules/tests/test_workspace_invite_timestamp_decode.py -q

Never point this at a real/shared database -- see this repo's own
standing rule against ever setting DATABASE_URL to anything but a
throwaway the agent created for itself.
"""

from __future__ import annotations

import os
import uuid

import pytest

from server_modules import control_plane_repository

_NO_PG_REASON = "No Postgres reachable (DATABASE_URL unset — export it to run this suite)"


def _database_url_available() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


@pytest.fixture(autouse=True)
def _database_url_from_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Presence, never truthiness -- see test_workspace_invite_project_access_
    man70.py's identical fixture for the full reasoning (MAN-202 shape: an
    operator's explicit DATABASE_URL= must never be silently overridden by a
    discoverable .env)."""
    if "DATABASE_URL" in os.environ:
        return
    try:
        from dotenv import dotenv_values, find_dotenv
    except Exception:
        return
    env_path = find_dotenv(usecwd=True)
    if not env_path:
        return
    database_url = str(dotenv_values(env_path).get("DATABASE_URL") or "").strip()
    if database_url:
        monkeypatch.setenv("DATABASE_URL", database_url)


@pytest.mark.asyncio
async def test_record_workspace_invite_email_delivery_round_trips_on_real_postgres() -> None:
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)

    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        pytest.skip(_NO_PG_REASON)

    suffix = uuid.uuid4().hex[:12]
    workspace_id = f"ws_ts_decode_{suffix}"
    tenant_id = workspace_id
    try:
        invite = await control_plane_repository.create_workspace_invite(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            email=f"invitee-{suffix}@example.com",
            role="member",
            invited_by_user_id=f"user_{suffix}",
            invited_by_role="owner",
        )
        assert isinstance(invite, dict) and invite.get("id"), (
            "create_workspace_invite did not return an invite record"
        )
        invite_id = str(invite["id"])

        # This is the exact call that raised TypeError before the fix --
        # real Postgres, real SELECT *, real TIMESTAMPTZ columns.
        updated = await control_plane_repository.record_workspace_invite_email_delivery(
            invite_id=invite_id,
            delivery_status="sent",
        )
        assert isinstance(updated, dict), (
            "record_workspace_invite_email_delivery must return the updated "
            "record, not silently fail/return None"
        )
        assert updated["id"] == invite_id
        assert isinstance(updated["created_at"], int), (
            f"created_at must decode to an int on real Postgres, got "
            f"{type(updated['created_at'])!r}: {updated['created_at']!r}"
        )
        assert updated["created_at"] > 0
        assert updated["metadata"]["email_delivery_status"] == "sent"
    finally:
        try:
            await pool.execute(
                "DELETE FROM workspace_member_invites WHERE workspace_id = $1", workspace_id
            )
        except Exception:
            pass
