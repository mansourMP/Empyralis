"""BYO bot owner-recognition claim — the gap this closes:

command_registry._is_sender_owner and agent_turn_runtime_service.
_resolve_channel_sender_class both read personal_channels_repository as
their ONLY authoritative source for "is this sender the owner" (see
test_command_registry_channel_owner_identity.py / test_channel_sender_
owner_authority.py — CLAUDE.md's own "identity_links is a dead column"
finding). That store is populated by a real phone/QR pairing or login
event for every channel family EXCEPT the BYO-bot family
(hosted_bot_provisioning_service.py's sage_telegram_hosted / a future
discord/wechat equivalent) — a BYO bot has no pairing step at all, the
customer just pastes a token. So there was NO writer for this family, at
all, meaning EVERY sender on a BYO Telegram bot — the workspace's own
owner included — was permanently classified "audience": no shell/
hardware/memory_write/connector_write tools, and every owner-gated
command (/config /mcp /plugins /debug /bash) silently unreachable.

personal_channels_repository.claim_channel_owner_identity_if_unclaimed is
the fix: the first PRIVATE (never group) message on a fresh BYO binding
claims workspace-wide owner recognition for that channel family, one-shot
— a later sender can never displace an already-claimed identity, which
would let a stranger who messages the bot after the real owner inherit
tool authority the real owner already established. hosted_bot_
provisioning_service.route_agent_inbound calls this on every private
message, best-effort (a claim failure must never block the real turn).

Every test in this file fails against a build with no claim writer at
all (the pre-fix state: _resolve_channel_sender_class/_is_sender_owner
resolve "audience" for literally every BYO-channel sender, forever) and
passes once the claim path exists and is wired.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import command_registry
from server_modules import hosted_bot_provisioning_service as prov
from server_modules import personal_channels_repository
from server_modules import agent_turn_runtime_service


def _run(coro):
    return asyncio.run(coro)


_OWNER_ID = "1932934047"
_STRANGER_ID = "778899001"
_ANOTHER_STRANGER_ID = "445566778"


class ClaimRepositoryTests(unittest.TestCase):
    """Direct, DB-backed tests against a throwaway temp SQLite file (never
    the developer's real ~/.empyralis/state personal-channels database) —
    same discipline as test_channel_sender_owner_authority.py."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "byo-claim-test.sqlite3"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_first_claim_wins(self) -> None:
        won = personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-1", sender_id=_OWNER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        self.assertTrue(won)
        linked = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-1", db_path=self.db_path,
        )
        self.assertEqual(linked.get("telegram_agent_byo"), _OWNER_ID)

    def test_same_sender_reclaiming_is_idempotent(self) -> None:
        personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-1", sender_id=_OWNER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        won_again = personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-1", sender_id=_OWNER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        self.assertTrue(won_again)

    def test_a_second_different_sender_does_not_displace_the_first(self) -> None:
        """THE CORE SAFETY PROPERTY. Owner recognition must not silently
        migrate to whoever messaged last — that would let a stranger who
        messages the bot after the real owner inherit tool authority the
        real owner already established."""
        personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-1", sender_id=_OWNER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        stranger_won = personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-1", sender_id=_STRANGER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        self.assertFalse(stranger_won)
        linked = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-1", db_path=self.db_path,
        )
        # Still the original owner, byte for byte — not overwritten.
        self.assertEqual(linked.get("telegram_agent_byo"), _OWNER_ID)

    def test_a_second_bot_in_the_same_workspace_does_not_reset_the_claim(self) -> None:
        """A second BYO bot (different agent_id, same workspace) whose
        first message comes from a DIFFERENT sender must not steal the
        workspace's already-established owner identity — assign_byo_bot
        requires minimum_role="owner" on every BYO bot, so a second bot's
        binding is still the same workspace owner in the overwhelming
        case; a different first sender there is the anomaly, not the
        signal to trust."""
        personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-1", sender_id=_OWNER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        second_bot_stranger_won = personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-2",
            tenant_id="t-1", workspace_id="ws-1", sender_id=_ANOTHER_STRANGER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        self.assertFalse(second_bot_stranger_won)

    def test_claims_are_isolated_per_workspace(self) -> None:
        personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-1", sender_id=_OWNER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        won_in_other_workspace = personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-9",
            tenant_id="t-2", workspace_id="ws-2", sender_id=_STRANGER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        self.assertTrue(won_in_other_workspace)
        linked_ws1 = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-1", db_path=self.db_path,
        )
        linked_ws2 = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-2", db_path=self.db_path,
        )
        self.assertEqual(linked_ws1.get("telegram_agent_byo"), _OWNER_ID)
        self.assertEqual(linked_ws2.get("telegram_agent_byo"), _STRANGER_ID)

    def test_empty_sender_id_never_claims(self) -> None:
        won = personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-1", sender_id="",
            provider="telegram_byo", db_path=self.db_path,
        )
        self.assertFalse(won)
        linked = personal_channels_repository.list_owner_linked_channel_identities_for_workspace(
            "ws-1", db_path=self.db_path,
        )
        self.assertEqual(linked, {})


class ClaimReachesToolAuthorityAndCommandOwnershipTests(unittest.TestCase):
    """End-to-end: a written claim must actually change what the two real
    consumers (_resolve_channel_sender_class, command_registry.
    _is_sender_owner) decide — proving the claim lands in the exact table
    those functions already read, not a fourth, disconnected store."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "byo-claim-e2e-test.sqlite3"
        won = personal_channels_repository.claim_channel_owner_identity_if_unclaimed(
            gateway_id="byo", channel_key="telegram_agent_byo", agent_id="agent-1",
            tenant_id="t-1", workspace_id="ws-byo", sender_id=_OWNER_ID,
            provider="telegram_byo", db_path=self.db_path,
        )
        assert won

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _patched_db_path(self):
        return patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", self.db_path)

    def test_the_claimed_owner_gets_owner_tool_authority(self) -> None:
        with self._patched_db_path():
            result = _run(agent_turn_runtime_service._resolve_channel_sender_class(
                channel_origin="telegram_agent_byo", sender_id=_OWNER_ID, workspace_id="ws-byo",
                agent_id="agent-1",
            ))
        self.assertEqual(result, "owner")

    def test_a_stranger_on_the_same_bot_still_gets_only_audience_authority(self) -> None:
        with self._patched_db_path():
            result = _run(agent_turn_runtime_service._resolve_channel_sender_class(
                channel_origin="telegram_agent_byo", sender_id=_STRANGER_ID, workspace_id="ws-byo",
                agent_id="agent-1",
            ))
        self.assertEqual(result, "audience")

    def test_the_claimed_owner_can_run_owner_gated_commands(self) -> None:
        with (
            self._patched_db_path(),
            patch(
                "server_modules.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"created_by_user_id": "platform-user-uuid", "identity_links": {}}),
            ),
        ):
            result = _run(command_registry._is_sender_owner(_OWNER_ID, "ws-byo", "telegram_agent_byo"))
        self.assertTrue(result)

    def test_a_stranger_cannot_run_owner_gated_commands(self) -> None:
        with (
            self._patched_db_path(),
            patch(
                "server_modules.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"created_by_user_id": "platform-user-uuid", "identity_links": {}}),
            ),
        ):
            result = _run(command_registry._is_sender_owner(_STRANGER_ID, "ws-byo", "telegram_agent_byo"))
        self.assertFalse(result)


class RouteAgentInboundClaimWiringTests(unittest.IsolatedAsyncioTestCase):
    """route_agent_inbound is the one caller — assert it actually invokes
    the claim on a private message, with the right keys, and never on a
    group message (a group participant is not necessarily the person who
    created the bot)."""

    def _binding(self) -> dict:
        return {
            "workspace_id": "ws-1", "tenant_id": "tenant-1",
            "binding": {"bot_username": "parts_pro_bot", "credential_id": "cred-1"},
        }

    async def test_a_private_message_attempts_a_claim(self) -> None:
        with (
            patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=self._binding())),
            patch.object(prov, "resolve_bot_token", return_value="tok"),
            patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)),
            patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})),
            patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)),
            patch("server_modules.personal_channels_repository.claim_channel_owner_identity_if_unclaimed") as claim_mock,
        ):
            await prov.route_agent_inbound(
                agent_install_id="agent-1", chat_id="555", message="hi",
                sender_id=_OWNER_ID, chat_type="private",
            )
        claim_mock.assert_called_once()
        _, kwargs = claim_mock.call_args
        self.assertEqual(kwargs["channel_key"], prov.BYO_OWNER_CLAIM_CHANNEL_KEY)
        self.assertEqual(kwargs["agent_id"], "agent-1")
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["tenant_id"], "tenant-1")
        self.assertEqual(kwargs["sender_id"], _OWNER_ID)

    async def test_a_group_message_never_attempts_a_claim(self) -> None:
        with (
            patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=self._binding())),
            patch.object(prov, "resolve_bot_token", return_value="tok"),
            patch.object(prov, "resolve_byo_bot_id", new=AsyncMock(return_value="")),
            patch("server_modules.sage_telegram_hosted_service.text_addresses_bot", return_value=True),
            patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)),
            patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})),
            patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=AsyncMock(return_value=True)),
            patch("server_modules.personal_channels_repository.claim_channel_owner_identity_if_unclaimed") as claim_mock,
        ):
            await prov.route_agent_inbound(
                agent_install_id="agent-1", chat_id="-100999", message="@parts_pro_bot hi",
                sender_id=_STRANGER_ID, chat_type="group",
            )
        claim_mock.assert_not_called()

    async def test_a_claim_failure_never_blocks_the_real_turn(self) -> None:
        """Best-effort: the claim write is not on the critical path."""
        dispatched = {"called": False}

        async def _fake_dispatch(**kwargs):
            dispatched["called"] = True
            return True

        with (
            patch.object(prov.bindings, "get_channel_binding_by_agent_unscoped", new=AsyncMock(return_value=self._binding())),
            patch.object(prov, "resolve_bot_token", return_value="tok"),
            patch("server_modules.specialist_runtime_context.resolve_specialist_runtime_context", new=AsyncMock(return_value=None)),
            patch("server_modules.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(return_value={"metadata": {}})),
            patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch),
            patch(
                "server_modules.personal_channels_repository.claim_channel_owner_identity_if_unclaimed",
                side_effect=RuntimeError("db unavailable"),
            ),
        ):
            result = await prov.route_agent_inbound(
                agent_install_id="agent-1", chat_id="555", message="hi",
                sender_id=_OWNER_ID, chat_type="private",
            )
        self.assertTrue(dispatched["called"])
        self.assertTrue(result.get("reply_sent"))


if __name__ == "__main__":
    unittest.main()
