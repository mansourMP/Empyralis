from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import control_plane_repository, deployed_agent_memory_service
from server_modules.conversation_compaction import estimate_conversation_tokens


def _deployed_agent(
    *,
    deployed_agent_id: str = "dagent_1",
    backing_install_id: str = "ainstall_1",
    memory_enabled: bool = True,
) -> dict[str, object]:
    return {
        "id": deployed_agent_id,
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "backing_install_id": backing_install_id,
        "metadata": {
            "memory_enabled": memory_enabled,
        },
    }


def _event(
    *,
    event_id: str,
    direction: str,
    text: str,
    created_at: str,
) -> dict[str, object]:
    return {
        "id": event_id,
        "direction": direction,
        "text": text,
        "created_at": created_at,
    }


class DeployedAgentMemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global control_plane_repository, deployed_agent_memory_service
        control_plane_repository = importlib.import_module("server_modules.control_plane_repository")
        deployed_agent_memory_service = importlib.import_module(
            "server_modules.deployed_agent_memory_service"
        )

    def test_control_plane_schema_declares_conversation_memory_table(self) -> None:
        schema = control_plane_repository.CONTROL_PLANE_SCHEMA_SQL
        self.assertIn("CREATE TABLE IF NOT EXISTS deployed_agent_conversation_memory", schema)
        self.assertIn("UNIQUE(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)", schema)

    async def test_load_memory_context_is_disabled_when_deployment_memory_is_off(self) -> None:
        result = await deployed_agent_memory_service.load_deployed_agent_memory_context(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            deployed_agent=_deployed_agent(memory_enabled=False),
            channel_key="telegram",
            external_user_id="telegram-user-1",
            session_key="telegram:bot:telegram-user-1",
            responder_install_id="ainstall_1",
        )

        self.assertFalse(result["enabled"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["prior_messages"], [])
        self.assertEqual(result["business_plan"], "")

    async def test_load_memory_context_uses_same_user_same_deployment_history(self) -> None:
        with (
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.get_deployed_agent_conversation_memory",
                new=AsyncMock(
                    return_value={
                        "summary_text": "Persistent customer memory:\nEarlier persisted summary:\nCustomer prefers OEM parts.",
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.list_deployed_agent_conversation_events",
                new=AsyncMock(
                    return_value=[
                        _event(
                            event_id="evt-1",
                            direction="inbound",
                            text="I need wipers for my 2022 Tesla Model 3.",
                            created_at="2026-04-13T12:00:00Z",
                        ),
                        _event(
                            event_id="evt-2",
                            direction="outbound",
                            text="I can help with that.",
                            created_at="2026-04-13T12:00:03Z",
                        ),
                    ]
                ),
            ) as list_events_mock,
        ):
            result = await deployed_agent_memory_service.load_deployed_agent_memory_context(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agent=_deployed_agent(),
                channel_key="telegram",
                external_user_id="telegram-user-1",
                session_key="telegram:partspro-bot:telegram-user-1",
                responder_install_id="ainstall_1",
            )

        self.assertTrue(result["enabled"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["summary_present"])
        self.assertEqual(
            list_events_mock.await_args.kwargs["deployed_agent_id"],
            "dagent_1",
        )
        self.assertEqual(
            list_events_mock.await_args.kwargs["session_id"],
            "telegram:partspro-bot:telegram-user-1",
        )
        self.assertIn("Customer prefers OEM parts.", result["business_plan"])
        self.assertIn(
            {"role": "user", "content": "I need wipers for my 2022 Tesla Model 3."},
            result["prior_messages"],
        )

    async def test_load_memory_context_strips_red_facts_before_prompt_context(self) -> None:
        with (
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.get_deployed_agent_conversation_memory",
                new=AsyncMock(
                    return_value={
                        "summary_text": (
                            "Persistent customer memory:\n"
                            "Customer prefers OEM parts.\n"
                            "RED: customer token abcdefghijklmnopqrstuvwxyz123456"
                        ),
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.list_deployed_agent_conversation_events",
                new=AsyncMock(
                    return_value=[
                        _event(
                            event_id="evt-1",
                            direction="inbound",
                            text="Safe note.\n- [RED] Production API key: sk-agent-secret-123456789",
                            created_at="2026-04-13T12:00:00Z",
                        ),
                    ]
                ),
            ),
        ):
            result = await deployed_agent_memory_service.load_deployed_agent_memory_context(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agent=_deployed_agent(),
                channel_key="telegram",
                external_user_id="telegram-user-1",
                session_key="telegram:partspro-bot:telegram-user-1",
                responder_install_id="ainstall_1",
            )

        rendered = "\n".join(
            [item["content"] for item in result["prior_messages"]]
            + [str(result.get("business_plan") or "")]
        )
        self.assertIn("Customer prefers OEM parts.", rendered)
        self.assertIn("Safe note.", rendered)
        self.assertIn("RED memory fact(s) stripped", rendered)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", rendered)
        self.assertNotIn("sk-agent-secret-123456789", rendered)

    async def test_load_memory_context_is_isolated_by_user_and_deployment(self) -> None:
        async def fake_get_memory(**kwargs):
            if (
                kwargs["deployed_agent_id"] == "dagent_1"
                and kwargs["external_user_id"] == "telegram-user-1"
            ):
                return {
                    "summary_text": "Persistent customer memory:\nEarlier persisted summary:\nCustomer likes premium support.",
                }
            return None

        async def fake_list_events(**kwargs):
            if (
                kwargs["deployed_agent_id"] == "dagent_1"
                and kwargs["session_id"] == "telegram:bot:telegram-user-1"
            ):
                return [
                    _event(
                        event_id="evt-1",
                        direction="inbound",
                        text="Remember my premium support plan.",
                        created_at="2026-04-13T12:00:00Z",
                    ),
                ]
            return []

        with (
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.get_deployed_agent_conversation_memory",
                new=AsyncMock(side_effect=fake_get_memory),
            ),
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.list_deployed_agent_conversation_events",
                new=AsyncMock(side_effect=fake_list_events),
            ),
        ):
            same_user_other_deployment = await deployed_agent_memory_service.load_deployed_agent_memory_context(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agent=_deployed_agent(deployed_agent_id="dagent_2", backing_install_id="ainstall_2"),
                channel_key="telegram",
                external_user_id="telegram-user-1",
                session_key="telegram:bot:telegram-user-1",
                responder_install_id="ainstall_2",
            )
            other_user_same_deployment = await deployed_agent_memory_service.load_deployed_agent_memory_context(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agent=_deployed_agent(deployed_agent_id="dagent_1", backing_install_id="ainstall_1"),
                channel_key="telegram",
                external_user_id="telegram-user-2",
                session_key="telegram:bot:telegram-user-2",
                responder_install_id="ainstall_1",
            )

        self.assertFalse(same_user_other_deployment["applied"])
        self.assertEqual(same_user_other_deployment["prior_messages"], [])
        self.assertFalse(other_user_same_deployment["applied"])
        self.assertEqual(other_user_same_deployment["prior_messages"], [])

    async def test_persist_and_reload_long_history_generates_summary_within_prompt_budget(self) -> None:
        state: dict[str, object | None] = {"memory_row": None}
        long_events = []
        for index in range(18):
            direction = "inbound" if index % 2 == 0 else "outbound"
            prefix = "Customer" if direction == "inbound" else "Assistant"
            long_events.append(
                _event(
                    event_id=f"evt-{index}",
                    direction=direction,
                    text=f"{prefix} turn {index} " + ("detail " * 60),
                    created_at=f"2026-04-13T12:{index:02d}:00Z",
                )
            )

        async def fake_get_memory(**kwargs):
            return state["memory_row"]

        async def fake_upsert_memory(**kwargs):
            row = {
                "tenant_id": kwargs["tenant_id"],
                "workspace_id": kwargs["workspace_id"],
                "deployed_agent_id": kwargs["deployed_agent_id"],
                "channel_key": kwargs["channel_key"],
                "external_user_id": kwargs["external_user_id"],
                "session_key": kwargs["session_key"],
                "summary_text": kwargs["summary_text"],
                "recent_message_count": kwargs["recent_message_count"],
                "source_message_count": kwargs["source_message_count"],
                "metadata": kwargs["metadata"],
            }
            state["memory_row"] = row
            return row

        with (
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.get_deployed_agent_conversation_memory",
                new=AsyncMock(side_effect=fake_get_memory),
            ),
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.list_deployed_agent_conversation_events",
                new=AsyncMock(return_value=long_events),
            ),
            patch(
                "server_modules.deployed_agent_memory_service.control_plane_repository.upsert_deployed_agent_conversation_memory",
                new=AsyncMock(side_effect=fake_upsert_memory),
            ),
            patch(
                "server_modules.deployed_agent_memory_service.session_transcript_store.save_session_transcript",
            ),
            patch(
                "server_modules.deployed_agent_memory_service.activity_ledger_service.append_activity_event",
                new=AsyncMock(),
            ) as append_activity_event_mock,
            patch(
                "server_modules.deployed_agent_memory_service.provider_profiles.context_window_for_model",
                return_value=8_000,
            ),
        ):
            persisted = await deployed_agent_memory_service.persist_deployed_agent_memory_snapshot(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agent=_deployed_agent(),
                channel_key="telegram",
                external_user_id="telegram-user-1",
                session_key="telegram:partspro-bot:telegram-user-1",
                thread_id="thread-1",
                responder_install_id="ainstall_1",
                user_message="Latest customer message",
                assistant_reply="Latest assistant reply",
            )
            reloaded = await deployed_agent_memory_service.load_deployed_agent_memory_context(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                deployed_agent=_deployed_agent(),
                channel_key="telegram",
                external_user_id="telegram-user-1",
                session_key="telegram:partspro-bot:telegram-user-1",
                responder_install_id="ainstall_1",
            )

        self.assertIsNotNone(persisted)
        self.assertTrue(str(persisted["summary_text"]).startswith("Persistent customer memory:"))
        self.assertLessEqual(
            int(persisted["recent_message_count"]),
            deployed_agent_memory_service.DEFAULT_MEMORY_PRESERVE_LAST_MESSAGES,
        )
        self.assertTrue(reloaded["summary_present"])
        self.assertLessEqual(
            estimate_conversation_tokens(reloaded["prior_messages"]),
            deployed_agent_memory_service.DEFAULT_MEMORY_TOKEN_LIMIT,
        )
        append_activity_event_mock.assert_awaited_once()
        event_kwargs = append_activity_event_mock.await_args.kwargs
        self.assertEqual(event_kwargs["event_class"], "memory_update")
        self.assertEqual(event_kwargs["action"], "deployed_agent_memory_snapshot_written")


if __name__ == "__main__":
    unittest.main()
