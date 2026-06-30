from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules.sage_agent_runtime_contract import (
    SAGE_MODE,
    SAGE_RESPONSE_KEYS,
    SageTurnResult,
    normalize_sage_mode,
)
from server_modules.sage_turn_adapter import (
    execute_sage_turn,
    execute_sage_turn_for_channel,
)


def _run(coro):
    return asyncio.run(coro)


class SageTurnAdapterParityTests(unittest.TestCase):
    def _mock_sage_chat(self, **overrides):
        base = {
            "message": "Hello from Sage",
            "used_context": ["sage_profile", "sage_memory"],
            "tool_calls": [],
            "available_tools": [],
            "blocked_tools": [],
            "approvals_required": [],
            "memory_updates": [],
            "trace_id": "trace-1",
            "provider": "openai",
            "model": "gpt-4o",
        }
        base.update(overrides)
        return base

    def test_api_path_returns_sage_turn_result(self):
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value=self._mock_sage_chat()),
        ):
            result = _run(execute_sage_turn(
                workspace_id="ws-1",
                message="hello",
            ))

        self.assertIsInstance(result, SageTurnResult)
        self.assertEqual(result.message, "Hello from Sage")

    def test_channel_path_returns_dict_with_all_keys(self):
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value=self._mock_sage_chat()),
        ):
            result = _run(execute_sage_turn_for_channel(
                workspace_id="ws-1",
                message="hello from whatsapp",
                surface_channel="whatsapp_personal",
                remote_jid="123456",
            ))

        for key in SAGE_RESPONSE_KEYS:
            self.assertIn(key, result, f"Channel result missing key: {key}")

    def test_both_paths_enforce_owner_sage_mode(self):
        for surface_channel in ("whatsapp_personal", "telegram_personal"):
            with patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(return_value=self._mock_sage_chat()),
            ) as mock_handle:
                _run(execute_sage_turn_for_channel(
                    workspace_id="ws-1",
                    message="hello",
                    surface_channel=surface_channel,
                    remote_jid="123",
                ))

                kwargs = mock_handle.call_args.kwargs
                self.assertEqual(kwargs["mode"], SAGE_MODE,
                                 f"Channel {surface_channel} did not enforce owner_sage mode")

    def test_both_paths_include_approvals_required(self):
        blocked_result = self._mock_sage_chat(
            blocked_tools=[{"skill_id": "email-access", "label": "Email", "action_class": "write"}],
            approvals_required=[{"type": "tool_action", "skill_id": "email-access", "label": "Email", "reason": "test"}],
        )
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value=blocked_result),
        ):
            api_result = _run(execute_sage_turn(workspace_id="ws-1", message="send email"))
            channel_result = _run(execute_sage_turn_for_channel(
                workspace_id="ws-1", message="send email",
                surface_channel="whatsapp_personal", remote_jid="123",
            ))

        self.assertTrue(len(api_result.approvals_required) > 0, "API path missing approvals_required")
        self.assertTrue(len(channel_result["approvals_required"]) > 0, "Channel path missing approvals_required")

    def test_both_paths_emit_same_response_keys(self):
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value=self._mock_sage_chat()),
        ):
            api_result = _run(execute_sage_turn(workspace_id="ws-1", message="hello"))
            channel_result = _run(execute_sage_turn_for_channel(
                workspace_id="ws-1", message="hello",
                surface_channel="whatsapp_personal", remote_jid="123",
            ))

        api_dict = api_result.as_dict()
        for key in SAGE_RESPONSE_KEYS:
            self.assertIn(key, api_dict, f"API result missing key: {key}")
            self.assertIn(key, channel_result, f"Channel result missing key: {key}")

    def test_channel_surface_detection(self):
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value=self._mock_sage_chat()),
        ) as mock_handle:
            _run(execute_sage_turn_for_channel(
                workspace_id="ws-1",
                message="hello",
                surface_channel="whatsapp_personal",
                remote_jid="123",
                gateway_id="gw-1",
                push_name="Test",
            ))

            kwargs = mock_handle.call_args.kwargs
            self.assertEqual(kwargs["surface"], "chat")
            self.assertEqual(kwargs["workspace_id"], "ws-1")

    def test_rejects_invalid_mode_at_adapter_level(self):
        with self.assertRaises(ValueError):
            _run(execute_sage_turn(
                workspace_id="ws-1",
                message="hello",
                mode="customer_live",
            ))

    def test_channel_path_excludes_restricted_memory(self):
        memory_context_log: list = []

        async def fake_handle(**kwargs):
            memory_context_log.append(kwargs.get("mode"))
            return self._mock_sage_chat()

        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(side_effect=fake_handle),
        ):
            _run(execute_sage_turn_for_channel(
                workspace_id="ws-1",
                message="hello",
                surface_channel="whatsapp_personal",
                remote_jid="123",
            ))

        self.assertIn(SAGE_MODE, memory_context_log)


if __name__ == "__main__":
    unittest.main()
