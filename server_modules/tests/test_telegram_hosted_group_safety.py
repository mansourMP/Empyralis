"""Tests for Telegram Hosted Path B group-pairing + sender-identity safety.

FIX B regression cover for two distinct bugs in the SAME hosted-bot pairing
flow (sage_telegram_hosted_service.py):

1. handle_inbound_message() had no chat.type == "private" check, so ANYONE
   typing "/start <token>" inside a group the bot had been added to could
   permanently pair the WHOLE group to that workspace — and even short of a
   successful pairing, an unpaired group got the "Welcome to Empyralis...
   pairing code" reply on EVERY single message, since that branch replied
   unconditionally either way.
2. _process_update (and the routes_sage_telegram_hosted.py / BYO
   equivalents) substituted chat_id for sender_id when calling the shared
   command dispatcher / reply dispatcher. A group chat_id is shared by every
   member, so this collapsed every distinct sender into the same identity —
   two different people typing in the same paired group would be
   indistinguishable to _is_sender_owner() and to any audit trail.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch


def _telegram_update(*, update_id: int, chat_id: int, chat_type: str, text: str, from_id: int, message_id: int = 1, first_name: str = "Someone", addressed: bool = False) -> dict:
    message: dict = {
        "message_id": message_id,
        "date": 1700000000,
        "chat": {"id": chat_id, "type": chat_type, "title": "Some Chat" if chat_type != "private" else None},
        "from": {"id": from_id, "first_name": first_name, "is_bot": False},
        "text": text,
    }
    if addressed:
        # These sender-identity tests are about _process_update's sender_id
        # threading, not the group-addressing gate (see
        # test_telegram_hosted_group_gate.py for that) — a group fixture
        # must reply-to-bot (matching is_message_addressed_to_bot's default
        # SAGE_TELEGRAM_HOSTED_BOT_USER_ID fallback) so it isn't silenced by
        # that separate, later gate before sender_id is ever observed.
        message["reply_to_message"] = {"message_id": 1, "from": {"id": 8870032163}}
    return {"update_id": update_id, "message": message}


class PairingIsPrivateChatOnlyTests(unittest.IsolatedAsyncioTestCase):
    """handle_inbound_message must never pair, or reply to, a non-private chat."""

    def setUp(self) -> None:
        import server_modules.sage_telegram_hosted_service as hosted

        self.hosted = hosted
        # Isolate module-level pairing state from whatever the real dev
        # machine or other tests may have persisted, and never touch disk.
        self._pairs_backup = dict(hosted._SAGE_HOSTED_PAIRS)
        self._codes_backup = dict(hosted._PENDING_PAIRING_CODES)
        self._code_times_backup = dict(hosted._PENDING_PAIRING_CODE_TIMES)
        self._tokens_backup = dict(hosted._PENDING_DEEP_LINK_TOKENS)
        hosted._SAGE_HOSTED_PAIRS.clear()
        hosted._PENDING_PAIRING_CODES.clear()
        hosted._PENDING_PAIRING_CODE_TIMES.clear()
        hosted._PENDING_DEEP_LINK_TOKENS.clear()
        self._persist_patch = patch.object(hosted, "_persist_after_mutation", lambda: None)
        self._persist_patch.start()

    def tearDown(self) -> None:
        self._persist_patch.stop()
        self.hosted._SAGE_HOSTED_PAIRS.clear()
        self.hosted._SAGE_HOSTED_PAIRS.update(self._pairs_backup)
        self.hosted._PENDING_PAIRING_CODES.clear()
        self.hosted._PENDING_PAIRING_CODES.update(self._codes_backup)
        self.hosted._PENDING_PAIRING_CODE_TIMES.clear()
        self.hosted._PENDING_PAIRING_CODE_TIMES.update(self._code_times_backup)
        self.hosted._PENDING_DEEP_LINK_TOKENS.clear()
        self.hosted._PENDING_DEEP_LINK_TOKENS.update(self._tokens_backup)

    async def test_start_with_valid_token_from_a_group_is_rejected(self) -> None:
        hosted = self.hosted
        hosted._PENDING_DEEP_LINK_TOKENS["good-token"] = "ws-1"
        with patch.object(hosted, "send_message", new=AsyncMock(return_value={"ok": True})) as mock_send:
            result = await hosted.handle_inbound_message(
                {"chat_id": "-100999", "chat_type": "group", "text": "/start good-token", "message_id": 1}
            )
        self.assertIsNone(result, "a group must never come back as a paired/usable chat_id")
        self.assertFalse(hosted.is_paired("-100999"), "the group must not be paired")
        # The token must not be consumed either — a legitimate owner in a
        # private chat should still be able to use it afterward.
        self.assertIn("good-token", hosted._PENDING_DEEP_LINK_TOKENS)
        mock_send.assert_not_called()

    async def test_start_with_valid_token_from_a_supergroup_is_rejected(self) -> None:
        hosted = self.hosted
        hosted._PENDING_DEEP_LINK_TOKENS["good-token"] = "ws-1"
        with patch.object(hosted, "send_message", new=AsyncMock(return_value={"ok": True})) as mock_send:
            result = await hosted.handle_inbound_message(
                {"chat_id": "-100999", "chat_type": "supergroup", "text": "/start good-token", "message_id": 1}
            )
        self.assertIsNone(result)
        self.assertFalse(hosted.is_paired("-100999"))
        mock_send.assert_not_called()

    async def test_unpaired_group_ordinary_message_gets_no_reply(self) -> None:
        # THE spam bug: previously any message (not just /start) in an
        # unpaired group/channel got the "Welcome to Empyralis... pairing
        # code" reply, because this branch replied unconditionally.
        hosted = self.hosted
        with patch.object(hosted, "send_message", new=AsyncMock(return_value={"ok": True})) as mock_send:
            result = await hosted.handle_inbound_message(
                {"chat_id": "-100999", "chat_type": "group", "text": "anyone up for dinner?", "message_id": 2}
            )
        self.assertIsNone(result)
        mock_send.assert_not_called()

    async def test_unpaired_channel_post_shaped_chat_type_gets_no_reply(self) -> None:
        hosted = self.hosted
        with patch.object(hosted, "send_message", new=AsyncMock(return_value={"ok": True})) as mock_send:
            result = await hosted.handle_inbound_message(
                {"chat_id": "-100888", "chat_type": "channel", "text": "announcement", "message_id": 3}
            )
        self.assertIsNone(result)
        mock_send.assert_not_called()

    async def test_start_with_valid_token_from_private_chat_still_pairs(self) -> None:
        # Regression guard: the legitimate owner DM pairing flow must be
        # completely unaffected.
        hosted = self.hosted
        hosted._PENDING_DEEP_LINK_TOKENS["good-token"] = "ws-1"
        with patch.object(hosted, "send_message", new=AsyncMock(return_value={"ok": True})) as mock_send:
            result = await hosted.handle_inbound_message(
                {"chat_id": "555444", "chat_type": "private", "text": "/start good-token", "message_id": 4}
            )
        self.assertEqual(result, None)  # returns None on the pairing turn itself, same as before
        self.assertTrue(hosted.is_paired("555444"))
        self.assertEqual(hosted.get_workspace_for_chat("555444"), "ws-1")
        mock_send.assert_awaited_once()
        self.assertIn("connected to Empyralis", mock_send.await_args.args[1])

    async def test_private_chat_with_no_valid_code_still_gets_help_instructions(self) -> None:
        # Regression guard: a genuine DM with garbage/no code still gets the
        # "how to pair" reply — only groups/channels are silenced.
        hosted = self.hosted
        with patch.object(hosted, "send_message", new=AsyncMock(return_value={"ok": True})) as mock_send:
            result = await hosted.handle_inbound_message(
                {"chat_id": "555444", "chat_type": "private", "text": "hi there", "message_id": 5}
            )
        self.assertIsNone(result)
        mock_send.assert_awaited_once()
        self.assertIn("Welcome to Empyralis", mock_send.await_args.args[1])

    async def test_already_paired_group_chat_is_unaffected_by_this_check(self) -> None:
        # The private-only restriction only gates NEW pairing attempts (the
        # `if not is_paired(chat_id)` branch) — a chat that is ALREADY
        # paired (e.g. legacy state from before this fix) must still reach
        # the caller as before; group-mention gating for that case is a
        # separate, later fix (is_message_addressed_to_bot).
        hosted = self.hosted
        hosted._SAGE_HOSTED_PAIRS["-100777"] = {"workspace_id": "ws-legacy", "paired_at": "2020-01-01T00:00:00+00:00"}
        result = await hosted.handle_inbound_message(
            {"chat_id": "-100777", "chat_type": "group", "text": "hello", "message_id": 6}
        )
        self.assertEqual(result, "-100777")


class ProcessUpdateSenderIdentityTests(unittest.IsolatedAsyncioTestCase):
    """_process_update must carry the real per-message sender id, not chat_id."""

    def setUp(self) -> None:
        import server_modules.sage_telegram_hosted_service as hosted

        self.hosted = hosted
        self._pairs_backup = dict(hosted._SAGE_HOSTED_PAIRS)
        hosted._SAGE_HOSTED_PAIRS.clear()
        # A group already paired (legacy state) — this is exactly the case
        # where two different real people can post into the SAME chat_id.
        hosted._SAGE_HOSTED_PAIRS["-100555"] = {"workspace_id": "ws-1", "paired_at": "2020-01-01T00:00:00+00:00"}
        self._persist_patch = patch.object(hosted, "_persist_after_mutation", lambda: None)
        self._persist_patch.start()

    def tearDown(self) -> None:
        self._persist_patch.stop()
        self.hosted._SAGE_HOSTED_PAIRS.clear()
        self.hosted._SAGE_HOSTED_PAIRS.update(self._pairs_backup)

    async def test_two_different_group_members_get_two_distinct_sender_ids(self) -> None:
        hosted = self.hosted
        captured_sender_ids = []

        async def _fake_dispatch_sage_reply_safe(**kwargs):
            captured_sender_ids.append(kwargs.get("sender_id"))
            return True

        with patch("server_modules.agent_command_dispatcher.dispatch_command", new=AsyncMock(return_value=None)), \
             patch("server_modules.agent_reply_dispatcher.dispatch_sage_reply_safe", new=_fake_dispatch_sage_reply_safe):
            await hosted._process_update(
                _telegram_update(update_id=1, chat_id=-100555, chat_type="group", text="hi from alice", from_id=111, first_name="Alice", addressed=True)
            )
            await hosted._process_update(
                _telegram_update(update_id=2, chat_id=-100555, chat_type="group", text="hi from bob", from_id=222, first_name="Bob", addressed=True)
            )

        self.assertEqual(len(captured_sender_ids), 2)
        self.assertEqual(captured_sender_ids[0], "111")
        self.assertEqual(captured_sender_ids[1], "222")
        self.assertNotEqual(captured_sender_ids[0], captured_sender_ids[1])
        # Neither sender_id is the chat_id — the old collapse bug.
        for sid in captured_sender_ids:
            self.assertNotEqual(sid, "-100555")

    async def test_sender_id_reaches_the_command_dispatcher_too(self) -> None:
        hosted = self.hosted
        captured = {}

        async def _fake_dispatch_command(**kwargs):
            captured.update(kwargs)
            return "compacted"

        with patch("server_modules.agent_command_dispatcher.dispatch_command", new=_fake_dispatch_command), \
             patch.object(hosted, "send_message_safe", new=AsyncMock(return_value=True)):
            await hosted._process_update(
                _telegram_update(update_id=3, chat_id=-100555, chat_type="group", text="/compact", from_id=333, first_name="Carol", addressed=True)
            )

        self.assertEqual(captured.get("sender_id"), "333")


if __name__ == "__main__":
    unittest.main()
