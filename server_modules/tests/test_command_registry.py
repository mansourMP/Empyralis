"""command_registry — owner-gated command access control.

Tests:
  (a) _is_sender_owner: matches sender_id against workspace identity_links
      (channel-agnostic); False on no match, missing input, or lookup error
  (b) dispatch(): an owner-gated command is silently unrecognized (None) for
      a non-owner sender, and executes for a sender that matches identity_links
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import command_registry


def _run(coro):
    return asyncio.run(coro)


class IsSenderOwnerTests(unittest.TestCase):

    def test_empty_sender_id_or_workspace_id_is_false(self):
        self.assertFalse(_run(command_registry._is_sender_owner("", "ws-1")))
        self.assertFalse(_run(command_registry._is_sender_owner("sender-1", "")))

    def test_matches_linked_user_id_on_any_channel(self):
        workspace = {
            "identity_links": {
                "telegram_personal": {"user_id": "tg-owner-123", "sender_hash": ""},
            }
        }
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertTrue(_run(command_registry._is_sender_owner("tg-owner-123", "ws-1")))

    def test_matches_owner_sender_hash_on_any_channel(self):
        workspace = {
            "identity_links": {
                "discord_personal": {"user_id": "", "sender_hash": "hash-abc"},
            }
        }
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertTrue(_run(command_registry._is_sender_owner("hash-abc", "ws-1")))

    def test_no_match_is_false(self):
        workspace = {
            "identity_links": {
                "telegram_personal": {"user_id": "tg-owner-123", "sender_hash": ""},
            }
        }
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertFalse(_run(command_registry._is_sender_owner("some-customer-id", "ws-1")))

    def test_string_encoded_identity_links_are_parsed(self):
        workspace = {
            "identity_links": '{"telegram_personal": {"user_id": "tg-owner-123"}}'
        }
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertTrue(_run(command_registry._is_sender_owner("tg-owner-123", "ws-1")))

    def test_missing_workspace_is_false(self):
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=None),
        ):
            self.assertFalse(_run(command_registry._is_sender_owner("tg-owner-123", "ws-1")))

    def test_lookup_error_fails_closed_to_not_owner(self):
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ):
            self.assertFalse(_run(command_registry._is_sender_owner("tg-owner-123", "ws-1")))

    def test_matches_workspace_created_by_user_id_for_the_web_surface(self):
        """The web surface's sender_id is the authenticated platform user's
        own id — never a channel identity, so identity_links can never
        match it. Before this fix every owner-gated command silently
        failed the owner check for every web caller, including the actual
        workspace owner."""
        workspace = {"created_by_user_id": "user-abc-123", "identity_links": {}}
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertTrue(_run(command_registry._is_sender_owner("user-abc-123", "ws-1")))

    def test_created_by_user_id_mismatch_falls_through_to_identity_links(self):
        """A workspace member who did not create the workspace but IS
        linked as a channel owner (an edge case, but the two checks must
        not shadow each other) still resolves as owner."""
        workspace = {
            "created_by_user_id": "someone-else",
            "identity_links": {"telegram_personal": {"user_id": "tg-owner-123"}},
        }
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertTrue(_run(command_registry._is_sender_owner("tg-owner-123", "ws-1")))

    def test_neither_created_by_user_id_nor_identity_links_match_is_false(self):
        workspace = {"created_by_user_id": "someone-else", "identity_links": {}}
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            self.assertFalse(_run(command_registry._is_sender_owner("user-abc-123", "ws-1")))


class OwnerGatedDispatchTests(unittest.TestCase):
    """End-to-end proof that dispatch() gates access via the fixed
    _is_sender_owner(), not the old always-False stub."""

    def setUp(self):
        self._test_command_name = "__test_owner_only_mandate_probe__"
        self.handler = AsyncMock(return_value={"reply": "executed"})
        command_registry.register(
            self._test_command_name,
            self.handler,
            access="owner",
            scope="both",
        )

    def tearDown(self):
        command_registry._registry.pop(self._test_command_name, None)
        command_registry._handlers.pop(self._test_command_name, None)

    def test_non_owner_sender_gets_unrecognized_command(self):
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"identity_links": {}}),
        ):
            result = _run(
                command_registry.dispatch(
                    text=f"/{self._test_command_name}",
                    workspace_id="ws-1",
                    sender_id="some-customer-id",
                )
            )
        self.assertIsNone(result)
        self.handler.assert_not_called()

    def test_owner_sender_executes_the_command(self):
        workspace = {"identity_links": {"telegram_personal": {"user_id": "owner-1"}}}
        with patch(
            "server_modules.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value=workspace),
        ):
            result = _run(
                command_registry.dispatch(
                    text=f"/{self._test_command_name}",
                    workspace_id="ws-1",
                    sender_id="owner-1",
                )
            )
        self.assertEqual(result, {"reply": "executed"})
        self.handler.assert_called_once()


class HandleThinkingPersistsPerAgentTests(unittest.TestCase):
    """/thinking used to write to workspace-global sage_ai_reasoning_effort
    metadata that nothing but /config's own display ever read back — a
    value set here never reached an actual reply turn. It now persists to
    the ACTING agent's own model_config.reasoning_effort, the same
    per-agent field the Fleet Model tab's picker reads/writes and that
    sage_agent_runtime_service.py's handle_sage_chat / _run_sage_action_
    loop_v3 actually consult for the completion."""

    @staticmethod
    def _bundle(agent_id="agent-x", model_config=None):
        return {
            "id": agent_id,
            "install_metadata": {"model_config": dict(model_config or {})},
        }

    def _dispatch_thinking(self, level="high", **kwargs):
        return _run(
            command_registry.dispatch(
                text=f"/thinking {level}",
                workspace_id="ws-1",
                surface="web",
                **kwargs,
            )
        )

    def test_invalid_level_never_touches_storage(self):
        exploding = AsyncMock(side_effect=AssertionError("must not resolve tenant/agent for a bad level"))
        with patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=exploding,
        ):
            result = self._dispatch_thinking(level="ultra-mega")
        self.assertIn("Usage:", result["reply"])

    def test_no_agent_install_id_targets_the_workspace_master(self):
        master = self._bundle(agent_id="sage-main-1", model_config={"mode": "platform_credits"})
        captured = {}

        async def _capture_configure(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        with (
            patch(
                "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                new=AsyncMock(return_value="tenant-1"),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value=master),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=master),
            ),
            patch(
                "server_modules.fleet_tools.fleet_configure_agent",
                new=_capture_configure,
            ),
        ):
            result = self._dispatch_thinking(level="high")
        self.assertEqual(captured["agent_id"], "sage-main-1")
        self.assertEqual(captured["workspace_id"], "ws-1")
        self.assertEqual(captured["patch"]["model_config"]["reasoning_effort"], "high")
        self.assertIn("persisted for this agent", result["reply"])

    def test_an_explicit_agent_install_id_targets_that_specialist_not_the_master(self):
        specialist = self._bundle(
            agent_id="agent-specialist-1",
            model_config={"mode": "cli_subscription", "runtime": "codex", "gateway_binding": "gw-1"},
        )
        exploding_master_lookup = AsyncMock(
            side_effect=AssertionError("must not look up the master when an agent_install_id is given")
        )
        captured = {}

        async def _capture_configure(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        with (
            patch(
                "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                new=AsyncMock(return_value="tenant-1"),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_master_agent_install",
                new=exploding_master_lookup,
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=specialist),
            ),
            patch(
                "server_modules.fleet_tools.fleet_configure_agent",
                new=_capture_configure,
            ),
        ):
            result = self._dispatch_thinking(level="off", agent_install_id="agent-specialist-1")
        self.assertEqual(captured["agent_id"], "agent-specialist-1")
        # Preserves the specialist's existing binding — never wholesale-wipes
        # mode/runtime/gateway_binding just to set reasoning_effort.
        self.assertEqual(captured["patch"]["model_config"], {
            "mode": "cli_subscription", "runtime": "codex", "gateway_binding": "gw-1",
            "reasoning_effort": "off",
        })
        self.assertIn("persisted for this agent", result["reply"])

    def test_a_value_invalid_for_the_targeted_agents_mode_surfaces_the_real_error(self):
        """e.g. "off" saved while bound to claude_code, which has no such
        --effort value — fleet_configure_agent's own validation rejects it;
        the command must report that honestly, not claim success."""
        specialist = self._bundle(
            agent_id="agent-specialist-1",
            model_config={"mode": "cli_subscription", "runtime": "claude_code"},
        )
        with (
            patch(
                "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
                new=AsyncMock(return_value="tenant-1"),
            ),
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=specialist),
            ),
        ):
            result = self._dispatch_thinking(level="off", agent_install_id="agent-specialist-1")
        self.assertNotIn("persisted for this agent", result["reply"])
        self.assertIn("off", result["reply"])


if __name__ == "__main__":
    unittest.main()
