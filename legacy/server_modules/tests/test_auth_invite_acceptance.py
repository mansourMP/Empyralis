from __future__ import annotations

from unittest.mock import Mock, patch

from server_modules import auth


class _FakeAuthConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        return self

    def commit(self):
        return None


def test_accept_workspace_invites_for_user_creates_memberships_and_marks_acceptance() -> None:
    invites = [
        {"id": "invite-1", "workspace_id": "ws-1", "role": "member"},
        {"id": "invite-2", "workspace_id": "ws-2", "role": "viewer"},
    ]
    granted_memberships: list[tuple[str, str, str]] = []

    with (
        patch.object(auth, "_control_plane_call", side_effect=[invites, {"id": "invite-1"}, {"id": "invite-2"}]),
        patch.object(auth.control_plane_repository, "list_pending_workspace_invites_for_email", new=Mock(return_value=None)),
        patch.object(auth.control_plane_repository, "accept_workspace_invite", new=Mock(return_value=None)),
        patch.object(
            auth,
            "upsert_workspace_membership",
            side_effect=lambda user_id, workspace_id, role: granted_memberships.append((user_id, workspace_id, role)) or {"user_id": user_id, "workspace_id": workspace_id, "role": role},
        ),
    ):
        result = auth.accept_workspace_invites_for_user("user-1", "owner@example.com")

    assert [item["workspace_id"] for item in result] == ["ws-1", "ws-2"]
    assert granted_memberships == [
        ("user-1", "ws-1", "member"),
        ("user-1", "ws-2", "viewer"),
    ]
    assert result[0]["invite_id"] == "invite-1"
    assert result[1]["invite_id"] == "invite-2"


def test_login_user_accepts_pending_workspace_invites_before_resolving_access() -> None:
    user = {"id": "user-1", "email": "owner@example.com", "password_hash": "hash"}
    membership_rows = [
        {"workspace_id": "ws-home", "role": "owner"},
        {"workspace_id": "ws-invited", "role": "member"},
    ]

    with (
        patch.object(auth, "_find_user_by_email", return_value=user),
        patch.object(auth.control_plane_repository, "get_local_auth_identity_by_email", new=Mock(return_value=None)),
        patch.object(auth, "_control_plane_call", return_value=None),
        patch.object(auth, "_verify_password", return_value=True),
        patch.object(auth, "accept_workspace_invites_for_user") as accept_mock,
        patch.object(auth, "_list_workspace_memberships", return_value=membership_rows),
        patch.object(auth, "_effective_workspace_access", return_value={"ws-home": {"workspace_id": "ws-home"}, "ws-invited": {"workspace_id": "ws-invited"}}),
        patch.object(auth, "_issue_authenticated_user_payload", return_value={"ok": True}) as issue_mock,
        patch.object(auth, "load_user_enterprise_security", return_value={}),
    ):
        payload = auth.login_user("owner@example.com", "password-123")

    assert payload == {"ok": True}
    accept_mock.assert_called_once_with("user-1", "owner@example.com")
    assert issue_mock.call_args.kwargs["workspace_access"]["ws-invited"]["workspace_id"] == "ws-invited"


def test_register_user_accepts_pending_workspace_invites_before_resolving_access() -> None:
    membership_rows = [
        {"workspace_id": "ws-home", "role": "owner"},
        {"workspace_id": "ws-invited", "role": "member"},
    ]

    with (
        patch.object(auth, "_find_user_by_email", return_value=None),
        patch.object(auth, "_hash_password", return_value="hash"),
        patch.object(auth.control_plane_repository, "create_local_password_account", new=Mock(return_value=None)),
        patch.object(auth.control_plane_repository, "get_local_auth_identity_by_email", new=Mock(return_value=None)),
        patch.object(
            auth,
            "_control_plane_call",
            return_value={
                "user": {"id": "user-1", "email": "owner@example.com"},
                "memberships": [{"workspace_id": "ws-home", "tenant_id": "tenant-home", "role": "owner"}],
            },
        ),
        patch.object(auth, "_connect_auth_db", return_value=_FakeAuthConnection()),
        patch.object(auth, "_upsert_user_auth_method_locked"),
        patch.object(auth, "_ensure_user_identity_versions_locked"),
        patch.object(auth, "_find_user_by_id", return_value={"id": "user-1", "email": "owner@example.com"}),
        patch.object(auth, "accept_workspace_invites_for_user") as accept_mock,
        patch.object(auth, "_list_workspace_memberships", return_value=membership_rows),
        patch.object(
            auth,
            "_effective_workspace_access",
            return_value={
                "ws-home": {"workspace_id": "ws-home"},
                "ws-invited": {"workspace_id": "ws-invited"},
            },
        ),
        patch.object(auth, "_issue_authenticated_user_payload", return_value={"ok": True}) as issue_mock,
    ):
        payload = auth.register_user("owner@example.com", "password-123", name="Owner")

    assert payload == {"ok": True}
    accept_mock.assert_called_once_with("user-1", "owner@example.com")
    assert issue_mock.call_args.kwargs["workspace_access"]["ws-invited"]["workspace_id"] == "ws-invited"
