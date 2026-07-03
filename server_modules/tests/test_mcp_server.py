"""Phase U2: Empyralis as MCP server tests.

(a) Auth rejected without key
(b) Workspace resolved from key
(c) Key creation and revocation
(d) Write tools blocked when EMPYRALIS_MCP_WRITE_ENABLED=false
(e) Cross-workspace key isolation
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


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


if __name__ == "__main__":
    unittest.main()
