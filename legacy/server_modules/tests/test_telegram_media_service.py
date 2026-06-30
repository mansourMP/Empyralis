import json
import tempfile
import unittest
from pathlib import Path

from server_modules.connectors.telegram_media_service import TelegramMediaService


class TelegramMediaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="telegram-media-service-")
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.media_root = self.root / "media"
        self.service = TelegramMediaService(
            media_dir=self.media_root,
            media_enabled=True,
            media_max_items=4,
            media_max_bytes=1024 * 1024,
            media_include_in_goal=True,
            telegram_api_request=lambda bot_token, method, **kwargs: {"file_path": "photos/file.jpg"},
        )

    def test_extract_message_prefers_largest_photo(self) -> None:
        update = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123},
                "from": {"id": 456, "is_bot": False},
                "caption": "check this",
                "photo": [
                    {"file_id": "small", "width": 50, "height": 50, "file_size": 1000},
                    {"file_id": "large", "width": 1000, "height": 1000, "file_size": 100000},
                ],
            },
        }

        message = self.service.extract_message(update)

        self.assertIsInstance(message, dict)
        attachments = message.get("attachments") if isinstance(message, dict) else []
        self.assertEqual(len(attachments), 1)
        self.assertEqual(str(attachments[0].get("file_id")), "large")

    def test_build_goal_with_attachments_includes_paths(self) -> None:
        merged = self.service.build_goal_with_attachments(
            "Help me understand this screenshot",
            [{"relative_path": ".orion-media/telegram/default/1932/img1.jpg", "mime_type": "image/jpeg"}],
        )

        self.assertIn("Help me understand this screenshot", merged)
        self.assertIn(".orion-media/telegram/default/1932/img1.jpg", merged)

    def test_store_attachments_persists_downloaded_file_metadata(self) -> None:
        def fake_download(bot_token: str, remote_file_path: str, dest_path: Path, max_bytes: int) -> int:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"hello")
            return 5

        self.service.download_file = fake_download  # type: ignore[method-assign]

        stored = self.service.store_attachments(
            bot_token="bot-token",
            workspace_id="default",
            chat_id="123",
            update_id=99,
            message_id="777",
            attachments=[
                {
                    "kind": "photo",
                    "file_id": "file-1",
                    "file_unique_id": "uniq-1",
                    "mime_type": "image/jpeg",
                }
            ],
        )

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["bytes"], 5)
        self.assertTrue(Path(stored[0]["path"]).exists())
        self.assertIn("99_777_1", stored[0]["path"])
