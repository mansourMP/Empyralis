import asyncio
import importlib
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from server_modules import activity_ledger_service


class ActivityLedgerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global activity_ledger_service
        activity_ledger_service = importlib.import_module("server_modules.activity_ledger_service")
        self._history_cutoff_patcher = patch(
            "server_modules.activity_ledger_service._workspace_history_cutoff_ts",
            return_value=None,
        )
        self._history_cutoff_patcher.start()

    def tearDown(self) -> None:
        self._history_cutoff_patcher.stop()

    def test_list_notification_feed_items_projects_bounded_notification_shape(self) -> None:
        created_at = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-1",
                        "workspace_id": "workspace-1",
                        "actor_type": "system",
                        "actor_id": "runtime",
                        "event_class": "approval",
                        "detail_level": "feed_summary",
                        "channel": "runtime",
                        "direction": "system",
                        "session_key": "run:run-1",
                        "run_id": "run-1",
                        "action": "approval_requested",
                        "title": "Approval required",
                        "summary": "Approve deleting the selected file?",
                        "metadata": {"path": "/approvals"},
                        "created_at": created_at,
                    }
                ]
            ),
        ):
            items = asyncio.run(
                activity_ledger_service.list_notification_feed_items(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual(items[0]["action"], "approval_requested")
        self.assertEqual(items[0]["metadata"]["detail_level"], "feed_summary")
        self.assertEqual(items[0]["session_key"], "run:run-1")

    def test_append_activity_event_redacts_before_persistence(self) -> None:
        repo_mock = AsyncMock(return_value={"id": "aevt-1"})
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.append_activity_ledger_event",
            new=repo_mock,
        ):
            asyncio.run(
                activity_ledger_service.append_activity_event(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    actor_type="system",
                    actor_id="system",
                    event_class="system_activity",
                    title="Authorization: Bearer secret-token-value",
                    summary="API key sk-test-secret-1234567890",
                    payload={"headers": {"Cookie": "sid=secret"}},
                    metadata={"api_key": "sk-test-secret-1234567890", "safe": "visible"},
                    artifacts=[{"path": "export?token=secret"}],
                )
            )

        kwargs = repo_mock.await_args.kwargs
        self.assertEqual(kwargs["metadata"]["api_key"], "[redacted]")
        self.assertEqual(kwargs["metadata"]["safe"], "visible")
        self.assertEqual(kwargs["payload"]["headers"]["Cookie"], "[redacted]")
        self.assertNotIn("Bearer secret-token-value", kwargs["title"])
        self.assertNotIn("sk-test-secret", kwargs["summary"])
        self.assertNotIn("token=secret", str(kwargs["artifacts"]))

    def test_list_notification_feed_items_forwards_cursor_filters(self) -> None:
        repo_mock = AsyncMock(return_value=[])
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=repo_mock,
        ):
            asyncio.run(
                activity_ledger_service.list_notification_feed_items(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    since_ts="2026-04-10T09:00:00Z",
                    since_id="aevt-1",
                    limit=25,
                )
            )

        self.assertEqual(repo_mock.await_args.kwargs["since_created_at"], "2026-04-10T09:00:00Z")
        self.assertEqual(repo_mock.await_args.kwargs["since_id"], "aevt-1")

    def test_list_activity_timeline_payload_includes_artifacts_and_review_summary(self) -> None:
        created_at = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-1",
                        "workspace_id": "workspace-1",
                        "actor_type": "specialist",
                        "actor_id": "install-1",
                        "install_id": "install-1",
                        "event_class": "artifact_created",
                        "detail_level": "timeline_detail",
                        "action": "artifact_created",
                        "summary": "Created 1 artifact.",
                        "review_required": True,
                        "artifacts": [{"path": "knowledge/out.md", "review_required": True}],
                        "metadata": {},
                        "payload": {},
                        "created_at": created_at,
                    }
                ]
            ),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_activity_timeline_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["summary"]["review_required_count"], 1)
        self.assertEqual(payload["items"][0]["artifacts"][0]["path"], "knowledge/out.md")

    def test_list_activity_timeline_payload_projects_surface_shape_aliases(self) -> None:
        created_at = datetime(2026, 4, 10, 9, 5, tzinfo=timezone.utc)
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-2",
                        "workspace_id": "workspace-1",
                        "actor_type": "specialist",
                        "actor_id": "install-1",
                        "install_id": "install-1",
                        "app_id": "notes",
                        "event_class": "artifact_created",
                        "detail_level": "timeline_detail",
                        "action": "artifact_created",
                        "status": "completed",
                        "summary": "Created 1 artifact.",
                        "review_required": True,
                        "artifacts": [
                            {
                                "path": "knowledge/out.md",
                                "name": "Knowledge summary",
                                "preview_path": "/preview/out",
                                "review_required": True,
                            }
                        ],
                        "metadata": {},
                        "payload": {},
                        "created_at": created_at,
                    }
                ]
            ),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_activity_timeline_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        item = payload["items"][0]
        self.assertEqual(item["created_at"], "2026-04-10T09:05:00Z")
        self.assertEqual(item["ts"], "2026-04-10T09:05:00Z")
        self.assertEqual(item["actor_type"], "specialist")
        self.assertEqual(item["actor_id"], "install-1")
        self.assertEqual(item["actor"]["install_id"], "install-1")
        self.assertEqual(item["app_id"], "notes")
        self.assertEqual(item["artifacts"][0]["label"], "Knowledge summary")
        self.assertEqual(item["artifacts"][0]["preview_url"], "/preview/out")

    def test_list_activity_timeline_payload_projects_visible_and_admin_audit_fields(self) -> None:
        created_at = datetime(2026, 4, 10, 9, 5, tzinfo=timezone.utc)
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-ledger-1",
                        "workspace_id": "workspace-1",
                        "actor_type": "sage",
                        "actor_id": "install-sage",
                        "event_class": "payment_request",
                        "detail_level": "timeline_detail",
                        "action": "payment_authorization_requested",
                        "summary": "Payment approval requested.",
                        "review_required": True,
                        "metadata": {
                            "public_tier": "pro",
                            "credit_quantity": 8,
                            "credit_multiplier": 1,
                            "credit_item_type": "virtual_browser_minutes",
                            "effective_provider": "deepseek",
                            "effective_model": "deepseek-v4-pro",
                            "fallback_provider": "deepseek",
                            "fallback_model": "deepseek-v4-flash",
                            "prompt_tokens": 80,
                            "completion_tokens": 20,
                            "total_tokens": 100,
                            "runtime_duration_seconds": 180,
                            "ledger_item_ids": ["shost_abc"],
                        },
                        "payload": {},
                        "created_at": created_at,
                    }
                ]
            ),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_activity_timeline_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        item = payload["items"][0]
        self.assertEqual(item["visible_activity"]["sage_tier"], "Pro")
        self.assertEqual(item["visible_activity"]["used_credits"], 8.0)
        self.assertEqual(item["visible_activity"]["virtual_browser_minutes"], 8.0)
        self.assertTrue(item["visible_activity"]["owner_approval_required_for_payment"])
        self.assertNotIn("raw_provider", item["visible_activity"])
        self.assertNotIn("raw_model", item["visible_activity"])
        self.assertEqual(item["admin_audit"]["raw_provider"], "deepseek")
        self.assertEqual(item["admin_audit"]["raw_model"], "deepseek-v4-pro")
        self.assertEqual(item["admin_audit"]["fallback_model"], "deepseek-v4-flash")
        self.assertEqual(item["admin_audit"]["token_usage"]["total_tokens"], 100)
        self.assertEqual(item["admin_audit"]["runtime_duration_seconds"], 180.0)
        self.assertEqual(item["admin_audit"]["ledger_item_ids"], ["aevt-ledger-1", "shost_abc"])

    def test_list_activity_timeline_payload_applies_workspace_history_window(self) -> None:
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-old",
                        "workspace_id": "workspace-1",
                        "actor_type": "specialist",
                        "actor_id": "install-1",
                        "event_class": "artifact_created",
                        "action": "artifact_created",
                        "summary": "Old artifact.",
                        "created_at": datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
                    },
                    {
                        "id": "aevt-new",
                        "workspace_id": "workspace-1",
                        "actor_type": "specialist",
                        "actor_id": "install-1",
                        "event_class": "artifact_created",
                        "action": "artifact_created",
                        "summary": "New artifact.",
                        "created_at": datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc),
                    },
                ]
            ),
        ), patch(
            "server_modules.activity_ledger_service._workspace_history_cutoff_ts",
            return_value=datetime(2026, 4, 5, tzinfo=timezone.utc).timestamp(),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_activity_timeline_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual([item["id"] for item in payload["items"]], ["aevt-new"])

    def test_list_activity_timeline_payload_forwards_trace_id_filter(self) -> None:
        repo_mock = AsyncMock(return_value=[])
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=repo_mock,
        ):
            asyncio.run(
                activity_ledger_service.list_activity_timeline_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    trace_id="trace-lookup-1",
                )
            )

        self.assertEqual(repo_mock.await_args.kwargs["trace_id"], "trace-lookup-1")

    def test_list_activity_timeline_payload_redacts_public_metadata_and_payload(self) -> None:
        created_at = datetime(2026, 4, 10, 9, 5, tzinfo=timezone.utc)
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-sensitive",
                        "workspace_id": "workspace-1",
                        "actor_type": "sage",
                        "actor_id": "install-sage",
                        "event_class": "sage_activity",
                        "detail_level": "timeline_detail",
                        "action": "sage_chat.completed",
                        "summary": "Sage responded.",
                        "metadata": {
                            "api_key": "sk-test-secret",
                            "safe": "visible",
                            "nested": {"authorization": "Bearer secret-token"},
                        },
                        "payload": {
                            "headers": {"cookie": "session_cookie=secret"},
                            "note": "Call me at +1 415 555 1234",
                        },
                        "created_at": created_at,
                    }
                ]
            ),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_activity_timeline_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    trace_id="trace-sensitive",
                )
            )

        item = payload["items"][0]
        self.assertEqual(item["metadata"]["safe"], "visible")
        self.assertEqual(item["metadata"]["api_key"], "[redacted]")
        self.assertEqual(item["metadata"]["nested"]["authorization"], "[redacted]")
        self.assertEqual(item["payload"]["headers"]["cookie"], "[redacted]")
        self.assertNotIn("+1 415 555 1234", item["payload"]["note"])

    def test_list_activity_timeline_payload_strips_raw_reasoning_fields(self) -> None:
        created_at = datetime(2026, 4, 10, 9, 5, tzinfo=timezone.utc)
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-cot",
                        "workspace_id": "workspace-1",
                        "actor_type": "sage",
                        "actor_id": "install-sage",
                        "event_class": "sage_activity",
                        "detail_level": "timeline_detail",
                        "action": "sage_chat.completed",
                        "summary": "Sage responded.",
                        "metadata": {
                            "raw_chain_of_thought": "hidden",
                            "model_internals": {"scratchpad": "hidden"},
                            "safe": "visible",
                        },
                        "payload": {
                            "chain_of_thought": "hidden",
                            "steps": [{"internal_reasoning": "hidden", "title": "Checked policy"}],
                        },
                        "created_at": created_at,
                    }
                ]
            ),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_activity_timeline_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    trace_id="trace-cot",
                )
            )

        item = payload["items"][0]
        self.assertNotIn("raw_chain_of_thought", item["metadata"])
        self.assertNotIn("model_internals", item["metadata"])
        self.assertEqual(item["metadata"]["safe"], "visible")
        self.assertNotIn("chain_of_thought", item["payload"])
        self.assertEqual(item["payload"]["steps"][0], {"title": "Checked policy"})

    def test_list_sage_recent_activity_payload_groups_by_class(self) -> None:
        created_at = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-1",
                        "workspace_id": "workspace-1",
                        "actor_type": "sage",
                        "actor_id": "install-sage",
                        "event_class": "delegation",
                        "action": "event_trigger",
                        "summary": "Delegated wake request scheduled.",
                        "artifacts": [],
                        "created_at": created_at,
                    },
                    {
                        "id": "aevt-2",
                        "workspace_id": "workspace-1",
                        "actor_type": "application",
                        "actor_id": "notes",
                        "event_class": "memory_update",
                        "action": "note_changed",
                        "summary": "Project note updated.",
                        "artifacts": [],
                        "created_at": created_at,
                    },
                ]
            ),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_sage_recent_activity_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual(payload["summary"]["count"], 2)
        self.assertEqual(payload["summary"]["by_class"]["delegation"], 1)
        self.assertEqual(payload["summary"]["by_class"]["memory_update"], 1)

    def test_list_sage_recent_activity_payload_includes_created_at_alias_and_review_count(self) -> None:
        created_at = datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-3",
                        "workspace_id": "workspace-1",
                        "actor_type": "sage",
                        "actor_id": "install-sage",
                        "event_class": "artifact_created",
                        "action": "artifact_created",
                        "summary": "Created 1 artifact.",
                        "review_required": True,
                        "artifacts": [{"path": "artifacts/out.md", "name": "Out"}],
                        "created_at": created_at,
                    }
                ]
            ),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_sage_recent_activity_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual(payload["summary"]["review_required_count"], 1)
        self.assertEqual(payload["items"][0]["created_at"], "2026-04-10T10:00:00Z")
        self.assertEqual(payload["items"][0]["artifacts"][0]["label"], "Out")

    def test_list_sage_recent_activity_payload_applies_workspace_history_window(self) -> None:
        with patch(
            "server_modules.activity_ledger_service.control_plane_repository.list_activity_ledger_events",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "aevt-old",
                        "workspace_id": "workspace-1",
                        "actor_type": "sage",
                        "actor_id": "install-sage",
                        "event_class": "delegation",
                        "action": "event_trigger",
                        "summary": "Old event.",
                        "created_at": datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
                    },
                    {
                        "id": "aevt-new",
                        "workspace_id": "workspace-1",
                        "actor_type": "sage",
                        "actor_id": "install-sage",
                        "event_class": "memory_update",
                        "action": "note_changed",
                        "summary": "New event.",
                        "created_at": datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc),
                    },
                ]
            ),
        ), patch(
            "server_modules.activity_ledger_service._workspace_history_cutoff_ts",
            return_value=datetime(2026, 4, 5, tzinfo=timezone.utc).timestamp(),
        ):
            payload = asyncio.run(
                activity_ledger_service.list_sage_recent_activity_payload(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual([item["id"] for item in payload["items"]], ["aevt-new"])


if __name__ == "__main__":
    unittest.main()
