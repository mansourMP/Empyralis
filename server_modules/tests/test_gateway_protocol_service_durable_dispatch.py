import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server_modules import gateway_protocol_service


class DispatchToolInvokeDurableTests(unittest.TestCase):
    # The enforcement chain (quota / protocol route / message decision /
    # request frame) is real production logic, each with its own
    # rust-kernel-decision payload shape and expected next_action — already
    # covered on its own terms by test_gateway_protocol_service_tool_invoke_
    # rust_gate.py. What THIS file tests is the durability mechanism sitting
    # on top of it, so the enforcement calls themselves are no-op'd directly
    # rather than re-deriving every rust-kernel payload shape here too.
    def _patches(self, connection) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None))
        stack.enter_context(patch("server_modules.gateway_protocol_service._get_live_connection", return_value=connection))
        stack.enter_context(patch(
            "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
            return_value={"gateway_id": "gw-1", "workspace_id": "ws-1", "tenant_id": "tenant-1", "device_id": "device-1"},
        ))
        stack.enter_context(patch("server_modules.gateway_protocol_service._enforce_gateway_quota_check", return_value=None))
        stack.enter_context(patch("server_modules.gateway_protocol_service._enforce_gateway_tool_execute_protocol_route", return_value=None))
        stack.enter_context(patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_message_decision", return_value=None))
        stack.enter_context(patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_request_frame", return_value={}))
        stack.enter_context(patch("server_modules.gateway_protocol_service.gateway_state_repository.record_gateway_event", return_value=None))
        return stack

    def test_resolves_via_a_later_connection_than_the_one_that_sent_the_request(self) -> None:
        """The core durability guarantee: a response arriving on a DIFFERENT
        connection than the one that sent the request still resolves the
        original caller — simulating the Gateway finishing codex exec after
        the original WS connection died and a new one took its place."""
        sent_frames: list[dict] = []
        connection_a = SimpleNamespace(
            session_id="sess-a",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_frame=AsyncMock(side_effect=lambda frame: sent_frames.append(frame)),
        )

        async def run_test() -> None:
            with self._patches(connection_a):
                dispatch_task = asyncio.ensure_future(
                    gateway_protocol_service.dispatch_tool_invoke_durable(
                        gateway_id="gw-1",
                        capability_id="llm.generate",
                        arguments={"prompt": "hello"},
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        deadline_seconds=10,
                        request_id="durable-req-1",
                    )
                )

                for _ in range(100):
                    if sent_frames:
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(len(sent_frames), 1, "the request should have been written via connection_a")
                self.assertEqual(sent_frames[0]["id"], "durable-req-1")

                # connection_a is gone now, with no response ever delivered on
                # it — a brand new connection_b (a REAL _LiveGatewayConnection,
                # not a stub, so resolve_response's actual fallback path runs)
                # receives the late result instead.
                connection_b = gateway_protocol_service._LiveGatewayConnection(
                    websocket=object(),
                    gateway_id="gw-1",
                    session_id="sess-b",
                    scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
                )
                resolved_type = connection_b.resolve_response({
                    "kind": "response",
                    "id": "durable-req-1",
                    "ok": True,
                    "payload": {"result": {"text": "hello back"}},
                })
                self.assertEqual(resolved_type, "tool.invoke")

                result = await asyncio.wait_for(dispatch_task, timeout=5)
                self.assertEqual(result, {"result": {"text": "hello back"}})

        asyncio.run(run_test())

    def test_retries_the_send_when_no_connection_exists_yet_then_delivers(self) -> None:
        """The other half: nothing was ever written (no live connection at
        all when the dispatch started) — safe to retry the send itself,
        unlike the delivered-but-no-response case above."""
        attempts = {"get_connection_calls": 0}
        connection_a = SimpleNamespace(
            session_id="sess-a",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_frame=AsyncMock(return_value=None),
        )

        def flaky_get_live_connection(_gateway_id):
            attempts["get_connection_calls"] += 1
            if attempts["get_connection_calls"] < 3:
                return None
            return connection_a

        async def run_test() -> None:
            with (
                patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
                patch(
                    "server_modules.gateway_protocol_service._get_live_connection",
                    side_effect=flaky_get_live_connection,
                ),
                patch(
                    "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                    return_value={"gateway_id": "gw-1", "workspace_id": "ws-1", "tenant_id": "tenant-1", "device_id": "device-1"},
                ),
                patch("server_modules.gateway_protocol_service._enforce_gateway_quota_check", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_tool_execute_protocol_route", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_message_decision", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_request_frame", return_value={}),
                patch("server_modules.gateway_protocol_service.gateway_state_repository.record_gateway_event", return_value=None),
                patch(
                    "server_modules.gateway_protocol_service._DURABLE_DISPATCH_RETRY_SLEEP_SECONDS",
                    0.01,
                ),
            ):
                dispatch_task = asyncio.ensure_future(
                    gateway_protocol_service.dispatch_tool_invoke_durable(
                        gateway_id="gw-1",
                        capability_id="llm.generate",
                        arguments={"prompt": "hello"},
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        deadline_seconds=10,
                        request_id="durable-req-2",
                    )
                )

                for _ in range(200):
                    if connection_a.send_frame.await_count:
                        break
                    await asyncio.sleep(0.02)
                self.assertEqual(connection_a.send_frame.await_count, 1)
                self.assertGreaterEqual(attempts["get_connection_calls"], 3)

                connection_b = gateway_protocol_service._LiveGatewayConnection(
                    websocket=object(),
                    gateway_id="gw-1",
                    session_id="sess-b",
                    scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
                )
                connection_b.resolve_response({
                    "kind": "response",
                    "id": "durable-req-2",
                    "ok": True,
                    "payload": {"result": {"text": "eventually delivered"}},
                })

                result = await asyncio.wait_for(dispatch_task, timeout=5)
                self.assertEqual(result, {"result": {"text": "eventually delivered"}})

        asyncio.run(run_test())

    def test_gives_up_honestly_after_the_outer_deadline_with_no_resend(self) -> None:
        """Delivered, but no response ever arrives — must fail after the
        deadline, and must NOT have tried to resend (that would risk
        double-executing work that might still be running)."""
        connection_a = SimpleNamespace(
            session_id="sess-a",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_frame=AsyncMock(return_value=None),
        )

        async def run_test() -> None:
            with self._patches(connection_a):
                with self.assertRaises(RuntimeError) as raised:
                    await gateway_protocol_service.dispatch_tool_invoke_durable(
                        gateway_id="gw-1",
                        capability_id="llm.generate",
                        arguments={"prompt": "hello"},
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        deadline_seconds=1,
                        request_id="durable-req-3",
                    )
            self.assertIn("no response arrived", str(raised.exception))
            self.assertEqual(connection_a.send_frame.await_count, 1, "must not resend once delivered")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
