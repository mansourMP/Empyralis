"""Tests for TelegramMediaService.extract_message — Telegram Hosted Path A.

FIX A regression cover: a broadcast channel's `channel_post` /
`edited_channel_post` update must be hard-dropped at extraction, never
reaching a live dispatched reply. Before this fix, `extract_message` folded
channel_post/edited_channel_post into the same candidate list as
message/edited_message, so under the "allow any chat" config
public-deployed-agent connectors use, any post from an administered
broadcast channel was extracted and routed exactly like a private message —
the "comments under every post" incident. Path B's parse_telegram_update
(sage_telegram_hosted_service.py:709-712) never had this bug — it only ever
read message/edited_message — so these tests also pin that the two paths
now agree.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from server_modules.connectors.telegram.media import TelegramMediaService


def _make_service() -> TelegramMediaService:
    return TelegramMediaService(
        media_dir=Path("/tmp/empyralis-test-telegram-media"),
        media_enabled=True,
        media_max_items=4,
        media_max_bytes=1024 * 1024,
        media_include_in_goal=True,
        telegram_api_request=lambda *args, **kwargs: {},
    )


class ExtractMessageChannelPostDropTests(unittest.TestCase):
    def test_channel_post_is_dropped(self) -> None:
        service = _make_service()
        update = {
            "update_id": 1,
            "channel_post": {
                "message_id": 55,
                "date": 1700000000,
                "chat": {"id": -100123456789, "type": "channel", "title": "Broadcast Co"},
                "text": "New product launch! Check it out.",
            },
        }
        self.assertIsNone(service.extract_message(update))

    def test_edited_channel_post_is_dropped(self) -> None:
        service = _make_service()
        update = {
            "update_id": 2,
            "edited_channel_post": {
                "message_id": 56,
                "date": 1700000001,
                "chat": {"id": -100123456789, "type": "channel", "title": "Broadcast Co"},
                "text": "New product launch! Check it out (edited).",
            },
        }
        self.assertIsNone(service.extract_message(update))

    def test_channel_post_dropped_even_when_it_mentions_the_bot(self) -> None:
        # A broadcast post that happens to @mention the bot's own username
        # (cross-promo, credits, unrelated content) must not sneak through
        # via some future mention-based gate either — it never even becomes
        # a candidate message.
        service = _make_service()
        update = {
            "update_id": 3,
            "channel_post": {
                "message_id": 57,
                "date": 1700000002,
                "chat": {"id": -100123456789, "type": "channel", "title": "Broadcast Co"},
                "text": "Shoutout to @our_bot for the help!",
                "entities": [{"type": "mention", "offset": 11, "length": 9}],
            },
        }
        self.assertIsNone(service.extract_message(update))

    def test_message_still_extracted(self) -> None:
        service = _make_service()
        update = {
            "update_id": 4,
            "message": {
                "message_id": 10,
                "date": 1700000003,
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 555, "first_name": "Alice"},
                "text": "hello",
            },
        }
        extracted = service.extract_message(update)
        self.assertIsNotNone(extracted)
        assert extracted is not None
        self.assertEqual(extracted["kind"], "message")
        self.assertEqual(extracted["text"], "hello")

    def test_edited_message_still_extracted(self) -> None:
        service = _make_service()
        update = {
            "update_id": 5,
            "edited_message": {
                "message_id": 11,
                "date": 1700000004,
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 555, "first_name": "Alice"},
                "text": "hello, edited",
            },
        }
        extracted = service.extract_message(update)
        self.assertIsNotNone(extracted)
        assert extracted is not None
        self.assertEqual(extracted["kind"], "edited_message")

    def test_business_message_still_extracted(self) -> None:
        # Only channel_post/edited_channel_post are dropped — business
        # messages are a distinct, already-1:1 feature and out of this fix's
        # scope.
        service = _make_service()
        update = {
            "update_id": 6,
            "business_message": {
                "message_id": 12,
                "date": 1700000005,
                "chat": {"id": 777, "type": "private"},
                "from": {"id": 777, "first_name": "Bob"},
                "text": "business chat",
            },
        }
        extracted = service.extract_message(update)
        self.assertIsNotNone(extracted)
        assert extracted is not None
        self.assertEqual(extracted["kind"], "business_message")

    def test_update_with_only_channel_post_keys_returns_none_not_first_match(self) -> None:
        # Guards against a regression where some future edit re-adds
        # channel_post/edited_channel_post to the loop order — an update
        # carrying ONLY those two keys must resolve to None, full stop.
        service = _make_service()
        update = {
            "update_id": 7,
            "channel_post": {"message_id": 1, "chat": {"id": -1, "type": "channel"}, "text": "a"},
            "edited_channel_post": {"message_id": 2, "chat": {"id": -1, "type": "channel"}, "text": "b"},
        }
        self.assertIsNone(service.extract_message(update))


if __name__ == "__main__":
    unittest.main()
