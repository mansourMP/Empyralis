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


if __name__ == "__main__":
    unittest.main()
