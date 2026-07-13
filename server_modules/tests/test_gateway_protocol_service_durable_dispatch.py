import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server_modules import gateway_protocol_service


class DispatchToolInvokeDurableTests(unittest.TestCase):
    def tearDown(self) -> None:
        # These durability stores are module-global; a test that fails mid-way
        # could otherwise leak a pending invoke / waiter into the next test.
        with gateway_protocol_service._PENDING_GATEWAY_INVOKES_LOCK:
            gateway_protocol_service._PENDING_GATEWAY_INVOKES.clear()
        with gateway_protocol_service._DURABLE_DISPATCH_WAITERS_LOCK:
            gateway_protocol_service._DURABLE_DISPATCH_WAITERS.clear()


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

                # Delivery is driven only by the flush (which the WS handler
                # runs on connect/heartbeat) — here we invoke it directly for
                # connection_a to simulate that heartbeat-flush.
                for _ in range(100):
                    if gateway_protocol_service._snapshot_pending_invokes("gw-1"):
                        break
                    await asyncio.sleep(0.02)
                await gateway_protocol_service._flush_pending_invokes("gw-1", connection_a)
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

    def test_enqueues_when_offline_then_the_connect_flush_delivers(self) -> None:
        """The core new guarantee: when NO connection is live at dispatch time,
        the invoke is ENQUEUED (not dropped, not dependent on the dispatcher
        polling for a socket) and delivered by the connect/heartbeat flush the
        moment the gateway reappears — then the response resolves the original
        caller. This is the durable-INBOUND mirror of the gateway replaying its
        own outbox on reconnect."""
        connection_b_sent: list[dict] = []

        async def run_test() -> None:
            with (
                patch("server_modules.gateway_protocol_service.assert_not_killed", return_value=None),
                # No live connection exists when the dispatch starts.
                patch("server_modules.gateway_protocol_service._get_live_connection", return_value=None),
                patch(
                    "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                    return_value={"gateway_id": "gw-1", "workspace_id": "ws-1", "tenant_id": "tenant-1", "device_id": "device-1"},
                ),
                patch("server_modules.gateway_protocol_service._enforce_gateway_quota_check", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_tool_execute_protocol_route", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_message_decision", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_request_frame", return_value={}),
                patch("server_modules.gateway_protocol_service.gateway_state_repository.record_gateway_event", return_value=None),
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

                # The dispatch should reach the "enqueued, waiting" state
                # without any socket ever being live.
                for _ in range(200):
                    if gateway_protocol_service._snapshot_pending_invokes("gw-1"):
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(
                    gateway_protocol_service._snapshot_pending_invokes("gw-1"),
                    "invoke should be enqueued while the gateway is offline",
                )

                # The gateway reconnects: a fresh connection appears and the
                # flush hook delivers the enqueued invoke onto it.
                connection_b = SimpleNamespace(
                    session_id="sess-b",
                    scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
                    send_frame=AsyncMock(side_effect=lambda frame: connection_b_sent.append(frame)),
                )
                await gateway_protocol_service._flush_pending_invokes("gw-1", connection_b)
                self.assertEqual(len(connection_b_sent), 1, "flush should deliver the enqueued invoke once")
                self.assertEqual(connection_b_sent[0]["id"], "durable-req-2")
                self.assertFalse(
                    gateway_protocol_service._snapshot_pending_invokes("gw-1"),
                    "invoke should be dequeued once delivered",
                )

                # The result comes back on that connection and resolves the
                # original caller.
                real_conn = gateway_protocol_service._LiveGatewayConnection(
                    websocket=object(),
                    gateway_id="gw-1",
                    session_id="sess-b",
                    scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
                )
                real_conn.resolve_response({
                    "kind": "response",
                    "id": "durable-req-2",
                    "ok": True,
                    "payload": {"result": {"text": "eventually delivered"}},
                })

                result = await asyncio.wait_for(dispatch_task, timeout=5)
                self.assertEqual(result, {"result": {"text": "eventually delivered"}})

        asyncio.run(run_test())

    def test_repeated_flushes_deliver_exactly_once(self) -> None:
        """Two flushes on the same connection (e.g. back-to-back heartbeats)
        must never both send the same invoke — the claim/delivered guard makes
        delivery exactly-once."""
        connection = SimpleNamespace(
            session_id="sess-a",
            scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
            send_frame=AsyncMock(return_value=None),
        )

        async def run_test() -> None:
            with self._patches(connection):
                dispatch_task = asyncio.ensure_future(
                    gateway_protocol_service.dispatch_tool_invoke_durable(
                        gateway_id="gw-1",
                        capability_id="llm.generate",
                        arguments={"prompt": "hello"},
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        deadline_seconds=10,
                        request_id="durable-req-4",
                    )
                )
                for _ in range(100):
                    if gateway_protocol_service._snapshot_pending_invokes("gw-1"):
                        break
                    await asyncio.sleep(0.02)
                # First heartbeat-flush delivers; a second must not resend.
                await gateway_protocol_service._flush_pending_invokes("gw-1", connection)
                await gateway_protocol_service._flush_pending_invokes("gw-1", connection)
                self.assertEqual(connection.send_frame.await_count, 1, "must deliver exactly once")

                real_conn = gateway_protocol_service._LiveGatewayConnection(
                    websocket=object(),
                    gateway_id="gw-1",
                    session_id="sess-a",
                    scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
                )
                real_conn.resolve_response({
                    "kind": "response",
                    "id": "durable-req-4",
                    "ok": True,
                    "payload": {"result": {"text": "once"}},
                })
                result = await asyncio.wait_for(dispatch_task, timeout=5)
                self.assertEqual(result, {"result": {"text": "once"}})

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
                dispatch_task = asyncio.ensure_future(
                    gateway_protocol_service.dispatch_tool_invoke_durable(
                        gateway_id="gw-1",
                        capability_id="llm.generate",
                        arguments={"prompt": "hello"},
                        run_id="run-1",
                        trace_id="trace-1",
                        workspace_id="ws-1",
                        deadline_seconds=1,
                        request_id="durable-req-3",
                    )
                )
                # Deliver via the flush, but no response ever comes back.
                for _ in range(100):
                    if gateway_protocol_service._snapshot_pending_invokes("gw-1"):
                        break
                    await asyncio.sleep(0.02)
                await gateway_protocol_service._flush_pending_invokes("gw-1", connection_a)
                with self.assertRaises(RuntimeError) as raised:
                    await dispatch_task
            self.assertIn("no response arrived", str(raised.exception))
            self.assertEqual(connection_a.send_frame.await_count, 1, "must not resend once delivered")

        asyncio.run(run_test())


    def test_flush_preempts_a_claim_held_by_a_superseded_session(self) -> None:
        """Regression for the 2026-07-13 self-inflicted bug: a fast-path send
        hung against a dying socket held the delivery claim, so the fresh
        reconnect's flush skipped the invoke and the turn failed at the
        deadline. A flush on a DIFFERENT (newer) session must PREEMPT such a
        stuck claim and deliver — and once actually delivered, nobody resends."""
        connection_b_sent: list[dict] = []

        async def run_test() -> None:
            pending = gateway_protocol_service._PendingInvoke(
                request_id="durable-req-5",
                gateway_id="gw-1",
                capability_id="llm.generate",
                payload={
                    "capability_id": "llm.generate", "arguments": {},
                    "run_id": "run-1", "trace_id": "trace-1", "workspace_id": "ws-1",
                },
                workspace_id="ws-1",
                run_id="run-1",
                trace_id="trace-1",
                enforce_request_id="trace-1",
            )
            gateway_protocol_service._enqueue_pending_invoke(pending)

            # The old connection's fast-path claimed the invoke and then hung
            # mid-send: claim held, never released, never delivered.
            self.assertTrue(gateway_protocol_service._claim_pending_invoke(pending, "sess-old"))
            # A redundant attempt by that SAME stuck session must not re-send.
            self.assertFalse(gateway_protocol_service._claim_pending_invoke(pending, "sess-old"))

            connection_b = SimpleNamespace(
                session_id="sess-new",
                scope={"workspace_id": "ws-1", "tenant_id": "tenant-1"},
                send_frame=AsyncMock(side_effect=lambda frame: connection_b_sent.append(frame)),
            )
            with (
                patch(
                    "server_modules.gateway_protocol_service.gateway_state_repository.get_gateway_registration",
                    return_value={"gateway_id": "gw-1", "workspace_id": "ws-1", "tenant_id": "tenant-1", "device_id": "device-1"},
                ),
                patch("server_modules.gateway_protocol_service._enforce_gateway_quota_check", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_tool_execute_protocol_route", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_message_decision", return_value=None),
                patch("server_modules.gateway_protocol_service._enforce_gateway_protocol_request_frame", return_value={}),
                patch("server_modules.gateway_protocol_service.gateway_state_repository.record_gateway_event", return_value=None),
            ):
                # The fresh reconnect's flush must preempt the stuck claim.
                await gateway_protocol_service._flush_pending_invokes("gw-1", connection_b)

            self.assertEqual(len(connection_b_sent), 1, "fresh session must preempt the stuck claim and deliver")
            self.assertEqual(connection_b_sent[0]["id"], "durable-req-5")
            self.assertTrue(pending.delivered)
            # Now that it is truly delivered, no session — not even a brand new
            # one — may resend (the gateway does not dedup inbound invokes).
            self.assertFalse(gateway_protocol_service._claim_pending_invoke(pending, "sess-newer"))

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
