"""Proof for Fix 4: hosted Telegram webhook is fail-closed and rate-limited.

Regression cover for the audit finding that verify_webhook_signature returned
True when no secret was configured (any forged POST drove a paired agent), and
that the hosted webhooks bypassed the shared public-webhook rate limiter.
"""

import hashlib
import hmac
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
        body = b'{"update_id":1}'
        with patch.dict("os.environ", {_SECRET_ENV: "s3cr3t"}, clear=False):
            good = hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
            self.assertTrue(hosted.verify_webhook_signature(good, body))
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
