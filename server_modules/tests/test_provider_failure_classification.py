"""Every provider failure has its OWN name, and that name routes correctly.

Two halves, and the second is the one that matters:

1. The classifier maps a real provider response to the right code.
2. Each code, driven through the REAL ``agent_command_dispatcher.
   classify_error``, selects the reply a person can act on.

Half 2 is what stops this becoming a second vocabulary. The expected set
lives in ``provider_failure_classification``; the actual set comes out of
``classify_error``, a different module with its own keyword buckets. A code
that stops routing — because someone reorders a bucket, or renames a
keyword — fails here instead of silently degrading to "Something went
wrong. Try again."

Red-before-green: against the pre-fix adapter every one of the seven
``generate`` cases raised ``"DeepSeek returned no choices."``
"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from server_modules import provider_failure_classification as pfc
from server_modules.provider_failure_classification import (
    AUTH_FAILED,
    EMPTY_RESPONSE,
    MODEL_NOT_FOUND,
    PAYMENT_REQUIRED,
    RATE_LIMITED,
    REQUEST_REJECTED,
    UNREACHABLE,
    ProviderCallError,
    classify_provider_http_failure,
)


def _classify(status, body, *, model=""):
    return classify_provider_http_failure(
        status, body, provider_label="DeepSeek", model=model
    )


class ClassifierTests(unittest.TestCase):
    """Real provider bodies in, one named failure out."""

    def test_401_is_auth_not_an_empty_response(self):
        # DeepSeek's actual 401 body, observed live 2026-08-29.
        failure = _classify(
            401,
            {
                "error": {
                    "message": "Authentication Fails, Your api key: ****cked is invalid",
                    "type": "authentication_error",
                    "code": "invalid_request_error",
                }
            },
        )
        self.assertIsNotNone(failure)
        self.assertEqual(failure.code, AUTH_FAILED)
        self.assertEqual(failure.status, 401)

    def test_403_is_auth(self):
        self.assertEqual(_classify(403, {}).code, AUTH_FAILED)

    def test_402_is_payment_required(self):
        self.assertEqual(_classify(402, {}).code, PAYMENT_REQUIRED)

    def test_429_is_rate_limited(self):
        self.assertEqual(_classify(429, {"error": {"code": "rate_limit_exceeded"}}).code, RATE_LIMITED)

    def test_429_with_insufficient_quota_is_payment_not_rate_limit(self):
        """The distinction the HTTP status alone cannot make.

        OpenAI answers 429 for BOTH ordinary throttling and a hard "you are
        out of credit". Those need opposite advice — "try again in a moment"
        versus "top the account up" — so reading the status alone would tell
        a customer with an empty balance to keep retrying forever.
        """
        failure = _classify(
            429,
            {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}},
        )
        self.assertEqual(failure.code, PAYMENT_REQUIRED)
        self.assertEqual(failure.status, 429)

    def test_404_is_model_not_found_and_names_the_model(self):
        failure = _classify(404, {"error": {"code": "model_not_found"}}, model="deepseek-v9")
        self.assertEqual(failure.code, MODEL_NOT_FOUND)
        self.assertIn("deepseek-v9", str(failure))

    def test_model_not_found_by_provider_code_on_a_400(self):
        """Some providers answer 400, not 404, and say so only in their code."""
        failure = _classify(400, {"error": {"code": "model_not_found"}}, model="nope")
        self.assertEqual(failure.code, MODEL_NOT_FOUND)

    def test_5xx_is_unreachable(self):
        self.assertEqual(_classify(500, {}).code, UNREACHABLE)
        self.assertEqual(_classify(503, {}).code, UNREACHABLE)

    def test_408_is_unreachable(self):
        self.assertEqual(_classify(408, {}).code, UNREACHABLE)

    def test_other_4xx_is_a_rejected_request(self):
        self.assertEqual(_classify(400, {"error": {"message": "max_tokens too large"}}).code, REQUEST_REJECTED)

    def test_2xx_is_not_a_failure(self):
        """None means "this may carry a real answer" — the caller still checks."""
        self.assertIsNone(_classify(200, {"choices": [{"message": {"content": "hi"}}]}))
        self.assertIsNone(_classify(200, {"choices": []}))

    def test_unparseable_status_is_not_silently_a_success(self):
        """A missing/garbage status must not read as 2xx and fall through."""
        self.assertIsNone(_classify(None, {}))
        self.assertIsNone(_classify("nonsense", {}))

    def test_empty_response_keeps_its_own_name(self):
        failure = pfc.empty_response_error("DeepSeek")
        self.assertEqual(failure.code, EMPTY_RESPONSE)
        self.assertIn("no completion", str(failure))


class ProviderProseNeverReachesTheCustomerTests(unittest.TestCase):
    """The provider's words go to the log, never into the message.

    DeepSeek's own 401 body echoes a partial key back at us. A stable code
    plus our own sentence cannot leak a provider's response however that
    response is worded next month.
    """

    def test_masked_key_is_not_in_the_message(self):
        failure = _classify(
            401,
            {"error": {"message": "Authentication Fails, Your api key: ****cked is invalid"}},
        )
        self.assertNotIn("****cked", str(failure))
        self.assertNotIn("api key: ", str(failure))

    def test_provider_detail_is_kept_for_operators(self):
        failure = _classify(401, {"error": {"message": "Authentication Fails, Your api key: ****cked is invalid"}})
        self.assertIn("Authentication Fails", failure.provider_detail)

    def test_provider_detail_is_redacted(self):
        """A provider echoing a real-looking secret must not land in a log intact."""
        failure = _classify(
            400, {"error": {"message": "bad token sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}}
        )
        self.assertNotIn("sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", failure.provider_detail)

    def test_provider_detail_is_bounded(self):
        failure = _classify(400, {"error": {"message": "x" * 5000}})
        self.assertLessEqual(len(failure.provider_detail), 400)


class CodeRoutingTests(unittest.TestCase):
    """Drive the REAL classify_error. Different module, different source.

    This is the half that makes the codes above a shared vocabulary rather
    than a private one.
    """

    def _reply(self, failure, **kwargs):
        from server_modules.agent_command_dispatcher import classify_error

        return classify_error(str(failure), raw_error=str(failure), **kwargs)

    def test_auth_routes_to_the_auth_reply(self):
        from server_modules.agent_command_dispatcher import (
            SAGE_AI_NEEDS_ATTENTION_PLATFORM_REPLY,
            SAGE_AI_NEEDS_ATTENTION_REPLY,
        )

        failure = _classify(401, {})
        self.assertEqual(self._reply(failure, is_platform_credits=False), SAGE_AI_NEEDS_ATTENTION_REPLY)
        self.assertEqual(self._reply(failure), SAGE_AI_NEEDS_ATTENTION_PLATFORM_REPLY)

    def test_payment_routes_to_the_balance_reply_split_by_owner(self):
        from server_modules.agent_command_dispatcher import (
            SAGE_PAYMENT_REQUIRED_BYOK_REPLY,
            SAGE_PAYMENT_REQUIRED_PLATFORM_REPLY,
        )

        failure = _classify(402, {})
        self.assertEqual(self._reply(failure), SAGE_PAYMENT_REQUIRED_PLATFORM_REPLY)
        self.assertEqual(self._reply(failure, is_platform_credits=False), SAGE_PAYMENT_REQUIRED_BYOK_REPLY)

    def test_payment_beats_auth_when_both_could_match(self):
        """An empty balance must never be reported as an auth problem.

        classify_error's payment bucket is checked before its auth bucket
        deliberately. Asserted here because the two are one bucket-reorder
        apart and the failure would be silent.
        """
        from server_modules.agent_command_dispatcher import SAGE_AI_NEEDS_ATTENTION_PLATFORM_REPLY

        self.assertNotEqual(self._reply(_classify(402, {})), SAGE_AI_NEEDS_ATTENTION_PLATFORM_REPLY)

    def test_rate_limited_routes_to_the_rate_limit_reply(self):
        from server_modules.agent_command_dispatcher import SAGE_RATE_LIMITED_REPLY

        self.assertEqual(self._reply(_classify(429, {})), SAGE_RATE_LIMITED_REPLY)

    def test_quota_429_routes_to_balance_not_try_again(self):
        """The whole point of reading the provider's own code field."""
        from server_modules.agent_command_dispatcher import (
            SAGE_PAYMENT_REQUIRED_PLATFORM_REPLY,
            SAGE_RATE_LIMITED_REPLY,
        )

        failure = _classify(429, {"error": {"code": "insufficient_quota"}})
        self.assertEqual(self._reply(failure), SAGE_PAYMENT_REQUIRED_PLATFORM_REPLY)
        self.assertNotEqual(self._reply(failure), SAGE_RATE_LIMITED_REPLY)

    def test_model_not_found_routes_to_its_own_reply_not_try_again(self):
        from server_modules.agent_command_dispatcher import (
            SAGE_ERROR_REPLY,
            SAGE_MODEL_NOT_FOUND_REPLY,
        )

        failure = _classify(404, {}, model="deepseek-v9")
        self.assertEqual(self._reply(failure), SAGE_MODEL_NOT_FOUND_REPLY)
        # Retrying is the one action that can never fix a wrong model id.
        self.assertNotEqual(self._reply(failure), SAGE_ERROR_REPLY)

    def test_unreachable_routes_to_the_unreachable_reply(self):
        from server_modules.agent_command_dispatcher import SAGE_PROVIDER_UNREACHABLE_REPLY

        self.assertEqual(self._reply(_classify(500, {})), SAGE_PROVIDER_UNREACHABLE_REPLY)

    def test_every_code_routes_somewhere_deliberate(self):
        """No code may land in the catch-all by accident.

        REQUEST_REJECTED and EMPTY_RESPONSE are allowed there ON PURPOSE —
        one is our own malformed request and the other is a healthy provider
        with nothing to say; "Something went wrong" is honest for both. Any
        OTHER code arriving there is a routing bug.
        """
        from server_modules.agent_command_dispatcher import SAGE_ERROR_REPLY

        allowed_generic = {REQUEST_REJECTED, EMPTY_RESPONSE}
        for code in pfc.ALL_CODES:
            failure = ProviderCallError(code, "a sentence about it.", status=500)
            reply = self._reply(failure)
            if code in allowed_generic:
                self.assertEqual(reply, SAGE_ERROR_REPLY, f"{code} should be generic")
            else:
                self.assertNotEqual(reply, SAGE_ERROR_REPLY, f"{code} fell into the catch-all")

    def test_public_generation_error_code_recovers_the_bare_code(self):
        """The chat path's own code extractor must see the code, not the prose."""
        from server_modules.direct_chat_generation_service import _public_generation_error_code

        for code in pfc.ALL_CODES:
            failure = ProviderCallError(code, "a sentence about it.", status=500)
            self.assertEqual(_public_generation_error_code(str(failure)), code)


# ── The live path: the real adapter, against real response bodies ──────────

_CASES = {
    "/auth401": (401, {"error": {"message": "Authentication Fails, Your api key: ****cked is invalid", "type": "authentication_error"}}),
    "/balance402": (402, {"error": {"message": "Insufficient Balance"}}),
    "/rate429": (429, {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}}),
    "/model404": (404, {"error": {"message": "The model does not exist", "code": "model_not_found"}}),
    "/server500": (500, {"error": {"message": "Internal server error"}}),
    "/badreq400": (400, {"error": {"message": "max_tokens is too large"}}),
    "/emptyok": (200, {"id": "x", "choices": []}),
    "/goodok": (200, {"id": "x", "choices": [{"message": {"role": "assistant", "content": "hello"}}]}),
}

_EXPECTED = {
    "/auth401": AUTH_FAILED,
    "/balance402": PAYMENT_REQUIRED,
    "/rate429": RATE_LIMITED,
    "/model404": MODEL_NOT_FOUND,
    "/server500": UNREACHABLE,
    "/badreq400": REQUEST_REJECTED,
    "/emptyok": EMPTY_RESPONSE,
}


class _StubProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        status, body = _CASES.get(self.path.split("/chat/completions")[0] or "/", (200, {"choices": []}))
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


class AdapterLivePathTests(unittest.TestCase):
    """The real OpenAICompatibleAdapter.generate, over real HTTP.

    A stub rather than a live provider — this suite may never reach one
    (conftest's socket guard) — but everything on OUR side of the wire is
    the production code path, including http_json_request's own status and
    error-body preservation, which is exactly what the old code ignored.
    """

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _StubProvider)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _adapter(self):
        from server_modules.provider_profiles import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter("deepseek", "DeepSeek")

    def test_each_failure_gets_its_own_code(self):
        adapter = self._adapter()
        for case, expected in _EXPECTED.items():
            with self.subTest(case=case):
                creds = {"api_key": "sk-throwaway-blocked", "base_url": f"http://127.0.0.1:{self.port}{case}"}
                with self.assertRaises(ProviderCallError) as ctx:
                    adapter.generate("sys", "hi", "deepseek-chat", creds)
                self.assertEqual(ctx.exception.code, expected)

    def test_no_two_failures_share_a_message(self):
        """The defect, stated as an assertion.

        Before the fix all seven of these produced the identical string
        "DeepSeek returned no choices."
        """
        adapter = self._adapter()
        seen = set()
        for case in _EXPECTED:
            creds = {"api_key": "sk-throwaway-blocked", "base_url": f"http://127.0.0.1:{self.port}{case}"}
            try:
                adapter.generate("sys", "hi", "deepseek-chat", creds)
            except ProviderCallError as exc:
                seen.add(str(exc))
        self.assertEqual(len(seen), len(_EXPECTED), f"messages collapsed: {sorted(seen)}")

    def test_a_healthy_response_still_returns_its_text(self):
        """The canary: if the stub or the adapter stopped working entirely,
        every case above would 'pass' by raising for the wrong reason."""
        adapter = self._adapter()
        creds = {"api_key": "sk-throwaway-blocked", "base_url": f"http://127.0.0.1:{self.port}/goodok"}
        self.assertEqual(adapter.generate("sys", "hi", "deepseek-chat", creds), "hello")

    def test_failures_are_still_RuntimeErrors(self):
        """Callers catching RuntimeError must keep catching these.

        runs_engine and model_router both catch broadly; a new exception
        type that escaped them would turn a classified failure into an
        unhandled crash.
        """
        adapter = self._adapter()
        creds = {"api_key": "sk-throwaway-blocked", "base_url": f"http://127.0.0.1:{self.port}/auth401"}
        with self.assertRaises(RuntimeError):
            adapter.generate("sys", "hi", "deepseek-chat", creds)


if __name__ == "__main__":
    unittest.main()
