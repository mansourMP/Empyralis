"""Real Postgres: the workspaces.identity_links column must actually exist.

THE BUG THIS FILE EXISTS FOR
-----------------------------
get_workspace_by_id's SELECT was fixed (2622b8928) to name identity_links,
and update_workspace_identity_links's UPDATE has named it for a while --
but the column itself was never added anywhere in this codebase's
VERSIONED schema code: not in CONTROL_PLANE_SCHEMA_SQL's base CREATE TABLE,
not in the ALTER TABLE IF NOT EXISTS migration block right below it, not in
migrations/*.sql. Production's own `workspaces` table evidently has it
already (get_workspace_by_id does not 500 there), so it was added
out-of-band at some point -- by hand, against that one database -- and
never captured.

Reproduced live while standing up a throwaway stack to verify MAN-343
(signup false-failure): the very first `auth.register_user()` call, on a
genuinely fresh Postgres, raised asyncpg.exceptions.UndefinedColumnError
from inside ensure_workspace_billing_defaults -> get_workspace_by_id --
before ever reaching email verification, before the account was usable at
all. This is not a narrow gap; EVERY test file in this suite that calls
auth.register_user() against a fresh database (test_workspace_invite_
project_access_man70.py, test_man335_workspace_invite_default_project_
access.py, this file, and more) would have failed the same way on a
database nobody had already hand-patched -- a false negative unrelated to
whatever those tests actually mean to prove.

Fixed by adding the missing "ALTER TABLE workspaces ADD COLUMN IF NOT
EXISTS identity_links JSONB DEFAULT '{}'::jsonb" to the same in-code
migration block that already carries channel_active_threads and
slack_team_id (control_plane_repository.py, ~line 4046) -- same pattern,
same file, immediately adjacent.

Real Postgres required (skipped otherwise), same opt-in pattern as
test_workspace_invite_project_access_man70.py. Run with:

    DATABASE_URL=postgresql://...@localhost:5432/<disposable-db> \
        python3 -m pytest server_modules/tests/test_workspace_identity_links_column_exists.py -q

Never point this at a real/shared database -- see this repo's own
standing rule against ever setting DATABASE_URL to anything but a
throwaway the agent created for itself.
"""

from __future__ import annotations

import os
import uuid

import pytest

from server_modules import auth, control_plane_repository

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
async def test_identity_links_column_exists_and_round_trips() -> None:
    if not _database_url_available():
        pytest.skip(_NO_PG_REASON)

    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        pytest.skip(_NO_PG_REASON)

    # A schema-only assertion first, isolated from any application code
    # path, so a failure here points straight at the column rather than at
    # some other layer: this is exactly the query information_schema exists
    # for.
    column_row = await pool.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'workspaces' AND column_name = 'identity_links'"
    )
    assert column_row is not None, (
        "workspaces.identity_links does not exist -- the ALTER TABLE ADD "
        "COLUMN IF NOT EXISTS migration in control_plane_repository.py's "
        "ensure_control_plane_schema() did not run, or was removed"
    )
    assert column_row["data_type"] == "jsonb"

    # Now the real path: registering a user must not raise, and the
    # resulting workspace must come back with a real, dict-typed
    # identity_links -- proving both the column exists AND
    # get_workspace_by_id's SELECT (2622b8928) actually reads it.
    email = f"identity-links-col-{uuid.uuid4().hex[:10]}@example.com"
    auth.register_user(email, "Own3r-Secret-Pass!")
    user = auth._find_user_by_email(email)
    assert isinstance(user, dict) and user.get("id")
    user_id = str(user["id"]).strip()

    memberships = auth._list_workspace_memberships(user_id)
    assert memberships, "registration did not create a bootstrap workspace membership"
    workspace_id = str(memberships[0]["workspace_id"]).strip()

    try:
        workspace = await control_plane_repository.get_workspace_by_id(workspace_id)
        assert isinstance(workspace, dict)
        assert isinstance(workspace.get("identity_links"), dict)

        # Round-trip a real write through the same path the owner/audience
        # channel-identity linking flow uses, and confirm the SELECT above
        # sees it -- not just an empty default.
        ok = await control_plane_repository.update_workspace_identity_links(
            workspace_id, {"telegram": {"chat_id": "12345", "linked_as": "owner"}}
        )
        assert ok is True
        reread = await control_plane_repository.get_workspace_by_id(workspace_id)
        assert reread["identity_links"] == {"telegram": {"chat_id": "12345", "linked_as": "owner"}}
    finally:
        try:
            await pool.execute("DELETE FROM workspace_memberships WHERE workspace_id = $1", workspace_id)
            await pool.execute("DELETE FROM workspaces WHERE workspace_id = $1", workspace_id)
        except Exception:
            pass
