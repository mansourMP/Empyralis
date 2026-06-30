import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server_modules.connectors import telegram_space_service


class TelegramSpaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="telegram-space-service-")
        self.addCleanup(self._tmpdir.cleanup)
        self.project_root = Path(self._tmpdir.name)
        spaces_root = self.project_root / "spaces"
        spaces_root.mkdir(parents=True, exist_ok=True)
        pool_dir = spaces_root / "pool"
        pool_dir.mkdir(parents=True, exist_ok=True)
        (pool_dir / "config.json").write_text(
            json.dumps({"space_name": "Main Pool", "aliases": ["swimming pool"]}),
            encoding="utf-8",
        )
        lobby_dir = spaces_root / "lobby"
        lobby_dir.mkdir(parents=True, exist_ok=True)
        (lobby_dir / "config.json").write_text(json.dumps({"space_name": "Main Lobby"}), encoding="utf-8")

    def test_catalog_and_resolve_space_id_use_space_configs(self) -> None:
        catalog = telegram_space_service.telegram_space_catalog(self.project_root)

        self.assertEqual(catalog[0]["space_id"], "lobby")
        self.assertEqual(catalog[1]["space_id"], "pool")
        self.assertEqual(
            telegram_space_service.telegram_resolve_space_id(
                "Is the swimming pool open right now?",
                project_root=self.project_root,
            ),
            "pool",
        )

    def test_looks_like_space_question_matches_expected_phrasing(self) -> None:
        self.assertTrue(telegram_space_service.telegram_looks_like_space_question("How many people are in the pool?"))
        self.assertTrue(telegram_space_service.telegram_looks_like_space_question("Is the lobby open?"))
        self.assertFalse(telegram_space_service.telegram_looks_like_space_question("Send a Slack update."))

    def test_mcp_result_payload_prefers_structured_content_and_parses_text_content(self) -> None:
        structured = SimpleNamespace(structuredContent={"space_id": "pool", "status": "open"})
        text_result = SimpleNamespace(content=[SimpleNamespace(text='{"space_id":"pool","status":"busy"}')])
        plain_text = SimpleNamespace(content=[SimpleNamespace(text="Pool looks clear.")])

        self.assertEqual(
            telegram_space_service.mcp_result_payload(structured),
            {"space_id": "pool", "status": "open"},
        )
        self.assertEqual(
            telegram_space_service.mcp_result_payload(text_result),
            {"space_id": "pool", "status": "busy"},
        )
        self.assertEqual(
            telegram_space_service.mcp_result_payload(plain_text),
            {"text": "Pool looks clear."},
        )

    def test_render_space_answer_handles_occupancy_busy_and_open_questions(self) -> None:
        state = {
            "space_name": "Main Pool",
            "status": "open_busy",
            "occupancy_count": 7,
            "timestamp": "2026-04-04T10:00:00Z",
            "summary_lines": ["Main Pool is active."],
        }

        occupancy = telegram_space_service.telegram_render_space_answer("How many people are in the pool?", state, "pool")
        busy = telegram_space_service.telegram_render_space_answer("Is the pool busy?", state, "pool")
        open_now = telegram_space_service.telegram_render_space_answer("Is the pool open?", state, "pool")

        self.assertIn("7 people", occupancy)
        self.assertIn("busy", busy.lower())
        self.assertIn("open", open_now.lower())

    def test_space_question_returns_unhandled_when_disabled(self) -> None:
        result = telegram_space_service.telegram_space_question_via_mcp(
            "Is the pool open?",
            enabled=False,
            project_root=self.project_root,
        )

        self.assertEqual(result, {"handled": False, "response": ""})

    def test_space_question_returns_handled_response_when_mcp_payload_exists(self) -> None:
        with patch.object(
            telegram_space_service,
            "telegram_space_status_via_mcp_async",
            new=AsyncMock(
                return_value={
                    "space_id": "pool",
                    "space_name": "Main Pool",
                    "status": "open",
                    "occupancy_count": 3,
                    "timestamp": "2026-04-04T12:00:00Z",
                }
            ),
        ):
            result = telegram_space_service.telegram_space_question_via_mcp(
                "Is the swimming pool open?",
                enabled=True,
                project_root=self.project_root,
            )

        self.assertTrue(result["handled"])
        self.assertIn("Main Pool", result["response"])
        self.assertEqual(result["metadata"]["space_id"], "pool")
