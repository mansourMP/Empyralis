import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import threading

from server_modules import gateway_protocol_service


class GatewayProtocolServiceTests(unittest.TestCase):
    def test_parse_frame_accepts_valid_object_frame(self) -> None:
        frame = gateway_protocol_service._parse_frame(
            json.dumps(
                {
                    "kind": "request",
                    "protocolVersion": "v1alpha2",
                    "id": "connect",
                    "type": "gateway.connect",
                    "payload": {"ok": True},
                }
            )
        )

        self.assertEqual(frame["type"], "gateway.connect")

    def test_connect_frame_protocol_version_accepts_client_camel_case(self) -> None:
        frame = {
            "kind": "request",
            "protocolVersion": "v1alpha2",
            "id": "connect",
            "type": "gateway.connect",
            "payload": {"gateway_version": "0.1.0"},
        }

        version = gateway_protocol_service._connect_frame_protocol_version(frame, frame["payload"])

        self.assertEqual(version, "v1alpha2")

    def test_parse_frame_rejects_oversized_frame_before_json_parse(self) -> None:
        oversized = "x" * (gateway_protocol_service.MAX_GATEWAY_FRAME_BYTES + 1)

        with self.assertRaises(gateway_protocol_service.GatewayFrameValidationError) as raised:
            gateway_protocol_service._parse_frame(oversized)

        self.assertEqual(raised.exception.error_code, "gateway_frame_too_large")
        self.assertEqual(raised.exception.close_code, 4409)

    def test_parse_frame_accepts_exact_size_boundary(self) -> None:
        base = {"kind": "request", "id": "a", "type": "gateway.heartbeat", "payload": {"padding": ""}}
        raw = json.dumps(base, separators=(",", ":"))
        remaining = gateway_protocol_service.MAX_GATEWAY_FRAME_BYTES - len(raw.encode("utf-8"))
        base["payload"]["padding"] = "x" * max(0, remaining)
        raw = json.dumps(base, separators=(",", ":"))
        while len(raw.encode("utf-8")) > gateway_protocol_service.MAX_GATEWAY_FRAME_BYTES:
            base["payload"]["padding"] = base["payload"]["padding"][:-1]
            raw = json.dumps(base, separators=(",", ":"))

        frame = gateway_protocol_service._parse_frame(raw)

        self.assertEqual(frame["type"], "gateway.heartbeat")

    def test_parse_frame_rejects_invalid_json(self) -> None:
        with self.assertRaises(gateway_protocol_service.GatewayFrameValidationError) as raised:
            gateway_protocol_service._parse_frame("{not-json")

        self.assertEqual(raised.exception.error_code, "gateway_frame_invalid_json")

    def test_parse_frame_rejects_deeply_nested_json(self) -> None:
        value = "leaf"
        for _ in range(gateway_protocol_service.MAX_GATEWAY_JSON_DEPTH + 2):
            value = {"nested": value}

        with self.assertRaises(gateway_protocol_service.GatewayFrameValidationError) as raised:
            gateway_protocol_service._parse_frame(json.dumps(value))

        self.assertEqual(raised.exception.error_code, "gateway_frame_too_deep")

    def test_parse_frame_rejects_non_object_json(self) -> None:
        with self.assertRaises(gateway_protocol_service.GatewayFrameValidationError) as raised:
            gateway_protocol_service._parse_frame(json.dumps(["not", "a", "frame"]))

        self.assertEqual(raised.exception.error_code, "gateway_frame_not_object")

    def test_scope_match_requires_all_scope_fields(self) -> None:
        registration = {
            "tenant_id": "tenant-1",
            "workspace_id": "default",
            "user_id": "owner-1",
            "device_id": "device-1",
            "gateway_id": "gateway-1",
        }
        matching_scope = {
            "tenant_id": "tenant-1",
            "workspace_id": "default",
            "user_id": "owner-1",
            "device_id": "device-1",
            "gateway_id": "gateway-1",
        }
        missing_scope = {
            "tenant_id": "tenant-1",
            "workspace_id": "default",
            "user_id": "owner-1",
            "device_id": "device-1",
        }

        self.assertTrue(gateway_protocol_service._scope_matches_registration(matching_scope, registration))
        self.assertFalse(gateway_protocol_service._scope_matches_registration(missing_scope, registration))

    def test_normalize_frame_seq_ack_rejects_invalid_sequence(self) -> None:
        with self.assertRaises(gateway_protocol_service.GatewayFrameValidationError) as raised:
            gateway_protocol_service._normalize_frame_seq_ack({"seq": "bad"})
        self.assertEqual(raised.exception.error_code, "gateway_frame_invalid_sequence")

        with self.assertRaises(gateway_protocol_service.GatewayFrameValidationError) as raised_negative:
            gateway_protocol_service._normalize_frame_seq_ack({"ack": -1})
        self.assertEqual(raised_negative.exception.error_code, "gateway_frame_invalid_sequence")

    def test_normalize_frame_seq_ack_accepts_positive_and_missing_values(self) -> None:
        seq, ack = gateway_protocol_service._normalize_frame_seq_ack({"seq": "7", "ack": 3})
        self.assertEqual(seq, 7)
        self.assertEqual(ack, 3)

        seq_missing, ack_missing = gateway_protocol_service._normalize_frame_seq_ack({})
        self.assertIsNone(seq_missing)
        self.assertIsNone(ack_missing)

    def test_live_connection_rejects_duplicate_inbound_frame_ids(self) -> None:
        connection = gateway_protocol_service._LiveGatewayConnection(
            websocket=object(),
            gateway_id="gateway-1",
            session_id="session-1",
            scope={"tenant_id": "tenant-1", "workspace_id": "workspace-1"},
        )

        self.assertTrue(connection.remember_inbound_frame_id("frame-1"))
        self.assertFalse(connection.remember_inbound_frame_id("frame-1"))
        self.assertTrue(connection.remember_inbound_frame_id("frame-2"))
        self.assertTrue(connection.remember_inbound_frame_id(""))

    def test_parse_frame_rejects_empty_frame(self) -> None:
        with self.assertRaises(gateway_protocol_service.GatewayFrameValidationError) as raised:
            gateway_protocol_service._parse_frame("")
        self.assertEqual(raised.exception.error_code, "gateway_frame_invalid_json")

    def test_connection_ready_probes_before_tool_dispatch(self) -> None:
        connection = SimpleNamespace(
            session_id="session-1",
            is_stale=lambda: False,
            send_request=AsyncMock(return_value={"ok": True, "payload": {"status": "ready"}}),
        )

        async def run_test() -> None:
            ready = await gateway_protocol_service._connection_ready_for_tool_dispatch(
                gateway_id="gateway-1",
                connection=connection,
            )

            self.assertIs(ready, connection)
            connection.send_request.assert_awaited_once()
            kwargs = connection.send_request.await_args.kwargs
            self.assertEqual(kwargs["message_type"], "gateway.probe")
            self.assertEqual(kwargs["payload"], {"purpose": "pre_dispatch"})

        import asyncio

        asyncio.run(run_test())

    def test_fresh_live_connection_waits_for_recent_inbound_frame(self) -> None:
        calls = {"recent": 0}

        def has_recent_inbound_frame() -> bool:
            calls["recent"] += 1
            return calls["recent"] >= 2

        connection = SimpleNamespace(
            session_id="session-1",
            is_stale=lambda: False,
            has_recent_inbound_frame=has_recent_inbound_frame,
        )

        async def run_test() -> None:
            with (
                patch(
                    "server_modules.gateway_protocol_service._get_live_connection",
                    return_value=connection,
                ),
                patch(
                    "server_modules.gateway_protocol_service.asyncio.sleep",
                    new=AsyncMock(),
                ) as sleep_mock,
            ):
                ready = await gateway_protocol_service._fresh_live_connection(
                    "gateway-1",
                    require_recent_inbound=True,
                )

            self.assertIs(ready, connection)
            self.assertGreaterEqual(calls["recent"], 2)
            sleep_mock.assert_awaited()

        import asyncio

        asyncio.run(run_test())

    def test_live_connection_send_frame_marshals_to_owner_loop(self) -> None:
        import asyncio

        class FakeWebSocket:
            def __init__(self) -> None:
                self.frames: list[str] = []
                self.sent = threading.Event()

            async def send_text(self, frame: str) -> None:
                self.frames.append(str(frame))
                self.sent.set()

        websocket = FakeWebSocket()
        ready = threading.Event()
        stop = threading.Event()
        holder: dict[str, gateway_protocol_service._LiveGatewayConnection] = {}

        async def owner_loop() -> None:
            connection = gateway_protocol_service._LiveGatewayConnection(
                websocket=websocket,
                gateway_id="gateway-1",
                session_id="session-1",
                scope={"tenant_id": "tenant-1", "workspace_id": "workspace-1"},
            )
            connection.start_writer()
            holder["connection"] = connection
            ready.set()
            try:
                while not stop.is_set():
                    await asyncio.sleep(0.01)
            finally:
                connection.fail_pending("test complete")

        thread = threading.Thread(target=lambda: asyncio.run(owner_loop()), daemon=True)
        thread.start()
        self.assertTrue(ready.wait(timeout=2))
        connection = holder["connection"]

        async def send_from_caller_loop() -> None:
            await connection.send_frame({"kind": "request", "id": "frame-1", "type": "tool.invoke"})

        try:
            asyncio.run(send_from_caller_loop())
            self.assertTrue(websocket.sent.wait(timeout=2))
            self.assertEqual(json.loads(websocket.frames[-1])["id"], "frame-1")
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_dispatch_tool_invoke_receives_cross_loop_response(self) -> None:
        import asyncio

        class FakeWebSocket:
            def __init__(self) -> None:
                self.frames: list[str] = []
                self.sent = threading.Event()
                self.connection: gateway_protocol_service._LiveGatewayConnection | None = None

            async def send_text(self, frame: str) -> None:
                self.frames.append(str(frame))
                self.sent.set()
                payload = json.loads(frame)
                if payload.get("type") != "tool.invoke" or self.connection is None:
                    return

                async def respond() -> None:
                    await asyncio.sleep(0.01)
                    assert self.connection is not None
                    self.connection.resolve_response(
                        {
                            "kind": "response",
                            "id": payload.get("id"),
                            "ok": True,
                            "payload": {
                                "request_id": payload.get("id"),
                                "run_id": "run-1",
                                "capability_id": "shell.execute",
                                "result": {"stdout": "hello from hardware\n"},
                            },
                        }
                    )

                asyncio.create_task(respond())

        websocket = FakeWebSocket()
        ready = threading.Event()
        stop = threading.Event()
        holder: dict[str, gateway_protocol_service._LiveGatewayConnection] = {}

        async def owner_loop() -> None:
            connection = gateway_protocol_service._LiveGatewayConnection(
                websocket=websocket,
                gateway_id="gateway-1",
                session_id="session-1",
                scope={"tenant_id": "tenant-1", "workspace_id": "workspace-1"},
            )
            connection.start_writer()
            websocket.connection = connection
            holder["connection"] = connection
            ready.set()
            try:
                while not stop.is_set():
                    await asyncio.sleep(0.01)
            finally:
                connection.fail_pending("test complete")

        thread = threading.Thread(target=lambda: asyncio.run(owner_loop()), daemon=True)
        thread.start()
        self.assertTrue(ready.wait(timeout=2))
        connection = holder["connection"]

        async def dispatch_from_caller_loop() -> dict:
            with (
                patch(
                    "server_modules.gateway_protocol_service._fresh_live_connection",
                    new=AsyncMock(return_value=connection),
                ),
                patch(
                    "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                    return_value={
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "device_id": "device-1",
                    },
                ),
                patch("server_modules.gateway_protocol_service.assert_not_killed"),
                patch("server_modules.gateway_protocol_service._enforce_gateway_quota_check"),
                patch("server_modules.gateway_protocol_service._enforce_gateway_tool_execute_protocol_route"),
                patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_message_decision"),
                patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_request_frame"),
                patch("server_modules.gateway_protocol_service.gateway_state_repository.record_gateway_event"),
            ):
                return await gateway_protocol_service.dispatch_tool_invoke(
                    gateway_id="gateway-1",
                    capability_id="shell.execute",
                    arguments={"command": "echo hello from hardware"},
                    run_id="run-1",
                    trace_id="trace-1",
                    workspace_id="workspace-1",
                    timeout_seconds=2,
                    request_id="request-1",
                    runtime_access_mode="full_access",
                    empyralis_approved=True,
                    agent_scope="sage",
                )

        try:
            result = asyncio.run(dispatch_from_caller_loop())
            self.assertTrue(websocket.sent.wait(timeout=2))
            self.assertEqual(json.loads(websocket.frames[-1])["type"], "tool.invoke")
            self.assertEqual(result["result"]["stdout"], "hello from hardware\n")
        finally:
            stop.set()
            thread.join(timeout=2)

    def test_unregister_live_connection_fails_pending_requests(self) -> None:
        import asyncio

        class FakeWebSocket:
            async def send_text(self, frame: str) -> None:
                return None

        async def run_test() -> None:
            loop = asyncio.get_running_loop()
            connection = gateway_protocol_service._LiveGatewayConnection(
                websocket=FakeWebSocket(),
                gateway_id="gateway-1",
                session_id="session-1",
                scope={"tenant_id": "tenant-1", "workspace_id": "workspace-1"},
            )
            future: asyncio.Future[dict] = loop.create_future()
            connection._pending_requests["request-1"] = gateway_protocol_service._PendingGatewayRequest(
                message_type="tool.invoke",
                future=future,
                loop=loop,
            )
            gateway_protocol_service._register_live_connection(connection)
            try:
                gateway_protocol_service._unregister_live_connection(
                    gateway_id="gateway-1",
                    session_id="session-1",
                    reason="gateway stale",
                )
                with self.assertRaisesRegex(RuntimeError, "gateway stale"):
                    await asyncio.wait_for(future, timeout=1)
                self.assertFalse(connection._pending_requests)
                self.assertIsNone(gateway_protocol_service._get_live_connection("gateway-1"))
            finally:
                with gateway_protocol_service._LIVE_GATEWAY_CONNECTIONS_LOCK:
                    gateway_protocol_service._LIVE_GATEWAY_CONNECTIONS_BY_GATEWAY.pop("gateway-1", None)
                    gateway_protocol_service._LIVE_GATEWAY_CONNECTIONS_BY_SESSION.pop("session-1", None)

        asyncio.run(run_test())


    def test_live_connection_send_frame_same_loop_direct_write(self) -> None:
        """Verify send_frame takes the direct-write branch when caller_loop is owner_loop.

        This is the path exercised by the asyncio bridge fix: when a hardware
        tool call runs on the same event loop as the WebSocket connection,
        send_frame() must detect same-loop and write directly (no outbound
        queue, no call_soon_threadsafe crossing). A deadlock here would mean
        the fix (execute_single_direct_tool_call_async) is insufficient.
        """
        import asyncio

        class FakeWebSocket:
            def __init__(self) -> None:
                self.frames: list[str] = []
                self.send_count = 0

            async def send_text(self, frame: str) -> None:
                self.frames.append(str(frame))
                self.send_count += 1

            async def close(self, code: int = 1000, reason: str = "") -> None:
                pass

        async def run_test() -> None:
            websocket = FakeWebSocket()
            connection = gateway_protocol_service._LiveGatewayConnection(
                websocket=websocket,
                gateway_id="gateway-same-loop",
                session_id="session-same-loop",
                scope={"tenant_id": "tenant-1", "workspace_id": "workspace-1"},
            )
            connection.start_writer()

            # Confirm same-loop: owner loop is the running loop
            owner = connection._owner_loop
            caller = asyncio.get_running_loop()
            self.assertIs(owner, caller, "owner_loop must be the running loop for same-loop test")

            # --- send_frame on same loop writes directly ---
            # No outbound queue, no call_soon_threadsafe — the frame goes
            # straight to websocket.send_text() via _write_frame_direct.
            frame = {
                "kind": "request",
                "id": "direct-1",
                "type": "tool.invoke",
                "ts": "2026-01-01T00:00:00Z",
                "scope": {},
                "payload": {},
            }
            await connection.send_frame(frame)
            self.assertEqual(websocket.send_count, 1, "send_frame should write directly on same loop")
            self.assertEqual(len(websocket.frames), 1)
            parsed = json.loads(websocket.frames[-1])
            self.assertEqual(parsed["id"], "direct-1")

            # --- Multiple send_frame calls on same loop ---
            frame2 = {
                "kind": "request",
                "id": "direct-2",
                "type": "tool.invoke",
                "ts": "2026-01-01T00:00:01Z",
                "scope": {},
                "payload": {},
            }
            await connection.send_frame(frame2)
            self.assertEqual(websocket.send_count, 2, "second send_frame should also write directly")
            self.assertEqual(len(websocket.frames), 2)

            # --- send_request round-trip with resolve_response ---
            # send_request calls record_gateway_event which needs the real
            # state repository — mock it for the round-trip test.
            from unittest.mock import patch
            from server_modules import gateway_state_repository as _gsr

            with patch.object(_gsr, "record_gateway_event", return_value=None):
                request_task = asyncio.create_task(
                    connection.send_request(
                        message_type="tool.invoke",
                        payload={"capability_id": "shell.execute", "arguments": {"command": "echo hello"}},
                        timeout_seconds=5,
                        request_id="req-roundtrip-1",
                    )
                )

                # Let the event loop run so send_frame completes inside send_request
                await asyncio.sleep(0)

                # Frame should have been written directly
                self.assertEqual(websocket.send_count, 3, "send_request should write frame on same loop")
                sent_frame = json.loads(websocket.frames[-1])
                request_id = sent_frame["id"]
                self.assertTrue(
                    request_id.startswith("req-roundtrip-1") or request_id == "req-roundtrip-1"
                )

                # Resolve the response — the request task is awaiting it
                connection.resolve_response({
                    "kind": "response",
                    "id": request_id,
                    "ok": True,
                    "payload": {
                        "request_id": request_id,
                        "run_id": "run-1",
                        "capability_id": "shell.execute",
                        "result": {"stdout": "hello from same-loop\n"},
                    },
                })

                response = await asyncio.wait_for(request_task, timeout=2)
                self.assertTrue(response.get("ok"))
                self.assertEqual(
                    response.get("payload", {}).get("result", {}).get("stdout"),
                    "hello from same-loop\n",
                )
                self.assertEqual(len(connection._pending_requests), 0)

            # --- Cleanup ---
            connection.fail_pending("test complete")
            self.assertTrue(connection._closed)

            writer = connection._writer_task
            if writer is not None and not writer.done():
                writer.cancel()
                try:
                    await writer
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
