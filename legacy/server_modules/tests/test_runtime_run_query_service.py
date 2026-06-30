import threading
import unittest
from datetime import datetime
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_run_query_service


class RuntimeRunQueryServiceTests(unittest.TestCase):
    @staticmethod
    def _allow_get_run(command, payload, allow_approval_required=False):
        return {
            "ok": True,
            "decision": "allow",
            "operation": "get_run",
            "next_action": "get_run",
        }

    def test_build_default_run_detail_response_callbacks_imports_default_modules(self):
        modules = {
            "server_modules.runs_delegation": type(
                "RunsDelegationModule",
                (),
                {
                    "_build_delegation_summary": staticmethod(lambda snapshot, child_runs: {"count": len(child_runs)}),
                    "_find_run_relationships": staticmethod(lambda run_id, snapshot: (None, [])),
                },
            )(),
            "server_modules.runs_output": type(
                "RunsOutputModule",
                (),
                {
                    "_get_replay_payload": staticmethod(lambda run_id: {"run_id": run_id}),
                    "_limited_node_states_view": staticmethod(lambda value: {"trimmed": True}),
                    "_resolve_run_connector_binding": staticmethod(lambda snapshot: {"connector": "telegram"}),
                    "_serialize_run_snapshot": staticmethod(lambda run_id, run: {"run_id": run_id}),
                    "redact_sensitive": staticmethod(lambda context: {"redacted": True}),
                },
            )(),
            "server_modules.memory_service": type(
                "MemoryServiceModule",
                (),
                {
                    "trim_memory_trace": staticmethod(lambda value: {"trimmed": True}),
                },
            )(),
        }

        callbacks = runtime_run_query_service.build_default_run_detail_response_callbacks(
            import_module=lambda name, fromlist=(): modules[name],
            enforce_run_owner_access=lambda current_user, snapshot: None,
            can_view_sensitive_run_payload=lambda current_user: False,
            limited_run_context_view=lambda context: {},
            limited_result_data_view_fn=lambda value: {},
            get_pending_confirmation_fn=lambda run: None,
            build_archived_run_detail_response=lambda **kwargs: {},
            build_live_run_detail_response=lambda **kwargs: {},
            build_run_browser_checkpoint_payload=lambda **kwargs: {},
            build_run_browser_session_payload=lambda **kwargs: {},
        )

        self.assertIn("get_replay_payload", callbacks)
        self.assertIn("find_run_relationships", callbacks)
        self.assertIn("trim_memory_trace_fn", callbacks)

    def test_build_run_detail_response_callbacks_preserves_callables(self):
        get_replay_payload = lambda run_id: {"run_id": run_id}
        callbacks = runtime_run_query_service.build_run_detail_response_callbacks(
            get_replay_payload=get_replay_payload,
            serialize_run_snapshot=lambda run_id, run: {},
            enforce_run_owner_access=lambda current_user, snapshot: None,
            can_view_sensitive_run_payload=lambda current_user: False,
            limited_run_context_view=lambda context: {},
            build_delegation_summary=lambda snapshot, child_runs: {},
            find_run_relationships=lambda run_id, snapshot: (None, []),
            resolve_run_connector_binding=lambda snapshot: {},
            redact_sensitive=lambda context: {},
            limited_result_data_view_fn=lambda value: None,
            limited_node_states_view_fn=lambda value: None,
            trim_memory_trace_fn=lambda value: None,
            get_pending_confirmation_fn=lambda run: None,
            build_archived_run_detail_response=lambda **kwargs: {},
            build_live_run_detail_response=lambda **kwargs: {},
            build_run_browser_checkpoint_payload=lambda **kwargs: {},
            build_run_browser_session_payload=lambda **kwargs: {},
        )

        self.assertIs(callbacks["get_replay_payload"], get_replay_payload)
        self.assertIn("build_live_run_detail_response", callbacks)

    def test_build_run_detail_response_returns_archived_payload_when_live_run_missing(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_get_run,
        ):
            payload = runtime_run_query_service.build_run_detail_response(
                "run-1",
                current_user={"auth_type": "api_key"},
                runs={},
                get_replay_payload=lambda run_id: {
                    "run_id": run_id,
                    "context": {"metadata": {"agent_role": "researcher"}, "secret": True},
                },
                serialize_run_snapshot=lambda run_id, run: {},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                can_view_sensitive_run_payload=lambda current_user: False,
                limited_run_context_view=lambda context: {"workspace_id": "default"},
                build_delegation_summary=lambda snapshot, child_runs: {"count": len(child_runs)},
                find_run_relationships=lambda run_id, snapshot: (None, [{"run_id": "child-1"}]),
                resolve_run_connector_binding=lambda snapshot: {"connector": "telegram"},
                redact_sensitive=lambda context: {"redacted": True},
                limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
                limited_node_states_view_fn=lambda value: {"trimmed": True},
                trim_memory_trace_fn=lambda value: {"trimmed": True},
                get_pending_confirmation_fn=lambda run: None,
                build_archived_run_detail_response=lambda **kwargs: {
                    "archived": True,
                    "safe_context": kwargs["safe_context"],
                    "delegation_summary": kwargs["delegation_summary"],
                },
                build_live_run_detail_response=lambda **kwargs: {"archived": False},
            )

        self.assertTrue(payload["archived"])
        self.assertEqual(payload["safe_context"], {"workspace_id": "default"})
        self.assertEqual(payload["delegation_summary"], {"count": 1})

    def test_build_run_detail_response_returns_live_payload_when_run_exists(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_get_run,
        ):
            payload = runtime_run_query_service.build_run_detail_response(
                "run-1",
                current_user={"auth_type": "api_key"},
                runs={
                    "run-1": {
                        "context": {"metadata": {"owner_user_id": "user-1"}, "secret": True},
                        "memory_trace": {"raw": True},
                    }
                },
                get_live_run_fn=lambda run_id: {
                    "context": {"metadata": {"owner_user_id": "user-1"}, "secret": True},
                    "memory_trace": {"raw": True},
                },
                get_replay_payload=lambda run_id: {},
                serialize_run_snapshot=lambda run_id, run: {"run_id": run_id, "snapshot": True},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                can_view_sensitive_run_payload=lambda current_user: True,
                limited_run_context_view=lambda context: {"workspace_id": "default"},
                build_delegation_summary=lambda snapshot, child_runs: {"count": len(child_runs)},
                find_run_relationships=lambda run_id, snapshot: ({"run_id": "parent-1"}, []),
                resolve_run_connector_binding=lambda snapshot: {"connector": "telegram"},
                redact_sensitive=lambda context: {"redacted": True},
                limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
                limited_node_states_view_fn=lambda value: {"trimmed": True},
                trim_memory_trace_fn=lambda value: {"trimmed": bool(value)},
                get_pending_confirmation_fn=lambda run: {"approval_id": "approval-1"},
                build_archived_run_detail_response=lambda **kwargs: {"archived": True},
                build_live_run_detail_response=lambda **kwargs: {
                    "archived": False,
                    "safe_context": kwargs["safe_context"],
                    "pending_confirmation": kwargs["get_pending_confirmation_fn"](kwargs["run"]),
                },
            )

        self.assertFalse(payload["archived"])
        self.assertEqual(payload["safe_context"], {"redacted": True})
        self.assertEqual(payload["pending_confirmation"], {"approval_id": "approval-1"})

    def test_build_run_detail_response_accepts_route_callback_bundle(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_get_run,
        ):
            payload = runtime_run_query_service.build_run_detail_response(
                "run-1",
                current_user={"auth_type": "api_key"},
                runs={"run-1": {"context": {"metadata": {"owner_user_id": "user-1"}}}},
                get_live_run_fn=lambda run_id: None,
                get_replay_payload=lambda run_id: {},
                serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                can_view_sensitive_run_payload=lambda current_user: False,
                limited_run_context_view=lambda context: {},
                build_delegation_summary=lambda snapshot, child_runs: {},
                find_run_relationships=lambda run_id, snapshot: (None, []),
                resolve_run_connector_binding=lambda snapshot: {},
                redact_sensitive=lambda context: {},
                limited_result_data_view_fn=lambda value: None,
                limited_node_states_view_fn=lambda value: None,
                trim_memory_trace_fn=lambda value: None,
                get_pending_confirmation_fn=lambda run: None,
                build_archived_run_detail_response=lambda **kwargs: {"archived": True},
                build_live_run_detail_response=lambda **kwargs: {"archived": False},
                build_run_browser_checkpoint_payload=lambda **kwargs: {"checkpoint": True},
                build_run_browser_session_payload=lambda **kwargs: {"session": True},
            )

        self.assertFalse(payload["archived"])

    def test_build_run_browser_checkpoint_response_uses_archived_payload_builder(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_get_run,
        ):
            payload = runtime_run_query_service.build_run_browser_checkpoint_response(
                "run-1",
                current_user={"auth_type": "session"},
                runs={},
                get_replay_payload=lambda run_id: {
                    "run_id": run_id,
                    "context": {"metadata": {"browser_session_profile": "qa-browser", "tenant_id": "tenant-1"}},
                    "browser_checkpoint": {"next_action_index": 3},
                },
                serialize_run_snapshot=lambda run_id, run: {},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                can_view_sensitive_run_payload=lambda current_user: False,
                build_run_browser_checkpoint_payload=lambda **kwargs: {
                    "archived": kwargs["archived"],
                    "include_sensitive": kwargs["include_sensitive"],
                    "session_profile": kwargs["metadata"].get("browser_session_profile"),
                    "next_action_index": kwargs["source"]["browser_checkpoint"]["next_action_index"],
                },
            )

        self.assertTrue(payload["archived"])
        self.assertFalse(payload["include_sensitive"])
        self.assertEqual(payload["session_profile"], "qa-browser")
        self.assertEqual(payload["next_action_index"], 3)

    def test_build_run_browser_session_response_uses_live_run_when_available(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_get_run,
        ):
            payload = runtime_run_query_service.build_run_browser_session_response(
                "run-1",
                current_user={"auth_type": "api_key"},
                runs={
                    "run-1": {
                        "context": {"metadata": {"browser_session_profile": "qa-browser", "tenant_id": "tenant-1"}},
                        "browser_checkpoint": {"next_action_index": 4},
                        "result_data": {"outputs": {"actions": []}},
                    }
                },
                get_live_run_fn=lambda run_id: None,
                get_replay_payload=lambda run_id: {"archived": True},
                serialize_run_snapshot=lambda run_id, run: {"run_id": run_id},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                can_view_sensitive_run_payload=lambda current_user: True,
                build_run_browser_session_payload=lambda **kwargs: {
                    "archived": kwargs["archived"],
                    "include_sensitive": kwargs["include_sensitive"],
                    "has_checkpoint": bool(kwargs["source"].get("browser_checkpoint")),
                    "session_profile": kwargs["metadata"].get("browser_session_profile"),
                },
            )

        self.assertFalse(payload["archived"])
        self.assertTrue(payload["include_sensitive"])
        self.assertTrue(payload["has_checkpoint"])
        self.assertEqual(payload["session_profile"], "qa-browser")

    def test_build_run_detail_response_prefers_explicit_runs_map_over_repository_lookup(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_get_run,
        ):
            payload = runtime_run_query_service.build_run_detail_response(
                "run-1",
                current_user={"auth_type": "api_key"},
                runs={
                    "run-1": {
                        "context": {"metadata": {"owner_user_id": "user-1"}, "secret": True},
                        "memory_trace": {"raw": True},
                    }
                },
                get_live_run_fn=lambda run_id: {
                    "context": {"metadata": {"owner_user_id": "user-1"}, "secret": False},
                    "memory_trace": {},
                },
                get_replay_payload=lambda run_id: {"archived": True},
                serialize_run_snapshot=lambda run_id, run: {"run_id": run_id, "secret": run["context"].get("secret")},
                enforce_run_owner_access=lambda current_user, snapshot: None,
                can_view_sensitive_run_payload=lambda current_user: True,
                limited_run_context_view=lambda context: {"workspace_id": "default"},
                build_delegation_summary=lambda snapshot, child_runs: {"count": len(child_runs)},
                find_run_relationships=lambda run_id, snapshot: (None, []),
                resolve_run_connector_binding=lambda snapshot: {"connector": "telegram"},
                redact_sensitive=lambda context: {"secret": context.get("secret")},
                limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
                limited_node_states_view_fn=lambda value: {"trimmed": True},
                trim_memory_trace_fn=lambda value: {"trimmed": bool(value)},
                get_pending_confirmation_fn=lambda run: None,
                build_archived_run_detail_response=lambda **kwargs: {"archived": True},
                build_live_run_detail_response=lambda **kwargs: {
                    "archived": False,
                    "safe_context": kwargs["safe_context"],
                    "snapshot": kwargs["snapshot"],
                },
            )

        self.assertFalse(payload["archived"])
        self.assertEqual(payload["safe_context"], {"secret": True})
        self.assertEqual(payload["snapshot"], {"run_id": "run-1", "secret": True})

    def test_build_run_detail_response_raises_not_found_when_no_live_or_archived_run_exists(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=self._allow_get_run,
        ):
            with self.assertRaises(HTTPException):
                runtime_run_query_service.build_run_detail_response(
                    "run-1",
                    current_user={"auth_type": "api_key"},
                    runs={},
                    get_replay_payload=lambda run_id: (_ for _ in ()).throw(HTTPException(status_code=404)),
                    serialize_run_snapshot=lambda run_id, run: {},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    can_view_sensitive_run_payload=lambda current_user: False,
                    limited_run_context_view=lambda context: {},
                    build_delegation_summary=lambda snapshot, child_runs: {},
                    find_run_relationships=lambda run_id, snapshot: (None, []),
                    resolve_run_connector_binding=lambda snapshot: {},
                    redact_sensitive=lambda context: {},
                    limited_result_data_view_fn=lambda value: None,
                    limited_node_states_view_fn=lambda value: None,
                    trim_memory_trace_fn=lambda value: None,
                    get_pending_confirmation_fn=lambda run: None,
                    build_archived_run_detail_response=lambda **kwargs: {},
                    build_live_run_detail_response=lambda **kwargs: {},
                )

    def test_build_run_browser_checkpoint_response_request_owner_approval_blocks_before_builder(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "require_approval",
                "operation": "get_run",
                "reason": "owner_approval_required",
                "next_action": "request_owner_approval",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_query_service.build_run_browser_checkpoint_response(
                    "run-1",
                    current_user={"auth_type": "session"},
                    runs={},
                    get_replay_payload=lambda run_id: {
                        "run_id": run_id,
                        "context": {"metadata": {"browser_session_profile": "qa-browser", "tenant_id": "tenant-1"}},
                        "browser_checkpoint": {"next_action_index": 3},
                    },
                    serialize_run_snapshot=lambda run_id, run: {},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    can_view_sensitive_run_payload=lambda current_user: False,
                    build_run_browser_checkpoint_payload=lambda **kwargs: self.fail("builder should not run"),
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Rust run-api gate blocked get_run", str(raised.exception.detail))

    def test_build_run_detail_response_wrong_rust_next_action_blocks_before_builder(self):
        with mock.patch.object(
            runtime_run_query_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "get_run",
                "next_action": "pause_run",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_run_query_service.build_run_detail_response(
                    "run-1",
                    current_user={"auth_type": "api_key"},
                    runs={
                        "run-1": {
                            "context": {"metadata": {"owner_user_id": "user-1"}, "secret": True},
                            "memory_trace": {"raw": True},
                        }
                    },
                    get_replay_payload=lambda run_id: {},
                    serialize_run_snapshot=lambda run_id, run: {"run_id": run_id, "snapshot": True},
                    enforce_run_owner_access=lambda current_user, snapshot: None,
                    can_view_sensitive_run_payload=lambda current_user: True,
                    limited_run_context_view=lambda context: {"workspace_id": "default"},
                    build_delegation_summary=lambda snapshot, child_runs: {"count": len(child_runs)},
                    find_run_relationships=lambda run_id, snapshot: ({"run_id": "parent-1"}, []),
                    resolve_run_connector_binding=lambda snapshot: {"connector": "telegram"},
                    redact_sensitive=lambda context: {"redacted": True},
                    limited_result_data_view_fn=lambda value: {"summary": "trimmed"},
                    limited_node_states_view_fn=lambda value: {"trimmed": True},
                    trim_memory_trace_fn=lambda value: {"trimmed": bool(value)},
                    get_pending_confirmation_fn=lambda run: {"approval_id": "approval-1"},
                    build_archived_run_detail_response=lambda **kwargs: self.fail("archived builder should not run"),
                    build_live_run_detail_response=lambda **kwargs: self.fail("live builder should not run"),
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertIn("unexpected next_action", str(raised.exception.detail))

    def test_build_run_list_response_merges_live_and_history_without_duplicates(self):
        runs = {
            "run-live": {
                "run_id": "run-live",
                "status": "running",
                "workspace_id": "default",
                "owner_user_id": "user-1",
                "updated_at": "2026-04-06T10:00:00Z",
                "created_at": "2026-04-06T09:55:00Z",
            }
        }
        history = [
            {
                "run_id": "run-live",
                "status": "completed",
                "workspace_id": "default",
                "owner_user_id": "user-1",
                "updated_at": "2026-04-06T09:59:00Z",
                "created_at": "2026-04-06T09:50:00Z",
            },
            {
                "run_id": "run-archived",
                "status": "completed",
                "workspace_id": "default",
                "owner_user_id": "user-1",
                "updated_at": "2026-04-06T08:00:00Z",
                "created_at": "2026-04-06T07:55:00Z",
            },
        ]

        payload = runtime_run_query_service.build_run_list_response(
            limit=50,
            offset=0,
            workspace_id="default",
            status=None,
            pack_id=None,
            current_user={"user_id": "user-1"},
            runs=runs,
            list_live_runs_fn=lambda: list(runs.values()),
            run_history_lock=threading.Lock(),
            run_history=history,
            serialize_run_snapshot=lambda run_id, run: dict(run),
            history_item_matches=lambda item, workspace_id, status, pack_id: (
                (not workspace_id or item.get("workspace_id") == workspace_id)
                and (not status or str(item.get("status") or "").lower() == str(status).lower())
            ),
            current_user_is_privileged=lambda current_user: False,
            extract_run_owner_user_id=lambda item: str(item.get("owner_user_id") or ""),
            summarize_history_item=lambda item: {
                "run_id": item.get("run_id"),
                "status": item.get("status"),
                "updated_at": item.get("updated_at"),
                "created_at": item.get("created_at"),
            },
            parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
        )

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["items"][0]["run_id"], "run-live")
        self.assertEqual(payload["items"][0]["source"], "live")
        self.assertEqual(payload["items"][1]["run_id"], "run-archived")
        self.assertEqual(payload["items"][1]["source"], "history")

    def test_build_run_list_response_supports_paged_live_run_queries(self):
        runs = {}
        history = [
            {
                "run_id": "run-history",
                "status": "completed",
                "workspace_id": "default",
                "owner_user_id": "user-1",
                "updated_at": "2026-04-06T08:00:00Z",
                "created_at": "2026-04-06T07:55:00Z",
            }
        ]
        pages = {
            0: [
                {
                    "run_id": "run-live-1",
                    "status": "running",
                    "workspace_id": "default",
                    "owner_user_id": "user-1",
                    "updated_at": "2026-04-06T10:00:00Z",
                    "created_at": "2026-04-06T09:55:00Z",
                }
            ],
            200: [],
        }
        calls = []

        payload = runtime_run_query_service.build_run_list_response(
            limit=50,
            offset=0,
            workspace_id="default",
            status=None,
            pack_id=None,
            current_user={"user_id": "user-1"},
            runs=runs,
            list_live_runs_fn=lambda: (_ for _ in ()).throw(AssertionError("full live run scan should not be used")),
            run_history_lock=threading.Lock(),
            run_history=history,
            serialize_run_snapshot=lambda run_id, run: dict(run),
            history_item_matches=lambda item, workspace_id, status, pack_id: (
                (not workspace_id or item.get("workspace_id") == workspace_id)
                and (not status or str(item.get("status") or "").lower() == str(status).lower())
            ),
            current_user_is_privileged=lambda current_user: False,
            extract_run_owner_user_id=lambda item: str(item.get("owner_user_id") or ""),
            summarize_history_item=lambda item: {
                "run_id": item.get("run_id"),
                "status": item.get("status"),
                "updated_at": item.get("updated_at"),
                "created_at": item.get("created_at"),
            },
            parse_utc_ts=lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
            list_live_runs_page_fn=lambda limit, offset, workspace_id, states: calls.append((limit, offset, workspace_id, states)) or list(pages.get(offset, [])),
        )

        self.assertEqual(payload["count"], 2)
        self.assertEqual([item["run_id"] for item in payload["items"]], ["run-live-1", "run-history"])
        self.assertEqual(calls[0][1], 0)
        self.assertEqual(calls[0][2], "default")


if __name__ == "__main__":
    unittest.main()
