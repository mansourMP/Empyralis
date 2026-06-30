import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import sage_services_service


class SageServicesServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_service_entry_persists_flashcard_and_publishes_event(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with (
                patch("server_modules.sage_services_service.workspace_context.workspace_scope_dir", return_value=root),
                patch(
                    "server_modules.sage_services_service.personal_context_engine.publish_event",
                    new=AsyncMock(return_value={"id": "evt-1"}),
                ) as publish_mock,
            ):
                payload = await sage_services_service.create_service_entry(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    service_id="flashcards",
                    entry={
                        "deck": "Contracts",
                        "topic": "Warranties",
                        "front": "What is a warranty?",
                        "back": "A promise about facts or condition.",
                    },
                    actor_user_id="user-1",
                )

            service = payload["service"]
            self.assertEqual(service["entries"][0]["deck"], "Contracts")
            self.assertEqual(service["entries"][0]["summary"], "Contracts: What is a warranty? -> A promise about facts or condition.")
            self.assertEqual(publish_mock.await_args.kwargs["event_type"], "flashcards_created")
            self.assertTrue((root / "sage_services.json").exists())

    async def test_update_profile_shapes_memory_snapshot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with (
                patch("server_modules.sage_services_service.workspace_context.workspace_scope_dir", return_value=root),
                patch(
                    "server_modules.sage_services_service.personal_context_engine.publish_event",
                    new=AsyncMock(return_value={"id": "evt-2"}),
                ) as publish_mock,
            ):
                payload = await sage_services_service.update_service_profile(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    service_id="language_coach",
                    profile={
                        "target_language": "Spanish",
                        "current_level": "B1",
                        "focus_area": "Deal terms",
                    },
                    actor_user_id="user-1",
                )

            snapshot = payload["service"]["memory_snapshot"]
            self.assertIn("Target language: Spanish", snapshot["preferences"])
            self.assertIn("Focus area: Deal terms", snapshot["active_context"])
            self.assertEqual(publish_mock.await_args.kwargs["event_type"], "saved_preference_updated")
            self.assertEqual(
                publish_mock.await_args.kwargs["metadata"]["write_policy"]["mode"],
                "actor_user",
            )

    async def test_create_service_entry_blocks_silent_write_without_intent_or_approval(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with (
                patch("server_modules.sage_services_service.workspace_context.workspace_scope_dir", return_value=root),
                patch(
                    "server_modules.sage_services_service.personal_context_engine.publish_event",
                    new=AsyncMock(return_value={"id": "evt-blocked"}),
                ),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await sage_services_service.create_service_entry(
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        service_id="flashcards",
                        entry={
                            "deck": "Contracts",
                            "front": "SPA",
                            "back": "Share Purchase Agreement",
                        },
                        actor_user_id=None,
                    )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("explicit_user_intent", str(ctx.exception.detail))

    async def test_create_service_entry_allows_explicit_intent_and_records_policy_metadata(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with (
                patch("server_modules.sage_services_service.workspace_context.workspace_scope_dir", return_value=root),
                patch(
                    "server_modules.sage_services_service.personal_context_engine.publish_event",
                    new=AsyncMock(return_value={"id": "evt-explicit"}),
                ) as publish_mock,
            ):
                payload = await sage_services_service.create_service_entry(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    service_id="flashcards",
                    entry={
                        "deck": "Contracts",
                        "front": "SPA",
                        "back": "Share Purchase Agreement",
                    },
                    actor_user_id=None,
                    write_authorization={
                        "explicit_user_intent": True,
                        "approval_source": "test_case",
                    },
                )

            service = payload["service"]
            self.assertEqual(service["entries"][0]["deck"], "Contracts")
            policy = service["recent_activity"][0]["policy"]
            self.assertEqual(policy["mode"], "explicit_intent")
            self.assertTrue(policy["explicit_user_intent"])
            self.assertEqual(
                publish_mock.await_args.kwargs["metadata"]["write_policy"]["mode"],
                "explicit_intent",
            )

    def test_build_memory_block_summarizes_all_three_services(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_services_service.workspace_context.workspace_scope_dir", return_value=root):
                sage_services_service._save_state(
                    "workspace-1",
                    {
                        "version": 1,
                        "updated_at": "2026-04-15T00:00:00Z",
                        "services": {
                            "flashcards": {
                                "profile": {"focus_topic": "M&A", "active_deck": "Deal terms"},
                                "entries": [{"id": "f-1", "summary": "Deal terms: SPA -> Share Purchase Agreement", "pinned": True}],
                                "activity": [],
                                "updated_at": "2026-04-15T00:00:00Z",
                            },
                            "language_coach": {
                                "profile": {"target_language": "Japanese", "current_level": "A2", "focus_area": "Travel"},
                                "entries": [{"id": "l-1", "summary": "Japanese: こんにちは -> hello", "pinned": False}],
                                "activity": [],
                                "updated_at": "2026-04-15T00:00:00Z",
                            },
                            "nutrition_log": {
                                "profile": {"daily_calorie_target": 2200, "daily_protein_target": 140, "dietary_pattern": "High protein"},
                                "entries": [{"id": "n-1", "summary": "Lunch · 650 kcal · 42g protein", "occurred_on": "2026-04-15", "pinned": False}],
                                "activity": [],
                                "updated_at": "2026-04-15T00:00:00Z",
                            },
                        },
                    },
                )
                block = sage_services_service.build_sage_services_memory_block(workspace_id="workspace-1")

            self.assertIn("Flashcards", block)
            self.assertIn("Language Coach", block)
            self.assertIn("Nutrition Log", block)


if __name__ == "__main__":
    unittest.main()
