import unittest
from unittest.mock import AsyncMock, patch

from server_modules import conversation_memory_facade_service as service
from server_modules.conversation_memory_policy import (
    DIRECT_CHAT_PROFILE,
    DURABLE_RUN_PROFILE,
    EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
)


class ConversationMemoryFacadeServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_build_workspace_context_text_uses_workspace_adapter(self) -> None:
        with patch.object(
            service.workspace_context_memory_adapter,
            "load_workspace_context_payload",
            return_value={
                "contextual_blocks": ["SOUL.md\nAssistant posture", "Runtime Memory Facts\n- timezone: Asia/Shanghai"],
                "semantic_hits": [],
                "diagnostics": {"semantic_hit_count": 0},
            },
        ):
            text = service.build_workspace_context_text(
                service.ConversationMemorySubject(
                    workspace_id="default",
                    surface_kind=service.DIRECT_CHAT_SURFACE,
                ),
                DIRECT_CHAT_PROFILE,
                query_text="timezone",
            )

        self.assertIn("SOUL.md", text)
        self.assertIn("Runtime Memory Facts", text)

    async def test_channel_load_context_routes_through_channel_adapter(self) -> None:
        with patch.object(
            service.deployed_channel_memory_adapter,
            "load_deployed_channel_context",
            new=AsyncMock(
                return_value={
                    "prior_messages": [{"role": "user", "content": "hello"}],
                    "business_plan": "Persistent customer memory",
                    "summary_text": "Persistent customer memory",
                    "compacted": True,
                    "enabled": True,
                    "applied": True,
                    "summary_present": True,
                    "message_count": 4,
                }
            ),
        ):
            context = await service.aload_context(
                service.ConversationMemorySubject(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    surface_kind=service.DEPLOYED_AGENT_CHANNEL_SURFACE,
                    session_key="telegram:bot:user-1",
                    channel_key="telegram",
                    external_user_id="user-1",
                ),
                EXTERNAL_CHANNEL_CUSTOMER_PROFILE,
                metadata={"deployed_agent": {"id": "dagent_1"}},
            )

        self.assertEqual(context.prior_messages[0]["content"], "hello")
        self.assertTrue(context.compacted)
        self.assertTrue(context.diagnostics["summary_present"])

    def test_persist_run_completion_routes_through_runtime_adapter(self) -> None:
        with patch.object(
            service.semantic_runtime_memory_adapter,
            "persist_run_memory",
            return_value=None,
        ) as persist_mock:
            result = service.persist_run_completion(
                service.ConversationMemorySubject(
                    workspace_id="default",
                    surface_kind=service.DURABLE_RUN_SURFACE,
                ),
                DURABLE_RUN_PROFILE,
                run_summary="completed work",
                metadata={"run_id": "run-1", "run": {"status": "completed"}},
            )

        persist_mock.assert_called_once()
        self.assertTrue(result["persisted"])


if __name__ == "__main__":
    unittest.main()
