import threading
import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_usage_service


class RuntimeUsageServiceTests(unittest.TestCase):
    def test_normalize_usage_period_defaults_to_all(self):
        self.assertEqual(runtime_usage_service.normalize_usage_period("week"), "week")
        self.assertEqual(runtime_usage_service.normalize_usage_period("bad"), "all")
        self.assertEqual(runtime_usage_service.normalize_usage_period(None), "all")

    def test_usage_snapshots_for_user_merges_archived_and_live_snapshots(self):
        snapshots = runtime_usage_service.usage_snapshots_for_user(
            {"user_id": "user-1"},
            refresh_server_exports=lambda: None,
            run_history_lock=threading.Lock(),
            run_history=[{"run_id": "archived-1", "owner_user_id": "user-1"}],
            runs={
                "live-1": {"usage_masked": {"tokens": 1}, "owner_user_id": "user-1"},
                "skip-1": {"usage_masked": None},
            },
            serialize_snapshot=lambda run_id, run: {"run_id": run_id, "owner_user_id": run.get("owner_user_id")},
            current_user_is_privileged=lambda current_user: False,
            extract_run_owner_user_id=lambda payload: str(payload.get("owner_user_id") or ""),
        )

        self.assertEqual(
            {item["run_id"] for item in snapshots},
            {"archived-1", "live-1"},
        )

    def test_usage_snapshots_for_user_includes_usage_accounting_only_runs(self):
        snapshots = runtime_usage_service.usage_snapshots_for_user(
            {"user_id": "user-1"},
            refresh_server_exports=lambda: None,
            run_history_lock=threading.Lock(),
            run_history=[],
            runs={
                "live-1": {"usage_accounting": {"usage_record_id": "uacct_1"}, "owner_user_id": "user-1"},
            },
            serialize_snapshot=lambda run_id, run: {"run_id": run_id, "owner_user_id": run.get("owner_user_id")},
            current_user_is_privileged=lambda current_user: False,
            extract_run_owner_user_id=lambda payload: str(payload.get("owner_user_id") or ""),
        )

        self.assertEqual([item["run_id"] for item in snapshots], ["live-1"])

    def test_usage_snapshots_for_user_requires_user_id_for_non_privileged_users(self):
        with self.assertRaises(HTTPException):
            runtime_usage_service.usage_snapshots_for_user(
                {},
                refresh_server_exports=lambda: None,
                run_history_lock=threading.Lock(),
                run_history=[],
                runs={},
                serialize_snapshot=lambda run_id, run: {},
                current_user_is_privileged=lambda current_user: False,
                extract_run_owner_user_id=lambda payload: "",
            )

    def test_usage_snapshots_for_user_workspace_scoped_rust_gate_runs_before_snapshot_scan(self):
        called = {}
        with mock.patch.object(
            runtime_usage_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "list_runs",
                "next_action": "list_runs",
            },
        ) as rust_mock:
            snapshots = runtime_usage_service.usage_snapshots_for_user(
                {"user_id": "user-1"},
                refresh_server_exports=lambda: None,
                run_history_lock=threading.Lock(),
                run_history=[{"run_id": "archived-1", "owner_user_id": "user-1"}],
                runs={
                    "live-1": {"usage_masked": {"tokens": 1}, "owner_user_id": "user-1"},
                },
                serialize_snapshot=lambda run_id, run: called.setdefault("serialized", []).append(run_id) or {"run_id": run_id, "owner_user_id": run.get("owner_user_id")},
                current_user_is_privileged=lambda current_user: False,
                extract_run_owner_user_id=lambda payload: str(payload.get("owner_user_id") or ""),
                allowed_workspace_ids_fn=lambda current_user: {"ws-1"},
                resolve_workspace_tenant_id=lambda current_user, workspace_id: f"tenant-for-{workspace_id}",
            )

        self.assertEqual({item["run_id"] for item in snapshots}, {"archived-1", "live-1"})
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "list_runs")
        self.assertEqual(rust_mock.call_args.args[1]["workspace_id"], "ws-1")

    def test_usage_snapshots_for_user_wrong_rust_next_action_blocks_before_snapshot_scan(self):
        scanned = {"serialized": False}
        with mock.patch.object(
            runtime_usage_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "list_runs",
                "next_action": "get_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_usage_service.usage_snapshots_for_user(
                    {"user_id": "user-1"},
                    refresh_server_exports=lambda: None,
                    run_history_lock=threading.Lock(),
                    run_history=[],
                    runs={
                        "live-1": {"usage_masked": {"tokens": 1}, "owner_user_id": "user-1"},
                    },
                    serialize_snapshot=lambda run_id, run: scanned.update({"serialized": True}) or {"run_id": run_id, "owner_user_id": run.get("owner_user_id")},
                    current_user_is_privileged=lambda current_user: False,
                    extract_run_owner_user_id=lambda payload: str(payload.get("owner_user_id") or ""),
                    allowed_workspace_ids_fn=lambda current_user: {"ws-1"},
                    resolve_workspace_tenant_id=lambda current_user, workspace_id: f"tenant-for-{workspace_id}",
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertFalse(scanned["serialized"])

    def test_usage_summary_payload_normalizes_period(self):
        payload = runtime_usage_service.usage_summary_payload(
            period="bogus",
            current_user={"user_id": "user-1"},
            usage_snapshots_for_user_fn=lambda current_user: [{"run_id": "run-1"}],
            aggregate_usage_summary_fn=lambda snapshots, period: {"count": len(snapshots), "period": period},
        )

        self.assertEqual(payload, {"count": 1, "period": "all"})

    def test_usage_runs_payload_passes_limit_offset_and_period(self):
        payload = runtime_usage_service.usage_runs_payload(
            limit=10,
            offset=5,
            period="week",
            current_user={"user_id": "user-1"},
            usage_snapshots_for_user_fn=lambda current_user: [{"run_id": "run-1"}],
            list_usage_runs_fn=lambda snapshots, period, limit, offset: {
                "snapshots": len(snapshots),
                "period": period,
                "limit": limit,
                "offset": offset,
            },
        )

        self.assertEqual(payload, {"snapshots": 1, "period": "week", "limit": 10, "offset": 5})


if __name__ == "__main__":
    unittest.main()
