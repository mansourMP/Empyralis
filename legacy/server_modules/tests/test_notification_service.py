import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Any, Dict

from server_modules import notification_service
from server_modules import outbox_service
from server_modules import runtime_state_store


def _workspace_access_entry(*, workspace_id: str, tenant_id: str, role: str) -> Dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "tenant_role": role,
        "role": role,
        "capabilities": {"allow": [], "deny": []},
        "tenant_capabilities": {"allow": [], "deny": []},
        "workspace_capabilities": {"allow": [], "deny": []},
        "dangerous_action_classes": {"allow": [], "deny": []},
        "tenant_dangerous_action_classes": {"allow": [], "deny": []},
        "workspace_dangerous_action_classes": {"allow": [], "deny": []},
        "connectors": {"allow": [], "deny": []},
        "tenant_connectors": {"allow": [], "deny": []},
        "workspace_connectors": {"allow": [], "deny": []},
        "machine_enrollment_scope": "workspace",
        "trusted_owner_machine_ids": [],
        "owner_user_id": "user-1",
        "owner_email": "user@example.com",
    }


def _current_user(*, workspace_id: str, tenant_id: str, role: str = "owner") -> Dict[str, Any]:
    return {
        "auth_type": "bearer",
        "user_id": "user-1",
        "email": "user@example.com",
        "role": role,
        "is_admin": role == "owner",
        "identity_versions": {"membership_version": 1},
        "workspace_access": {
            workspace_id: _workspace_access_entry(workspace_id=workspace_id, tenant_id=tenant_id, role=role),
        },
    }


class NotificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global notification_service, outbox_service, runtime_state_store
        notification_service = importlib.import_module("server_modules.notification_service")
        outbox_service = importlib.import_module("server_modules.outbox_service")
        runtime_state_store = importlib.import_module("server_modules.runtime_state_store")

    def test_register_notification_device_denies_push_gracefully_when_plan_disables_it(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)

            with patch(
                "server_modules.auth.enforce_workspace_access",
                return_value="workspace-1",
            ), patch(
                "server_modules.auth.workspace_tenant_id",
                return_value="tenant-1",
            ), patch(
                "server_modules.notification_service.billing_service.workspace_billing_summary_for_workspace_id",
                return_value={
                    "workspace_id": "workspace-1",
                    "tenant_id": "tenant-1",
                    "subscription": {"metadata": {"source": "workspace_default"}},
                },
            ):
                result = notification_service.register_notification_device(
                    current_user=_current_user(workspace_id="workspace-1", tenant_id="tenant-1"),
                    workspace_id="workspace-1",
                    device_id="device-1",
                    push_token="ExponentPushToken[test]",
                    workspace={"metadata": {"billing": {"plan": "free"}}},
                    db_path=db_path,
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "mobile_push_unavailable")
            devices = runtime_state_store.list_notification_devices(
                db_path,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            )
            self.assertEqual(devices, [])

    def test_deliver_notification_from_outbox_event_persists_and_fans_out(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            runtime_state_store.upsert_notification_device(
                db_path,
                {
                    "device_id": "device-1",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "reader_key": "api_key:owner",
                    "provider": "expo",
                    "push_token": "ExponentPushToken[test]",
                    "platform": "ios",
                    "status": "active",
                    "capabilities": ["push"],
                    "registered_at": "2026-04-08T00:00:00Z",
                },
            )
            event = outbox_service.OutboxEvent(
                event_id="approval-evt-1",
                event_type="approval_requested",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                run_id="run-1",
                trace_id="trace-1",
                payload={
                    "prompt": "Approve deleting the selected file?",
                    "approval_id": "approval-1",
                    "metadata": {"path": "/approvals"},
                    "browser": {
                        "session_profile": "qa-browser",
                        "immutable_plan_hash": "hash-1",
                    },
                },
                created_at="2026-04-08T00:00:00Z",
            )

            notification = notification_service.deliver_notification_from_outbox_event(
                event,
                db_path=db_path,
                send_push_messages_fn=lambda messages: [{"status": "ok"} for _ in messages],
            )

            self.assertIsNotNone(notification)
            with patch(
                "server_modules.auth.enforce_workspace_access",
                return_value="workspace-1",
            ), patch(
                "server_modules.auth.workspace_tenant_id",
                return_value="tenant-1",
            ), patch(
                "server_modules.notification_service.entitlements_service.workspace_entitlement_payload_for_workspace_id",
                return_value={"capabilities": {"history_window_days": 90}},
            ):
                payload = notification_service.list_notification_payload(
                    current_user=_current_user(workspace_id="workspace-1", tenant_id="tenant-1"),
                    limit=10,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    db_path=db_path,
                )
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["action"], "approval_requested")
            self.assertEqual(payload["items"][0]["title"], "Approval required")
            self.assertEqual(payload["items"][0]["metadata"]["browser"]["session_profile"], "qa-browser")
            deliveries = runtime_state_store.list_notification_delivery_statuses(
                db_path,
                notification_id="approval-evt-1",
            )
            self.assertEqual(deliveries["device-1"]["status"], "delivered")

    def test_mark_notifications_read_is_durable(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            tenant_id = "tenant-mark-read"
            workspace_id = "workspace-mark-read"
            runtime_state_store.upsert_notification(
                db_path,
                {
                    "id": "notif-1",
                    "ts": "2026-04-08T00:00:00Z",
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "channel": "runtime",
                    "direction": "system",
                    "event_type": "notification",
                    "action": "run_completed",
                    "title": "Run completed",
                    "text": "Run run-1 completed.",
                    "run_id": "run-1",
                    "session_key": "run:run-1",
                    "session_id": "run:run-1",
                    "metadata": {"path": "/runs/run-1"},
                },
            )
            current_user = _current_user(workspace_id=workspace_id, tenant_id=tenant_id)

            with patch(
                "server_modules.auth.enforce_workspace_access",
                return_value=workspace_id,
            ), patch(
                "server_modules.auth.workspace_tenant_id",
                return_value=tenant_id,
            ), patch(
                "server_modules.notification_service.entitlements_service.workspace_entitlement_payload_for_workspace_id",
                return_value={"capabilities": {"history_window_days": 90}},
            ):
                before = notification_service.list_notification_payload(
                    current_user=current_user,
                    limit=10,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    db_path=db_path,
                )
            before_item = next(
                item for item in before["items"] if str(item.get("id") or "").strip() == "notif-1"
            )
            self.assertIsNone(before_item.get("read_at"))

            marked = notification_service.mark_notifications_read(
                current_user=current_user,
                notification_ids=["notif-1"],
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                db_path=db_path,
            )
            self.assertEqual(marked["marked_count"], 1)

            with patch(
                "server_modules.auth.enforce_workspace_access",
                return_value=workspace_id,
            ), patch(
                "server_modules.auth.workspace_tenant_id",
                return_value=tenant_id,
            ), patch(
                "server_modules.notification_service.entitlements_service.workspace_entitlement_payload_for_workspace_id",
                return_value={"capabilities": {"history_window_days": 90}},
            ):
                after = notification_service.list_notification_payload(
                    current_user=current_user,
                    limit=10,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    db_path=db_path,
                )
            after_item = next(
                item for item in after["items"] if str(item.get("id") or "").strip() == "notif-1"
            )
            self.assertTrue(bool(after_item.get("read_at")))

    def test_list_notification_payload_respects_workspace_history_window(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            tenant_id = "tenant-history"
            workspace_id = "workspace-history"
            runtime_state_store.upsert_notification(
                db_path,
                {
                    "id": "notif-old",
                    "ts": "2026-03-20T00:00:00Z",
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "channel": "runtime",
                    "direction": "system",
                    "event_type": "notification",
                    "action": "run_completed",
                    "title": "Old run",
                    "text": "Old notification",
                    "session_key": "run:old",
                    "session_id": "run:old",
                    "metadata": {"path": "/runs/run-old"},
                },
            )
            runtime_state_store.upsert_notification(
                db_path,
                {
                    "id": "notif-new",
                    "ts": "2026-04-08T00:00:00Z",
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "channel": "runtime",
                    "direction": "system",
                    "event_type": "notification",
                    "action": "run_completed",
                    "title": "New run",
                    "text": "New notification",
                    "session_key": "run:new",
                    "session_id": "run:new",
                    "metadata": {"path": "/runs/run-new"},
                },
            )
            current_user = {
                "auth_type": "bearer",
                "user_id": "user-1",
                "role": "owner",
                "workspace_access": {
                    workspace_id: {"tenant_id": tenant_id, "role": "owner"},
                },
            }

            with patch("server_modules.notification_service.time.time", return_value=1775865600.0), patch(
                "server_modules.auth.enforce_workspace_access",
                return_value=workspace_id,
            ), patch(
                "server_modules.auth.workspace_tenant_id",
                return_value=tenant_id,
            ), patch(
                "server_modules.notification_service.entitlements_service.workspace_entitlement_payload_for_workspace_id",
                return_value={"capabilities": {"history_window_days": 7}},
            ):
                payload = notification_service.list_notification_payload(
                    current_user=current_user,
                    limit=10,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    db_path=db_path,
                )

            self.assertEqual([item["id"] for item in payload["items"]], ["notif-new"])

    def test_list_notification_payload_prefers_activity_ledger_for_workspace_feed(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            runtime_state_store.upsert_notification(
                db_path,
                {
                    "id": "notif-1",
                    "ts": "2026-04-08T00:00:00Z",
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                    "channel": "runtime",
                    "direction": "system",
                    "event_type": "approval",
                    "action": "approval_requested",
                    "title": "Approval required",
                    "text": "Approve deleting the selected file?",
                    "run_id": "run-1",
                    "session_key": "run:run-1",
                    "session_id": "run:run-1",
                    "metadata": {"path": "/approvals"},
                },
            )
            current_user = _current_user(workspace_id="workspace-1", tenant_id="tenant-1")

            with patch(
                "server_modules.notification_service.activity_ledger_service.list_notification_feed_items_sync",
                return_value=[
                    {
                        "id": "notif-1",
                        "ts": "2026-04-08T00:00:00Z",
                        "workspace_id": "workspace-1",
                        "channel": "runtime",
                        "direction": "system",
                        "event_type": "approval",
                        "session_key": "run:run-1",
                        "session_id": "run:run-1",
                        "run_id": "run-1",
                        "action": "approval_requested",
                        "title": "Approval required",
                        "text": "Approve deleting the selected file?",
                        "metadata": {"path": "/approvals", "detail_level": "feed_summary"},
                    }
                ],
            ), patch(
                "server_modules.auth.enforce_workspace_access",
                return_value="workspace-1",
            ), patch(
                "server_modules.auth.workspace_tenant_id",
                return_value="tenant-1",
            ), patch(
                "server_modules.notification_service.entitlements_service.workspace_entitlement_payload_for_workspace_id",
                return_value={"capabilities": {"history_window_days": 90}},
            ):
                payload = notification_service.list_notification_payload(
                    current_user=current_user,
                    limit=10,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    db_path=db_path,
                )

            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["metadata"]["detail_level"], "feed_summary")

    def test_run_failed_and_machine_revoked_are_notification_worthy(self) -> None:
        failed_run_event = outbox_service.OutboxEvent(
            event_id="run-failed-1",
            event_type="run_transition",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            run_id="run-9",
            payload={"to_state": "failed", "from_state": "executing"},
            created_at="2026-04-08T00:00:00Z",
        )
        revoked_machine_event = outbox_service.OutboxEvent(
            event_id="machine-revoked-1",
            event_type="machine_event",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            machine_id="machine-9",
            payload={"action": "revoked", "display_name": "Kitchen Mac"},
            created_at="2026-04-08T00:00:00Z",
        )

        failed = notification_service.build_notification_from_outbox_event(failed_run_event)
        revoked = notification_service.build_notification_from_outbox_event(revoked_machine_event)

        self.assertEqual(failed["action"], "run_failed")
        self.assertEqual(revoked["action"], "machine_revoked")
        self.assertEqual(failed["metadata"]["status"], "failed")

    def test_approval_resolved_notifications_and_activity_metadata_are_visible(self) -> None:
        approval_event = outbox_service.OutboxEvent(
            event_id="approval-resolved-1",
            event_type="approval_resolved",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            run_id="run-1",
            trace_id="trace-1",
            payload={
                "approval_id": "approval-1",
                "resolution": "approved",
                "reason": "Looks good.",
                "browser": {
                    "session_profile": "qa-browser",
                    "immutable_plan_hash": "hash-1",
                    "reviewed_approval_required": True,
                },
            },
            created_at="2026-04-08T00:00:00Z",
        )

        notification = notification_service.build_notification_from_outbox_event(approval_event)

        self.assertEqual(notification["action"], "approval_approved")
        self.assertEqual(notification["title"], "Approval approved")
        self.assertEqual(notification["text"], "Looks good.")
        self.assertEqual(notification["metadata"]["activity_event_class"], "approval")
        self.assertEqual(notification["metadata"]["status"], "approved")
        self.assertEqual(notification["metadata"]["path"], "/runs/run-1")
        self.assertEqual(notification["metadata"]["browser"]["session_profile"], "qa-browser")

    def test_deliver_notification_from_outbox_event_records_activity_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            event = outbox_service.OutboxEvent(
                event_id="approval-evt-1",
                event_type="approval_requested",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                run_id="run-1",
                trace_id="trace-1",
                payload={"prompt": "Approve deleting the selected file?"},
                created_at="2026-04-08T00:00:00Z",
            )

            with patch(
                "server_modules.notification_service.activity_ledger_service.record_notification_activity"
            ) as record_mock:
                notification_service.deliver_notification_from_outbox_event(
                    event,
                    db_path=db_path,
                    send_push_messages_fn=lambda messages: [{"status": "ok"} for _ in messages],
                )

            self.assertEqual(record_mock.call_count, 1)

    def test_iter_notifications_stream_replays_bounded_backlog_from_durable_store(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            for notification_id, ts in (
                ("notif-1", "2026-04-08T00:00:00Z"),
                ("notif-2", "2026-04-08T00:01:00Z"),
                ("notif-3", "2026-04-08T00:02:00Z"),
            ):
                runtime_state_store.upsert_notification(
                    db_path,
                    {
                        "id": notification_id,
                        "ts": ts,
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "channel": "runtime",
                        "direction": "system",
                        "event_type": "notification",
                        "action": "run_completed",
                        "title": notification_id,
                        "text": notification_id,
                        "session_key": f"run:{notification_id}",
                        "session_id": f"run:{notification_id}",
                    },
                )

            iterator = notification_service.iter_notifications_stream(
                reader_key="user:user-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                include_backlog=True,
                limit=3,
                db_path=db_path,
            )
            with patch(
                "server_modules.notification_service.activity_ledger_service.list_notification_feed_items_sync",
                return_value=[],
            ):
                events = [next(iterator), next(iterator), next(iterator)]
            iterator.close()

            self.assertEqual(
                [json.loads(event["data"])["id"] for event in events],
                ["notif-1", "notif-2", "notif-3"],
            )

    def test_iter_notifications_stream_resumes_from_since_id_with_bounded_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            for notification_id, ts in (
                ("notif-1", "2026-04-08T00:00:00Z"),
                ("notif-2", "2026-04-08T00:01:00Z"),
                ("notif-3", "2026-04-08T00:02:00Z"),
            ):
                runtime_state_store.upsert_notification(
                    db_path,
                    {
                        "id": notification_id,
                        "ts": ts,
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "channel": "runtime",
                        "direction": "system",
                        "event_type": "notification",
                        "action": "run_completed",
                        "title": notification_id,
                        "text": notification_id,
                        "session_key": f"run:{notification_id}",
                        "session_id": f"run:{notification_id}",
                    },
                )

            iterator = notification_service.iter_notifications_stream(
                reader_key="user:user-1",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                since_id="notif-2",
                limit=3,
                db_path=db_path,
            )
            with patch(
                "server_modules.notification_service.activity_ledger_service.list_notification_feed_items_sync",
                return_value=[],
            ), patch(
                "server_modules.notification_service.activity_ledger_service.get_notification_feed_item_sync",
                return_value=None,
            ):
                event = next(iterator)
            iterator.close()

            self.assertEqual(json.loads(event["data"])["id"], "notif-3")

    def test_runtime_notification_queries_degrade_when_runtime_db_is_unavailable(self) -> None:
        unavailable_db = Path("/tmp/empyralis-missing-runtime-state.sqlite3")
        with patch(
            "server_modules.notification_service.sqlite3.connect",
            side_effect=notification_service.sqlite3.OperationalError("unable to open database file"),
        ):
            self.assertEqual(
                notification_service._runtime_notification_query_ids(
                    db_path=unavailable_db,
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    limit=1,
                ),
                [],
            )
            self.assertIsNone(
                notification_service._resolve_runtime_notification_cursor(
                    db_path=unavailable_db,
                    notification_id="notif-1",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )


if __name__ == "__main__":
    unittest.main()
