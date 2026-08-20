from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, Mock, patch

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


def test_signing_in_never_grants_workspace_membership() -> None:
    """The founder's own report: "I didn't do anything. I didn't accept. The
    moment I came to the platform it was just present."

    auth.accept_workspace_invites_for_user used to run on every login AND
    every registration and GRANT membership for every pending invite matching
    the caller's email. It is deleted. Its replacement,
    pending_workspace_invites_for_user, only reads -- so this asserts the
    absence of a writer, which no behavioural test of the remaining code can
    do on its own.
    """
    assert not hasattr(auth, "accept_workspace_invites_for_user")

    granted: list[tuple[str, str, str]] = []
    invites = [
        {"id": "invite-1", "workspace_id": "ws-1", "role": "member"},
        {"id": "invite-2", "workspace_id": "ws-2", "role": "viewer"},
    ]
    with (
        patch.object(auth, "_control_plane_call", return_value=invites),
        patch.object(
            auth,
            "upsert_workspace_membership",
            side_effect=lambda user_id, workspace_id, role: granted.append((user_id, workspace_id, role)),
        ),
        patch.object(auth.control_plane_repository, "accept_workspace_invite", new=Mock()) as accept_mock,
    ):
        result = auth.pending_workspace_invites_for_user("owner@example.com")

    assert [item["workspace_id"] for item in result] == ["ws-1", "ws-2"]
    # The two things that must NOT happen: no membership, no invite consumed.
    assert granted == []
    accept_mock.assert_not_called()


def test_login_user_resolves_existing_access_without_touching_invites() -> None:
    """Logging in reports what you already have. It never changes it -- an
    already-member caller must not have any invite machinery run at all."""
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
        patch.object(auth, "pending_workspace_invites_for_user", return_value=[]) as pending_mock,
        patch.object(auth, "upsert_workspace_membership") as grant_mock,
        patch.object(auth, "_ensure_personal_workspace_for_user") as mint_mock,
        patch.object(auth, "_list_workspace_memberships", return_value=membership_rows),
        patch.object(auth, "_effective_workspace_access", return_value={"ws-home": {"workspace_id": "ws-home"}, "ws-invited": {"workspace_id": "ws-invited"}}),
        patch.object(auth, "_issue_authenticated_user_payload", return_value={"ok": True}) as issue_mock,
        patch.object(auth, "load_user_enterprise_security", return_value={}),
    ):
        payload = auth.login_user("owner@example.com", "password-123")

    assert payload == {"ok": True}
    grant_mock.assert_not_called()
    # Nothing about a caller who already has access needs the invite list or
    # the personal-workspace fallback -- neither is consulted.
    pending_mock.assert_not_called()
    mint_mock.assert_not_called()
    assert issue_mock.call_args.kwargs["workspace_access"]["ws-invited"]["workspace_id"] == "ws-invited"


def _patched_register_user_dependencies():
    """Shared mock set for a bare register_user() call, mirroring
    test_register_user_accepts_pending_workspace_invites_before_resolving_access
    below -- factored out so the email-verification-hook tests don't have to
    repeat the whole plumbing just to reach the new call at the end of the
    function."""
    return (
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
        patch.object(auth, "pending_workspace_invites_for_user", return_value=[]),
        patch.object(auth, "_list_workspace_memberships", return_value=[{"workspace_id": "ws-home", "role": "owner"}]),
        patch.object(auth, "_effective_workspace_access", return_value={"ws-home": {"workspace_id": "ws-home"}}),
        patch.object(auth, "_issue_authenticated_user_payload", return_value={"ok": True}),
    )


def test_register_user_kicks_off_email_verification_for_the_new_account() -> None:
    """The actual wiring point for signup email verification (MAN --
    docs/design/email-verification-plan.md): register_user() must call
    email_verification_service.start_verification with the new user's id and
    email once the account exists, without changing what register_user
    returns to the caller."""
    with contextlib.ExitStack() as stack:
        for dependency in _patched_register_user_dependencies():
            stack.enter_context(dependency)
        start_mock = stack.enter_context(
            patch.object(auth.email_verification_service, "start_verification", new=AsyncMock(return_value=None))
        )
        payload = auth.register_user("owner@example.com", "password-123", name="Owner")

    # Still exact equality: the payload carries the issued-user payload PLUS
    # the verification-email outcome, and nothing else. See
    # test_signup_verification_email_delivery.py for why the outcome is
    # reported at all -- a silent failure here left people waiting on a code
    # that was never sent.
    assert payload == {"ok": True, "email_verification": {"status": "sent"}}
    start_mock.assert_awaited_once_with(user_id="user-1", email="owner@example.com")


def test_register_user_signup_survives_email_verification_provider_failure() -> None:
    """Account creation must not be undone or fail just because the email
    provider is unconfigured/unreachable -- register_user() logs and
    continues (see the try/except right after the start_verification call)
    rather than raising, which would turn a signup into a 500 whenever
    EMAIL_PROVIDER_API_KEY is unset."""
    with contextlib.ExitStack() as stack:
        for dependency in _patched_register_user_dependencies():
            stack.enter_context(dependency)
        stack.enter_context(
            patch.object(
                auth.email_verification_service,
                "start_verification",
                new=AsyncMock(side_effect=RuntimeError("EMAIL_PROVIDER_API_KEY is not configured.")),
            )
        )
        payload = auth.register_user("owner@example.com", "password-123", name="Owner")

    # The account still exists and the caller still gets its payload -- that is
    # the point of this test and it is unchanged. What is added is that the
    # caller is now TOLD the email did not go out: a bare RuntimeError is not
    # EmailProviderUnavailable, so it reports "failed" (worth a retry) rather
    # than "not_configured" (never worth a retry).
    assert payload == {"ok": True, "email_verification": {"status": "failed"}}


def test_register_user_never_accepts_pending_workspace_invites() -> None:
    """Signing up is not accepting. A brand-new account whose email has a
    pending invite is registered and left with the invite still pending, so
    PendingWorkspaceInvitesBanner can offer a real Join / Decline choice."""
    membership_rows = [{"workspace_id": "ws-home", "role": "owner"}]

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
        patch.object(
            auth,
            "pending_workspace_invites_for_user",
            return_value=[{"id": "invite-1", "workspace_id": "ws-invited", "tenant_id": "tenant-invited", "role": "member"}],
        ),
        patch.object(auth, "upsert_workspace_membership") as grant_mock,
        patch.object(auth.control_plane_repository, "accept_workspace_invite", new=Mock()) as accept_mock,
        patch.object(auth, "_list_workspace_memberships", return_value=membership_rows),
        patch.object(
            auth,
            "_effective_workspace_access",
            return_value={"ws-home": {"workspace_id": "ws-home"}},
        ),
        patch.object(auth, "_issue_authenticated_user_payload", return_value={"ok": True}),
    ):
        payload = auth.register_user("owner@example.com", "password-123", name="Owner")

    assert payload == {"ok": True, "email_verification": {"status": "not_configured"}}
    grant_mock.assert_not_called()
    accept_mock.assert_not_called()
