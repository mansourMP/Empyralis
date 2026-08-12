"""ensure_workspace_membership() must never seed a brand-new workspace's
`name` from its own machine id (e.g. `ws_b5c1fa225ae6` on screen). It must
derive a human name the same way create_local_password_account() already
does -- display name (or email prefix) + "'s Workspace" -- and it must never
rename an EXISTING workspace as a side effect of a later membership update
(that would be a read/update silently overwriting something a customer may
have renamed).

These tests exercise the SQLite fallback path (DATABASE_URL unset in the test
environment per this repo's standing rule -- see server_modules/tests/conftest.py's
_isolate_empyralis_state, which points control_plane_repository.LOCAL_IDENTITY_DB_FILE
at a per-test file), which is exactly the path `login_external_user` /
`provision_user_account` hit for OAuth/mobile login on a fresh machine.
"""

import sqlite3
import uuid

import pytest

from server_modules import control_plane_repository


def _read_local_workspace_name(workspace_id: str) -> str:
    with sqlite3.connect(control_plane_repository.LOCAL_IDENTITY_DB_FILE) as conn:
        row = conn.execute(
            "SELECT name FROM workspace_registry WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    assert row is not None, f"workspace_registry row missing for {workspace_id}"
    return str(row[0])


@pytest.mark.asyncio
async def test_new_workspace_gets_a_human_name_not_its_own_id():
    workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"

    result = await control_plane_repository.ensure_workspace_membership(
        user_id=str(uuid.uuid4()),
        email="ada@example.com",
        display_name="Ada Lovelace",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="owner",
    )
    assert result is not None

    stored_name = _read_local_workspace_name(workspace_id)
    # The defect: stored_name == workspace_id (the machine id on screen).
    assert stored_name != workspace_id
    assert stored_name == "Ada Lovelace's Workspace"

    # End-to-end through the read path the UI actually calls.
    fetched = await control_plane_repository.get_workspace_by_id(workspace_id)
    assert fetched is not None
    assert fetched["name"] == "Ada Lovelace's Workspace"


@pytest.mark.asyncio
async def test_new_workspace_falls_back_to_email_prefix_when_no_display_name():
    workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"

    await control_plane_repository.ensure_workspace_membership(
        user_id=str(uuid.uuid4()),
        email="grace@example.com",
        display_name=None,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="owner",
    )

    stored_name = _read_local_workspace_name(workspace_id)
    assert stored_name != workspace_id
    assert stored_name == "grace's Workspace"


@pytest.mark.asyncio
async def test_existing_workspace_name_is_never_overwritten_by_a_later_membership_update():
    workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"

    await control_plane_repository.ensure_workspace_membership(
        user_id=str(uuid.uuid4()),
        email="ada@example.com",
        display_name="Ada Lovelace",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="owner",
    )
    original_name = _read_local_workspace_name(workspace_id)
    assert original_name == "Ada Lovelace's Workspace"

    # A second person joins the SAME already-created workspace. This must not
    # rename the workspace to derive from the second person's identity --
    # only the create path ever sets the name.
    await control_plane_repository.ensure_workspace_membership(
        user_id=str(uuid.uuid4()),
        email="bob@example.com",
        display_name="Bob Someone",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="member",
    )

    assert _read_local_workspace_name(workspace_id) == original_name
