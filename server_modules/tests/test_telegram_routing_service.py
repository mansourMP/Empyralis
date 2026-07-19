"""Tests for agent.routing_service.is_addressed_to_bot — the group-addressing
signal that was completely missing for Telegram Path A connectors (FIX C).

Before this fix, the ONLY gate a group/supergroup message went through was
route_message()'s prefix convention (profile["prefix"] / require_prefix),
which is off by default for free-text ("allow any chat") profiles — under
that config, ordinary group chatter with nobody asking for the bot's
attention reached a live, dispatched reply.
"""

from __future__ import annotations

import unittest

from server_modules.agent.routing_service import is_addressed_to_bot


def _mention_entity(offset: int, length: int) -> dict:
    return {"type": "mention", "offset": offset, "length": length}


def _text_mention_entity(offset: int, length: int, user_id: str) -> dict:
    return {"type": "text_mention", "offset": offset, "length": length, "user": {"id": user_id}}


class IsAddressedToBotTests(unittest.TestCase):
    def test_no_entities_no_reply_is_not_addressed(self) -> None:
        self.assertFalse(
            is_addressed_to_bot(
                text="anyone up for dinner tonight?",
                entities=[],
                reply_to_from_id="",
                bot_id="",
                bot_username="parts_pro_bot",
            )
        )

    def test_mention_entity_matching_bot_username_is_addressed(self) -> None:
        text = "@parts_pro_bot can you help with brake pads?"
        self.assertTrue(
            is_addressed_to_bot(
                text=text,
                entities=[_mention_entity(0, len("@parts_pro_bot"))],
                reply_to_from_id="",
                bot_id="",
                bot_username="parts_pro_bot",
            )
        )

    def test_mention_entity_is_case_insensitive_and_at_sign_optional_on_stored_username(self) -> None:
        text = "@Parts_Pro_Bot what do you think?"
        self.assertTrue(
            is_addressed_to_bot(
                text=text,
                entities=[_mention_entity(0, len("@Parts_Pro_Bot"))],
                reply_to_from_id="",
                bot_id="",
                bot_username="@parts_pro_bot",
            )
        )

    def test_mention_entity_for_a_different_bot_is_not_addressed(self) -> None:
        text = "@some_other_bot handle this one"
        self.assertFalse(
            is_addressed_to_bot(
                text=text,
                entities=[_mention_entity(0, len("@some_other_bot"))],
                reply_to_from_id="",
                bot_id="",
                bot_username="parts_pro_bot",
            )
        )

    def test_text_mention_entity_carrying_bot_id_is_addressed(self) -> None:
        self.assertTrue(
            is_addressed_to_bot(
                text="hey can you look into this",
                entities=[_text_mention_entity(4, 3, "bot-id-9")],
                reply_to_from_id="",
                bot_id="bot-id-9",
                bot_username="parts_pro_bot",
            )
        )

    def test_text_mention_entity_for_a_different_id_is_not_addressed(self) -> None:
        self.assertFalse(
            is_addressed_to_bot(
                text="hey can you look into this",
                entities=[_text_mention_entity(4, 3, "someone-else")],
                reply_to_from_id="",
                bot_id="bot-id-9",
                bot_username="parts_pro_bot",
            )
        )

    def test_reply_to_from_id_matching_bot_id_is_addressed(self) -> None:
        self.assertTrue(
            is_addressed_to_bot(
                text="yes please do that",
                entities=[],
                reply_to_from_id="bot-id-9",
                bot_id="bot-id-9",
                bot_username="parts_pro_bot",
            )
        )

    def test_reply_to_from_id_for_a_different_sender_is_not_addressed(self) -> None:
        # This is the exact "reply to the OWNER, not the bot" false-positive
        # class the gateway's hasExplicitTelegramMention fix closed for the
        # full-account GramJS path — proving the bot-side equivalent never
        # had that hole in the first place (reply_to_from_id must equal the
        # BOT's own id, never any other participant's).
        self.assertFalse(
            is_addressed_to_bot(
                text="sounds good, see you then",
                entities=[],
                reply_to_from_id="some-other-group-member",
                bot_id="bot-id-9",
                bot_username="parts_pro_bot",
            )
        )

    def test_no_bot_username_and_no_bot_id_never_matches(self) -> None:
        # Fail-closed: a connector with no resolvable stored identity must
        # never accidentally treat something as addressed.
        self.assertFalse(
            is_addressed_to_bot(
                text="@anything goes here",
                entities=[_mention_entity(0, len("@anything"))],
                reply_to_from_id="",
                bot_id="",
                bot_username="",
            )
        )

    def test_offset_length_out_of_bounds_entity_is_ignored_not_crashed(self) -> None:
        self.assertFalse(
            is_addressed_to_bot(
                text="hi",
                entities=[_mention_entity(50, 20)],
                reply_to_from_id="",
                bot_id="",
                bot_username="parts_pro_bot",
            )
        )

    def test_malformed_entity_offset_is_ignored_not_crashed(self) -> None:
        self.assertFalse(
            is_addressed_to_bot(
                text="hi @parts_pro_bot",
                entities=[{"type": "mention", "offset": "not-a-number", "length": 5}],
                reply_to_from_id="",
                bot_id="",
                bot_username="parts_pro_bot",
            )
        )


if __name__ == "__main__":
    unittest.main()
