from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import conversation_memory_facade_service as conversation_memory
from server_modules import memory_service, workspace_context
from server_modules.conversation_memory_policy import EXTERNAL_CHANNEL_CUSTOMER_PROFILE


def _deployed_agent() -> dict[str, object]:
    return {
        "id": "dagent-support",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "default",
        "backing_install_id": "install-support",
        "metadata": {
            "memory_enabled": True,
            "memory_policy": {
                "memory_enabled": True,
                "context_budget_preset": "balanced",
                "retention_preset": "standard",
            },
        },
    }


class CrossAgentMemoryIsolationCertificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global memory_service
        global workspace_context
        memory_service = importlib.import_module("server_modules.memory_service")
        workspace_context = importlib.import_module("server_modules.workspace_context")
        self._tmpdir = tempfile.TemporaryDirectory(prefix="cross-agent-memory-cert-")
        self.addCleanup(self._tmpdir.cleanup)
        tmp_root = Path(self._tmpdir.name)
        self._workspace_root = tmp_root / "workspace"
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._memory_root = tmp_root / "runtime-memory"
        self._patchers = [
            patch.object(workspace_context, "_WORKSPACE_DIR", self._workspace_root),
            patch.object(memory_service._workspace_memory_store, "_MEMORY_DIR", self._memory_root),
            patch.object(memory_service._workspace_memory_store, "_SEMANTIC_MODEL", False),
        ]
        for patcher in self._patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_agent_b_cannot_read_agent_a_private_workspace_memory(self) -> None:
        workspace_context.write_workspace_context_file(
            "USER.md",
            "AGENT_A_PRIVATE_PROFILE_DO_NOT_LEAK",
            workspace_id="default",
            agent_install_id="install-a",
        )
        workspace_context.write_workspace_context_file(
            "USER.md",
            "Agent B profile.",
            workspace_id="default",
            agent_install_id="install-b",
        )
        memory_service.save_memory(
            "default",
            "agent_a_secret",
            "AGENT_A_PRIVATE_MEMORY_DO_NOT_LEAK",
            agent_install_id="install-a",
        )
        memory_service.save_memory(
            "default",
            "agent_b_fact",
            "Agent B visible private memory.",
            agent_install_id="install-b",
        )
        memory_service.save_daily_log(
            "default",
            "AGENT_A_PRIVATE_LOG_DO_NOT_LEAK",
            agent_install_id="install-a",
        )
        memory_service.save_daily_log(
            "default",
            "Agent B visible private log.",
            agent_install_id="install-b",
        )

        agent_b_entries = memory_service.list_memory_entries("default", agent_install_id="install-b")
        agent_b_memory = memory_service.get_memory("default", agent_install_id="install-b")
        agent_b_search = memory_service.semantic_search(
            "default",
            "private memory",
            agent_install_id="install-b",
        )
        agent_b_context = memory_service.direct_chat_workspace_context_text(
            "default",
            memory_query="private memory",
            agent_install_id="install-b",
        )

        self.assertEqual([entry["key"] for entry in agent_b_entries], ["agent_b_fact"])
        self.assertIn("Agent B visible private memory.", agent_b_memory)
        self.assertIn("Agent B visible private memory.", agent_b_context)
        self.assertIn("Agent B visible private log.", agent_b_context)
        self.assertTrue(all(item["key"] == "agent_b_fact" for item in agent_b_search))
        self.assertNotIn("AGENT_A_PRIVATE_PROFILE_DO_NOT_LEAK", agent_b_context)
        self.assertNotIn("AGENT_A_PRIVATE_MEMORY_DO_NOT_LEAK", agent_b_memory)
        self.assertNotIn("AGENT_A_PRIVATE_MEMORY_DO_NOT_LEAK", agent_b_context)
        self.assertNotIn("AGENT_A_PRIVATE_LOG_DO_NOT_LEAK", agent_b_context)

    async def test_deployed_customer_memory_does_not_load_sage_or_direct_private_memory(self) -> None:
        sage_secret = "SAGE_DIRECT_PRIVATE_MEMORY_DO_NOT_LEAK"
        specialist_secret = "SPECIALIST_PRIVATE_MEMORY_DO_NOT_LEAK"
        workspace_context.write_workspace_context_file(
            "SOUL.md",
            "Sage private identity.",
            workspace_id="default",
            agent_install_id="install-sage",
        )
        memory_service.save_memory(
            "default",
            "sage_private",
            sage_secret,
            agent_install_id="install-sage",
        )
        memory_service.save_memory(
            "default",
            "specialist_private",
            specialist_secret,
            agent_install_id="install-specialist",
        )
        direct_context = memory_service.direct_chat_workspace_context_text(
            "default",
            memory_query="remember private",
            agent_install_id="install-sage",
        )
        self.assertIn(sage_secret, direct_context)

        async def fake_get_deployed_memory(**kwargs):
            self.assertEqual(kwargs["deployed_agent_id"], "dagent-support")
            self.assertEqual(kwargs["external_user_id"], "customer-1")
            return {
                "summary_text": (
                    "Persistent customer memory:\n"
                    "Customer previously asked about return windows."
                ),
                "updated_at": "2026-05-07T00:00:00Z",
            }

        async def fake_list_deployed_events(**kwargs):
            self.assertEqual(kwargs["deployed_agent_id"], "dagent-support")
            self.assertEqual(kwargs["backing_install_id"], "install-support")
            return [
                {
                    "id": "evt-2",
                    "direction": "outbound",
                    "text": "Yes, size 42 is available.",
                    "created_at": "2026-05-07T12:00:03Z",
                },
                {
                    "id": "evt-1",
                    "direction": "inbound",
                    "text": "Do you have size 42 in stock?",
                    "created_at": "2026-05-07T12:00:00Z",
                },
            ]

        with (
            patch.object(
                conversation_memory.workspace_context_memory_adapter,
                "load_workspace_context_payload",
                side_effect=AssertionError("deployed customer memory must not load Sage workspace context"),
            ),
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.get_deployed_agent_conversation_memory",
                new=AsyncMock(side_effect=fake_get_deployed_memory),
            ),
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.list_deployed_agent_conversation_events",
                new=AsyncMock(side_effect=fake_list_deployed_events),
            ),
        ):
            deployed_context = await conversation_memory.aload_context(
                conversation_memory.ConversationMemorySubject(
                    tenant_id="tenant-1",
                    workspace_id="default",
                    surface_kind=conversation_memory.DEPLOYED_AGENT_CHANNEL_SURFACE,
                    responder_install_id="install-support",
                    session_key="telegram:shop:customer-1",
                    deployed_agent_id="dagent-support",
                    channel_key="telegram",
                    external_user_id="customer-1",
                ),
                EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
                metadata={"deployed_agent": _deployed_agent()},
            )

        rendered = "\n".join(
            [deployed_context.business_plan or ""]
            + [str(item.get("content") or "") for item in deployed_context.prior_messages]
        )
        self.assertIn("Customer previously asked about return windows.", rendered)
        self.assertIn("Do you have size 42 in stock?", rendered)
        self.assertNotIn(sage_secret, rendered)
        self.assertNotIn(specialist_secret, rendered)
        self.assertEqual(deployed_context.contextual_blocks, [])
        self.assertEqual(deployed_context.semantic_hits, [])


if __name__ == "__main__":
    unittest.main()
