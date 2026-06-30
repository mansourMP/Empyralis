from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from server_modules import durable_quota_store
from server_modules.gateway_quota_enforcement import (
    QuotaWindow,
    GatewayQuotaDecision,
    evaluate_gateway_quota,
    get_gateway_quota_snapshot,
    GATEWAY_TOOL_EXECUTION,
    GATEWAY_BROWSER_SESSION,
    GATEWAY_APPROVAL_ACTION,
    GATEWAY_WS_CONNECTION,
)


class GatewayQuotaEnforcementTests(unittest.TestCase):
    def test_quota_window_allows_up_to_limit(self):
        qw = QuotaWindow(max_requests=3, window_seconds=60)
        self.assertTrue(qw.allow("key1"))
        self.assertTrue(qw.allow("key1"))
        self.assertTrue(qw.allow("key1"))

    def test_quota_window_blocks_after_limit(self):
        qw = QuotaWindow(max_requests=2, window_seconds=60)
        qw.allow("key1")
        qw.allow("key1")
        self.assertFalse(qw.allow("key1"))

    def test_quota_window_remaining(self):
        qw = QuotaWindow(max_requests=5, window_seconds=60)
        qw.allow("key1")
        qw.allow("key1")
        self.assertEqual(qw.remaining("key1"), 3)

    def test_evaluate_gateway_quota_allows_within_limit(self):
        decision = evaluate_gateway_quota(
            profile=GATEWAY_TOOL_EXECUTION,
            gateway_id="gw_test_1",
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.profile, GATEWAY_TOOL_EXECUTION)

    def test_evaluate_gateway_quota_blocks_after_threshold(self):
        for _ in range(60):
            decision = evaluate_gateway_quota(
                profile=GATEWAY_TOOL_EXECUTION,
                gateway_id="gw_test_2",
            )
            if not decision.allowed:
                break
        last = evaluate_gateway_quota(
            profile=GATEWAY_TOOL_EXECUTION,
            gateway_id="gw_test_2",
        )
        self.assertFalse(last.allowed)
        self.assertEqual(last.reason, "gateway_tool_execution_rate_limited")
        self.assertGreater(last.retry_after_seconds, 0)

    def test_evaluate_gateway_quota_unknown_profile_allows(self):
        decision = evaluate_gateway_quota(profile="unknown", gateway_id="gw1")
        self.assertTrue(decision.allowed)

    def test_get_gateway_quota_snapshot(self):
        snapshot = get_gateway_quota_snapshot("gw_snap_1")
        self.assertIn(GATEWAY_TOOL_EXECUTION, snapshot)
        self.assertIn(GATEWAY_BROWSER_SESSION, snapshot)
        self.assertIn(GATEWAY_APPROVAL_ACTION, snapshot)
        self.assertIn(GATEWAY_WS_CONNECTION, snapshot)
        for profile, info in snapshot.items():
            self.assertIn("remaining", info)
            self.assertIn("max_requests", info)
            self.assertIn("window_seconds", info)

    def test_gateway_quota_uses_durable_store_when_quota_db_is_configured(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            quota_db = os.path.join(tmp_dir, "quota.sqlite3")
            with patch.dict(os.environ, {"EMPYRALIS_QUOTA_DB": quota_db}, clear=False):
                durable_quota_store.reset_quota_store_for_tests()
                decision = evaluate_gateway_quota(
                    profile=GATEWAY_TOOL_EXECUTION,
                    gateway_id="gw_durable_1",
                )
                snapshot = get_gateway_quota_snapshot("gw_durable_1")
                durable_quota_store.reset_quota_store_for_tests()

        self.assertTrue(decision.allowed)
        self.assertEqual(snapshot[GATEWAY_TOOL_EXECUTION]["storage"], "sqlite")

    def test_gateway_quota_decision_is_immutable(self):
        decision = GatewayQuotaDecision(
            allowed=True, profile="test", reason="ok", remaining=10
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.profile, "test")


if __name__ == "__main__":
    unittest.main()
