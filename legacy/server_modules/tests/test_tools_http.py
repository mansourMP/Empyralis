import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server_modules import egress_policy, tools_http


ROOT_DIR = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT_DIR / "server_modules" / "direct_chat_runtime_exports.py"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
spec = importlib.util.spec_from_file_location("operator_chat_http_tools_under_test", MODULE_PATH)
operator_chat = importlib.util.module_from_spec(spec)
sys.modules["operator_chat_http_tools_under_test"] = operator_chat
assert spec and spec.loader
spec.loader.exec_module(operator_chat)


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, headers: dict | None = None, body: bytes = b"", url: str = "") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.url = url

    async def aread(self) -> bytes:
        return self._body


class _FakeAsyncClient:
    response = _FakeResponse()
    last_request = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def request(self, **kwargs):
        type(self).last_request = kwargs
        return type(self).response


class HttpToolTests(unittest.TestCase):
    def test_get_request_no_approval(self):
        approval = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "http_request",
                    "arguments": json.dumps({"method": "GET", "url": "https://example.com/data"}),
                }
            ],
            tool_capabilities=[],
        )

        self.assertIsNone(approval)

        _FakeAsyncClient.response = _FakeResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"ok": true}',
        )
        with patch("server_modules.tools_http.httpx.AsyncClient", _FakeAsyncClient):
            result = asyncio.run(
                tools_http.http_request(
                    method="GET",
                    url="https://example.com/data",
                )
            )

        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["body"], {"ok": True})

    def test_post_external_requires_approval(self):
        payload = operator_chat._build_direct_tool_approval_response(
            tool_calls=[
                {
                    "name": "http_request",
                    "arguments": json.dumps(
                        {
                            "method": "POST",
                            "url": "https://api.example.com/items",
                            "body": {"name": "widget"},
                        }
                    ),
                }
            ],
            tool_capabilities=[],
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["actions"][0]["connector"], "http")
        self.assertEqual(payload["actions"][0]["action"], "request")

    def test_response_truncated_at_100kb(self):
        _FakeAsyncClient.response = _FakeResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            body=b"a" * (tools_http.MAX_RESPONSE_BYTES + 64),
        )
        with patch("server_modules.tools_http.httpx.AsyncClient", _FakeAsyncClient):
            result = asyncio.run(
                tools_http.http_request(
                    method="GET",
                    url="https://example.com/large",
                )
            )

        self.assertTrue(result["truncated"])
        self.assertIn("[truncated:", result["body"])

    def test_http_request_denies_unapproved_host_under_active_egress_policy(self):
        async def _run() -> None:
            append_mock = AsyncMock(return_value=None)
            with patch("server_modules.egress_policy.control_plane_repository.append_agent_egress_event", new=append_mock):
                with egress_policy.activate_egress_policy(
                    egress_policy.build_egress_policy(
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        runtime_mode="hosted_secure",
                        runtime_scope={
                            "network_policy": {
                                "allow_outbound": True,
                                "allowed_hosts": [],
                                "allowed_providers": [],
                                "allow_private_hosts": False,
                            }
                        },
                        allowed_action_classes=["read"],
                    )
                ):
                    with self.assertRaises(egress_policy.EgressPolicyDeniedError) as raised:
                        await tools_http.http_request(method="GET", url="https://example.com/data")
            self.assertEqual(raised.exception.code, "host_not_allowed")
            await asyncio.sleep(0)
            self.assertEqual(append_mock.await_count, 1)
            self.assertEqual(append_mock.await_args.kwargs["status"], "denied")

        asyncio.run(_run())

    def test_http_request_rechecks_final_redirect_url_for_private_hosts(self):
        _FakeAsyncClient.response = _FakeResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            body=b"private",
            url="http://127.0.0.1/private",
        )
        with patch("server_modules.tools_http.httpx.AsyncClient", _FakeAsyncClient):
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(tools_http.http_request(method="GET", url="https://example.com/redirect"))

        self.assertIn("Outbound HTTP URL is not allowed", str(raised.exception))

    def test_http_request_allows_allowlisted_provider_under_active_egress_policy(self):
        async def _run() -> None:
            append_mock = AsyncMock(return_value=None)
            _FakeAsyncClient.response = _FakeResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=b'{"ok": true}',
            )
            with patch("server_modules.egress_policy.control_plane_repository.append_agent_egress_event", new=append_mock):
                with patch("server_modules.tools_http.httpx.AsyncClient", _FakeAsyncClient):
                    with egress_policy.activate_egress_policy(
                        egress_policy.build_egress_policy(
                            tenant_id="tenant-1",
                            workspace_id="workspace-1",
                            runtime_mode="hosted_secure",
                            runtime_scope={
                                "network_policy": {
                                    "allow_outbound": True,
                                    "allowed_hosts": [],
                                    "allowed_providers": ["openai"],
                                    "allow_private_hosts": False,
                                }
                            },
                            allowed_action_classes=["read"],
                        )
                    ):
                        result = await tools_http.http_request(method="GET", url="https://api.openai.com/v1/models")
            self.assertEqual(result["status_code"], 200)
            await asyncio.sleep(0)
            self.assertEqual(append_mock.await_count, 1)
            self.assertEqual(append_mock.await_args.kwargs["status"], "allowed")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
