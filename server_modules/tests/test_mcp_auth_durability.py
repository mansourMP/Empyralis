"""
MCP Phase D — auth durability for unattended agents (Linear MAN-61).
docs/design/mcp-applications-plan.md, Phase D.

An agent can run unattended for hours or days. If a connected MCP server's
OAuth token expires and refresh fails mid-task, the owner must find out —
and the agent must get an honest, short error instead of a raw transport
failure it might retry blindly forever.

Covers:
  (a) auth failure -> one forced refresh through the EXISTING oauth refresh
      machinery -> retry once -> success is invisible to the agent
      (McpAuthRecoveryTests.test_auth_failure_then_refresh_then_retry_succeeds,
      + the async-entrypoint twin)
  (b) refresh failure -> registry status flips to reauth_required, the
      workspace owner is alerted exactly once via the existing notification
      path (outbox_service.emit_notification_event +
      activity_ledger_service.append_activity_event(review_required=True) —
      the same pairing deployed_agent_cost_cap_service.py:212/331-359 uses),
      and the agent gets a short, honest error
      (McpAuthRecoveryTests.test_refresh_failure_flips_status_and_alerts_owner_and_honest_error,
      plus edge cases: no credential on file, and refresh succeeds but the
      retried call still auth-fails)
  (c) non-auth errors are completely unaffected — no refresh attempt, no
      alert, no status change, the original exception propagates unchanged
      (McpAuthRecoveryTests.test_non_auth_errors_pass_through_unaffected)

Plus focused unit coverage of the auth-error detector
(_is_mcp_auth_error / _iter_exception_chain) and of the new forced-refresh
entrypoint in connection_oauth_service.py (refresh_oauth_token_now), which
is the ONE refresh implementation both the opportunistic
(refresh_oauth_token_if_needed) and forced paths share.

Registry state is seeded via the real mcp_registry_service.
upsert_workspace_mcp_server() with the Rust-kernel write gate mocked to
"allow" — the same pattern test_mcp_tool_calling_wiring.py and
test_mcp_registry_service_rust_gate.py already use (calling
upsert_workspace_mcp_server() unmocked fails in this sandbox with
McpRegistryRustGateError: unexpected_next_action — a pre-existing,
unrelated test-infra gap; see the run notes in this task's final report).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from server_modules import connection_oauth_service
from server_modules import mcp_registry_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "save_mcp_server_registry",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.com/mcp")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code} error", request=request, response=response)


# ═══════════════════════════════════════════════════════════════════════════
# _is_mcp_auth_error — pure detection logic, no mocking needed
# ═══════════════════════════════════════════════════════════════════════════


class IsMcpAuthErrorTests(unittest.TestCase):
    def test_bare_401_is_an_auth_error(self) -> None:
        self.assertTrue(mcp_registry_service._is_mcp_auth_error(_http_status_error(401)))

    def test_bare_403_is_an_auth_error(self) -> None:
        self.assertTrue(mcp_registry_service._is_mcp_auth_error(_http_status_error(403)))

    def test_bare_500_is_not_an_auth_error(self) -> None:
        self.assertFalse(mcp_registry_service._is_mcp_auth_error(_http_status_error(500)))

    def test_generic_timeout_is_not_an_auth_error(self) -> None:
        self.assertFalse(mcp_registry_service._is_mcp_auth_error(RuntimeError("MCP tool call timed out")))

    def test_401_wrapped_in_runtime_error_cause_chain_is_detected(self) -> None:
        # The mcp SDK's streamable_http transport runs inside anyio task
        # groups, which commonly surface the underlying httpx error via
        # __cause__/__context__ rather than raising it directly.
        inner = _http_status_error(401)
        try:
            try:
                raise inner
            except httpx.HTTPStatusError as exc:
                raise RuntimeError("session initialize failed") from exc
        except RuntimeError as wrapped:
            self.assertTrue(mcp_registry_service._is_mcp_auth_error(wrapped))

    def test_401_inside_exception_group_is_detected(self) -> None:
        inner = _http_status_error(401)
        group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
        self.assertTrue(mcp_registry_service._is_mcp_auth_error(group))

    def test_message_token_fallback_catches_wrapped_invalid_token_text(self) -> None:
        self.assertTrue(
            mcp_registry_service._is_mcp_auth_error(RuntimeError("server rejected: invalid_token"))
        )


# ═══════════════════════════════════════════════════════════════════════════
# The registry-level auth-recovery flow — invoke_workspace_mcp_tool(_async)
# ═══════════════════════════════════════════════════════════════════════════


class McpAuthRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "mcp_servers.json"
        self.registry_file_patcher = patch.object(
            mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", self.registry_path
        )
        self.registry_file_patcher.start()
        self.rust_gate_patcher = patch.object(
            mcp_registry_service.rust_runtime_kernel_client,
            "runtime_state_store_decision",
            return_value=dict(_ALLOW_DECISION),
        )
        self.rust_gate_patcher.start()

    def tearDown(self) -> None:
        self.rust_gate_patcher.stop()
        self.registry_file_patcher.stop()
        self.temp_dir.cleanup()
        super().tearDown()

    def _seed_server(self, *, credential_id: str | None = "cred-1") -> None:
        mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            label="Inventory Feed",
            transport="streamable_http",
            endpoint="https://example.com/mcp",
            tools=[
                {
                    "name": "get_thing",
                    "label": "Get Thing",
                    "description": "Read a thing.",
                    "action_class": "read",
                    "connector_scopes": ["inventory"],
                    "approved": True,
                }
            ],
            metadata={},
            credential_id=credential_id,
        )

    def _metering_patches_sync(self):
        return (
            patch("server_modules.mcp_registry_service.agent_action_metering_service.record_started_sync"),
            patch("server_modules.mcp_registry_service.agent_action_metering_service.record_completed_sync"),
            patch("server_modules.mcp_registry_service.agent_action_metering_service.record_failed_sync"),
        )

    def _invoke_sync(self):
        return mcp_registry_service.invoke_workspace_mcp_tool(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            tool_name="get_thing",
            arguments={},
            agent_label="Sage",
        )

    # ── (a) auth failure -> refresh -> retry once -> success is invisible ──

    def test_auth_failure_then_refresh_then_retry_succeeds(self) -> None:
        self._seed_server()
        call_mock = AsyncMock(side_effect=[_http_status_error(401), {"reply": "ok now", "value": 42}])
        refresh_mock = MagicMock(return_value={"ok": True, "credential": {"access_token": "new-token"}})

        started, completed, failed = self._metering_patches_sync()
        with started, completed, failed, \
            patch.object(mcp_registry_service, "_call_streamable_http_tool_async", new=call_mock), \
            patch.object(connection_oauth_service, "refresh_oauth_token_now", new=refresh_mock), \
            patch("server_modules.outbox_service.emit_notification_event") as notify_mock, \
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock:
            result = self._invoke_sync()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reply"], "ok now")
        self.assertEqual(call_mock.await_count, 2)
        refresh_mock.assert_called_once_with("cred-1")
        notify_mock.assert_not_called()
        ledger_mock.assert_not_awaited()

        server = mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed")
        self.assertNotEqual(server.get("status"), "reauth_required")

    def test_auth_failure_then_refresh_then_retry_succeeds_async_entrypoint(self) -> None:
        # The async live-loop entrypoint (skills_service.py's dispatch calls
        # invoke_workspace_mcp_tool_async directly) shares the exact same
        # recovery coroutine as the sync twin above — no separate
        # implementation to duplicate-test, but worth one direct check.
        import asyncio

        self._seed_server()
        call_mock = AsyncMock(side_effect=[_http_status_error(401), {"reply": "ok now"}])
        refresh_mock = MagicMock(return_value={"ok": True, "credential": {"access_token": "new-token"}})

        with patch.object(mcp_registry_service, "_call_streamable_http_tool_async", new=call_mock), \
            patch.object(connection_oauth_service, "refresh_oauth_token_now", new=refresh_mock), \
            patch("server_modules.mcp_registry_service.agent_action_metering_service.record_started", new=AsyncMock()), \
            patch("server_modules.mcp_registry_service.agent_action_metering_service.record_completed", new=AsyncMock()), \
            patch("server_modules.mcp_registry_service.agent_action_metering_service.record_failed", new=AsyncMock()), \
            patch("server_modules.outbox_service.emit_notification_event") as notify_mock, \
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock:
            result = asyncio.run(
                mcp_registry_service.invoke_workspace_mcp_tool_async(
                    workspace_id="workspace-1",
                    server_id="inventory-feed",
                    tool_name="get_thing",
                    arguments={},
                    agent_label="Sage",
                )
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(call_mock.await_count, 2)
        notify_mock.assert_not_called()
        ledger_mock.assert_not_awaited()

    # ── (b) refresh failure -> status flip + owner alert + honest error ───

    def test_refresh_failure_flips_status_and_alerts_owner_and_honest_error(self) -> None:
        self._seed_server()
        call_mock = AsyncMock(side_effect=[_http_status_error(401)])
        refresh_mock = MagicMock(
            return_value={"ok": False, "reason": "invalid_grant", "detail": "refresh_token was revoked"}
        )
        ledger_mock = AsyncMock()

        started, completed, failed = self._metering_patches_sync()
        with started, completed, failed, \
            patch.object(mcp_registry_service, "_call_streamable_http_tool_async", new=call_mock), \
            patch.object(connection_oauth_service, "refresh_oauth_token_now", new=refresh_mock), \
            patch("server_modules.outbox_service.emit_notification_event") as notify_mock, \
            patch("server_modules.activity_ledger_service.append_activity_event", new=ledger_mock):
            with self.assertRaises(RuntimeError) as ctx:
                self._invoke_sync()

        message = str(ctx.exception)
        self.assertIn("Inventory Feed", message)
        self.assertIn("needs the owner to reconnect it", message)
        self.assertIn("notified", message)

        # Never retry forever: exactly one call attempt once refresh itself
        # already failed — no point spending a second network round trip.
        self.assertEqual(call_mock.await_count, 1)
        refresh_mock.assert_called_once_with("cred-1")

        notify_mock.assert_called_once()
        self.assertEqual(notify_mock.call_args.kwargs["action"], "mcp_server_reauth_required")
        self.assertEqual(notify_mock.call_args.kwargs["workspace_id"], "workspace-1")
        self.assertEqual(notify_mock.call_args.kwargs["metadata"]["server_id"], "inventory-feed")
        self.assertEqual(notify_mock.call_args.kwargs["metadata"]["priority"], "high")

        ledger_mock.assert_awaited_once()
        ledger_kwargs = ledger_mock.call_args.kwargs
        self.assertEqual(ledger_kwargs["event_class"], "blocked_action")
        self.assertTrue(ledger_kwargs["review_required"])
        self.assertEqual(ledger_kwargs["workspace_id"], "workspace-1")

        server = mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed")
        self.assertEqual(server.get("status"), "reauth_required")
        self.assertIn("invalid_grant", server.get("status_detail") or "")
        self.assertIsNotNone(server.get("status_updated_at"))

    def test_server_without_credential_skips_refresh_and_still_alerts(self) -> None:
        self._seed_server(credential_id=None)
        call_mock = AsyncMock(side_effect=[_http_status_error(401)])
        refresh_mock = MagicMock()

        started, completed, failed = self._metering_patches_sync()
        with started, completed, failed, \
            patch.object(mcp_registry_service, "_call_streamable_http_tool_async", new=call_mock), \
            patch.object(connection_oauth_service, "refresh_oauth_token_now", new=refresh_mock), \
            patch("server_modules.outbox_service.emit_notification_event") as notify_mock, \
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock:
            with self.assertRaises(RuntimeError):
                self._invoke_sync()

        refresh_mock.assert_not_called()
        notify_mock.assert_called_once()
        ledger_mock.assert_awaited_once()
        self.assertEqual(ledger_mock.call_args.kwargs["metadata"]["reason"], "no_credential")

        server = mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed")
        self.assertEqual(server.get("status"), "reauth_required")

    def test_retry_after_successful_refresh_still_auth_failing_alerts_once(self) -> None:
        self._seed_server()
        call_mock = AsyncMock(side_effect=[_http_status_error(401), _http_status_error(401)])
        refresh_mock = MagicMock(return_value={"ok": True, "credential": {"access_token": "new-but-still-bad"}})

        started, completed, failed = self._metering_patches_sync()
        with started, completed, failed, \
            patch.object(mcp_registry_service, "_call_streamable_http_tool_async", new=call_mock), \
            patch.object(connection_oauth_service, "refresh_oauth_token_now", new=refresh_mock), \
            patch("server_modules.outbox_service.emit_notification_event") as notify_mock, \
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock:
            with self.assertRaises(RuntimeError):
                self._invoke_sync()

        # Exactly one retry attempt total — never loop.
        self.assertEqual(call_mock.await_count, 2)
        refresh_mock.assert_called_once_with("cred-1")
        notify_mock.assert_called_once()
        ledger_mock.assert_awaited_once()

        server = mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed")
        self.assertEqual(server.get("status"), "reauth_required")

    # ── (c) non-auth errors are completely unaffected ──────────────────────

    def test_non_auth_errors_pass_through_unaffected(self) -> None:
        self._seed_server()
        boom = RuntimeError("some unrelated tool failure")
        call_mock = AsyncMock(side_effect=[boom])
        refresh_mock = MagicMock()

        started, completed, failed = self._metering_patches_sync()
        with started, completed, failed, \
            patch.object(mcp_registry_service, "_call_streamable_http_tool_async", new=call_mock), \
            patch.object(connection_oauth_service, "refresh_oauth_token_now", new=refresh_mock), \
            patch("server_modules.outbox_service.emit_notification_event") as notify_mock, \
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock:
            with self.assertRaises(RuntimeError) as ctx:
                self._invoke_sync()

        self.assertIs(ctx.exception, boom)
        self.assertEqual(call_mock.await_count, 1)
        refresh_mock.assert_not_called()
        notify_mock.assert_not_called()
        ledger_mock.assert_not_awaited()

        server = mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed")
        self.assertNotEqual((server or {}).get("status"), "reauth_required")

    def test_http_500_is_also_unaffected(self) -> None:
        self._seed_server()
        call_mock = AsyncMock(side_effect=[_http_status_error(500)])
        refresh_mock = MagicMock()

        started, completed, failed = self._metering_patches_sync()
        with started, completed, failed, \
            patch.object(mcp_registry_service, "_call_streamable_http_tool_async", new=call_mock), \
            patch.object(connection_oauth_service, "refresh_oauth_token_now", new=refresh_mock), \
            patch("server_modules.outbox_service.emit_notification_event") as notify_mock, \
            patch("server_modules.activity_ledger_service.append_activity_event", new=AsyncMock()) as ledger_mock:
            with self.assertRaises(httpx.HTTPStatusError):
                self._invoke_sync()

        refresh_mock.assert_not_called()
        notify_mock.assert_not_called()
        ledger_mock.assert_not_awaited()

    # ── reconnect clears the flag (closes the loop the alert opened) ──────

    def test_reconnect_upsert_with_new_credential_clears_reauth_required(self) -> None:
        self._seed_server()
        mcp_registry_service._mark_mcp_server_reauth_required(
            workspace_id="workspace-1",
            server=mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed"),
            reason="invalid_grant",
            detail="refresh_token was revoked",
        )
        flagged = mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed")
        self.assertEqual(flagged.get("status"), "reauth_required")

        # The owner reconnects: OAuth flow re-runs, landing a fresh
        # credential_id via the same upsert_workspace_mcp_server() path
        # connection_oauth_service._register_mcp_servers_for_provider() uses.
        mcp_registry_service.upsert_workspace_mcp_server(
            workspace_id="workspace-1",
            server_id="inventory-feed",
            label="Inventory Feed",
            transport="streamable_http",
            endpoint="https://example.com/mcp",
            enabled=True,
            credential_id="cred-2",
        )
        reconnected = mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed")
        self.assertEqual(reconnected.get("status"), "ok")

    def test_unrelated_resave_does_not_disturb_reauth_required(self) -> None:
        self._seed_server()
        mcp_registry_service._mark_mcp_server_reauth_required(
            workspace_id="workspace-1",
            server=mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed"),
            reason="invalid_grant",
            detail="refresh_token was revoked",
        )
        # A plain tool-approval edit (no credential change) must not
        # silently clear a flag the owner hasn't actually fixed yet.
        mcp_registry_service.approve_mcp_tool(
            workspace_id="workspace-1", server_id="inventory-feed", tool_name="get_thing"
        )
        server = mcp_registry_service.get_workspace_mcp_server("workspace-1", "inventory-feed")
        self.assertEqual(server.get("status"), "reauth_required")


# ═══════════════════════════════════════════════════════════════════════════
# connection_oauth_service.refresh_oauth_token_now — the ONE refresh
# implementation, forced-refresh entrypoint
# ═══════════════════════════════════════════════════════════════════════════


class RefreshOauthTokenNowTests(unittest.TestCase):
    def test_success_updates_vault_and_returns_refreshed_credential(self) -> None:
        credential = {
            "provider": "linear",
            "refresh_token": "old-refresh",
            "access_token": "old-access",
        }
        update_mock = MagicMock()
        with patch("server_modules.vault_helpers.resolve_vault_credential", return_value=dict(credential)), \
            patch.object(
                connection_oauth_service, "_resolve_oauth_client_for_refresh", return_value=("cid", "secret")
            ), \
            patch.object(
                connection_oauth_service,
                "_post_form_json",
                return_value={"access_token": "new-access", "expires_in": 3600, "refresh_token": "new-refresh"},
            ), \
            patch("server_modules.vault_store.update_credential_secret", new=update_mock), \
            patch("server_modules.vault_store._openssl_encrypt", return_value="cipher"):
            outcome = connection_oauth_service.refresh_oauth_token_now("cred-1")

        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["credential"]["access_token"], "new-access")
        self.assertEqual(outcome["credential"]["refresh_token"], "new-refresh")
        update_mock.assert_called_once()
        self.assertEqual(update_mock.call_args.kwargs["encrypted_secret"], "cipher")

    def test_no_refresh_token_fails_fast_without_a_network_call(self) -> None:
        credential = {"provider": "linear", "access_token": "old-access"}
        post_mock = MagicMock()
        with patch("server_modules.vault_helpers.resolve_vault_credential", return_value=dict(credential)), \
            patch.object(connection_oauth_service, "_post_form_json", new=post_mock):
            outcome = connection_oauth_service.refresh_oauth_token_now("cred-1")

        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["reason"], "no_refresh_token")
        post_mock.assert_not_called()

    def test_provider_rejects_refresh_reports_failure_not_exception(self) -> None:
        credential = {"provider": "linear", "refresh_token": "old-refresh"}
        with patch("server_modules.vault_helpers.resolve_vault_credential", return_value=dict(credential)), \
            patch.object(
                connection_oauth_service, "_resolve_oauth_client_for_refresh", return_value=("cid", "secret")
            ), \
            patch.object(
                connection_oauth_service,
                "_post_form_json",
                side_effect=RuntimeError("400 invalid_grant"),
            ):
            outcome = connection_oauth_service.refresh_oauth_token_now("cred-1")

        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["reason"], "refresh_request_failed")
        self.assertIn("invalid_grant", outcome["detail"])

    def test_no_credential_id_short_circuits(self) -> None:
        outcome = connection_oauth_service.refresh_oauth_token_now("")
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["reason"], "no_credential_id")

    def test_opportunistic_refresh_and_forced_refresh_share_one_implementation(self) -> None:
        # refresh_oauth_token_if_needed() (expiry-gated) and
        # refresh_oauth_token_now() (forced) must both bottom out in
        # _perform_oauth_refresh — asserting this directly guards against a
        # future edit accidentally forking a second implementation.
        import inspect

        source_if_needed = inspect.getsource(connection_oauth_service.refresh_oauth_token_if_needed)
        source_now = inspect.getsource(connection_oauth_service.refresh_oauth_token_now)
        self.assertIn("_perform_oauth_refresh(", source_if_needed)
        self.assertIn("_perform_oauth_refresh(", source_now)


if __name__ == "__main__":
    unittest.main()
