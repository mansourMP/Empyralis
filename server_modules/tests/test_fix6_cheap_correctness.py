"""Proofs for Fix 6 (cheap-correctness bundle).

Covers: MCP revoke-key IDOR closed, MCP inbound ledger restored, dead MCP memory
tools removed + configure tool fixed, and pairing-code TTL.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import mcp_server
from server_modules import mcp_server_auth as auth_mod
from server_modules import sage_telegram_hosted_service as hosted


class McpRevokeIdorTests(unittest.TestCase):
    def _store(self):
        return {"keys": {"mcp_key_a": {
            "key_id": "mcp_key_a", "workspace_id": "ws-A", "revoked": False, "label": "x",
        }}}

    def test_cross_workspace_revoke_rejected(self):
        store = self._store()
        with patch.object(auth_mod, "_load_keys", return_value=store), \
             patch.object(auth_mod, "_save_keys"):
            result = asyncio.run(auth_mod.revoke_workspace_mcp_api_key("mcp_key_a", workspace_id="ws-B"))
        self.assertFalse(result["ok"])                          # rejected
        self.assertFalse(store["keys"]["mcp_key_a"]["revoked"])  # not revoked

    def test_same_workspace_revoke_succeeds(self):
        store = self._store()
        with patch.object(auth_mod, "_load_keys", return_value=store), \
             patch.object(auth_mod, "_save_keys"):
            result = asyncio.run(auth_mod.revoke_workspace_mcp_api_key("mcp_key_a", workspace_id="ws-A"))
            ws = asyncio.run(auth_mod.get_mcp_api_key_workspace("mcp_key_a"))
        self.assertTrue(result["ok"])
        self.assertTrue(store["keys"]["mcp_key_a"]["revoked"])
        self.assertEqual(ws, "ws-A")


class McpLedgerRestoredTests(unittest.TestCase):
    def test_inbound_call_writes_mcp_inbound_ledger_event(self):
        mock_append = AsyncMock()

        async def scenario():
            with patch("server_modules.activity_ledger_service.append_activity_event", mock_append), \
                 patch("server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                       AsyncMock(return_value="ten-1")):
                await mcp_server._ledger_mcp_call("ws-1", "empyralis_list_agents", True, agent_count=3)

        asyncio.run(scenario())
        mock_append.assert_awaited_once()
        kwargs = mock_append.await_args.kwargs
        self.assertEqual(kwargs["event_class"], "mcp_inbound")
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["actor_id"], "external_mcp_client")


class McpToolSurfaceTests(unittest.TestCase):
    def test_dead_memory_tools_removed_configure_kept(self):
        for dead in ("empyralis_memory_read", "empyralis_memory_list", "empyralis_memory_write"):
            self.assertNotIn(dead, mcp_server.EMPYRALIST_MCP_TOOLS)
        self.assertIn("empyralis_configure_agent", mcp_server.EMPYRALIST_MCP_TOOLS)


class PairingCodeTtlTests(unittest.TestCase):
    def setUp(self):
        hosted._PENDING_PAIRING_CODES.clear()
        hosted._PENDING_PAIRING_CODE_TIMES.clear()

    def test_code_valid_within_ttl(self):
        with patch.object(hosted, "_persist_after_mutation"):
            with patch.object(hosted.time, "time", return_value=1000.0):
                code = hosted.generate_pairing_code(workspace_id="ws-9")
            within = 1000.0 + hosted._PAIRING_CODE_TTL_SECONDS - 1
            with patch.object(hosted.time, "time", return_value=within):
                self.assertEqual(hosted.consume_pairing_code(code), "ws-9")

    def test_code_expires_after_ttl(self):
        with patch.object(hosted, "_persist_after_mutation"):
            with patch.object(hosted.time, "time", return_value=2000.0):
                code = hosted.generate_pairing_code(workspace_id="ws-9")
            after = 2000.0 + hosted._PAIRING_CODE_TTL_SECONDS + 1
            with patch.object(hosted.time, "time", return_value=after):
                self.assertIsNone(hosted.consume_pairing_code(code))       # expired
                self.assertNotIn(code, hosted._PENDING_PAIRING_CODES)      # purged


if __name__ == "__main__":
    unittest.main()
