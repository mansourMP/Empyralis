import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import runtime_runtime_api


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register("DELETE", path, **kwargs)


class _FakeRequest:
    def __init__(self, *, content_type: str, body: bytes) -> None:
        self.headers = {"content-type": content_type}
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _FakeEventSourceResponse:
    def __init__(self, iterator, ping):
        self.iterator = iterator
        self.ping = ping


class RuntimeRuntimeApiMediaControlRustGateTests(unittest.TestCase):
    @staticmethod
    def _rust_allow_decision(operation: str = "runtime_api_test"):
        next_action_map = {
            "stt_request": "transcribe_audio",
            "tts_request": "synthesize_tts",
            "runtime_control_stream": "stream_runtime_control",
        }
        return {
            "ok": True,
            "decision": "allow",
            "reason": "runtime_session_api_policy_satisfied",
            "operation": operation,
            "next_action": next_action_map.get(operation, "review_runtime_api_request"),
            "audit_visibility": "standard",
            "approval_required": False,
            "cacheable": False,
        }

    def test_stt_route_calls_rust_before_provider_execution(self) -> None:
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/stt")]
        content_type = sorted(runtime_runtime_api.SUPPORTED_STT_CONTENT_TYPES)[0]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value=self._rust_allow_decision("stt_request"),
        ) as rust_mock, patch.object(
            runtime_runtime_api,
            "_transcribe_audio_bytes",
            new=AsyncMock(return_value={"transcript": "hello", "confidence": 0.92}),
        ) as transcribe_mock:
            result = asyncio.run(handler(_FakeRequest(content_type=content_type, body=b"audio-bytes")))

        self.assertEqual(result["transcript"], "hello")
        self.assertEqual(rust_mock.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "stt_request")
        self.assertEqual(rust_mock.call_args.args[1]["content_type"], content_type)
        self.assertEqual(rust_mock.call_args.args[1]["audio_bytes"], len(b"audio-bytes"))
        transcribe_mock.assert_awaited_once()

    def test_stt_route_fails_closed_before_provider_when_rust_returns_wrong_action(self) -> None:
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/stt")]
        content_type = sorted(runtime_runtime_api.SUPPORTED_STT_CONTENT_TYPES)[0]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                **self._rust_allow_decision("stt_request"),
                "next_action": "synthesize_tts",
            },
        ), patch.object(
            runtime_runtime_api,
            "_transcribe_audio_bytes",
            new=AsyncMock(return_value={"transcript": "should-not-run"}),
        ) as transcribe_mock:
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(handler(_FakeRequest(content_type=content_type, body=b"audio-bytes")))

        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(exc.exception.detail, "unexpected_next_action:synthesize_tts")
        transcribe_mock.assert_not_awaited()

    def test_stt_route_fails_closed_before_provider_when_rust_blocks(self) -> None:
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/stt")]
        content_type = sorted(runtime_runtime_api.SUPPORTED_STT_CONTENT_TYPES)[0]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=runtime_runtime_api.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "stt_audio_exceeds_hard_limit",
                },
                command="runtime-session-api-decision",
            ),
        ), patch.object(
            runtime_runtime_api,
            "_transcribe_audio_bytes",
            new=AsyncMock(return_value={"transcript": "should-not-run"}),
        ) as transcribe_mock:
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(handler(_FakeRequest(content_type=content_type, body=b"audio-bytes")))

        self.assertEqual(exc.exception.status_code, 403)
        transcribe_mock.assert_not_awaited()

    def test_tts_route_calls_rust_before_provider_execution(self) -> None:
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("POST", "/tts")]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value=self._rust_allow_decision("tts_request"),
        ) as rust_mock, patch.object(
            runtime_runtime_api,
            "_synthesize_tts_chunks",
            new=AsyncMock(return_value=[b"audio"]),
        ) as synthesize_mock:
            response = asyncio.run(
                handler(runtime_runtime_api.RuntimeTtsPayload(text="hello", voice="alloy", speed=1.0))
            )

        self.assertEqual(response.media_type, "audio/mpeg")
        self.assertEqual(rust_mock.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "tts_request")
        self.assertEqual(rust_mock.call_args.args[1]["text_length"], len("hello"))
        synthesize_mock.assert_awaited_once()

    def test_runtime_control_stream_calls_rust_before_opening_stream(self) -> None:
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("GET", "/runtime/runtimes/{runtime_id}/control/stream")]

        async def _stream_marker():
            if False:
                yield {"event": "noop"}

        stream_marker = _stream_marker()
        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value=self._rust_allow_decision("runtime_control_stream"),
        ) as rust_mock, patch.object(
            runtime_runtime_api.local_queue,
            "_assert_runtime_session",
            return_value=None,
        ) as assert_session_mock, patch.object(
            runtime_runtime_api.local_queue,
            "aiter_runtime_control_stream",
            return_value=stream_marker,
        ) as stream_mock, patch.object(
            runtime_runtime_api,
            "EventSourceResponse",
            side_effect=lambda iterator, ping: _FakeEventSourceResponse(iterator, ping),
        ):
            response = asyncio.run(
                handler(
                    "worker-1",
                    session_token="sess",
                    instance_id="inst",
                    since_sequence=3,
                    heartbeat_seconds=4.0,
                    timeout_seconds=12.0,
                )
            )

        self.assertIs(response.iterator, stream_marker)
        self.assertEqual(rust_mock.call_args.args[0], "runtime-session-api-decision")
        self.assertEqual(rust_mock.call_args.args[1]["operation"], "runtime_control_stream")
        self.assertEqual(rust_mock.call_args.args[1]["runtime_id"], "worker-1")
        assert_session_mock.assert_called_once_with("worker-1", "sess", instance_id="inst")
        stream_mock.assert_called_once()

    def test_runtime_control_stream_fails_closed_on_wrong_rust_action(self) -> None:
        app = _FakeApp()
        runtime_runtime_api.register_runtime_routes(app)
        handler = app.routes[("GET", "/runtime/runtimes/{runtime_id}/control/stream")]

        with patch.object(
            runtime_runtime_api.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                **self._rust_allow_decision("runtime_control_stream"),
                "next_action": "register_runtime",
            },
        ), patch.object(
            runtime_runtime_api.local_queue,
            "_assert_runtime_session",
            return_value=None,
        ) as assert_session_mock, patch.object(
            runtime_runtime_api.local_queue,
            "aiter_runtime_control_stream",
        ) as stream_mock:
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(
                    handler(
                        "worker-1",
                        session_token="sess",
                        instance_id="inst",
                        since_sequence=3,
                        heartbeat_seconds=4.0,
                        timeout_seconds=12.0,
                    )
                )

        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(exc.exception.detail, "unexpected_next_action:register_runtime")
        assert_session_mock.assert_not_called()
        stream_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
