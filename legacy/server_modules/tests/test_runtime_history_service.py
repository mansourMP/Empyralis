import threading
import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_history_service


class RuntimeHistoryServiceTests(unittest.TestCase):
    def test_build_runs_history_callbacks_preserves_entries(self):
        callbacks = runtime_history_service.build_runs_history_callbacks(
            refresh_server_exports=lambda: None,
            run_history_lock=threading.Lock(),
            run_history=[],
            history_item_matches=lambda item, workspace_id, status, pack_id: True,
            current_user_is_privileged=lambda current_user: False,
            extract_run_owner_user_id=lambda item: "",
            enforce_workspace_access=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
            resolve_workspace_tenant_id=lambda workspace_id: f"tenant-for-{workspace_id}",
            normalize_run_id_token=lambda value: str(value).strip() or None,
            summarize_history_item=lambda item: {"run_id": item.get("run_id")},
        )

        self.assertIn("run_history_lock", callbacks)
        self.assertIn("summarize_history_item", callbacks)

    def test_build_runs_history_payload_filters_and_counts_children(self):
        payload = runtime_history_service.build_runs_history_payload(
            limit=10,
            workspace_id=None,
            status=None,
            pack_id=None,
            current_user={"user_id": "user-1"},
            refresh_server_exports=lambda: None,
            run_history_lock=threading.Lock(),
            run_history=[
                {"run_id": "parent-1", "owner_user_id": "user-1"},
                {"run_id": "child-1", "owner_user_id": "user-1", "parent_run_id": "parent-1"},
                {"run_id": "other-1", "owner_user_id": "user-2"},
            ],
            history_item_matches=lambda item, workspace_id, status, pack_id: True,
            current_user_is_privileged=lambda current_user: False,
            extract_run_owner_user_id=lambda item: str(item.get("owner_user_id") or ""),
            enforce_workspace_access=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
            resolve_workspace_tenant_id=lambda workspace_id: f"tenant-for-{workspace_id}",
            normalize_run_id_token=lambda value: str(value).strip() or None,
            summarize_history_item=lambda item: {"run_id": item.get("run_id")},
        )

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["total"], 2)
        parent = next(item for item in payload["items"] if item["run_id"] == "parent-1")
        self.assertEqual(parent["child_run_count"], 1)

    def test_build_runs_history_payload_requires_user_id_for_non_privileged_user(self):
        with self.assertRaises(HTTPException):
            runtime_history_service.build_runs_history_payload(
                limit=10,
                workspace_id=None,
                status=None,
                pack_id=None,
                current_user={},
                refresh_server_exports=lambda: None,
                run_history_lock=threading.Lock(),
                run_history=[],
                history_item_matches=lambda item, workspace_id, status, pack_id: True,
                current_user_is_privileged=lambda current_user: False,
                extract_run_owner_user_id=lambda item: "",
                enforce_workspace_access=lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
                resolve_workspace_tenant_id=lambda workspace_id: f"tenant-for-{workspace_id}",
                normalize_run_id_token=lambda value: None,
                summarize_history_item=lambda item: {},
            )

    def test_build_runs_history_payload_resolves_workspace_on_server(self):
        seen = {}

        with mock.patch.object(
            runtime_history_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "list_runs",
                "next_action": "list_runs",
            },
        ) as rust_mock:
            payload = runtime_history_service.build_runs_history_payload(
                limit=10,
                workspace_id="default",
                status=None,
                pack_id=None,
                current_user={"user_id": "user-1"},
                refresh_server_exports=lambda: None,
                run_history_lock=threading.Lock(),
                run_history=[{"run_id": "run-1", "owner_user_id": "user-1"}],
                history_item_matches=lambda item, workspace_id, status, pack_id: seen.setdefault("workspace_id", workspace_id) == "ws-user-1",
                current_user_is_privileged=lambda current_user: False,
                extract_run_owner_user_id=lambda item: str(item.get("owner_user_id") or ""),
                enforce_workspace_access=lambda current_user, workspace_id, minimum_role="viewer": "ws-user-1",
                resolve_workspace_tenant_id=lambda workspace_id: f"tenant-for-{workspace_id}",
                normalize_run_id_token=lambda value: str(value).strip() or None,
                summarize_history_item=lambda item: {"run_id": item.get("run_id")},
            )

        self.assertEqual(seen["workspace_id"], "ws-user-1")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(rust_mock.call_args.args[0], "run-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "list_runs")

    def test_build_runs_history_payload_wrong_rust_next_action_blocks_before_history_scan(self):
        scanned = {"called": False}
        with mock.patch.object(
            runtime_history_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "list_runs",
                "next_action": "get_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_history_service.build_runs_history_payload(
                    limit=10,
                    workspace_id="default",
                    status=None,
                    pack_id=None,
                    current_user={"user_id": "user-1"},
                    refresh_server_exports=lambda: None,
                    run_history_lock=threading.Lock(),
                    run_history=[{"run_id": "run-1", "owner_user_id": "user-1"}],
                    history_item_matches=lambda item, workspace_id, status, pack_id: scanned.update({"called": True}) or True,
                    current_user_is_privileged=lambda current_user: False,
                    extract_run_owner_user_id=lambda item: str(item.get("owner_user_id") or ""),
                    enforce_workspace_access=lambda current_user, workspace_id, minimum_role="viewer": "ws-user-1",
                    resolve_workspace_tenant_id=lambda workspace_id: f"tenant-for-{workspace_id}",
                    normalize_run_id_token=lambda value: str(value).strip() or None,
                    summarize_history_item=lambda item: {"run_id": item.get("run_id")},
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))
        self.assertFalse(scanned["called"])


if __name__ == "__main__":
    unittest.main()
