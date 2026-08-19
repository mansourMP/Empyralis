"""Proof for Fix 4: hosted Telegram webhook is fail-closed and rate-limited.

Regression cover for the audit finding that verify_webhook_signature returned
True when no secret was configured (any forged POST drove a paired agent), and
that the hosted webhooks bypassed the shared public-webhook rate limiter.

SECOND REGRESSION, found live 2026-08-19: `test_valid_signature_accepted_
bad_rejected` originally asserted an HMAC-SHA256-of-the-body shape as the
"valid signature" — a scheme Telegram's Bot API does not use and never
sent. Telegram's `X-Telegram-Bot-Api-Secret-Token` is a SHARED SECRET,
echoed back byte-for-byte from whatever `secret_token` was passed to
`setWebhook`; there is no signing of the body at all. The implementation
matched this WRONG test exactly, so both were self-consistently green
while every real inbound Telegram message 403'd — proven live on
production, where the fix's own webhook re-registration produced a real,
observed `last_error_message: "Wrong response from the webhook: 403
Forbidden"` on every retried delivery. This is the exact "when a test
constructs the context object it passes in, the shape is an assumption,
not an observation" trap CLAUDE.md already documents elsewhere in this
codebase — the test invented Telegram's protocol instead of reading it.
"""

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from server_modules import sage_telegram_hosted_service as hosted
from server_modules import routes_sage_telegram_hosted as routes


_SECRET_ENV = "EMPYRALIS_TELEGRAM_HOSTED_WEBHOOK_SECRET"


class VerifyFailClosedTests(unittest.TestCase):
    def test_no_secret_configured_rejects_forged_post(self):
        with patch.dict("os.environ", {_SECRET_ENV: ""}, clear=False):
            # Previously returned True (accept anything); must now reject.
            self.assertFalse(hosted.verify_webhook_signature("anything", b'{"forged":1}'))
            self.assertFalse(hosted.is_webhook_secret_configured())

    def test_valid_signature_accepted_bad_rejected(self):
        # Telegram's real contract (Bot API docs): the header is the raw
        # secret_token value, sent back verbatim on every update -- never
        # an HMAC of the body. `body_bytes` is accepted by the function
        # purely to keep the call site's signature stable; it plays no
        # part in the comparison.
        body = b'{"update_id":1}'
        with patch.dict("os.environ", {_SECRET_ENV: "s3cr3t"}, clear=False):
            self.assertTrue(hosted.verify_webhook_signature("s3cr3t", body))
            self.assertFalse(hosted.verify_webhook_signature("deadbeef", body))
            self.assertFalse(hosted.verify_webhook_signature("", body))  # missing header
            self.assertTrue(hosted.is_webhook_secret_configured())


class _FakeRequest:
    def __init__(self, ip="9.9.9.9"):
        self._ip = ip


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        routes._HOSTED_WEBHOOK_RATE_BUCKETS.clear()

    def test_flood_is_throttled(self):
        req = _FakeRequest()
        with patch("server_modules.client_identity_service.resolve_client_ip", return_value="9.9.9.9"), \
             patch.object(routes, "_HOSTED_WEBHOOK_RATE_LIMIT_PER_MINUTE", 3):
            # First 3 within the window are allowed.
            for _ in range(3):
                routes._enforce_hosted_webhook_rate_limit(req, "/sage/telegram-hosted/webhook")
            # The 4th trips the limiter.
            with self.assertRaises(HTTPException) as ctx:
                routes._enforce_hosted_webhook_rate_limit(req, "/sage/telegram-hosted/webhook")
        self.assertEqual(ctx.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
