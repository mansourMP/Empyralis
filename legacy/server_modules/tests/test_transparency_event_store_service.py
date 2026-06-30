from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules.transparency_event_store_service import persist_transparency_events


class TransparencyEventStoreServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_persist_transparency_events_awaits_activity_writer(self) -> None:
        event = {
            "event_id": "stevt-test",
            "event_type": "final_response_sent",
            "title": "Response sent",
            "summary": "Sage replied",
            "status": "completed",
        }

        with patch(
            "server_modules.transparency_event_store_service.activity_ledger_service.append_activity_event",
            new_callable=AsyncMock,
        ) as append_activity_event:
            stored = await persist_transparency_events(
                trace_id="trace-123",
                workspace_id="workspace-1",
                events=[event],
            )

        self.assertEqual(stored, 1)
        append_activity_event.assert_awaited_once()
        kwargs = append_activity_event.await_args.kwargs
        self.assertEqual(kwargs["trace_id"], "trace-123")
        self.assertEqual(kwargs["workspace_id"], "workspace-1")
        self.assertEqual(kwargs["event_class"], "transparency_event")
        self.assertEqual(kwargs["tenant_id"], "default")

    async def test_persist_transparency_events_uses_explicit_tenant_scope(self) -> None:
        event = {
            "event_id": "stevt-tenant",
            "event_type": "final_response_sent",
            "title": "Response sent",
            "summary": "Sage replied",
            "status": "completed",
        }
        with patch(
            "server_modules.transparency_event_store_service.activity_ledger_service.append_activity_event",
            new_callable=AsyncMock,
        ) as append_activity_event:
            stored = await persist_transparency_events(
                trace_id="trace-tenant",
                tenant_id="tenant-42",
                workspace_id="workspace-1",
                events=[event],
            )
        self.assertEqual(stored, 1)
        kwargs = append_activity_event.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-42")

    async def test_persist_transparency_events_redacts_before_activity_writer(self) -> None:
        event = {
            "event_id": "stevt-secret",
            "event_type": "tool_failed",
            "title": "Authorization: Bearer secret-token-value",
            "summary": "OpenAI key sk-test-secret-1234567890",
            "status": "failed",
            "tool_name": "browser",
        }

        with patch(
            "server_modules.transparency_event_store_service.activity_ledger_service.append_activity_event",
            new_callable=AsyncMock,
        ) as append_activity_event:
            stored = await persist_transparency_events(
                trace_id="trace-123",
                workspace_id="workspace-1",
                events=[event],
            )

        self.assertEqual(stored, 1)
        kwargs = append_activity_event.await_args.kwargs
        self.assertEqual(kwargs["title"], "Authorization: [redacted]")
        self.assertNotIn("sk-test-secret", kwargs["summary"])

    async def test_persist_transparency_events_logs_best_effort_failure(self) -> None:
        event = {
            "event_id": "stevt-test",
            "event_type": "final_response_sent",
            "title": "Response sent",
            "summary": "Sage replied",
            "status": "completed",
        }

        with (
            patch(
                "server_modules.transparency_event_store_service.activity_ledger_service.append_activity_event",
                new_callable=AsyncMock,
            ) as append_activity_event,
            patch("server_modules.transparency_event_store_service.LOGGER.warning") as warning,
        ):
            append_activity_event.side_effect = RuntimeError("store down")
            stored = await persist_transparency_events(
                trace_id="trace-123",
                workspace_id="workspace-1",
                events=[event],
            )

        self.assertEqual(stored, 0)
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
