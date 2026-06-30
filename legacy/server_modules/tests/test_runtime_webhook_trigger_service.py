import threading
import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import runtime_webhook_trigger_service


class RuntimeWebhookTriggerServiceTests(unittest.TestCase):
    def test_build_load_webhook_triggers_fn_updates_loaded_flag(self):
        triggers = {}
        loaded = {"value": False}

        loader = runtime_webhook_trigger_service.build_load_webhook_triggers_fn(
            triggers,
            lock=threading.Lock(),
            get_loaded=lambda: loaded["value"],
            set_loaded=lambda value: loaded.__setitem__("value", value),
            path="/tmp/webhooks.json",
            safe_read_json=lambda path, default: {"items": [{"id": "trigger-1", "workspace_id": "default"}]},
        )

        loader()

        self.assertTrue(loaded["value"])
        self.assertIn("trigger-1", triggers)

    def test_load_webhook_triggers_populates_registry(self):
        triggers = {}

        loaded = runtime_webhook_trigger_service.load_webhook_triggers(
            triggers,
            lock=threading.Lock(),
            loaded=False,
            path="/tmp/webhooks.json",
            safe_read_json=lambda path, default: {"items": [{"id": "trigger-1", "workspace_id": "default"}]},
        )

        self.assertTrue(loaded)
        self.assertIn("trigger-1", triggers)

    def test_build_match_webhook_trigger_fn_loads_before_matching(self):
        calls = []
        matcher = runtime_webhook_trigger_service.build_match_webhook_trigger_fn(
            {
                "trigger-1": {
                    "id": "trigger-1",
                    "workspace_id": "default",
                    "url_pattern": "*/webhooks/ingest/default",
                    "enabled": True,
                }
            },
            lock=threading.Lock(),
            load_webhook_triggers_fn=lambda: calls.append("loaded"),
        )

        trigger = matcher("default", "https://example.com/webhooks/ingest/default")

        self.assertEqual(calls, ["loaded"])
        self.assertEqual(trigger["id"], "trigger-1")

    def test_match_webhook_trigger_filters_by_workspace_and_pattern(self):
        trigger = runtime_webhook_trigger_service.match_webhook_trigger(
            {
                "trigger-1": {
                    "id": "trigger-1",
                    "workspace_id": "default",
                    "url_pattern": "*/webhooks/ingest/default",
                    "enabled": True,
                }
            },
            lock=threading.Lock(),
            workspace_id="default",
            request_url="https://example.com/webhooks/ingest/default",
        )

        self.assertEqual(trigger["id"], "trigger-1")

    def test_build_webhook_trigger_normalizes_optional_fields(self):
        trigger = runtime_webhook_trigger_service.build_webhook_trigger(
            trigger_id="trigger-1",
            workspace_id="",
            url_pattern="/hook",
            workflow_id="wf-1",
            user_goal="",
            metadata=None,
            enabled=1,
            now_ts=123.0,
        )

        self.assertEqual(trigger["workspace_id"], "default")
        self.assertIsNone(trigger["user_goal"])
        self.assertEqual(trigger["metadata"], {})
        self.assertTrue(trigger["enabled"])
        self.assertEqual(trigger["created_at"], 123.0)

    def test_register_webhook_trigger_payload_validates_and_persists_trigger(self):
        triggers = {}
        persisted = []

        with mock.patch.object(
            runtime_webhook_trigger_service.rust_runtime_kernel_client,
            "run_trigger_decision",
            return_value={"ok": True, "decision": "allow", "next_action": "register_webhook_trigger"},
        ) as rust_mock:
            payload = runtime_webhook_trigger_service.register_webhook_trigger_payload(
                {
                    "workspace_id": "default",
                    "url_pattern": "/hook",
                    "workflow_id": "wf-1",
                },
                uuid_factory=lambda: "trigger-1",
                build_webhook_trigger_fn=runtime_webhook_trigger_service.build_webhook_trigger,
                triggers=triggers,
                lock=threading.Lock(),
                persist_webhook_triggers_locked=lambda: persisted.append(True),
            )

        self.assertTrue(payload["ok"])
        self.assertIn("trigger-1", triggers)
        self.assertEqual(persisted, [True])
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.kwargs["operation"], "register_webhook")

    def test_register_webhook_trigger_payload_rust_denial_blocks_persistence(self):
        triggers = {}
        persisted = []

        with mock.patch.object(
            runtime_webhook_trigger_service.rust_runtime_kernel_client,
            "run_trigger_decision",
            return_value={"ok": False, "decision": "block", "reason": "webhook_workflow_id_missing"},
        ) as rust_mock:
            with self.assertRaises(HTTPException) as raised:
                runtime_webhook_trigger_service.register_webhook_trigger_payload(
                    {
                        "workspace_id": "default",
                        "url_pattern": "/hook",
                        "workflow_id": "wf-1",
                    },
                    uuid_factory=lambda: "trigger-1",
                    build_webhook_trigger_fn=runtime_webhook_trigger_service.build_webhook_trigger,
                    triggers=triggers,
                    lock=threading.Lock(),
                    persist_webhook_triggers_locked=lambda: persisted.append(True),
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(triggers, {})
        self.assertEqual(persisted, [])
        rust_mock.assert_called_once()

    def test_register_webhook_trigger_payload_unexpected_next_action_blocks_persistence(self):
        triggers = {}
        persisted = []

        with mock.patch.object(
            runtime_webhook_trigger_service.rust_runtime_kernel_client,
            "run_trigger_decision",
            return_value={"ok": True, "decision": "allow", "next_action": "start_run"},
        ):
            with self.assertRaises(HTTPException) as raised:
                runtime_webhook_trigger_service.register_webhook_trigger_payload(
                    {
                        "workspace_id": "default",
                        "url_pattern": "/hook",
                        "workflow_id": "wf-1",
                    },
                    uuid_factory=lambda: "trigger-1",
                    build_webhook_trigger_fn=runtime_webhook_trigger_service.build_webhook_trigger,
                    triggers=triggers,
                    lock=threading.Lock(),
                    persist_webhook_triggers_locked=lambda: persisted.append(True),
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "unexpected_next_action:start_run")
        self.assertEqual(triggers, {})
        self.assertEqual(persisted, [])

    def test_register_webhook_trigger_payload_rejects_missing_required_fields(self):
        with self.assertRaises(HTTPException):
            runtime_webhook_trigger_service.register_webhook_trigger_payload(
                {"workflow_id": "wf-1"},
                uuid_factory=lambda: "trigger-1",
                build_webhook_trigger_fn=runtime_webhook_trigger_service.build_webhook_trigger,
                triggers={},
                lock=threading.Lock(),
                persist_webhook_triggers_locked=lambda: None,
            )

    def test_ingest_webhook_payload_builds_run_start_request_and_returns_trigger_id(self):
        class _RunStartRequest:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

        captured = {}

        async def _execute(request_payload, **kwargs):
            captured["request_payload"] = request_payload.kwargs
            return {"run_id": "run-1"}

        with mock.patch.object(
            runtime_webhook_trigger_service.rust_runtime_kernel_client,
            "run_trigger_decision",
            return_value={"ok": True, "decision": "allow", "next_action": "start_run"},
        ) as rust_mock:
            payload = self._run_async(
                runtime_webhook_trigger_service.ingest_webhook_payload(
                    workspace_id="default",
                    request_url="https://example.com/webhooks/ingest/default",
                    payload={"event": "created"},
                    current_user={"user_id": "user-1"},
                    match_webhook_trigger_fn=lambda workspace_id, request_url: {
                        "id": "trigger-1",
                        "workflow_id": "wf-1",
                        "url_pattern": "*/default",
                        "metadata": {"source_id": "abc"},
                    },
                    run_start_request_class=_RunStartRequest,
                    execute_run_start_request_via_turn_runtime=_execute,
                    stamp_request_owner_fn=lambda payload, current_user: payload,
                    run_execution_services=lambda: object(),
                )
            )

        self.assertEqual(payload, {"ok": True, "run_id": "run-1", "trigger_id": "trigger-1"})
        self.assertEqual(captured["request_payload"]["workflow_id"], "wf-1")
        self.assertEqual(captured["request_payload"]["metadata"]["webhook_payload"], {"event": "created"})
        self.assertEqual(captured["request_payload"]["metadata"]["source_id"], "abc")
        rust_mock.assert_called_once()
        self.assertEqual(rust_mock.call_args.kwargs["operation"], "ingest_webhook")

    def test_ingest_webhook_payload_rust_denial_blocks_run_start(self):
        class _RunStartRequest:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

        async def _execute(request_payload, **kwargs):
            raise AssertionError("run start must not execute when Rust blocks webhook ingest")

        with mock.patch.object(
            runtime_webhook_trigger_service.rust_runtime_kernel_client,
            "run_trigger_decision",
            return_value={"ok": False, "decision": "block", "reason": "webhook_trigger_disabled"},
        ) as rust_mock:
            with self.assertRaises(HTTPException) as raised:
                self._run_async(
                    runtime_webhook_trigger_service.ingest_webhook_payload(
                        workspace_id="default",
                        request_url="https://example.com/webhooks/ingest/default",
                        payload={"event": "created"},
                        current_user={"user_id": "user-1"},
                        match_webhook_trigger_fn=lambda workspace_id, request_url: {
                            "id": "trigger-1",
                            "workflow_id": "wf-1",
                            "url_pattern": "*/default",
                            "enabled": False,
                        },
                        run_start_request_class=_RunStartRequest,
                        execute_run_start_request_via_turn_runtime=_execute,
                        stamp_request_owner_fn=lambda payload, current_user: payload,
                        run_execution_services=lambda: object(),
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "webhook_trigger_disabled")
        rust_mock.assert_called_once()

    def test_ingest_webhook_payload_unexpected_next_action_blocks_run_start(self):
        class _RunStartRequest:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

        async def _execute(request_payload, **kwargs):
            raise AssertionError("run start must not execute when Rust returns the wrong next_action")

        with mock.patch.object(
            runtime_webhook_trigger_service.rust_runtime_kernel_client,
            "run_trigger_decision",
            return_value={"ok": True, "decision": "allow", "next_action": "register_webhook_trigger"},
        ):
            with self.assertRaises(HTTPException) as raised:
                self._run_async(
                    runtime_webhook_trigger_service.ingest_webhook_payload(
                        workspace_id="default",
                        request_url="https://example.com/webhooks/ingest/default",
                        payload={"event": "created"},
                        current_user={"user_id": "user-1"},
                        match_webhook_trigger_fn=lambda workspace_id, request_url: {
                            "id": "trigger-1",
                            "workflow_id": "wf-1",
                            "url_pattern": "*/default",
                            "metadata": {"source_id": "abc"},
                        },
                        run_start_request_class=_RunStartRequest,
                        execute_run_start_request_via_turn_runtime=_execute,
                        stamp_request_owner_fn=lambda payload, current_user: payload,
                        run_execution_services=lambda: object(),
                    )
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "unexpected_next_action:register_webhook_trigger")

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
