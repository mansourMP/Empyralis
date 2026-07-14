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


class SageTurnAdapterAgentIdRoutingTests(unittest.TestCase):
    """Item 3: a personal-channel session bound to a specialist agent must
    run turns AS that agent, not always Sage — reusing the SAME
    specialist_context mechanism Discord/Slack/hosted-Telegram already use
    (specialist_runtime_context.resolve_specialist_runtime_context)."""

    def _mock_sage_chat(self, **overrides):
        base = {
            "message": "Hello",
            "used_context": [],
            "tool_calls": [],
            "available_tools": [],
            "blocked_tools": [],
            "approvals_required": [],
            "memory_updates": [],
            "trace_id": "trace-1",
        }
        base.update(overrides)
        return base

    def test_agent_id_resolves_and_passes_a_real_specialist_context(self):
        fake_context = object()  # identity check is enough — we're testing plumbing, not the resolver
        with (
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(return_value=fake_context),
            ) as resolve_mock,
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(return_value=self._mock_sage_chat()),
            ) as handle_mock,
        ):
            _run(execute_sage_turn_for_channel(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                message="hello",
                surface_channel="telegram_personal",
                remote_jid="tg-user-1",
                gateway_id="gw-1",
                agent_id="ainstall_specialist_1",
            ))

        resolve_kwargs = resolve_mock.call_args.kwargs
        self.assertEqual(resolve_kwargs["active_agent_install_id"], "ainstall_specialist_1")
        self.assertEqual(resolve_kwargs["workspace_id"], "ws-1")
        self.assertEqual(resolve_kwargs["tenant_id"], "tenant-1")
        self.assertIs(handle_mock.call_args.kwargs["specialist_context"], fake_context)

    def test_empty_agent_id_stays_sage_exactly_as_before(self):
        """The pre-existing, still-default behavior: no agent_id means no
        resolver call at all and specialist_context=None reaches
        handle_sage_chat — byte-for-byte the old behavior."""
        with (
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(side_effect=AssertionError("must not be called when agent_id is empty")),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(return_value=self._mock_sage_chat()),
            ) as handle_mock,
        ):
            _run(execute_sage_turn_for_channel(
                workspace_id="ws-1",
                message="hello",
                surface_channel="whatsapp_personal",
                remote_jid="123",
            ))

        self.assertIsNone(handle_mock.call_args.kwargs["specialist_context"])

    def test_resolver_failure_fails_safe_to_sage_not_an_exception(self):
        """A specialist lookup that throws must never take the turn down
        with it — the pre-existing Sage behavior is always the safe
        fallback."""
        with (
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(side_effect=RuntimeError("registry unavailable")),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(return_value=self._mock_sage_chat()),
            ) as handle_mock,
        ):
            result = _run(execute_sage_turn_for_channel(
                workspace_id="ws-1",
                message="hello",
                surface_channel="telegram_personal",
                remote_jid="123",
                agent_id="ainstall_broken",
            ))

        self.assertIsNone(handle_mock.call_args.kwargs["specialist_context"])
        self.assertEqual(result["message"], "Hello")


if __name__ == "__main__":
    unittest.main()
