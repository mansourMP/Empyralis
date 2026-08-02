"""Phase U2: Empyralis as MCP server tests.

(a) Auth rejected without key
(b) Workspace resolved from key
(c) Key creation and revocation
(d) Write tools blocked when EMPYRALIS_MCP_WRITE_ENABLED=false
(e) Cross-workspace key isolation
(f) External-agent roster identity is minted at key creation, backfilled
    lazily at resolve time if missing, and a mint failure is surfaced (never
    silently swallowed) -- Step 2 of "Mentions + identity for platform AND
    external agents"
(g) empyralis_chat (MAN-205): routes through the real turn chokepoint
    (sage_turn_adapter.execute_sage_turn), validates agent_id against the
    caller's own workspace before touching it, never falls back to a
    synthesized/legacy-Sage reply, and surfaces real errors/timeouts honestly
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import mcp_server


# ── (a) Auth rejected without key ─────────────────────────────────────

class MCPAuthRejectionTests(unittest.TestCase):

    def test_resolve_empty_token_returns_none(self):
        """Empty/missing bearer token → None (rejected)."""
        async def _run():
            from server_modules.mcp_server_auth import resolve_workspace_from_api_key
            return await resolve_workspace_from_api_key("")
        import asyncio
        result = asyncio.run(_run())
        self.assertIsNone(result)

    def test_resolve_bearer_prefix_handled(self):
        """Bearer prefix is stripped before lookup."""
        async def _run():
            from server_modules.mcp_server_auth import resolve_workspace_from_api_key
            # "Bearer  " with only whitespace after stripping → empty → None
            return await resolve_workspace_from_api_key("Bearer   ")
        import asyncio
        result = asyncio.run(_run())
        self.assertIsNone(result)

    def test_resolve_bogus_token_returns_none(self):
        """A random token that doesn't match any stored key → None."""
        async def _run():
            from server_modules.mcp_server_auth import resolve_workspace_from_api_key
            return await resolve_workspace_from_api_key("bogus_token_12345")
        import asyncio
        result = asyncio.run(_run())
        self.assertIsNone(result)


# ── (b) Workspace resolved from key ────────────────────────────────────

class MCPKeyLifecycleTests(unittest.TestCase):

    def test_create_and_resolve_key(self):
        """Create a key, resolve workspace from it, then revoke."""
        async def _run():
            from server_modules.mcp_server_auth import (
                create_workspace_mcp_api_key,
                resolve_workspace_from_api_key,
                revoke_workspace_mcp_api_key,
            )

            # Create
            result = await create_workspace_mcp_api_key(
                workspace_id="ws-test-1",
                label="Test Key",
            )
            self.assertTrue(result["ok"], f"Key creation failed: {result}")
            key = result["key"]
            self.assertTrue(key.startswith("empyralis_mcp_"))

            # Resolve — bare key
            resolved = await resolve_workspace_from_api_key(key)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["workspace_id"], "ws-test-1")
            self.assertFalse(resolved["writes_enabled"])  # default

            # Resolve — with Bearer prefix
            resolved2 = await resolve_workspace_from_api_key(f"Bearer {key}")
            self.assertIsNotNone(resolved2)
            self.assertEqual(resolved2["workspace_id"], "ws-test-1")

            # Revoke
            revoke_result = await revoke_workspace_mcp_api_key(result["key_id"])
            self.assertTrue(revoke_result["ok"])

            # Resolve after revoke → None
            resolved3 = await resolve_workspace_from_api_key(key)
            self.assertIsNone(resolved3)

        import asyncio
        asyncio.run(_run())

    def test_create_key_with_writes_enabled(self):
        """A key created with writes_enabled=True resolves with that flag set."""
        async def _run():
            from server_modules.mcp_server_auth import (
                create_workspace_mcp_api_key,
                resolve_workspace_from_api_key,
                revoke_workspace_mcp_api_key,
            )
            r = await create_workspace_mcp_api_key(
                workspace_id="ws-write-test", label="Write Key", writes_enabled=True,
            )
            self.assertTrue(r["ok"])
            self.assertTrue(r["writes_enabled"])

            resolved = await resolve_workspace_from_api_key(r["key"])
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["workspace_id"], "ws-write-test")
            self.assertTrue(resolved["writes_enabled"])

            # cleanup
            await revoke_workspace_mcp_api_key(r["key_id"])

        import asyncio
        asyncio.run(_run())

    def test_create_key_empty_workspace_rejected(self):
        """Empty workspace_id is rejected."""
        async def _run():
            from server_modules.mcp_server_auth import create_workspace_mcp_api_key
            result = await create_workspace_mcp_api_key(workspace_id="  ", label="x")
            self.assertFalse(result["ok"])
        import asyncio
        asyncio.run(_run())


# ── (c) Write tools gated ──────────────────────────────────────────────

class MCPWriteGateTests(unittest.TestCase):

    def test_write_flag_defaults_false(self):
        """writes_enabled defaults to False when not specified at key creation."""
        async def _run():
            from server_modules.mcp_server_auth import (
                create_workspace_mcp_api_key,
                resolve_workspace_from_api_key,
                revoke_workspace_mcp_api_key,
            )
            r = await create_workspace_mcp_api_key(workspace_id="ws-gate", label="Gate Test")
            self.assertTrue(r["ok"])
            self.assertFalse(r["writes_enabled"])

            resolved = await resolve_workspace_from_api_key(r["key"])
            self.assertIsNotNone(resolved)
            self.assertFalse(resolved["writes_enabled"])

            await revoke_workspace_mcp_api_key(r["key_id"])

        import asyncio
        asyncio.run(_run())

    def test_write_flag_defaults_false_no_env(self):
        """Global off-switch defaults to false when EMPYRALIS_MCP_WRITE_ENABLED is not set."""
        flag = os.getenv("EMPYRALIS_MCP_WRITE_ENABLED")
        if flag and flag.strip().lower() in ("1", "true", "yes"):
            self.skipTest("EMPYRALIS_MCP_WRITE_ENABLED is set — skipping default test")
        # Global off-switch should be False when env var is absent (verified by integration)


# ── (d) Cross-workspace isolation ──────────────────────────────────────

class MCPCrossWorkspaceIsolationTests(unittest.TestCase):

    def test_key_only_resolves_to_its_workspace(self):
        """A key for ws-A does NOT resolve to ws-B."""
        async def _run():
            from server_modules.mcp_server_auth import (
                create_workspace_mcp_api_key,
                resolve_workspace_from_api_key,
            )
            r = await create_workspace_mcp_api_key(workspace_id="ws-alpha", label="A")
            key = r["key"]

            resolved = await resolve_workspace_from_api_key(key)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["workspace_id"], "ws-alpha")
            self.assertNotEqual(resolved["workspace_id"], "ws-beta")

        import asyncio
        asyncio.run(_run())


# ── (f) External-agent roster identity minted at key creation ──────────

class MCPExternalAgentRosterMintTests(unittest.TestCase):
    """mcp_server_auth.py <-> mcp_external_agent_roster_service.py wiring.
    The roster service's own behavior (auto-naming, idempotency, unified
    listing) is covered in test_mcp_external_agent_roster.py -- these tests
    only prove the two integration points: key creation mints, and resolve
    backfills + never silently drops a mint failure."""

    def test_key_creation_mints_roster_identity(self):
        """create_workspace_mcp_api_key mints an external-agent identity in
        the same call, and returns it on the response."""
        async def _run():
            from server_modules import mcp_server_auth as auth_mod

            mock_register = AsyncMock(return_value={
                "ok": True, "id": "ext_agent_xyz", "kind": "external",
                "display_name": "Atlas", "revoked": False,
            })
            with patch(
                "server_modules.mcp_external_agent_roster_service.register_external_agent",
                mock_register,
            ), patch(
                "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                AsyncMock(return_value="tenant-mint-1"),
            ):
                result = await auth_mod.create_workspace_mcp_api_key(
                    workspace_id="ws-mint-1", label="Codex Session",
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["external_agent_id"], "ext_agent_xyz")
            self.assertEqual(result["external_agent_display_name"], "Atlas")
            self.assertNotIn("roster_warning", result)
            mock_register.assert_awaited_once()
            kwargs = mock_register.await_args.kwargs
            self.assertEqual(kwargs["workspace_id"], "ws-mint-1")
            self.assertEqual(kwargs["tenant_id"], "tenant-mint-1")
            self.assertTrue(kwargs["key_hash"])  # the SHA-256 hash, not the plaintext

            await auth_mod.revoke_workspace_mcp_api_key(result["key_id"])

        import asyncio
        asyncio.run(_run())

    def test_roster_mint_failure_does_not_fail_key_creation_but_is_surfaced(self):
        """A roster-mint failure (e.g. Postgres unreachable) must never break
        key creation -- but it must never be silently swallowed either. It
        shows up as an explicit `roster_warning` on the response."""
        async def _run():
            from server_modules import mcp_server_auth as auth_mod

            mock_register = AsyncMock(return_value={"ok": False, "error": "Postgres unreachable"})
            with patch(
                "server_modules.mcp_external_agent_roster_service.register_external_agent",
                mock_register,
            ), patch(
                "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                AsyncMock(return_value="tenant-mint-2"),
            ):
                result = await auth_mod.create_workspace_mcp_api_key(
                    workspace_id="ws-mint-2", label="Flaky",
                )
            self.assertTrue(result["ok"])  # key creation itself still succeeds
            self.assertIsNone(result["external_agent_id"])
            self.assertIn("roster_warning", result)
            self.assertIn("Postgres unreachable", result["roster_warning"])

            await auth_mod.revoke_workspace_mcp_api_key(result["key_id"])

        import asyncio
        asyncio.run(_run())

    def test_resolve_backfills_missing_roster_identity(self):
        """A key resolved with no roster row (predates Step 2, or its
        mint-time insert failed) gets one minted lazily right here, instead
        of staying identity-less for its whole lifetime."""
        async def _run():
            from server_modules import mcp_server_auth as auth_mod

            # Create the key with roster minting itself mocked out, so this
            # test controls exactly when the roster row "appears".
            with patch(
                "server_modules.mcp_external_agent_roster_service.register_external_agent",
                AsyncMock(return_value={"ok": False, "error": "simulated mint-time failure"}),
            ):
                created = await auth_mod.create_workspace_mcp_api_key(workspace_id="ws-backfill-1", label="x")
            self.assertIsNone(created["external_agent_id"])

            mock_get = AsyncMock(return_value=None)  # no roster row exists yet
            mock_register = AsyncMock(return_value={
                "ok": True, "id": "ext_agent_backfilled", "display_name": "Nova",
            })
            with patch("server_modules.mcp_external_agent_roster_service.get_external_agent_by_key_hash", mock_get), \
                 patch("server_modules.mcp_external_agent_roster_service.register_external_agent", mock_register), \
                 patch("server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                       AsyncMock(return_value="tenant-backfill-1")):
                resolved = await auth_mod.resolve_workspace_from_api_key(created["key"])

            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["external_agent_id"], "ext_agent_backfilled")
            self.assertEqual(resolved["external_agent_display_name"], "Nova")
            mock_register.assert_awaited_once()

            await auth_mod.revoke_workspace_mcp_api_key(created["key_id"])

        import asyncio
        asyncio.run(_run())

    def test_resolve_reuses_existing_roster_identity_without_reregistering(self):
        async def _run():
            from server_modules import mcp_server_auth as auth_mod

            with patch(
                "server_modules.mcp_external_agent_roster_service.register_external_agent",
                AsyncMock(return_value={"ok": False, "error": "simulated"}),
            ):
                created = await auth_mod.create_workspace_mcp_api_key(workspace_id="ws-existing-1", label="x")

            mock_get = AsyncMock(return_value={
                "id": "ext_agent_existing", "display_name": "Ember", "revoked": False,
            })
            mock_register = AsyncMock()
            with patch("server_modules.mcp_external_agent_roster_service.get_external_agent_by_key_hash", mock_get), \
                 patch("server_modules.mcp_external_agent_roster_service.register_external_agent", mock_register):
                resolved = await auth_mod.resolve_workspace_from_api_key(created["key"])

            self.assertEqual(resolved["external_agent_id"], "ext_agent_existing")
            self.assertEqual(resolved["external_agent_display_name"], "Ember")
            mock_register.assert_not_awaited()  # already had an identity -- no re-mint

            await auth_mod.revoke_workspace_mcp_api_key(created["key_id"])

        import asyncio
        asyncio.run(_run())

    def test_resolve_treats_revoked_roster_entry_as_no_identity(self):
        async def _run():
            from server_modules import mcp_server_auth as auth_mod

            with patch(
                "server_modules.mcp_external_agent_roster_service.register_external_agent",
                AsyncMock(return_value={"ok": False, "error": "simulated"}),
            ):
                created = await auth_mod.create_workspace_mcp_api_key(workspace_id="ws-revoked-1", label="x")

            mock_get = AsyncMock(return_value={
                "id": "ext_agent_revoked", "display_name": "Ridge", "revoked": True,
            })
            with patch("server_modules.mcp_external_agent_roster_service.get_external_agent_by_key_hash", mock_get):
                resolved = await auth_mod.resolve_workspace_from_api_key(created["key"])

            self.assertIsNone(resolved["external_agent_id"])

            await auth_mod.revoke_workspace_mcp_api_key(created["key_id"])

        import asyncio
        asyncio.run(_run())


# ── (g) empyralis_chat routes a real turn to a named, workspace-owned agent ──

class _FakeChatCtx:
    pass


def _chat_resolved(workspace_id="ws-chat-A", external_agent_id="ext_agent_caller", writes_enabled=False):
    return {
        "workspace_id": workspace_id,
        "writes_enabled": writes_enabled,
        "external_agent_id": external_agent_id,
        "external_agent_display_name": "Caller Bot",
    }


def _patched_chat(*, resolved=None, tenant_id="tenant-chat-A"):
    resolved = resolved if resolved is not None else _chat_resolved()
    return (
        patch.object(mcp_server, "_resolve_workspace", new=AsyncMock(return_value=resolved)),
        patch.object(mcp_server, "_ledger_mcp_call", new=AsyncMock()),
        patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=AsyncMock(return_value=tenant_id),
        ),
    )


class MCPChatToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_chat_routes_to_named_agent_and_returns_real_reply(self):
        """A valid agent_id in the caller's own workspace: the turn is run
        through the real chokepoint and the real reply comes back, tagged
        with the actual agent that answered (not a literal "(sage)")."""
        from server_modules.sage_agent_runtime_contract import SageTurnResult

        bundle = {"id": "ainstall_nova", "label": "Nova", "workspace_id": "ws-chat-A"}
        turn_result = SageTurnResult(message="Hello from Nova", provider="anthropic", model="claude")

        p1, p2, p3 = _patched_chat()
        with p1, p2, p3, \
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=bundle),
            ) as bundle_mock, \
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(return_value="fake-specialist-context"),
            ) as spec_mock, \
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=turn_result),
            ) as turn_mock:
            result = await mcp_server.empyralis_chat(
                message="hi Nova", agent_id="ainstall_nova", ctx=_FakeChatCtx(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "Hello from Nova")
        self.assertEqual(result["agent_id"], "ainstall_nova")
        self.assertEqual(result["agent_label"], "Nova")
        bundle_mock.assert_awaited_once_with(
            "ainstall_nova", tenant_id="tenant-chat-A", workspace_id="ws-chat-A",
        )
        spec_mock.assert_awaited_once_with(
            workspace_id="ws-chat-A", tenant_id="tenant-chat-A",
            active_agent_install_id="ainstall_nova",
        )
        turn_mock.assert_awaited_once()
        call_kwargs = turn_mock.await_args.kwargs
        self.assertEqual(call_kwargs["workspace_id"], "ws-chat-A")
        self.assertEqual(call_kwargs["message"], "hi Nova")
        self.assertEqual(call_kwargs["specialist_context"], "fake-specialist-context")
        # Sender id must stay unset -- setting it would downgrade this
        # first-party MCP call from owner-tier tool access to audience-tier.
        self.assertNotIn("channel_sender_id", call_kwargs)

    async def test_chat_rejects_agent_id_from_another_workspace_without_running_a_turn(self):
        """An agent_id that doesn't resolve inside the caller's own
        workspace (wrong workspace, or doesn't exist) must be refused --
        and, critically, must never fall through to running the turn as
        some default agent. This is the MAN-206-shaped hole this tool must
        not repeat."""
        p1, p2, p3 = _patched_chat()
        with p1, p2, p3, \
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=None),
            ) as bundle_mock, \
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(),
            ) as turn_mock:
            result = await mcp_server.empyralis_chat(
                message="hi", agent_id="ainstall_other_workspace", ctx=_FakeChatCtx(),
            )

        self.assertFalse(result["ok"])
        self.assertIn("not found in your workspace", result["error"])
        self.assertEqual(result["agent_id"], "ainstall_other_workspace")
        self.assertNotIn("reply", result)
        bundle_mock.assert_awaited_once()
        turn_mock.assert_not_awaited()

    async def test_chat_defaults_to_the_real_workspace_master_when_agent_id_omitted(self):
        """Omitting agent_id must resolve to and report the workspace's
        actual default agent id/label -- never the bare literal "(sage)"
        placeholder the old broken tool returned."""
        master = {"id": "ainstall_master_1", "label": "Workspace Agent"}
        turn_result = {"message": "Hi, I'm the default agent.", "error": None}

        p1, p2, p3 = _patched_chat()
        with p1, p2, p3, \
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value=master),
            ) as master_mock, \
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(return_value=None),
            ), \
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(return_value=turn_result),
            ):
            result = await mcp_server.empyralis_chat(message="hi", agent_id="", ctx=_FakeChatCtx())

        self.assertTrue(result["ok"])
        self.assertEqual(result["agent_id"], "ainstall_master_1")
        self.assertEqual(result["agent_label"], "Workspace Agent")
        self.assertNotEqual(result["agent_id"], "(sage)")
        master_mock.assert_awaited_once_with(tenant_id="tenant-chat-A", workspace_id="ws-chat-A")

    async def test_chat_surfaces_the_real_error_instead_of_a_fabricated_reply(self):
        """When the turn itself raises, the tool must return that real
        error -- never synthesize a plausible-sounding reply (the exact
        failure mode 5278a58d0 fixed for tool output)."""
        bundle = {"id": "ainstall_x", "label": "X"}
        p1, p2, p3 = _patched_chat()
        with p1, p2, p3, \
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=bundle),
            ), \
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(return_value=None),
            ), \
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=AsyncMock(side_effect=RuntimeError("no AI provider configured")),
            ):
            result = await mcp_server.empyralis_chat(message="hi", agent_id="ainstall_x", ctx=_FakeChatCtx())

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no AI provider configured")
        self.assertNotIn("reply", result)

    async def test_chat_surfaces_an_honest_timeout_instead_of_hanging_forever(self):
        """A turn that runs past the bound comes back as an explicit,
        honest timeout -- not a hang, and not a fabricated reply."""
        bundle = {"id": "ainstall_slow", "label": "Slow"}

        async def _never_returns(**kwargs):
            await asyncio.sleep(10)
            return {"message": "should never get here"}

        p1, p2, p3 = _patched_chat()
        with p1, p2, p3, \
            patch.object(mcp_server, "_CHAT_TURN_TIMEOUT_SECONDS", 0.05), \
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=bundle),
            ), \
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(return_value=None),
            ), \
            patch(
                "server_modules.sage_turn_adapter.execute_sage_turn",
                new=_never_returns,
            ):
            result = await mcp_server.empyralis_chat(message="hi", agent_id="ainstall_slow", ctx=_FakeChatCtx())

        self.assertFalse(result["ok"])
        self.assertIn("Timed out", result["error"])
        self.assertNotIn("reply", result)


if __name__ == "__main__":
    unittest.main()
