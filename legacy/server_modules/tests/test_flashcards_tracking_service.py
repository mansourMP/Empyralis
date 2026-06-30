from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import flashcards_tracking_service, mini_apps_service, workspace_context


class FlashcardsTrackingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="flashcards-service-")
        self._workspace_patch = patch.object(workspace_context, "_WORKSPACE_DIR", Path(self._tmpdir.name) / "workspace")
        self._workspace_patch.start()

    def tearDown(self) -> None:
        self._workspace_patch.stop()
        self._tmpdir.cleanup()

    def test_create_card_review_and_deck_metadata_sync_contract(self) -> None:
        flashcards_tracking_service.create_flashcard(
            "ws-1",
            {
                "deck": "Spanish A1",
                "topic": "irregular verbs",
                "front": "Conjugate ir (yo)",
                "back": "voy",
            },
            write_authorization={"explicit_user_intent": True},
        )
        flashcards_tracking_service.update_deck_metadata(
            "ws-1",
            {
                "deck": "Spanish A1",
                "language": "Spanish",
                "target_reviews_per_day": 60,
            },
            write_authorization={"explicit_user_intent": True},
        )
        flashcards_tracking_service.log_review_result(
            "ws-1",
            {
                "deck": "Spanish A1",
                "topic": "irregular verbs",
                "correct": False,
                "quality": 2,
            },
            write_authorization={"explicit_user_intent": True},
        )

        contract = mini_apps_service.get_mini_app_contract("ws-1", "flashcards")
        self.assertEqual(contract["app_id"], "flashcards")
        self.assertEqual(contract["current_state"]["deck_count"], 1)
        self.assertEqual(contract["records_count"], 3)
        self.assertTrue(contract["weekly_summary"]["weak_topics"])

    def test_retrieve_records_by_deck_topic_and_date(self) -> None:
        flashcards_tracking_service.log_review_result(
            "ws-1",
            {
                "deck": "Spanish A1",
                "topic": "verbs",
                "correct": True,
                "timestamp": "2026-04-16T09:00:00Z",
            },
            write_authorization={"explicit_user_intent": True},
        )
        flashcards_tracking_service.log_review_result(
            "ws-1",
            {
                "deck": "Spanish A1",
                "topic": "food",
                "correct": False,
                "timestamp": "2026-04-17T09:00:00Z",
            },
            write_authorization={"explicit_user_intent": True},
        )
        flashcards_tracking_service.log_review_result(
            "ws-1",
            {
                "deck": "French A1",
                "topic": "verbs",
                "correct": False,
                "timestamp": "2026-04-17T10:00:00Z",
            },
            write_authorization={"explicit_user_intent": True},
        )

        filtered = flashcards_tracking_service.retrieve_flashcard_records(
            "ws-1",
            deck="Spanish A1",
            topic="food",
            since="2026-04-17T00:00:00Z",
            until="2026-04-17T23:59:59Z",
        )
        self.assertEqual(filtered["total_matches"], 1)
        self.assertEqual(filtered["items"][0]["topic"], "food")

    def test_write_requires_explicit_user_intent(self) -> None:
        with self.assertRaises(PermissionError):
            flashcards_tracking_service.create_flashcard(
                "ws-1",
                {
                    "deck": "Spanish A1",
                    "front": "hola",
                    "back": "hello",
                },
            )


if __name__ == "__main__":
    unittest.main()
