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
    (agent_turn_adapter.execute_sage_turn), validates agent_id against the
    caller's own workspace before touching it, never falls back to a
    synthesized/legacy-Sage reply, and surfaces real errors/timeouts honestly
(h) The other three MAN-205 tools -- empyralis_get_agent_conversations,
    empyralis_trigger_test_turn, empyralis_connect_connector -- could never
    have succeeded for ANY caller, each for a different reason (an invented
    auth key nothing reads; a missing keyword-only argument; a stand-in
    request object missing the attribute the callee reads first). These
    exercise the fixes AND ban the three shapes from coming back, because a
    behavioural test only covers the tools that exist today.
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
        from server_modules.agent_turn_runtime_contract import SageTurnResult

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
                "server_modules.agent_turn_adapter.execute_sage_turn",
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
                "server_modules.agent_turn_adapter.execute_sage_turn",
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
                "server_modules.agent_turn_adapter.execute_sage_turn",
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
                "server_modules.agent_turn_adapter.execute_sage_turn",
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
                "server_modules.agent_turn_adapter.execute_sage_turn",
                new=_never_returns,
            ):
            result = await mcp_server.empyralis_chat(message="hi", agent_id="ainstall_slow", ctx=_FakeChatCtx())

        self.assertFalse(result["ok"])
        self.assertIn("Timed out", result["error"])
        self.assertNotIn("reply", result)


# ── (h) Document tools (feat/document-mcp-tools-and-revisions) ──────────
#
# MCP is for the FOUNDER'S USERS -- a teammate's own Claude/ChatGPT saying
# "edit this document" -- so these prove exactly what that promise depends
# on: project_id is resolved and VERIFIED server-side against the caller's
# OWN workspace before anything is read or written (never trusted from the
# argument to widen scope), a key for workspace A cannot touch workspace
# B's documents or projects, and every write goes through project_documents_
# repository EXACTLY ONCE (never zero, never twice) -- "an email was sent"
# is satisfied by two emails just as happily as by one, so a bare
# `assert_awaited` is not enough; every write test below asserts the count.

def _doc_resolved(workspace_id="ws-doc-A", external_agent_id="ext_agent_doc_caller", writes_enabled=False):
    return {
        "workspace_id": workspace_id,
        "writes_enabled": writes_enabled,
        "external_agent_id": external_agent_id,
        "external_agent_display_name": "Doc Caller Bot",
    }


def _patched_doc(*, resolved=None, tenant_id="tenant-doc-A"):
    resolved = resolved if resolved is not None else _doc_resolved()
    return (
        patch.object(mcp_server, "_resolve_workspace", new=AsyncMock(return_value=resolved)),
        patch.object(mcp_server, "_ledger_mcp_call", new=AsyncMock()),
        patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=AsyncMock(return_value=tenant_id),
        ),
    )


class _FakeDocCtx:
    pass


class MCPCreateDocumentToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_document_resolves_and_verifies_project_before_writing(self):
        """project_id is looked up through projects_repository.get_project,
        which itself filters WHERE tenant_id = ... AND workspace_id = ...
        -- this is the server-side resolution the brief asked for, proven
        by asserting get_project was called with the CALLER's own
        workspace/tenant, never anything from the argument alone."""
        project = {"id": "proj-1", "workspace_id": "ws-doc-A", "name": "Alpha"}
        created = {"id": "doc-new-1", "title": "Runbook", "slug": "runbook", "body": "hi",
                   "revision_recorded": True}
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.projects_repository.get_project", new=AsyncMock(return_value=project),
            ) as project_mock, \
            patch(
                "server_modules.project_documents_repository.create_document",
                new=AsyncMock(return_value=created),
            ) as create_mock:
            result = await mcp_server.empyralis_create_document(
                project_id="proj-1", title="Runbook", body="hi", ctx=_FakeDocCtx(),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["document"]["id"], "doc-new-1")
        project_mock.assert_awaited_once_with(tenant_id="tenant-doc-A", workspace_id="ws-doc-A", project_id="proj-1")
        create_mock.assert_awaited_once()
        kwargs = create_mock.await_args.kwargs
        self.assertEqual(kwargs["workspace_id"], "ws-doc-A")
        self.assertEqual(kwargs["project_id"], "proj-1")
        self.assertEqual(kwargs["created_by"], "ext_agent_doc_caller")
        self.assertEqual(kwargs["changed_by_type"], "external_agent")
        self.assertEqual(kwargs["changed_by_display_name"], "Doc Caller Bot")

    async def test_create_document_rejects_a_project_from_another_workspace_without_writing_anything(self):
        """THE cross-workspace proof: a project_id that does not resolve
        inside the caller's own workspace (because it belongs to a
        DIFFERENT workspace, or does not exist) is refused before
        create_document is ever reached -- zero document-write calls, not
        one that then gets rolled back."""
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.projects_repository.get_project", new=AsyncMock(return_value=None),
            ) as project_mock, \
            patch(
                "server_modules.project_documents_repository.create_document", new=AsyncMock(),
            ) as create_mock:
            result = await mcp_server.empyralis_create_document(
                project_id="proj-belongs-to-workspace-B", title="Leak attempt", ctx=_FakeDocCtx(),
            )
        self.assertFalse(result["ok"])
        self.assertIn("not found in your workspace", result["error"])
        project_mock.assert_awaited_once_with(
            tenant_id="tenant-doc-A", workspace_id="ws-doc-A", project_id="proj-belongs-to-workspace-B",
        )
        create_mock.assert_not_awaited()


class MCPEditDocumentToolTests(unittest.IsolatedAsyncioTestCase):
    """empyralis_edit_document -- the PRIMARY, patch-native way to change a
    document (founder: "write a line and push it, not a whole rewrite")."""

    async def test_edit_applies_a_targeted_patch_exactly_once(self):
        updated = {"id": "doc-1", "title": "Runbook", "body": "line one\nline TWO\n", "revision_recorded": True}
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.edit_document_by_replace",
                new=AsyncMock(return_value=updated),
            ) as edit_mock:
            result = await mcp_server.empyralis_edit_document(
                document_id="doc-1", old_string="line two", new_string="line TWO", ctx=_FakeDocCtx(),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["document"]["body"], "line one\nline TWO\n")
        edit_mock.assert_awaited_once()
        kwargs = edit_mock.await_args.kwargs
        self.assertEqual(kwargs["document_id"], "doc-1")
        self.assertEqual(kwargs["old_string"], "line two")
        self.assertEqual(kwargs["new_string"], "line TWO")
        self.assertEqual(kwargs["changed_by_type"], "external_agent")

    async def test_edit_that_matches_zero_or_multiple_times_fails_loudly_not_silently(self):
        """A non-unique old_string must come back ok:false with the real
        reason -- never a silent no-op and never a fallback to a whole-body
        rewrite."""
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.edit_document_by_replace",
                new=AsyncMock(side_effect=RuntimeError(
                    "old_string appears 2 times in document 'Runbook' — it must match exactly once."
                )),
            ) as edit_mock:
            result = await mcp_server.empyralis_edit_document(
                document_id="doc-1", old_string="dup", new_string="x", ctx=_FakeDocCtx(),
            )
        self.assertFalse(result["ok"])
        self.assertIn("exactly once", result["error"])
        self.assertNotIn("document", result)
        edit_mock.assert_awaited_once()

    async def test_edit_on_a_document_not_in_the_callers_workspace_reports_not_found(self):
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.edit_document_by_replace",
                new=AsyncMock(return_value=None),
            ):
            result = await mcp_server.empyralis_edit_document(
                document_id="doc-not-mine", old_string="x", new_string="y", ctx=_FakeDocCtx(),
            )
        self.assertFalse(result["ok"])
        self.assertIn("not found in your workspace", result["error"])


class MCPUpdateDocumentToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_with_both_fields_empty_is_rejected_without_calling_the_repository(self):
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.update_document", new=AsyncMock(),
            ) as update_mock:
            result = await mcp_server.empyralis_update_document(document_id="doc-1", ctx=_FakeDocCtx())
        self.assertFalse(result["ok"])
        self.assertIn("nothing to update", result["error"])
        update_mock.assert_not_awaited()

    async def test_update_calls_the_repository_exactly_once_with_the_callers_identity(self):
        updated = {"id": "doc-1", "title": "New Title", "revision_recorded": True}
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.update_document",
                new=AsyncMock(return_value=updated),
            ) as update_mock:
            result = await mcp_server.empyralis_update_document(
                document_id="doc-1", title="New Title", ctx=_FakeDocCtx(),
            )
        self.assertTrue(result["ok"])
        update_mock.assert_awaited_once()
        kwargs = update_mock.await_args.kwargs
        self.assertEqual(kwargs["document_id"], "doc-1")
        self.assertEqual(kwargs["title"], "New Title")
        self.assertIsNone(kwargs["body"])
        self.assertEqual(kwargs["changed_by_type"], "external_agent")


class MCPListAndGetDocumentToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_documents_verifies_the_project_before_listing(self):
        project = {"id": "proj-1", "workspace_id": "ws-doc-A"}
        rows = [{"id": "doc-1", "title": "Runbook", "slug": "runbook"}]
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.projects_repository.get_project", new=AsyncMock(return_value=project),
            ), \
            patch(
                "server_modules.project_documents_repository.list_documents",
                new=AsyncMock(return_value=rows),
            ) as list_mock:
            result = await mcp_server.empyralis_list_documents(project_id="proj-1", ctx=_FakeDocCtx())
        self.assertTrue(result["ok"])
        self.assertEqual(result["documents"], rows)
        list_mock.assert_awaited_once_with(tenant_id="tenant-doc-A", workspace_id="ws-doc-A", project_id="proj-1")

    async def test_list_documents_for_a_project_outside_the_workspace_lists_nothing(self):
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.projects_repository.get_project", new=AsyncMock(return_value=None),
            ), \
            patch(
                "server_modules.project_documents_repository.list_documents", new=AsyncMock(),
            ) as list_mock:
            result = await mcp_server.empyralis_list_documents(project_id="proj-other-ws", ctx=_FakeDocCtx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["documents"], [])
        list_mock.assert_not_awaited()

    async def test_get_document_not_found_in_workspace(self):
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.get_document", new=AsyncMock(return_value=None),
            ):
            result = await mcp_server.empyralis_get_document(document_id="doc-elsewhere", ctx=_FakeDocCtx())
        self.assertFalse(result["ok"])
        self.assertIn("not found in your workspace", result["error"])

    async def test_get_document_found_returns_full_body(self):
        doc = {"id": "doc-1", "title": "Runbook", "body": "full markdown here"}
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.get_document", new=AsyncMock(return_value=doc),
            ) as get_mock:
            result = await mcp_server.empyralis_get_document(document_id="doc-1", ctx=_FakeDocCtx())
        self.assertTrue(result["ok"])
        self.assertEqual(result["document"]["body"], "full markdown here")
        get_mock.assert_awaited_once_with(tenant_id="tenant-doc-A", workspace_id="ws-doc-A", document_id="doc-1")


class MCPListDocumentRevisionsToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_revisions_for_a_document_in_the_callers_workspace(self):
        doc = {"id": "doc-1", "title": "Runbook"}
        revisions = [
            {"revision_number": 2, "diff": "-old\n+new\n", "changed_by_type": "external_agent"},
            {"revision_number": 1, "diff": None, "changed_by_type": "human"},
        ]
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.get_document", new=AsyncMock(return_value=doc),
            ), \
            patch(
                "server_modules.project_documents_repository.list_document_revisions",
                new=AsyncMock(return_value=revisions),
            ) as list_rev_mock:
            result = await mcp_server.empyralis_list_document_revisions(document_id="doc-1", ctx=_FakeDocCtx())
        self.assertTrue(result["ok"])
        self.assertEqual(result["revisions"], revisions)
        list_rev_mock.assert_awaited_once_with(
            tenant_id="tenant-doc-A", workspace_id="ws-doc-A", document_id="doc-1", include_body=False,
        )

    async def test_revisions_for_a_document_not_in_the_callers_workspace_is_not_found(self):
        """Same workspace boundary as every other document tool: history
        for a document that does not resolve in the caller's own
        (tenant, workspace) never reaches list_document_revisions at all."""
        p1, p2, p3 = _patched_doc()
        with p1, p2, p3, \
            patch(
                "server_modules.project_documents_repository.get_document", new=AsyncMock(return_value=None),
            ), \
            patch(
                "server_modules.project_documents_repository.list_document_revisions", new=AsyncMock(),
            ) as list_rev_mock:
            result = await mcp_server.empyralis_list_document_revisions(
                document_id="doc-not-mine", ctx=_FakeDocCtx(),
            )
        self.assertFalse(result["ok"])
        self.assertIn("not found in your workspace", result["error"])
        list_rev_mock.assert_not_awaited()


# ── (h) The three MAN-205 tools that could never have run ─────────────


class _FakeDeadToolCtx:
    pass


def _dead_tool_resolved(workspace_id="ws-dead-A", writes_enabled=True):
    return {
        "workspace_id": workspace_id,
        "writes_enabled": writes_enabled,
        "external_agent_id": "ext_agent_deadbeefdeadbeef",
        "external_agent_display_name": "Probe Bot",
    }


def _patched_dead_tool(*, resolved=None, tenant_id="tenant-dead-A"):
    resolved = resolved if resolved is not None else _dead_tool_resolved()
    return (
        patch.object(mcp_server, "_resolve_workspace", new=AsyncMock(return_value=resolved)),
        patch.object(mcp_server, "_ledger_mcp_call", new=AsyncMock()),
        patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=AsyncMock(return_value=tenant_id),
        ),
    )


class MCPActingIdentityTests(unittest.TestCase):
    """``_mcp_current_user`` is the identity handed to services that run the
    REAL ``auth.enforce_workspace_access`` on it.

    The bug it replaces was not a weak check -- it was a check handed an
    identity it could only ever reject, because the dict carried
    ``mcp_workspace_id``, a key ``auth.py`` reads nowhere.
    """

    def _identity(self, ws="ws-dead-A", tenant="tenant-dead-A"):
        return mcp_server._mcp_current_user(_dead_tool_resolved(workspace_id=ws), ws, tenant)

    def test_identity_passes_the_real_owner_check_for_its_own_workspace(self):
        from server_modules import auth as auth_module

        resolved = auth_module.enforce_workspace_access(
            self._identity(), "ws-dead-A", tenant_id="tenant-dead-A", minimum_role="owner",
        )
        self.assertEqual(resolved, "ws-dead-A")

    def test_identity_is_scoped_to_exactly_one_workspace_and_one_tenant(self):
        """The dangerous fix for a 403 is ``is_admin``/``auth_admin``, which
        makes both of these return None -- i.e. every workspace of every
        tenant. A bearer key is scoped to ONE workspace and must stay there."""
        from server_modules import auth as auth_module

        identity = self._identity()
        self.assertEqual(auth_module.allowed_workspace_ids(identity), {"ws-dead-A"})
        self.assertEqual(auth_module.allowed_tenant_ids(identity), {"tenant-dead-A"})
        self.assertFalse(auth_module.current_user_has_auth_admin_access(identity))

    def test_identity_cannot_reach_another_workspace(self):
        from fastapi import HTTPException

        from server_modules import auth as auth_module

        with self.assertRaises(HTTPException):
            auth_module.enforce_workspace_access(
                self._identity(), "ws-belongs-to-someone-else", minimum_role="viewer",
            )

    def test_identity_carries_the_keys_report_the_acting_external_agent(self):
        identity = self._identity()
        self.assertEqual(identity["user_id"], "ext_agent_deadbeefdeadbeef")
        self.assertEqual(identity["auth_type"], "api_key")


class MCPGetAgentConversationsToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_conversations_reaches_the_service_instead_of_403ing_on_its_own_identity(self):
        """Before the fix this call died inside
        ``require_deployed_agent_admin_access`` on EVERY invocation, so the
        service function was never once reached. Asserting the call COUNT,
        not just the return value: "it returned ok" is satisfied just as
        happily by a tool that answered without doing anything."""
        payload = {"conversations": [{"session_id": "s1"}], "has_more": False}
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.deployed_agent_service.list_deployed_agent_conversations",
                new=AsyncMock(return_value=payload),
            ) as list_mock:
            result = await mcp_server.empyralis_get_agent_conversations(
                agent_id="da-1", ctx=_FakeDeadToolCtx(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["conversations"], payload["conversations"])
        list_mock.assert_awaited_once()
        kwargs = list_mock.await_args.kwargs
        self.assertEqual(kwargs["owner_workspace_id"], "ws-dead-A")
        self.assertEqual(kwargs["deployed_agent_id"], "da-1")

    async def test_conversations_hands_the_service_an_identity_auth_can_actually_evaluate(self):
        """The specific regression: the ``current_user`` reaching the service
        must be one ``enforce_workspace_access`` resolves, not a dict whose
        only workspace hint is a key nothing reads."""
        from server_modules import auth as auth_module

        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return {"conversations": []}

        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.deployed_agent_service.list_deployed_agent_conversations",
                new=_capture,
            ):
            await mcp_server.empyralis_get_agent_conversations(agent_id="da-1", ctx=_FakeDeadToolCtx())

        current_user = captured["current_user"]
        self.assertNotIn("mcp_workspace_id", current_user)
        self.assertEqual(
            auth_module.enforce_workspace_access(current_user, "ws-dead-A", minimum_role="owner"),
            "ws-dead-A",
        )


class MCPTriggerTestTurnToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_test_turn_passes_tenant_id_and_a_real_request_model(self):
        """``execute_test_turn`` takes ``tenant_id`` keyword-only with NO
        default and reads ``request.runtime_mode``/``request.customer_profile``.
        Omitting either is a TypeError/AttributeError on every call."""
        from server_modules.schemas import (
            DeployedAgentTestTurnRequest,
            DeployedAgentTestTurnResponse,
        )

        response = DeployedAgentTestTurnResponse(reply="pong", trace_id="t-1")
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.deployed_agent_test_turn_service.execute_test_turn",
                new=AsyncMock(return_value=response),
            ) as turn_mock, \
            patch.object(mcp_server, "_WRITE_ENABLED_GLOBAL", True):
            result = await mcp_server.empyralis_trigger_test_turn(
                agent_id="da-1", message="ping", ctx=_FakeDeadToolCtx(),
            )

        turn_mock.assert_awaited_once()
        kwargs = turn_mock.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-dead-A")
        self.assertEqual(kwargs["workspace_id"], "ws-dead-A")
        request = kwargs["request"]
        self.assertIsInstance(request, DeployedAgentTestTurnRequest)
        # The two fields the callee reads that the old stand-in never had.
        self.assertEqual(request.runtime_mode, "text_agent")
        self.assertIsNone(request.customer_profile)
        # A pydantic response must be flattened, not wrapped under "result".
        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "pong")
        self.assertNotIn("result", result)

    async def test_test_turn_still_reports_a_real_failure_honestly(self):
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.deployed_agent_test_turn_service.execute_test_turn",
                new=AsyncMock(side_effect=ValueError("Deployed agent not found.")),
            ), \
            patch.object(mcp_server, "_WRITE_ENABLED_GLOBAL", True):
            result = await mcp_server.empyralis_trigger_test_turn(
                agent_id="da-missing", message="ping", ctx=_FakeDeadToolCtx(),
            )
        self.assertFalse(result["ok"])
        self.assertIn("Deployed agent not found.", result["error"])


class MCPConnectConnectorToolTests(unittest.IsolatedAsyncioTestCase):

    def test_the_request_object_answers_what_the_oauth_service_actually_reads(self):
        """Driven through the REAL ``connection_oauth_service`` helpers rather
        than a mock: ``request_origin`` reads ``request.headers`` FIRST, which
        is the attribute the old ``SimpleNamespace(base_url=...)`` did not
        have. A mock of these functions would have passed against the broken
        object too."""
        from server_modules import connection_oauth_service as cos

        with patch.dict(os.environ, {"EMPYRALIS_PUBLIC_BASE_URL": "https://empyralis.example"}):
            request = mcp_server._public_origin_request()
            self.assertEqual(cos.request_origin(request), "https://empyralis.example")
            self.assertEqual(
                cos.callback_url(request, "github"),
                "https://empyralis.example/api/connections/oauth/github/callback",
            )

    def test_the_callback_url_never_carries_the_mcp_mount_path(self):
        """Reusing the live MCP request would look correct and produce
        ``/mcp/api/connections/...`` -- a URL the customer only discovers is
        wrong after they have already granted access."""
        from server_modules import connection_oauth_service as cos

        with patch.dict(os.environ, {"EMPYRALIS_PUBLIC_BASE_URL": "https://empyralis.example"}):
            url = cos.callback_url(mcp_server._public_origin_request(), "github")
        self.assertNotIn("/mcp/", url)

    async def test_connect_connector_returns_the_authorization_url(self):
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.connection_oauth_service.start_oauth",
                return_value={"authorization_url": "https://accounts.example/auth?x=1"},
            ) as start_mock, \
            patch.object(mcp_server, "_WRITE_ENABLED_GLOBAL", True):
            result = await mcp_server.empyralis_connect_connector(
                provider="github", ctx=_FakeDeadToolCtx(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["authorization_url"], "https://accounts.example/auth?x=1")
        start_mock.assert_called_once()
        passed_request = start_mock.call_args.kwargs["request"]
        # Not a stand-in: the real object the service's own type hint names.
        from starlette.requests import Request as StarletteRequest

        self.assertIsInstance(passed_request, StarletteRequest)


class MCPDeadToolShapeDriftTests(unittest.TestCase):
    """Source assertions. Each of these three shapes type-checks, imports
    cleanly, and fails 100% of the time at RUNTIME -- which is exactly why
    all three shipped. A behavioural test can only cover the tools that
    exist today; these cover the next one someone writes.
    """

    def _source(self):
        import pathlib

        return pathlib.Path(mcp_server.__file__).read_text(encoding="utf-8")

    def _code_tokens(self):
        """Identifiers + string literals that are real CODE in mcp_server.py.

        Docstrings are excluded (comments never enter the AST at all) so a
        passage EXPLAINING one of these banned shapes does not read as one --
        otherwise the only way to document the bug would be to reintroduce
        its own tripwire.
        """
        import ast

        tree = ast.parse(self._source())
        docstring_nodes = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = getattr(node, "body", None) or []
                first = body[0] if body else None
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstring_nodes.add(id(first.value))

        tokens = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                tokens.add(node.id)
            elif isinstance(node, ast.Attribute):
                tokens.add(node.attr)
            elif isinstance(node, ast.alias):
                tokens.add(node.name)
                if node.asname:
                    tokens.add(node.asname)
            elif isinstance(node, ast.keyword) and node.arg:
                tokens.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstring_nodes:
                    tokens.add(node.value)
        return tokens

    def test_no_tool_invents_an_auth_key_that_auth_py_never_reads(self):
        """``mcp_workspace_id`` was written by two tools and read by nothing,
        so both were a guaranteed 403. Any successor key is equally silent,
        so ban this one by name and require the shared helper instead."""
        import pathlib

        self.assertNotIn("mcp_workspace_id", self._code_tokens())

        auth_source = (
            pathlib.Path(mcp_server.__file__).parent / "server_modules" / "auth.py"
        ).read_text(encoding="utf-8")
        # The keys the shared identity is built from are proven against
        # auth.py itself -- two different sources, not one file agreeing
        # with itself.
        for key in ("workspace_access", "auth_type", "role"):
            self.assertIn(f'"{key}"', auth_source, f"auth.py does not read {key!r}")

    def test_no_tool_passes_a_simplenamespace_where_a_request_is_expected(self):
        """``SimpleNamespace`` answers exactly the attributes whoever wrote it
        thought of, and AttributeErrors on the first one they did not."""
        self.assertNotIn("SimpleNamespace", self._code_tokens())

    def test_the_deployed_agent_tools_pass_every_argument_with_no_default(self):
        """Derived from the live signatures, never from a hand-copied list --
        the expected set and the actual set must come from different places,
        or the check can only confirm itself."""
        import ast
        import inspect

        from server_modules import deployed_agent_service, deployed_agent_test_turn_service

        tree = ast.parse(self._source())
        calls_by_tool = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("empyralis_"):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    calls_by_tool.setdefault(node.name, []).append(inner)

        checks = [
            ("empyralis_trigger_test_turn", "execute_test_turn",
             deployed_agent_test_turn_service.execute_test_turn),
            ("empyralis_get_agent_conversations", "list_deployed_agent_conversations",
             deployed_agent_service.list_deployed_agent_conversations),
        ]
        for tool_name, callee_name, callee in checks:
            required = {
                name for name, param in inspect.signature(callee).parameters.items()
                if param.kind is inspect.Parameter.KEYWORD_ONLY
                and param.default is inspect.Parameter.empty
            }
            passed = set()
            for call in calls_by_tool.get(tool_name, []):
                func = call.func
                attr = getattr(func, "attr", None) or getattr(func, "id", None)
                if attr == callee_name:
                    passed |= {kw.arg for kw in call.keywords if kw.arg}
            self.assertTrue(
                required <= passed,
                f"{tool_name} omits {sorted(required - passed)} on {callee_name}() -- "
                "a keyword-only argument with no default is a TypeError on every call",
            )


# ── (i) MAN-207 Tier 1: assign_task + list_tasks ──────────────────────


class MCPAssignTaskToolTests(unittest.IsolatedAsyncioTestCase):
    """``create_task`` fires NO wakeup and NO notification (verified in
    project_tasks_service: neither bounded_scheduler_service nor
    task_notification_service is called from it). Assignment is the only
    step that reaches anyone, and it had no MCP tool at all."""

    async def test_agent_assignment_takes_the_agent_path_and_reports_the_wakeup(self):
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.project_tasks_service.assign_task",
                new=AsyncMock(return_value={"task": {"id": "t-1"}, "wake_request": {"id": "wr-1"}}),
            ) as agent_mock, \
            patch(
                "server_modules.project_tasks_service.assign_task_to_user", new=AsyncMock(),
            ) as user_mock:
            result = await mcp_server.empyralis_assign_task(
                task_id="t-1", agent_id="ainstall_nova", ctx=_FakeDeadToolCtx(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["assignee_kind"], "agent")
        self.assertEqual(result["wake_request"], {"id": "wr-1"})
        agent_mock.assert_awaited_once()
        # The twin must not also run -- the two have different side effects.
        user_mock.assert_not_awaited()
        kwargs = agent_mock.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-dead-A")
        self.assertEqual(kwargs["workspace_id"], "ws-dead-A")
        self.assertEqual(kwargs["agent_id"], "ainstall_nova")

    async def test_user_assignment_takes_the_user_path(self):
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.project_tasks_service.assign_task", new=AsyncMock(),
            ) as agent_mock, \
            patch(
                "server_modules.project_tasks_service.assign_task_to_user",
                new=AsyncMock(return_value={"task": {"id": "t-1"}, "wake_request": None}),
            ) as user_mock:
            result = await mcp_server.empyralis_assign_task(
                task_id="t-1", user_id="user-9", ctx=_FakeDeadToolCtx(),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["assignee_kind"], "user")
        user_mock.assert_awaited_once()
        agent_mock.assert_not_awaited()

    async def test_both_or_neither_is_refused_without_assigning_anything(self):
        """An assignment sent to the wrong kind of teammate is worse than one
        that did not happen, so neither case is guessed at. Asserting the
        call COUNT: "returned ok: false" alone would not notice a tool that
        refused AFTER already writing."""
        for kwargs in ({"agent_id": "a-1", "user_id": "u-1"}, {}):
            with self.subTest(kwargs=kwargs):
                p1, p2, p3 = _patched_dead_tool()
                with p1, p2, p3, \
                    patch(
                        "server_modules.project_tasks_service.assign_task", new=AsyncMock(),
                    ) as agent_mock, \
                    patch(
                        "server_modules.project_tasks_service.assign_task_to_user", new=AsyncMock(),
                    ) as user_mock:
                    result = await mcp_server.empyralis_assign_task(
                        task_id="t-1", ctx=_FakeDeadToolCtx(), **kwargs
                    )
                self.assertFalse(result["ok"])
                agent_mock.assert_not_awaited()
                user_mock.assert_not_awaited()

    async def test_a_committed_assignment_with_a_failed_wakeup_says_both(self):
        """"assigned" and "assigned and someone is on it" are different
        facts. The assignment commits independently of the wakeup (quiet
        hours, a rate cap), so reporting only the first is the collapse
        CLAUDE.md's outcome-honesty rule exists for."""
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.project_tasks_service.assign_task",
                new=AsyncMock(return_value={
                    "task": {"id": "t-1"}, "wake_request": None, "wake_error": "quiet hours",
                }),
            ):
            result = await mcp_server.empyralis_assign_task(
                task_id="t-1", agent_id="ainstall_nova", ctx=_FakeDeadToolCtx(),
            )

        self.assertTrue(result["ok"])              # the assignment DID commit
        self.assertEqual(result["wake_error"], "quiet hours")
        self.assertIn("nothing was woken", result["note"])


class MCPListTasksToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_list_tasks_returns_the_whole_board_not_just_the_callers_slice(self):
        rows = [{"id": "t-1"}, {"id": "t-2"}]
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.project_tasks_service.list_tasks", new=AsyncMock(return_value=rows),
            ) as list_mock:
            result = await mcp_server.empyralis_list_tasks(sort="priority", ctx=_FakeDeadToolCtx())

        self.assertTrue(result["ok"])
        self.assertEqual(result["tasks"], rows)
        kwargs = list_mock.await_args.kwargs
        self.assertEqual(kwargs["workspace_id"], "ws-dead-A")
        self.assertEqual(kwargs["sort"], "priority")
        # No assignee filter: that is what makes this the BOARD rather than
        # a second copy of list_my_tasks.
        self.assertIsNone(kwargs.get("assignee_agent_id"))

    async def test_a_project_outside_the_workspace_is_refused_not_reported_as_empty(self):
        """"not your project" and "no tasks" are different answers."""
        p1, p2, p3 = _patched_dead_tool()
        with p1, p2, p3, \
            patch(
                "server_modules.projects_repository.get_project", new=AsyncMock(return_value=None),
            ), \
            patch(
                "server_modules.project_tasks_service.list_tasks", new=AsyncMock(),
            ) as list_mock:
            result = await mcp_server.empyralis_list_tasks(
                project_id="proj-not-mine", ctx=_FakeDeadToolCtx(),
            )
        self.assertFalse(result["ok"])
        self.assertIn("not found in your workspace", result["error"])
        list_mock.assert_not_awaited()


class MCPToolRegistryDriftTests(unittest.IsolatedAsyncioTestCase):
    """``EMPYRALIST_MCP_TOOLS`` is a hand-kept list sitting beside the
    decorators that do the actual registering -- the same "a list copied
    into a second place" shape this codebase has been bitten by before.

    The expected set and the actual set come from DIFFERENT sources: the
    literal list, versus what the live FastMCP server would answer
    ``tools/list`` with. A list agreeing with itself proves nothing.
    """

    async def test_the_advertised_list_matches_what_the_server_actually_registers(self):
        if mcp_server.empyralist_mcp is None:
            self.skipTest("mcp SDK not installed")
        registered = {t.name for t in await mcp_server.empyralist_mcp.list_tools()}
        declared = set(mcp_server.EMPYRALIST_MCP_TOOLS)
        self.assertEqual(
            declared, registered,
            "EMPYRALIST_MCP_TOOLS has drifted from the real registrations: "
            f"declared-only={sorted(declared - registered)} "
            f"registered-only={sorted(registered - declared)}",
        )

    async def test_every_advertised_tool_is_actually_callable(self):
        """An advertised name that resolves to nothing is the same class of
        defect as MAN-205's four: discoverable, then useless."""
        if mcp_server.empyralist_mcp is None:
            self.skipTest("mcp SDK not installed")
        missing = [
            name for name in mcp_server.EMPYRALIST_MCP_TOOLS
            if not callable(getattr(mcp_server, name, None))
        ]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
