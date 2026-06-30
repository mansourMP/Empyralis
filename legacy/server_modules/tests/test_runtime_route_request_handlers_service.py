import unittest

from fastapi import HTTPException

from server_modules import runtime_route_request_handlers_service


class _Request:
    def __init__(self, payload=None, *, headers=None, url="https://example.test/hook") -> None:
        self._payload = payload
        self.headers = headers or {}
        self.url = url

    async def json(self):
        return self._payload


class RuntimeRouteRequestHandlersServiceTests(unittest.TestCase):
    def test_trigger_heartbeat_response_maps_missing_scheduler_error(self):
        with self.assertRaises(HTTPException) as exc:
            runtime_route_request_handlers_service.trigger_heartbeat_response(
                heartbeat_scheduler=lambda: None,
                trigger_heartbeat_payload=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("missing")),
            )

        self.assertEqual(exc.exception.status_code, 503)

    def test_respond_chat_response_uses_header_cursor_and_authenticated_user(self):
        async def _build_response(**kwargs):
            return kwargs

        async def _run():
            return await runtime_route_request_handlers_service.respond_chat_response(
                request=_Request({"message": "hi"}, headers={"last-event-id": "12"}),
                current_user={"user_id": "user-1"},
                read_json_object_payload=lambda request, invalid_detail: request.json(),
                require_authenticated_user=lambda current_user: {"wrapped": current_user["user_id"]},
                build_direct_chat_stream_response=_build_response,
                direct_chat_stream_response_services=lambda: "services",
            )

        payload = self._run_async(_run())
        self.assertEqual(payload["current_user"], {"wrapped": "user-1"})
        self.assertEqual(payload["last_event_id"], "12")
        self.assertEqual(payload["services"], "services")

    def test_register_webhook_trigger_response_loads_and_registers(self):
        calls = []

        async def _run():
            return await runtime_route_request_handlers_service.register_webhook_trigger_response(
                request=_Request({"url_pattern": "*", "workflow_id": "wf-1"}),
                current_user={"user_id": "user-1"},
                load_webhook_triggers=lambda: calls.append("loaded"),
                read_json_payload=lambda request, invalid_detail: request.json(),
                enforce_workspace_access=lambda current_user, workspace_id, minimum_role="member": "ws-1",
                register_webhook_trigger_payload=lambda body, **kwargs: {"ok": True, "workflow_id": body["workflow_id"]},
                uuid_factory=lambda: "uuid-1",
                build_webhook_trigger_fn=lambda **kwargs: kwargs,
                triggers={},
                lock=object(),
                persist_webhook_triggers_locked=lambda: None,
            )

        payload = self._run_async(_run())
        self.assertEqual(calls, ["loaded"])
        self.assertEqual(payload["workflow_id"], "wf-1")

    def test_ingest_webhook_response_reads_payload_and_delegates(self):
        async def _ingest_payload(**kwargs):
            return kwargs

        async def _run():
            return await runtime_route_request_handlers_service.ingest_webhook_response(
                "default",
                request=_Request({"ok": True}),
                current_user={"user_id": "user-1"},
                enforce_workspace_access=lambda current_user, workspace_id, minimum_role="member": "ws-1",
                read_json_payload=lambda request, invalid_detail: request.json(),
                ingest_webhook_payload=_ingest_payload,
                match_webhook_trigger_fn=lambda workspace_id, request_url: {},
                run_start_request_class=lambda **kwargs: kwargs,
                execute_run_start_request_via_turn_runtime=lambda *args, **kwargs: {},
                stamp_request_owner_fn=lambda *args, **kwargs: None,
                run_execution_services=lambda: "services",
            )

        payload = self._run_async(_run())
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["payload"], {"ok": True})
        self.assertEqual(payload["request_url"], "https://example.test/hook")

    def test_update_workspace_context_file_response_reads_and_updates(self):
        async def _run():
            return await runtime_route_request_handlers_service.update_workspace_context_file_response(
                "notes.md",
                request=_Request({"content": "hello"}),
                read_json_payload=lambda request, invalid_detail: request.json(),
                update_workspace_context_file_payload=lambda filename, body, **kwargs: {
                    "filename": filename,
                    "content": body["content"],
                },
                write_workspace_context_file=lambda filename, content: {"filename": filename, "content": content},
            )

        payload = self._run_async(_run())
        self.assertEqual(payload, {"filename": "notes.md", "content": "hello"})

    def _run_async(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


if __name__ == "__main__":
    unittest.main()
