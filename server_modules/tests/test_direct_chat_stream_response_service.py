import asyncio
import contextlib
import threading
import types
import unittest
from unittest import mock

from fastapi.responses import JSONResponse, StreamingResponse

from server_modules import direct_chat_stream_state_service, direct_chat_stream_transport_service
from server_modules.direct_chat_stream_response_service import (
    DirectChatStreamResponseServices,
    build_direct_chat_stream_response,
)


class _Resolution:
    def __init__(self) -> None:
        # A real AgentTurnRequest carries .attachments/.workspace_id/
        # .session_id as attributes, not dict keys — build_agent_turn_
        # stream_response reads turn_request.attachments directly (its own
        # "Pre-resolve image descriptions" step). A plain {"kind": ...}
        # dict here was a pre-existing, unrelated stale fixture (confirmed
        # by running this file against main before this change: 4 of 5
        # tests already failed with AttributeError, independent of
        # anything in this durability/streaming fix) — this is the
        # "fixture that invents its own input" shape CLAUDE.md documents;
        # SimpleNamespace with the three attributes this module actually
        # reads is the minimal honest stand-in.
        self.turn_request = types.SimpleNamespace(
            workspace_id="default",
            session_id="thread-1",
            attachments=[],
        )
        self.workspace_id = "default"
        self.thread_id = "thread-1"
        self.client_request_id = "req-1"


def _real_session(*, key: str = "session-1", thread_id: str = "thread-1", request_id: str = "req-1") -> dict:
    """A session dict shaped exactly like the real, process-local sessions
    `chat_stream_runtime_service` hands out — a live threading.Condition and
    a real events list — rather than the bare {"producer_started": False}
    stand-in most of this file's other fakes use. The peek helper in
    direct_chat_stream_response_service.py drives the condition directly
    (the same way the real SSE reader does), so any test asserting on its
    behavior needs the real shape or it is asserting nothing: see this
    codebase's own "a fixture that invents its own input cannot notice the
    real input is shaped differently" rule."""
    return direct_chat_stream_state_service.default_chat_stream_session(
        key,
        thread_id=thread_id,
        request_id=request_id,
        workspace_id="default",
        now_ts=None,
        now_iso=lambda: "2026-01-01T00:00:00Z",
    )


def _real_start_chat_stream_producer(session: dict, producer) -> None:
    """The real transport AND state functions, wired together with a
    no-op persist hook — exercises the actual threading.Condition contract
    (`producer_started` guard, `condition.notify_all()`, events actually
    landing in session["events"], `completed` actually getting set) the
    peek helper and the SSE reader both depend on, instead of a lambda
    that merely records it was called without mutating the session at
    all."""
    direct_chat_stream_transport_service.start_chat_stream_producer(
        session,
        producer_fn=producer,
        append_event=lambda sess, event_name, payload: direct_chat_stream_state_service.append_chat_stream_event(
            sess,
            event_name=event_name,
            payload=payload,
            buffer_limit=200,
            persist_session_state=lambda *a, **k: None,
        ),
        complete_session=lambda sess: direct_chat_stream_state_service.complete_chat_stream_session(
            sess,
            persist_session_state=lambda *a, **k: None,
        ),
        capture_exception=lambda exc: None,
    )


class DirectChatStreamResponseServiceTests(unittest.TestCase):
    def setUp(self):
        self._rust_patch = mock.patch(
            "server_modules.direct_chat_stream_response_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "operation": "stream_chat",
                "next_action": "start_chat_stream",
            },
        )
        self.mock_rust = self._rust_patch.start()

    def tearDown(self):
        self._rust_patch.stop()

    def _services(self, **overrides):
        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter(()),
            }

        base = DirectChatStreamResponseServices(
            resolve_direct_chat_turn_request=lambda **kwargs: _Resolution(),
            chat_stream_request_signature=lambda **kwargs: "sig",
            execute_agent_turn_request=_execute_agent_turn_request,
            build_turn_execution_services=lambda **kwargs: {"services": kwargs},
            run_execution_services=lambda: "run-services",
            direct_chat_execution_services=lambda: "chat-services",
            get_chat_stream_state=lambda db_path, key: None,
            chat_stream_state_db_path=lambda: "/tmp/state.db",
            get_or_create_chat_stream_session=lambda *args, **kwargs: {"producer_started": False},
            extract_direct_chat_error_response=direct_chat_stream_transport_service.extract_direct_chat_error_response,
            start_chat_stream_producer=lambda session, producer: None,
            iter_chat_stream_events=lambda session, last_event_id: iter([b"data: ok\n\n"]),
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_returns_chat_unavailable_when_producer_finishes_immediately(self):
        session = _real_session()
        services = self._services(
            get_or_create_chat_stream_session=lambda *args, **kwargs: session,
            start_chat_stream_producer=_real_start_chat_stream_producer,
        )

        payload = asyncio.run(
            build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id=None,
                services=services,
            )
        )

        self.assertIsInstance(payload, JSONResponse)
        self.assertEqual(payload.status_code, 500)

    def test_returns_immediate_provider_error_response(self):
        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter([{"type": "final", "payload": {"error": "no_provider"}}]),
            }

        session = _real_session()
        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_or_create_chat_stream_session=lambda *args, **kwargs: session,
            start_chat_stream_producer=_real_start_chat_stream_producer,
        )

        payload = asyncio.run(
            build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id=None,
                services=services,
            )
        )

        self.assertIsInstance(payload, JSONResponse)
        self.assertEqual(payload.status_code, 409)

    def test_preflights_new_session_even_when_persisted_state_exists(self):
        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter([{"type": "final", "payload": {"error": "no_provider"}}]),
            }

        session = _real_session()
        started = threading.Event()

        def _start(session, producer):
            started.set()
            _real_start_chat_stream_producer(session, producer)

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_chat_stream_state=lambda db_path, key: {"status": "retryable"},
            get_or_create_chat_stream_session=lambda *args, **kwargs: session,
            start_chat_stream_producer=_start,
        )

        payload = asyncio.run(
            build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id=None,
                services=services,
            )
        )

        self.assertIsInstance(payload, JSONResponse)
        self.assertEqual(payload.status_code, 409)
        # Durability fix (see the call site's own comment in
        # direct_chat_stream_response_service.py): the producer's durable
        # thread is now started UNCONDITIONALLY, before this function ever
        # peeks at what it produced — a stale/retryable persisted DB state
        # no longer changes that. This is the deliberate flip side of this
        # test's old assertion (`assertNotIn("called", started)`), which
        # encoded the exact "don't start durably until we've decided the
        # turn is worth it" behavior this fix replaces, on purpose.
        self.assertTrue(started.is_set())

    def test_returns_streaming_response_for_live_stream(self):
        started = {}
        session = {"producer_started": False}

        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter([{"type": "chunk", "delta": "Hello"}]),
            }

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_or_create_chat_stream_session=lambda *args, **kwargs: session,
            start_chat_stream_producer=lambda session, producer: started.setdefault("called", True),
        )

        payload = asyncio.run(
            build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id="0",
                services=services,
            )
        )

        self.assertIsInstance(payload, StreamingResponse)
        self.assertTrue(started["called"])
        self.assertEqual(session["metadata"]["assistant_turn"]["workspace_id"], "default")
        self.assertEqual(session["metadata"]["assistant_turn"]["thread_id"], "thread-1")
        self.assertEqual(session["metadata"]["assistant_turn"]["request_id"], "req-1")

    def test_blocks_when_rust_stream_chat_next_action_is_unexpected(self):
        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": lambda: iter([{"type": "chunk", "delta": "Hello"}]),
            }

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_or_create_chat_stream_session=lambda *args, **kwargs: {"producer_started": False},
        )
        self.mock_rust.return_value = {
            "ok": True,
            "decision": "allow",
            "operation": "stream_chat",
            "next_action": "get_run",
        }

        with self.assertRaisesRegex(Exception, "unexpected next_action"):
            asyncio.run(
                build_direct_chat_stream_response(
                    current_user={"user_id": "user-1"},
                    body={"message": "hello"},
                    last_event_id="0",
                    services=services,
                )
            )

    def test_client_disconnect_before_slow_first_event_still_starts_durable_producer(self):
        """Reproduction of the founder's live report: a long turn on an
        engine that streams nothing until it finishes (claude_agent_sdk,
        the production default — see claude_agent_sdk_bridge.py's own
        companion fix) means the turn's first event can be arbitrarily far
        away. Before the durability fix, start_chat_stream_producer was
        only ever reached AFTER awaiting that first event, so a client
        disconnect (tab close, navigation, the frontend's own 90s silence
        watchdog — all of which cancel this exact coroutine) landing
        DURING that wait meant the turn's real work — still running to
        completion in its own orphaned thread, since nothing stops a plain
        threading.Thread — was silently thrown away: never persisted,
        never shown. This test cancels the awaiting task almost
        immediately, well before the producer's first event would ever
        arrive, and asserts the durable thread still gets started."""
        release_producer = threading.Event()
        started = threading.Event()

        def _slow_producer():
            # Models the claude_agent_sdk engine before its own live-sink
            # fix: nothing yielded until the whole turn is done.
            release_producer.wait(timeout=5)
            yield {"type": "final", "payload": {"reply": "done"}}

        session = _real_session()

        def _start(session, producer):
            started.set()
            _real_start_chat_stream_producer(session, producer)

        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": _slow_producer,
            }

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_or_create_chat_stream_session=lambda *args, **kwargs: session,
            start_chat_stream_producer=_start,
        )

        async def _run_and_disconnect():
            task = asyncio.ensure_future(
                build_direct_chat_stream_response(
                    current_user={"user_id": "user-1"},
                    body={"message": "hello"},
                    last_event_id=None,
                    services=services,
                )
            )
            # Let the coroutine start running, then simulate the client
            # going away — exactly what Starlette does to this coroutine
            # on a real ASGI disconnect.
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        try:
            asyncio.run(_run_and_disconnect())
            self.assertTrue(
                started.wait(timeout=2),
                "start_chat_stream_producer was never reached before the "
                "request was cancelled — the turn's work would have been "
                "silently discarded.",
            )
        finally:
            release_producer.set()

    def test_reconnect_against_the_same_session_never_re_executes_the_producer(self):
        """CLAUDE.md's own rule for this seam: metering and debiting are
        fused into one function precisely so "spend without charging" is
        inexpressible, and a durable turn plus a resume must never become a
        double debit. This durability fix makes reconnect-after-disconnect
        a normal, expected path (the whole point is that the turn survives
        a disconnect), so it has to be proven, not assumed, that a second
        request against the SAME session_key — a reconnect — never causes
        the underlying turn (and whatever metering/debiting lives inside
        it, standing in for it here as `producer_call_count`) to run a
        second time. The real guarantee is start_chat_stream_producer's own
        producer_started flag, guarded by session["condition"]'s lock —
        this test drives the REAL transport function, not a lambda that
        assumes the guard works, to prove the actual mechanism holds under
        two overlapping calls."""
        producer_call_count = {"n": 0}
        release_producer = threading.Event()

        def _producer():
            producer_call_count["n"] += 1
            release_producer.wait(timeout=5)
            yield {"type": "final", "payload": {"reply": "done"}}

        session = _real_session()

        async def _execute_agent_turn_request(**kwargs):
            return {
                "workspace_id": "default",
                "session_key": "session-1",
                "thread_id": "thread-1",
                "client_request_id": "req-1",
                "producer": _producer,
            }

        services = self._services(
            execute_agent_turn_request=_execute_agent_turn_request,
            get_or_create_chat_stream_session=lambda *args, **kwargs: session,
            start_chat_stream_producer=_real_start_chat_stream_producer,
        )

        async def _initial_connect():
            return await build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id=None,
                services=services,
            )

        async def _reconnect():
            # A real reconnect: same session_key resolves to the SAME
            # session object (get_or_create_chat_stream_session's own
            # contract), carrying last-event-id from wherever the first
            # connection left off.
            return await build_direct_chat_stream_response(
                current_user={"user_id": "user-1"},
                body={"message": "hello"},
                last_event_id="0",
                services=services,
            )

        async def _drive():
            return await asyncio.gather(_initial_connect(), _reconnect())

        try:
            first, second = asyncio.run(_drive())
        finally:
            release_producer.set()

        self.assertIsInstance(first, StreamingResponse)
        self.assertIsInstance(second, StreamingResponse)
        self.assertEqual(
            producer_call_count["n"],
            1,
            "the producer (and whatever metering/debit call lives inside "
            "the real turn it stands in for) ran more than once for a "
            "single turn across a reconnect — this is a double-charge.",
        )


if __name__ == "__main__":
    unittest.main()
