from __future__ import annotations

import unittest

from server_modules.safety_error_contract import (
    SafetyError,
    kill_switch_error,
    quota_exceeded_error,
    approval_required_error,
    blocked_action_error,
    workspace_isolation_error,
    to_http_body,
    to_http_status,
    ERROR_CODE_KILL_SWITCH,
    ERROR_CODE_QUOTA_EXCEEDED,
    ERROR_CODE_APPROVAL_REQUIRED,
    ERROR_CODE_BLOCKED_ACTION,
    ERROR_CODE_WORKSPACE_ISOLATION,
)


class SafetyErrorContractTests(unittest.TestCase):
    def test_kill_switch_error(self):
        err = kill_switch_error(scope="global", detail="Emergency stop active", trace_id="t1")
        self.assertEqual(err.code, ERROR_CODE_KILL_SWITCH)
        self.assertEqual(err.scope, "global")
        self.assertFalse(err.retryable)

    def test_quota_exceeded_error(self):
        err = quota_exceeded_error(profile="gateway_tool_execution", retry_after_seconds=30, trace_id="t2")
        self.assertEqual(err.code, ERROR_CODE_QUOTA_EXCEEDED)
        self.assertTrue(err.retryable)
        self.assertEqual(err.retry_after_seconds, 30)

    def test_approval_required_error(self):
        err = approval_required_error(action="send_email", approval_token="sap_abc", trace_id="t3")
        self.assertEqual(err.code, ERROR_CODE_APPROVAL_REQUIRED)
        self.assertFalse(err.retryable)
        self.assertIn("sap_abc", err.details["approval_token"])

    def test_blocked_action_error(self):
        err = blocked_action_error(action="shell_exec", reason="blocked_by_policy", trace_id="t4")
        self.assertEqual(err.code, ERROR_CODE_BLOCKED_ACTION)
        self.assertEqual(err.scope, "safety")

    def test_workspace_isolation_error(self):
        err = workspace_isolation_error(expected="ws-1", actual="ws-2", trace_id="t5")
        self.assertEqual(err.code, ERROR_CODE_WORKSPACE_ISOLATION)
        self.assertFalse(err.retryable)

    def test_to_http_body(self):
        err = kill_switch_error(scope="agent", detail="Agent stopped", trace_id="t6")
        body = to_http_body(err)
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], ERROR_CODE_KILL_SWITCH)
        self.assertEqual(body["error"]["trace_id"], "t6")

    def test_to_http_status_kill_switch(self):
        err = kill_switch_error(scope="global", detail="x")
        self.assertEqual(to_http_status(err), 503)

    def test_to_http_status_quota(self):
        err = quota_exceeded_error(profile="x", retry_after_seconds=1)
        self.assertEqual(to_http_status(err), 429)

    def test_to_http_status_approval(self):
        err = approval_required_error(action="x")
        self.assertEqual(to_http_status(err), 403)

    def test_to_http_status_isolation(self):
        err = workspace_isolation_error(expected="a", actual="b")
        self.assertEqual(to_http_status(err), 403)

    def test_safety_error_defaults(self):
        err = SafetyError(code="TEST", message="test")
        self.assertEqual(err.trace_id, "")
        self.assertEqual(err.details, {})
        self.assertFalse(err.retryable)


if __name__ == "__main__":
    unittest.main()
