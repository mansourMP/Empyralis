from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.orion_terminal.clients.runtime import RuntimeClient
from scripts.orion_terminal.errors import CompatibilityError


class RuntimeClientTests(unittest.TestCase):
    def test_health_compatibility_gate(self) -> None:
        client = RuntimeClient(
            api_url="http://runtime",
            api_key="key",
            cli_version="2026.1.0",
            http_retries=0,
        )
        payload = {"ok": True, "runtime_api_version": "1.0.0", "runtime_api_min_cli_version": "2026.2.0"}
        with patch("scripts.orion_terminal.clients.runtime.http_json", return_value=payload):
            with self.assertRaises(CompatibilityError):
                client.get_health(enforce_compatibility=True)

    def test_retry_on_transient_network_error(self) -> None:
        calls = {"count": 0}

        def _fake_http_json(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("Network error: connection refused")
            return {"ok": True}

        client = RuntimeClient(
            api_url="http://runtime",
            api_key="key",
            cli_version="2026.2.26",
            http_retries=1,
            http_backoff_seconds=0.01,
            http_backoff_max_seconds=0.01,
        )
        with (
            patch("scripts.orion_terminal.clients.runtime.http_json", side_effect=_fake_http_json),
            patch("scripts.orion_terminal.clients.runtime.time.sleep", return_value=None),
        ):
            out = client.get_health(enforce_compatibility=False)
        self.assertEqual(out.get("ok"), True)
        self.assertEqual(calls["count"], 2)

    def test_contract_compatibility_gate(self) -> None:
        client = RuntimeClient(
            api_url="http://runtime",
            api_key="key",
            cli_version="2026.1.0",
            http_retries=0,
        )
        payload = {
            "ok": True,
            "contract": {
                "contract_schema_version": "2026.2.0",
                "runtime_api_version": "1.0.0",
                "runtime_api_min_cli_version": "2026.2.0",
            },
        }
        with patch("scripts.orion_terminal.clients.runtime.http_json", return_value=payload):
            with self.assertRaises(CompatibilityError):
                client.get_contract(enforce_compatibility=True)

    def test_contract_falls_back_to_health_when_missing(self) -> None:
        calls = {"count": 0}

        def _fake_http_json(_api_url, path, _api_key, **_kwargs):
            calls["count"] += 1
            if path == "/contract":
                raise RuntimeError("HTTP 404: not found")
            if path == "/health":
                return {"ok": True, "runtime_api_version": "1.0.0", "runtime_api_min_cli_version": "2026.2.0"}
            raise RuntimeError(f"unexpected path: {path}")

        client = RuntimeClient(
            api_url="http://runtime",
            api_key="key",
            cli_version="2026.2.26",
            http_retries=0,
        )
        with patch("scripts.orion_terminal.clients.runtime.http_json", side_effect=_fake_http_json):
            payload = client.get_contract(enforce_compatibility=True)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("source"), "health_fallback")
        self.assertEqual(calls["count"], 2)

    def test_mutating_call_sets_idempotency_header(self) -> None:
        captured = {}

        def _fake_http_json(*args, **kwargs):
            captured["method"] = kwargs.get("method")
            captured["extra_headers"] = kwargs.get("extra_headers") or {}
            return {"run_id": "r-1", "status": "running"}

        client = RuntimeClient(api_url="http://runtime", api_key="key", http_retries=0)
        with patch("scripts.orion_terminal.clients.runtime.http_json", side_effect=_fake_http_json):
            payload = client.start_run({"goal": "test"})
        self.assertEqual(payload.get("run_id"), "r-1")
        headers = captured.get("extra_headers") or {}
        self.assertIn("X-Empyralis-CLI-Version", headers)
        self.assertIn("X-Empyralis-CLI-Session", headers)
        self.assertIn("X-Idempotency-Key", headers)
        self.assertTrue(str(headers["X-Idempotency-Key"]).startswith("orion-cli-"))

    def test_create_setup_session_prefers_onboarding_endpoint(self) -> None:
        captured = {}

        def _fake_http_json(_api_url, path, _api_key, **kwargs):
            captured["path"] = path
            captured["method"] = kwargs.get("method")
            captured["payload"] = kwargs.get("payload")
            return {"session": {"id": "sess-1", "state": "init"}}

        client = RuntimeClient(api_url="http://runtime", api_key="key", http_retries=0)
        with patch("scripts.orion_terminal.clients.runtime.http_json", side_effect=_fake_http_json):
            session = client.create_setup_session(workspace_id="default", provider="openai", flow="quickstart")
        self.assertEqual(session.get("id"), "sess-1")
        self.assertEqual(captured.get("path"), "/onboarding/sessions")
        self.assertEqual(captured.get("method"), "POST")
        self.assertEqual(captured.get("payload"), {"workspace_id": "default", "provider": "openai", "flow": "quickstart"})

    def test_create_setup_session_falls_back_to_legacy_setup_endpoint(self) -> None:
        calls = []

        def _fake_http_json(_api_url, path, _api_key, **kwargs):
            calls.append(path)
            if path == "/onboarding/sessions":
                raise RuntimeError("HTTP 404: not found")
            if path == "/setup/sessions":
                return {"session": {"id": "sess-legacy", "state": "init"}}
            raise RuntimeError(f"unexpected path: {path}")

        client = RuntimeClient(api_url="http://runtime", api_key="key", http_retries=0)
        with patch("scripts.orion_terminal.clients.runtime.http_json", side_effect=_fake_http_json):
            session = client.create_setup_session(workspace_id="default")
        self.assertEqual(session.get("id"), "sess-legacy")
        self.assertEqual(calls, ["/onboarding/sessions", "/setup/sessions"])

    def test_send_telegram_message_uses_runtime_endpoint(self) -> None:
        captured = {}

        def _fake_http_json(api_url, path, api_key, **kwargs):
            captured["api_url"] = api_url
            captured["path"] = path
            captured["method"] = kwargs.get("method")
            captured["payload"] = kwargs.get("payload")
            return {"ok": True, "chat_id": "123456", "connector_label": "Telegram Bot"}

        client = RuntimeClient(api_url="http://runtime", api_key="key", http_retries=0)
        payload = {"workspace_id": "default", "text": "hello"}
        with patch("scripts.orion_terminal.clients.runtime.http_json", side_effect=_fake_http_json):
            out = client.send_telegram_message(payload)
        self.assertEqual(out.get("ok"), True)
        self.assertEqual(captured.get("path"), "/channels/telegram/send")
        self.assertEqual(captured.get("method"), "POST")
        self.assertEqual(captured.get("payload"), payload)

    def test_list_cognitive_approvals_uses_runtime_endpoint(self) -> None:
        captured = {}

        def _fake_http_json(api_url, path, api_key, **kwargs):
            captured["path"] = path
            captured["method"] = kwargs.get("method")
            return {"ok": True, "items": [{"event_id": "evt-1"}]}

        client = RuntimeClient(api_url="http://runtime", api_key="key", http_retries=0)
        with patch("scripts.orion_terminal.clients.runtime.http_json", side_effect=_fake_http_json):
            items = client.list_cognitive_approvals(limit=12)
        self.assertEqual(items, [{"event_id": "evt-1"}])
        self.assertEqual(captured.get("path"), "/cognitive/approvals?limit=12")
        self.assertEqual(captured.get("method"), "GET")

    def test_resolve_cognitive_approval_uses_runtime_endpoint(self) -> None:
        captured = {}

        def _fake_http_json(api_url, path, api_key, **kwargs):
            captured["path"] = path
            captured["method"] = kwargs.get("method")
            captured["payload"] = kwargs.get("payload")
            return {"ok": True, "event_id": "evt-1", "status": "pending"}

        client = RuntimeClient(api_url="http://runtime", api_key="key", http_retries=0)
        with patch("scripts.orion_terminal.clients.runtime.http_json", side_effect=_fake_http_json):
            out = client.resolve_cognitive_approval(event_id="evt-1", decision="approve", note="ok")
        self.assertEqual(out.get("ok"), True)
        self.assertEqual(captured.get("path"), "/cognitive/approvals/evt-1/resolve")
        self.assertEqual(captured.get("method"), "POST")
        self.assertEqual(captured.get("payload"), {"decision": "approve", "note": "ok"})

    def test_stream_inbox_events_uses_stream_endpoint_and_parses_sse(self) -> None:
        class _FakeSseResponse:
            def __init__(self, lines):
                self._lines = [line.encode("utf-8") for line in lines]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def __iter__(self):
                return iter(self._lines)

        captured = {}

        def _fake_urlopen(req, timeout=None, context=None):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(req.header_items())
            return _FakeSseResponse(
                [
                    "event: inbox_event\n",
                    'data: {"id":"evt-1","channel":"telegram","direction":"inbound"}\n',
                    "\n",
                    "event: heartbeat\n",
                    'data: {"ts":"2026-02-28T00:00:00Z"}\n',
                    "\n",
                    "event: done\n",
                    'data: {"reason":"timeout"}\n',
                    "\n",
                ]
            )

        client = RuntimeClient(api_url="http://runtime", api_key="key", http_retries=0)
        with patch("scripts.orion_terminal.clients.runtime.urlrequest.urlopen", side_effect=_fake_urlopen):
            items = client.stream_inbox_events(
                workspace_id="default",
                channel="telegram",
                session_key="telegram:main",
                since_id="evt-0",
                timeout_seconds=0.2,
                poll_seconds=0.1,
            )
        self.assertEqual(items, [{"id": "evt-1", "channel": "telegram", "direction": "inbound"}])
        url = str(captured.get("url") or "")
        self.assertIn("/events/inbox/stream?", url)
        self.assertIn("workspace_id=default", url)
        self.assertIn("channel=telegram", url)
        self.assertIn("session_key=telegram%3Amain", url)
        self.assertIn("since_id=evt-0", url)
        headers = captured.get("headers") or {}
        self.assertIn("X-api-key", headers)
        self.assertIn("X-orion-cli-version", headers)
        self.assertIn("X-orion-cli-session", headers)


if __name__ == "__main__":
    unittest.main()
