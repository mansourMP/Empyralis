"""fix/google-connectors-honest-when-scopes-unavailable

2026-08-12: the OAuth consent screen for this deployment's Google Cloud
project (empyralis-gws-cli) was changed live to declare ONLY non-sensitive
scopes -- openid, email, profile, drive.file. The sensitive `calendar`
scope and the restricted `gmail.modify` scope were removed so Google
Sign-In and Drive stay unverified-clean (no 100-user cap, no "unverified
app" warning); Gmail and Calendar come back later behind a separate,
verified OAuth app.

`connection_oauth_service.py` still requested gmail.modify/calendar
unconditionally, so every "Connect Google Workspace" click failed with
Google's own raw invalid_scope error before ever reaching our code --  and
even fixing the OAuth request alone was not enough, because
`connector_validators.validate_google_workspace_connector` (run immediately
after every OAuth connect, see `connectors_actions.create_connector_vault`)
hard-required the Gmail scope, so a credential with ONLY identity+Drive
scopes -- exactly what this deployment's consent screen grants -- was
reported entirely invalid, Drive included.

This file covers the whole honesty chain, each piece matching an existing
pattern in this codebase rather than inventing a new one:

1. google_workspace_enabled_capabilities() -- the ONE source of truth for
   which Google scopes this deployment may currently request.
2. _effective_scopes()/_joined_scopes() -- the OAuth authorize URL only
   ever requests available scopes, and refuses with a coded error rather
   than ever building a request Google would reject (item 3 of the fix).
3. _register_mcp_servers_for_provider() -- an MCP server row is never
   created for a capability that cannot work (the same "endpoint=None is
   skipped" pattern APP_MCP_SERVER_MAP already uses, extended to scope
   availability). Matches CLAUDE.md's "a control that cannot be used in
   the current state is not rendered."
4. validate_google_workspace_connector() -- Gmail degrades to a warning
   exactly like Calendar/Drive already do, instead of failing the whole
   connector (the pattern Microsoft 365's validator already uses).
5. capability_verification_from_test_result() -- gmail read/write actions
   are gated on the connector's own test result, matching the existing
   calendar/drive conditional gating, so a Drive-only credential still
   reports runtime_usable=True and an agent is never handed a Gmail tool
   it cannot use.

Run: DATABASE_URL= venv/bin/python -m pytest server_modules/tests/test_google_workspace_scope_unavailable.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import connection_oauth_service
from server_modules import connector_validators
from server_modules import tool_availability_truth


# ---------------------------------------------------------------------------
# 1. google_workspace_enabled_capabilities() -- the single source of truth.
# ---------------------------------------------------------------------------

class GoogleWorkspaceEnabledCapabilitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.env_patcher = patch.dict(
            "os.environ",
            {
                "GOOGLE_WORKSPACE_OAUTH_SCOPES": "",
                "GOOGLE_OAUTH_SCOPES": "",
                "GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "",
                "GOOGLE_OAUTH_ENABLED_SCOPES": "",
                "GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE": "",
                "GOOGLE_OAUTH_ENABLE_DRIVE_SCOPE": "",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        super().tearDown()

    def test_default_is_drive_only(self) -> None:
        self.assertEqual(
            connection_oauth_service.google_workspace_enabled_capabilities(),
            frozenset({"drive"}),
        )

    def test_gmail_and_calendar_unavailable_by_default(self) -> None:
        self.assertFalse(connection_oauth_service.google_workspace_capability_available("gmail"))
        self.assertFalse(connection_oauth_service.google_workspace_capability_available("calendar"))

    def test_drive_and_identity_available_by_default(self) -> None:
        # Identity has no capability key at all -- it is never gated, see
        # OAUTH_PROVIDER_CONFIGS["google_workspace"].scopes.
        self.assertTrue(connection_oauth_service.google_workspace_capability_available("drive"))

    def test_enabling_gmail_and_calendar_via_config_flips_them_on(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "drive,gmail,calendar"}):
            enabled = connection_oauth_service.google_workspace_enabled_capabilities()
        self.assertEqual(enabled, frozenset({"drive", "gmail", "calendar"}))

    def test_enabling_only_gmail_leaves_calendar_off(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "gmail"}):
            enabled = connection_oauth_service.google_workspace_enabled_capabilities()
            self.assertEqual(enabled, frozenset({"gmail"}))
            self.assertFalse(connection_oauth_service.google_workspace_capability_available("calendar"))
            self.assertFalse(connection_oauth_service.google_workspace_capability_available("drive"))

    def test_unknown_capability_key_is_a_coded_config_error_not_a_silent_typo(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "gmial"}):
            with self.assertRaises(HTTPException) as excinfo:
                connection_oauth_service.google_workspace_enabled_capabilities()
        self.assertEqual(excinfo.exception.status_code, 500)
        self.assertIn("google_workspace_scope_config_invalid", str(excinfo.exception.detail))

    def test_legacy_drive_flag_explicit_true_still_enables_drive(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE": "true"}):
            self.assertTrue(connection_oauth_service.google_workspace_capability_available("drive"))

    def test_legacy_drive_flag_explicit_false_still_disables_drive(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE": "false"}):
            self.assertFalse(connection_oauth_service.google_workspace_capability_available("drive"))

    def test_new_var_supersedes_legacy_flag_when_both_set(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE": "false",
                "GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "drive",
            },
        ):
            self.assertTrue(connection_oauth_service.google_workspace_capability_available("drive"))


# ---------------------------------------------------------------------------
# 2. _effective_scopes()/_joined_scopes() -- the OAuth authorize URL.
# ---------------------------------------------------------------------------

class EffectiveScopesTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config = connection_oauth_service.OAUTH_PROVIDER_CONFIGS["google_workspace"]

    def test_default_scopes_are_identity_plus_drive(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GOOGLE_WORKSPACE_OAUTH_SCOPES": "",
                "GOOGLE_OAUTH_SCOPES": "",
                "GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "",
                "GOOGLE_OAUTH_ENABLED_SCOPES": "",
                "GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE": "",
                "GOOGLE_OAUTH_ENABLE_DRIVE_SCOPE": "",
            },
        ):
            scopes = connection_oauth_service._effective_scopes("google_workspace", self.config)
        self.assertEqual(
            set(scopes),
            {"openid", "email", "profile", "https://www.googleapis.com/auth/drive.file"},
        )

    def test_explicit_override_requesting_an_unavailable_scope_is_refused_not_sent_to_google(self) -> None:
        """Item 3 of the fix: even the raw GOOGLE_WORKSPACE_OAUTH_SCOPES
        escape hatch must not be able to build an authorize URL Google will
        bounce -- that whole class of failure happens OUTSIDE our redirect
        flow (Google shows its own raw error page), so the only way to
        prevent it is to never construct the request."""
        with patch.dict(
            "os.environ",
            {
                "GOOGLE_WORKSPACE_OAUTH_SCOPES": "openid email profile https://www.googleapis.com/auth/gmail.modify",
                "GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "",
                "GOOGLE_OAUTH_ENABLED_SCOPES": "",
                "GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE": "",
            },
        ):
            with self.assertRaises(HTTPException) as excinfo:
                connection_oauth_service._effective_scopes("google_workspace", self.config)
        self.assertEqual(excinfo.exception.status_code, 409)
        self.assertIn("google_workspace_scope_unavailable", str(excinfo.exception.detail))
        self.assertIn("gmail", str(excinfo.exception.detail))

    def test_other_providers_are_unaffected(self) -> None:
        github_config = connection_oauth_service.OAUTH_PROVIDER_CONFIGS["github"]
        scopes = connection_oauth_service._effective_scopes("github", github_config)
        self.assertEqual(scopes, github_config.scopes)


# ---------------------------------------------------------------------------
# 3. _register_mcp_servers_for_provider() -- an MCP row is never created for
#    an unavailable capability, matching the endpoint=None skip pattern.
# ---------------------------------------------------------------------------

class RegisterMcpServersScopeAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_gmail_and_calendar_are_skipped_by_default_drive_is_registered(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "",
                    "GOOGLE_OAUTH_ENABLED_SCOPES": "",
                    "GOOGLE_WORKSPACE_ENABLE_DRIVE_SCOPE": "",
                },
            ),
            patch(
                "server_modules.mcp_registry_service.upsert_workspace_mcp_server_async",
                new=AsyncMock(return_value={"id": "google-drive", "tools": []}),
            ) as mock_upsert,
        ):
            result = await connection_oauth_service._register_mcp_servers_for_provider(
                workspace_id="ws-1",
                normalized_provider="google_workspace",
                credential_id="cred-1",
            )

        # Exactly one network/registration attempt -- for Drive. Gmail and
        # Calendar were never attempted at all, not attempted-and-failed.
        self.assertEqual(mock_upsert.call_count, 1)
        self.assertEqual(mock_upsert.call_args.kwargs["server_id"], "google-drive")

        self.assertEqual(result["registered"], 1)
        self.assertEqual(result["failures"], [])
        skipped_ids = {item["server_id"] for item in result["skipped"]}
        self.assertEqual(skipped_ids, {"google-gmail", "google-calendar"})
        for item in result["skipped"]:
            self.assertEqual(item["reason"], "scope_unavailable")

    async def test_all_three_are_registered_once_all_capabilities_enabled(self) -> None:
        with (
            patch.dict("os.environ", {"GOOGLE_WORKSPACE_OAUTH_ENABLED_SCOPES": "drive,gmail,calendar"}),
            patch(
                "server_modules.mcp_registry_service.upsert_workspace_mcp_server_async",
                new=AsyncMock(return_value={"id": "x", "tools": []}),
            ) as mock_upsert,
        ):
            result = await connection_oauth_service._register_mcp_servers_for_provider(
                workspace_id="ws-1",
                normalized_provider="google_workspace",
                credential_id="cred-1",
            )

        self.assertEqual(mock_upsert.call_count, 3)
        self.assertEqual(result["registered"], 3)
        self.assertEqual(result["skipped"], [])

    async def test_a_provider_with_no_capability_keys_is_unaffected(self) -> None:
        """github's single MCP entry carries no "capability" key at all, so
        this whole mechanism is a no-op for it -- confirms the skip is
        opt-in per entry, not a blanket new gate on every provider."""
        with patch(
            "server_modules.mcp_registry_service.upsert_workspace_mcp_server_async",
            new=AsyncMock(return_value={"id": "github", "tools": []}),
        ) as mock_upsert:
            result = await connection_oauth_service._register_mcp_servers_for_provider(
                workspace_id="ws-1",
                normalized_provider="github",
                credential_id="cred-1",
            )
        self.assertEqual(mock_upsert.call_count, 1)
        self.assertEqual(result["skipped"], [])


# ---------------------------------------------------------------------------
# 4. validate_google_workspace_connector() -- Gmail degrades to a warning.
# ---------------------------------------------------------------------------

class ValidateGoogleWorkspaceConnectorGmailGracefulTests(unittest.TestCase):
    def test_drive_only_credential_is_reported_valid_not_broken(self) -> None:
        """Before this fix: a credential with only identity+Drive scopes --
        exactly what this deployment's consent screen grants today -- made
        the ENTIRE connector report invalid, Drive included, because Gmail
        was hard-required. That must never happen again."""

        def fake_http_json_request(url, headers=None, **_kwargs):
            if "userinfo" in url:
                return {"status": 200, "json": {"email": "owner@example.com"}}
            if "gmail.googleapis.com" in url:
                return {"status": 403, "json": {"error": "insufficient scope"}}
            if "calendar/v3" in url:
                return {"status": 403, "json": {"error": "insufficient scope"}}
            if "drive/v3" in url:
                return {"status": 200, "json": {"files": []}}
            raise AssertionError(f"unexpected URL: {url}")

        result = connector_validators.validate_google_workspace_connector(
            {"access_token": "tok-abc"}, fake_http_json_request,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"]["emailAddress"], "owner@example.com")
        self.assertFalse(result["gmail_access"])
        self.assertFalse(result["calendar_access"])
        self.assertTrue(result["files_access"])
        self.assertIn("Gmail scope is missing", result["warning"])

    def test_full_grant_reports_all_three_access_flags_true(self) -> None:
        def fake_http_json_request(url, headers=None, **_kwargs):
            if "userinfo" in url:
                return {"status": 200, "json": {"email": "owner@example.com"}}
            if "gmail.googleapis.com" in url:
                return {"status": 200, "json": {"emailAddress": "owner@example.com", "messagesTotal": 5}}
            if "calendar/v3" in url:
                return {"status": 200, "json": {"items": []}}
            if "drive/v3" in url:
                return {"status": 200, "json": {"files": []}}
            raise AssertionError(f"unexpected URL: {url}")

        result = connector_validators.validate_google_workspace_connector(
            {"access_token": "tok-abc"}, fake_http_json_request,
        )

        self.assertTrue(result["gmail_access"])
        self.assertTrue(result["calendar_access"])
        self.assertTrue(result["files_access"])
        self.assertIsNone(result["warning"])
        self.assertEqual(result["message"], "Google Workspace connector is valid.")

    def test_invalid_identity_still_raises_the_token_is_genuinely_dead(self) -> None:
        def fake_http_json_request(url, headers=None, **_kwargs):
            if "userinfo" in url:
                return {"status": 401, "json": {}}
            raise AssertionError(f"unexpected URL for a dead token: {url}")

        with self.assertRaises(RuntimeError):
            connector_validators.validate_google_workspace_connector(
                {"access_token": "tok-abc"}, fake_http_json_request,
            )

    def test_missing_access_token_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            connector_validators.validate_google_workspace_connector({}, lambda *a, **k: {})


# ---------------------------------------------------------------------------
# 5. capability_verification_from_test_result() -- gmail actions are gated
#    on the test result exactly like calendar/drive already are.
# ---------------------------------------------------------------------------

class CapabilityVerificationGoogleWorkspaceGmailGatingTests(unittest.TestCase):
    def test_drive_only_grant_reports_runtime_usable_without_gmail_actions(self) -> None:
        verification = tool_availability_truth.capability_verification_from_test_result(
            "google_workspace",
            {"ok": True, "gmail_access": False, "calendar_access": False, "files_access": True},
        )

        self.assertTrue(verification["authenticated"])
        self.assertTrue(verification["runtime_usable"])
        self.assertNotIn("gmail_threads.read", verification["read_actions"])
        self.assertNotIn("fetch_emails", verification["write_actions"])
        self.assertNotIn("draft_email", verification["write_actions"])
        self.assertNotIn("send_email", verification["write_actions"])
        self.assertIn("drive_files.read", verification["read_actions"])
        self.assertIn("create_doc", verification["write_actions"])

    def test_full_grant_includes_gmail_actions(self) -> None:
        verification = tool_availability_truth.capability_verification_from_test_result(
            "google_workspace",
            {"ok": True, "gmail_access": True, "calendar_access": True, "files_access": True},
        )
        self.assertIn("gmail_threads.read", verification["read_actions"])
        self.assertIn("send_email", verification["write_actions"])

    def test_no_access_at_all_is_not_runtime_usable(self) -> None:
        verification = tool_availability_truth.capability_verification_from_test_result(
            "google_workspace",
            {"ok": True, "gmail_access": False, "calendar_access": False, "files_access": False},
        )
        self.assertFalse(verification["runtime_usable"])
        self.assertEqual(verification["read_actions"], [])
        self.assertEqual(verification["write_actions"], [])

    def test_failed_test_result_is_never_runtime_usable_regardless_of_flags(self) -> None:
        verification = tool_availability_truth.capability_verification_from_test_result(
            "google_workspace",
            {"ok": False, "gmail_access": True, "calendar_access": True, "files_access": True},
        )
        self.assertFalse(verification["authenticated"])
        self.assertFalse(verification["runtime_usable"])


if __name__ == "__main__":
    unittest.main()
