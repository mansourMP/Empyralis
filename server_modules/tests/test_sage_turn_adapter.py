from __future__ import annotations

import asyncio
import types
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

    def test_both_paths_thread_media_from_handle_sage_chat(self):
        """handle_sage_chat's "media" key (populated by send_image /
        generate_image's auto-attach — see skills_service.py's
        session_ctx["pending_outbound_media"]) must survive both the
        SageTurnResult dataclass round-trip (execute_sage_turn) and the
        channel dict round-trip (execute_sage_turn_for_channel, which
        returns sage_result.as_dict())."""
        media_item = {"kind": "image", "source_path": "/tmp/fox.png", "mime_type": "image/png"}
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value=self._mock_sage_chat(media=[media_item])),
        ):
            api_result = _run(execute_sage_turn(workspace_id="ws-1", message="send me that fox"))
            channel_result = _run(execute_sage_turn_for_channel(
                workspace_id="ws-1",
                message="send me that fox",
                surface_channel="whatsapp_personal",
                remote_jid="123456",
            ))

        self.assertEqual(api_result.media, [media_item])
        self.assertEqual(channel_result["media"], [media_item])

    def test_media_defaults_to_empty_list_when_handle_sage_chat_omits_it(self):
        """The vast majority of turns never call send_image/generate_image —
        handle_sage_chat's result dict has no "media" key at all then, and
        both paths must default to [], not KeyError/None."""
        with patch(
            "server_modules.sage_agent_runtime_service.handle_sage_chat",
            new=AsyncMock(return_value=self._mock_sage_chat()),
        ):
            api_result = _run(execute_sage_turn(workspace_id="ws-1", message="hello"))
            channel_result = _run(execute_sage_turn_for_channel(
                workspace_id="ws-1", message="hello",
                surface_channel="whatsapp_personal", remote_jid="123456",
            ))

        self.assertEqual(api_result.media, [])
        self.assertEqual(channel_result["media"], [])

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


class TriageRulingTests(unittest.TestCase):
    """Founder ruling (2026-07-23): "Every single message goes to the
    reasoning model, absolutely. We are not going to have filters that flag
    a message and don't deliver it. No hardcoded outputs — everything is
    the agent's own reasoning." Phase P's input-blocking gate (formerly
    invoked right here, between directive processing and the envelope
    header) is gone; this proves an inbound message reaches handle_sage_chat
    unmodified even when the workspace's master install still carries the
    exact install_metadata.triage.enabled=True config that used to gate on
    a Layer-1 "no" verdict."""

    def _mock_sage_chat(self, **overrides):
        base = {
            "message": "This is the model's own real answer.",
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

    def test_triage_enabled_install_metadata_no_longer_blocks_the_turn(self):
        """A message that would previously have earned a Layer-1 "no" verdict
        (clearly out of a narrow "widget support" scope) and a workspace
        whose master Sage install is configured with triage fully enabled —
        the turn must still reach handle_sage_chat with the ORIGINAL
        message, and the caller must get the model's real reply back, not a
        canned decline/silence/escalation substitute."""
        triage_enabled_install = {
            "id": "agent-sage-1",
            "install_metadata": {
                "triage": {
                    "enabled": True,
                    "scope_description": "Widget support only",
                    "out_of_scope_behavior": "polite_decline",
                    "identity_rules": [
                        {"match": "owner", "behavior": "full"},
                        {"match": "audience", "behavior": "restricted"},
                        {"match": "unknown", "behavior": "restricted"},
                    ],
                }
            },
        }
        original_message = "I need legal advice about my divorce"
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value=triage_enabled_install),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(return_value=self._mock_sage_chat()),
            ) as handle_mock,
        ):
            result = _run(execute_sage_turn(
                workspace_id="ws-1",
                message=original_message,
            ))

        handle_mock.assert_awaited_once()
        self.assertEqual(handle_mock.call_args.kwargs["message"], original_message)
        self.assertEqual(result.message, "This is the model's own real answer.")
        self.assertNotIn("outside", result.message.lower())
        self.assertNotIn("configured scope", result.message.lower())

    def test_execute_triage_gate_no_longer_exists(self):
        """Guards against the gate quietly coming back: the function this
        ruling removed must not exist on the module at all."""
        from server_modules import triage_service
        self.assertFalse(hasattr(triage_service, "execute_triage_gate"))
        self.assertFalse(hasattr(triage_service, "run_scope_check"))
        self.assertFalse(hasattr(triage_service, "dispatch_out_of_scope"))
        self.assertFalse(hasattr(triage_service, "resolve_triage_config"))


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


class SageTurnAdapterThreadKeyingTests(unittest.TestCase):
    """Per-(agent, sender) thread keying: a resolved specialist turn must get
    a thread scoped to that agent + sender, so different agents and
    different senders on the same channel type never interleave into one
    "sage-main" bucket. LEGACY_UNSCOPED (no specialist resolved) must stay
    byte-for-byte on the pre-existing get_active_thread path."""

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

    def _fake_specialist_context(self, agent_install_id: str):
        return types.SimpleNamespace(agent_install_id=agent_install_id)

    def _capture_thread_id(self, *, agent_id: str, sender_id: str, channel: str = "whatsapp_personal"):
        with (
            patch(
                "server_modules.specialist_runtime_context.resolve_specialist_runtime_context",
                new=AsyncMock(return_value=self._fake_specialist_context(agent_id)),
            ),
            patch(
                "server_modules.sage_command_dispatcher.get_active_thread",
                new=AsyncMock(side_effect=AssertionError(
                    "get_active_thread must not be called for a resolved specialist turn"
                )),
            ),
            patch(
                "server_modules.sage_agent_runtime_service.handle_sage_chat",
                new=AsyncMock(return_value=self._mock_sage_chat()),
            ) as handle_mock,
        ):
            _run(execute_sage_turn_for_channel(
                workspace_id="ws-1",
                message="hello",
                surface_channel=channel,
                remote_jid=sender_id,
                agent_id=agent_id,
            ))
        return handle_mock.call_args.kwargs["thread_id"]

    def test_two_different_agents_get_separate_threads(self):
        """Same sender, two different specialist agents on the same channel
        type — today both would share one "sage-main"-per-channel bucket."""
        thread_a = self._capture_thread_id(agent_id="ainstall_agent_a", sender_id="same-sender")
        thread_b = self._capture_thread_id(agent_id="ainstall_agent_b", sender_id="same-sender")
        self.assertNotEqual(thread_a, thread_b)

    def test_two_different_senders_get_separate_threads(self):
        """Same agent, two different senders — customer isolation."""
        thread_customer_1 = self._capture_thread_id(agent_id="ainstall_agent_a", sender_id="customer-1")
        thread_customer_2 = self._capture_thread_id(agent_id="ainstall_agent_a", sender_id="customer-2")
        self.assertNotEqual(thread_customer_1, thread_customer_2)

    def test_same_agent_and_sender_is_deterministic_and_stable(self):
        """The same (agent, sender) pair always resolves to the same thread
        — no DB lookup, no drift between messages."""
        first = self._capture_thread_id(agent_id="ainstall_agent_a", sender_id="same-sender")
        second = self._capture_thread_id(agent_id="ainstall_agent_a", sender_id="same-sender")
        self.assertEqual(first, second)

    def test_owner_gets_a_stable_thread_same_as_any_other_sender(self):
        """An owner's self-chat sender_id is just another sender_id at this
        layer (see STEP 0 report — owner/audience isn't a resolved field
        here) — it still gets its own stable, isolated thread per agent."""
        first = self._capture_thread_id(agent_id="ainstall_agent_a", sender_id="owner-self-chat-jid")
        second = self._capture_thread_id(agent_id="ainstall_agent_a", sender_id="owner-self-chat-jid")
        self.assertEqual(first, second)
        customer_thread = self._capture_thread_id(agent_id="ainstall_agent_a", sender_id="customer-1")
        self.assertNotEqual(first, customer_thread)

    def test_legacy_unscoped_still_uses_get_active_thread(self):
        """No specialist resolved (running as Sage/master) — completely
        unchanged: still goes through get_active_thread, preserving
        "sage-main" and any existing per-channel override."""
        with (
            patch(
                "server_modules.sage_command_dispatcher.get_active_thread",
                new=AsyncMock(return_value="sage-main"),
            ) as get_active_mock,
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
                # No agent_id — the pre-existing default, runs as Sage.
            ))

        get_active_mock.assert_awaited_once_with("ws-1", "whatsapp_personal")
        self.assertEqual(handle_mock.call_args.kwargs["thread_id"], "sage-main")

    def test_legacy_unscoped_preserves_an_existing_stored_override(self):
        """A workspace that already ran /new on this channel (a stored
        channel_active_threads override) must keep resolving to that exact
        thread — no regression for an existing conversation."""
        with (
            patch(
                "server_modules.sage_command_dispatcher.get_active_thread",
                new=AsyncMock(return_value="thread_prior_conversation_abc123"),
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

        self.assertEqual(handle_mock.call_args.kwargs["thread_id"], "thread_prior_conversation_abc123")


if __name__ == "__main__":
    unittest.main()
