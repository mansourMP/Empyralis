import unittest
from unittest.mock import AsyncMock, patch

from server_modules import personal_context_engine


class PersonalContextEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_event_defaults_to_sage_scope_and_summary(self):
        persisted = {
            "id": "pctx-1",
            "source_app": "notifications",
            "event_type": "message_awaiting_reply",
            "entity_id": "thread-42",
            "summary": "Reply is still pending for Alex.",
            "payload": {"contact_name": "Alex"},
            "priority": 75,
            "scope": {"audience": ["sage"], "agent_install_ids": [], "visibility": "structured_event"},
            "seen_by_sage_at": None,
            "created_at": "2026-04-10T00:00:00Z",
            "status": "active",
            "metadata": {},
        }
        with patch.object(
            personal_context_engine.control_plane_repository,
            "append_personal_context_event",
            new=AsyncMock(return_value=persisted),
        ) as append_mock:
            event = await personal_context_engine.publish_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                source_app="notifications",
                event_type="message_awaiting_reply",
                entity_id="thread-42",
                payload={"contact_name": "Alex"},
        )

        self.assertEqual(event["summary"], "Reply is still pending for Alex.")
        self.assertEqual(append_mock.await_args.kwargs["scope"]["audience"], ["sage"])
        self.assertEqual(append_mock.await_args.kwargs["priority"], 75)

    async def test_specialist_feed_only_sees_explicitly_scoped_events(self):
        rows = [
            {
                "id": "pctx-sage",
                "source_app": "calendar",
                "event_type": "calendar_conflict",
                "entity_id": "final",
                "summary": "Calendar conflict detected for Math Final.",
                "payload": {},
                "priority": 82,
                "scope": {"audience": ["sage"]},
                "seen_by_sage_at": None,
                "created_at": "2026-04-10T00:00:00Z",
                "status": "active",
                "metadata": {},
            },
            {
                "id": "pctx-specialist",
                "source_app": "study",
                "event_type": "study_session_missed",
                "entity_id": "chemistry",
                "summary": "Study session for Chemistry was missed.",
                "payload": {},
                "priority": 68,
                "scope": {"audience": ["sage"], "agent_install_ids": ["install-study"]},
                "seen_by_sage_at": None,
                "created_at": "2026-04-10T00:00:00Z",
                "status": "active",
                "metadata": {},
            },
        ]
        with patch.object(
            personal_context_engine.control_plane_repository,
            "list_personal_context_events",
            new=AsyncMock(return_value=rows),
        ):
            items = await personal_context_engine.list_events(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                audience="specialist",
                agent_install_id="install-study",
                limit=10,
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "pctx-specialist")

    async def test_sage_context_payload_summarizes_recent_changes(self):
        with patch.object(
            personal_context_engine,
            "list_events",
            new=AsyncMock(return_value=[
                {
                    "id": "pctx-1",
                    "source_app": "calendar",
                    "event_type": "calendar_conflict",
                    "entity_id": "exam",
                    "summary": "Calendar conflict detected for Exam.",
                    "payload": {},
                    "priority": 82,
                    "scope": {"audience": ["sage"]},
                    "seen_by_sage_at": None,
                    "created_at": "2026-04-10T00:00:00Z",
                    "status": "active",
                    "metadata": {},
                }
            ]),
        ):
            payload = await personal_context_engine.build_sage_context_payload(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                limit=20,
            )

        self.assertEqual(payload["summary"]["count"], 1)
        self.assertEqual(payload["summary"]["by_type"]["calendar_conflict"], 1)
        self.assertEqual(payload["recent_changes"][0]["id"], "pctx-1")

    async def test_publish_event_supports_language_and_nutrition_updates(self):
        persisted = {
            "id": "pctx-2",
            "source_app": "language_coach",
            "event_type": "language_session_logged",
            "entity_id": "entry-1",
            "summary": "Language practice captured for Spanish: hola.",
            "payload": {"language": "Spanish", "phrase": "hola"},
            "priority": 30,
            "scope": {"audience": ["sage"], "agent_install_ids": [], "visibility": "structured_event"},
            "seen_by_sage_at": None,
            "created_at": "2026-04-15T00:00:00Z",
            "status": "active",
            "metadata": {},
        }
        with patch.object(
            personal_context_engine.control_plane_repository,
            "append_personal_context_event",
            new=AsyncMock(return_value=persisted),
        ) as append_mock:
            event = await personal_context_engine.publish_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                source_app="language_coach",
                event_type="language_session_logged",
                entity_id="entry-1",
                payload={"language": "Spanish", "phrase": "hola"},
            )

        self.assertEqual(event["summary"], "Language practice captured for Spanish: hola.")
        self.assertEqual(append_mock.await_args.kwargs["priority"], 30)


if __name__ == "__main__":
    unittest.main()
