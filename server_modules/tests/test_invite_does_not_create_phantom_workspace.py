"""An invited account must not be handed a workspace of its own (2026-08-20).

The founder invited a second account of his into his existing workspace and
got this in the workspace switcher:

    Mansur's Workspace     Owner   (checked)
    Untitled workspace     Owner            <- never asked for

Two independent defects produced it, and both are covered here:

  1. register_user ALWAYS minted a personal workspace, including for an
     account whose only reason to exist is an invite it has not answered yet.
  2. auth.accept_workspace_invites_for_user (deleted) GRANTED membership on
     every login and every registration, so the invite was consumed before
     anyone could accept it -- and PendingWorkspaceInvitesBanner, the built,
     mounted, Join/Decline surface, could never render a single row.

These are mocked unit tests over the real functions. The end-to-end proof was
a browser run against a disposable stack; see the branch's commit message.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, Mock, patch

import pytest

from server_modules import auth, control_plane_repository


class _FakeAuthConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        return self

    def commit(self):
        return None


def _register_dependencies(pending_invites, membership_rows, capture):
    """Everything register_user touches, stubbed, except the one decision
    under test: what it asks create_local_password_account to build."""

    def _capture_account(**kwargs):
        capture.update(kwargs)
        return None

    return (
        patch.object(auth, "_find_user_by_email", return_value=None),
        patch.object(auth, "_hash_password", return_value="hash"),
        patch.object(
            auth.control_plane_repository,
            "create_local_password_account",
            new=Mock(side_effect=_capture_account),
        ),
        patch.object(auth.control_plane_repository, "get_local_auth_identity_by_email", new=Mock(return_value=None)),
        patch.object(
            auth,
            "_control_plane_call",
            return_value={"user": {"id": "user-1", "email": "invited@example.com"}, "memberships": []},
        ),
        patch.object(auth, "_connect_auth_db", return_value=_FakeAuthConnection()),
        patch.object(auth, "_upsert_user_auth_method_locked"),
        patch.object(auth, "_ensure_user_identity_versions_locked"),
        patch.object(auth, "_find_user_by_id", return_value={"id": "user-1", "email": "invited@example.com"}),
        patch.object(auth, "pending_workspace_invites_for_user", return_value=pending_invites),
        patch.object(auth, "_list_workspace_memberships", return_value=membership_rows),
        patch.object(auth, "_effective_workspace_access", return_value={}),
        patch.object(auth, "_issue_authenticated_user_payload", return_value={"ok": True}),
    )


def test_signup_with_a_pending_invite_mints_no_workspace_of_its_own() -> None:
    capture: dict = {}
    invite = {
        "id": "invite-1",
        "workspace_id": "ws-inviting",
        "tenant_id": "tenant-inviting",
        "role": "member",
    }
    with contextlib.ExitStack() as stack:
        for dependency in _register_dependencies([invite], [], capture):
            stack.enter_context(dependency)
        auth.register_user("invited@example.com", "password-123", name="Invited")

    assert capture["bootstrap_personal_workspace"] is False
    # users.tenant_id / users.workspace_id are NOT NULL, so the home seed has
    # to name real rows -- the inviting workspace's, which is where this
    # account is headed.
    assert capture["tenant_id"] == "tenant-inviting"
    assert capture["workspace_id"] == "ws-inviting"


def test_signup_with_no_invite_still_mints_the_ordinary_personal_workspace() -> None:
    """The fix is scoped to invited signups. An ordinary signup is unchanged;
    without this assertion the change could silently leave every new customer
    with no workspace at all."""
    capture: dict = {}
    with contextlib.ExitStack() as stack:
        for dependency in _register_dependencies([], [{"workspace_id": "ws-home", "role": "owner"}], capture):
            stack.enter_context(dependency)
        auth.register_user("solo@example.com", "password-123", name="Solo")

    assert capture["bootstrap_personal_workspace"] is True
    assert capture["tenant_id"] is None
    assert capture["workspace_id"] is None


def test_an_invite_alone_is_enough_to_sign_in_with_no_workspace() -> None:
    """login_user used to 403 anyone with zero memberships. That is now the
    ordinary state of an invitee who has not pressed Join -- and refusing the
    session would lock them out of the only screen that can accept it."""
    user = {"id": "user-1", "email": "invited@example.com", "password_hash": "hash"}
    with (
        patch.object(auth, "_find_user_by_email", return_value=user),
        patch.object(auth.control_plane_repository, "get_local_auth_identity_by_email", new=Mock(return_value=None)),
        patch.object(auth, "_control_plane_call", return_value=None),
        patch.object(auth, "_verify_password", return_value=True),
        patch.object(auth, "_list_workspace_memberships", return_value=[]),
        patch.object(
            auth,
            "pending_workspace_invites_for_user",
            return_value=[{"id": "invite-1", "workspace_id": "ws-inviting", "role": "member"}],
        ),
        patch.object(auth, "_ensure_personal_workspace_for_user") as mint_mock,
        patch.object(auth, "_effective_workspace_access", return_value={}),
        patch.object(auth, "_issue_authenticated_user_payload", return_value={"ok": True}),
        patch.object(auth, "load_user_enterprise_security", return_value={}),
    ):
        payload = auth.login_user("invited@example.com", "password-123")

    assert payload == {"ok": True}
    # And it must NOT quietly hand them a workspace either -- that would be
    # the phantom back again, just later.
    mint_mock.assert_not_called()


def test_no_membership_and_no_invite_recovers_a_personal_workspace() -> None:
    """Declining (or having the invite revoked, or expire) must not be a
    one-way door out of your own account."""
    user = {"id": "user-1", "email": "declined@example.com", "password_hash": "hash", "name": "Dee"}
    minted = {"id": "ws-new", "workspace_id": "ws-new"}
    memberships: list[dict] = []

    def _mint(user_id, record=None):
        memberships.append({"workspace_id": "ws-new", "role": "owner"})
        return minted

    with (
        patch.object(auth, "_find_user_by_email", return_value=user),
        patch.object(auth.control_plane_repository, "get_local_auth_identity_by_email", new=Mock(return_value=None)),
        patch.object(auth, "_control_plane_call", return_value=None),
        patch.object(auth, "_verify_password", return_value=True),
        patch.object(auth, "_list_workspace_memberships", side_effect=lambda _uid: list(memberships)),
        patch.object(auth, "pending_workspace_invites_for_user", return_value=[]),
        patch.object(auth, "_ensure_personal_workspace_for_user", side_effect=_mint) as mint_mock,
        patch.object(auth, "_effective_workspace_access", return_value={"ws-new": {"workspace_id": "ws-new"}}),
        patch.object(auth, "_issue_authenticated_user_payload", return_value={"ok": True}),
        patch.object(auth, "load_user_enterprise_security", return_value={}),
    ):
        payload = auth.login_user("declined@example.com", "password-123")

    assert payload == {"ok": True}
    mint_mock.assert_called_once()


def test_no_membership_no_invite_and_no_recovery_still_refuses() -> None:
    """The original 403 is not deleted -- it is the last of three outcomes,
    not the only one."""
    user = {"id": "user-1", "email": "nobody@example.com", "password_hash": "hash"}
    with (
        patch.object(auth, "_find_user_by_email", return_value=user),
        patch.object(auth.control_plane_repository, "get_local_auth_identity_by_email", new=Mock(return_value=None)),
        patch.object(auth, "_control_plane_call", return_value=None),
        patch.object(auth, "_verify_password", return_value=True),
        patch.object(auth, "_list_workspace_memberships", return_value=[]),
        patch.object(auth, "pending_workspace_invites_for_user", return_value=[]),
        patch.object(auth, "_ensure_personal_workspace_for_user", return_value=None),
        patch.object(auth, "load_user_enterprise_security", return_value={}),
    ):
        with pytest.raises(Exception) as excinfo:
            auth.login_user("nobody@example.com", "password-123")

    assert getattr(excinfo.value, "status_code", None) == 403


class _RecordingConnection:
    """Stands in for the control plane's asyncpg connection and records the
    statements it is handed, so "which rows did this write" is answerable
    without a database."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def fetchrow(self, *_args, **_kwargs):
        return None

    async def execute(self, sql, *_args, **_kwargs):
        self.statements.append(" ".join(str(sql).split()))
        return None


def _scoped_connection_returning(connection):
    @contextlib.asynccontextmanager
    async def _fake(*_args, **_kwargs):
        yield connection

    return _fake


def test_create_local_password_account_writes_no_workspace_when_told_not_to() -> None:
    connection = _RecordingConnection()
    with (
        patch.object(control_plane_repository, "_scoped_connection", _scoped_connection_returning(connection)),
        patch.object(control_plane_repository, "get_user_bundle_by_id", new=AsyncMock(return_value=None)),
        patch.object(control_plane_repository, "ensure_workspace_billing_defaults", new=AsyncMock()) as billing_mock,
    ):
        asyncio.run(
            control_plane_repository.create_local_password_account(
                user_id="user-1",
                email="invited@example.com",
                display_name="Invited",
                password_hash="hash",
                tenant_id="tenant-inviting",
                workspace_id="ws-inviting",
                bootstrap_personal_workspace=False,
            )
        )

    joined = " || ".join(connection.statements)
    assert "INSERT INTO users" in joined
    assert "INSERT INTO auth_identities" in joined
    # The three writes that used to produce the phantom.
    assert "INSERT INTO workspaces" not in joined
    assert "INSERT INTO tenants" not in joined
    assert "INSERT INTO workspace_memberships" not in joined
    billing_mock.assert_not_called()


def test_create_local_password_account_still_bootstraps_by_default() -> None:
    connection = _RecordingConnection()
    with (
        patch.object(control_plane_repository, "_scoped_connection", _scoped_connection_returning(connection)),
        patch.object(control_plane_repository, "get_user_bundle_by_id", new=AsyncMock(return_value=None)),
        patch.object(control_plane_repository, "ensure_workspace_billing_defaults", new=AsyncMock()),
    ):
        asyncio.run(
            control_plane_repository.create_local_password_account(
                user_id="user-2",
                email="solo@example.com",
                display_name="Solo",
                password_hash="hash",
            )
        )

    joined = " || ".join(connection.statements)
    assert "INSERT INTO workspaces" in joined
    assert "INSERT INTO tenants" in joined
    assert "INSERT INTO workspace_memberships" in joined


def test_skipping_the_bootstrap_without_a_real_home_falls_back_to_minting_one() -> None:
    """Fail SAFE: a caller that says "no workspace" but names none for the
    NOT NULL home columns would leave the account with nowhere at all."""
    connection = _RecordingConnection()
    with (
        patch.object(control_plane_repository, "_scoped_connection", _scoped_connection_returning(connection)),
        patch.object(control_plane_repository, "get_user_bundle_by_id", new=AsyncMock(return_value=None)),
        patch.object(control_plane_repository, "ensure_workspace_billing_defaults", new=AsyncMock()),
    ):
        asyncio.run(
            control_plane_repository.create_local_password_account(
                user_id="user-3",
                email="orphan@example.com",
                display_name="Orphan",
                password_hash="hash",
                bootstrap_personal_workspace=False,
            )
        )

    joined = " || ".join(connection.statements)
    assert "INSERT INTO workspaces" in joined
    assert "INSERT INTO workspace_memberships" in joined
