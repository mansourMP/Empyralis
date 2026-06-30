import os
import unittest
from unittest.mock import patch

from server_modules import safe_mode_service, tool_broker_guard_service


def _claims(grant_id: str = "grant-test") -> dict:
    return {
        "grant_id": grant_id,
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "manifest_id": "manifest-1",
        "agent_install_id": "agent-1",
    }


class ToolBrokerGuardServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        tool_broker_guard_service.reset_state_for_tests()
        safe_mode_service.reset_state_for_tests()

    def test_limits_total_calls_per_capability_grant_window(self) -> None:
        env = {"EMPYRALIS_TOOL_BROKER_SESSION_LIMIT": "2"}
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims(),
                    tool_key="web-search",
                    action_class="read",
                    now=100.0,
                ).allowed
            )
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims(),
                    tool_key="inventory-tool",
                    action_class="read",
                    now=101.0,
                ).allowed
            )
            decision = tool_broker_guard_service.evaluate_tool_call(
                claims=_claims(),
                tool_key="browser",
                action_class="read",
                now=102.0,
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "broker_session_rate_limited")

    def test_execute_class_calls_trip_circuit_breaker(self) -> None:
        env = {
            "EMPYRALIS_TOOL_BROKER_SESSION_LIMIT": "20",
            "EMPYRALIS_TOOL_BROKER_EXECUTE_LIMIT": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims("grant-exec"),
                    tool_key="task-runner",
                    action_class="execute",
                    now=100.0,
                ).allowed
            )
            decision = tool_broker_guard_service.evaluate_tool_call(
                claims=_claims("grant-exec"),
                tool_key="task-runner",
                action_class="execute",
                now=101.0,
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "broker_execute_circuit_breaker")
        incident = safe_mode_service.resolve_workspace_incident_state(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
        )
        self.assertTrue(incident["active"])
        self.assertEqual(incident["mode"], "pause")

    def test_connector_write_calls_trip_circuit_breaker(self) -> None:
        env = {
            "EMPYRALIS_TOOL_BROKER_SESSION_LIMIT": "20",
            "EMPYRALIS_TOOL_BROKER_CONNECTOR_WRITE_LIMIT": "1",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims("grant-connector"),
                    tool_key="crm",
                    connector_scope="crm",
                    action_class="write",
                    surface="connector",
                    now=100.0,
                ).allowed
            )
            decision = tool_broker_guard_service.evaluate_tool_call(
                claims=_claims("grant-connector"),
                tool_key="email",
                connector_scope="email",
                action_class="write",
                surface="connector",
                now=101.0,
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "broker_connector_write_circuit_breaker")
        incident = safe_mode_service.resolve_connector_incident_state(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            connector_id="email",
        )
        self.assertTrue(incident["active"])
        self.assertEqual(incident["mode"], "pause")

    def test_tool_rate_limit_blocks_repeated_tool_calls(self) -> None:
        env = {
            "EMPYRALIS_TOOL_BROKER_SESSION_LIMIT": "20",
            "EMPYRALIS_TOOL_BROKER_TOOL_LIMIT": "2",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims("grant-tool-limit"),
                    tool_key="web-search",
                    action_class="read",
                    now=100.0,
                ).allowed
            )
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims("grant-tool-limit"),
                    tool_key="web-search",
                    action_class="read",
                    now=101.0,
                ).allowed
            )
            decision = tool_broker_guard_service.evaluate_tool_call(
                claims=_claims("grant-tool-limit"),
                tool_key="web-search",
                action_class="read",
                now=102.0,
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "broker_tool_rate_limited")

    def test_action_class_calls_have_generic_rate_limit(self) -> None:
        env = {
            "EMPYRALIS_TOOL_BROKER_SESSION_LIMIT": "20",
            "EMPYRALIS_TOOL_BROKER_TOOL_LIMIT": "20",
            "EMPYRALIS_TOOL_BROKER_ACTION_CLASS_LIMIT": "2",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims("grant-action-class"),
                    tool_key="browser-open",
                    action_class="read",
                    now=100.0,
                ).allowed
            )
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims("grant-action-class"),
                    tool_key="web-fetch",
                    action_class="read",
                    now=101.0,
                ).allowed
            )
            decision = tool_broker_guard_service.evaluate_tool_call(
                claims=_claims("grant-action-class"),
                tool_key="memory-read",
                action_class="read",
                now=102.0,
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "broker_action_class_rate_limited")
        self.assertEqual(decision.snapshot["action_class_count"], 2)

    def test_guard_blocks_tool_call_when_broker_rate_limit_exceeded(self) -> None:
        """Guard integration: when session limit is exceeded, all subsequent tool calls are blocked."""
        env = {"EMPYRALIS_TOOL_BROKER_SESSION_LIMIT": "1"}
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(
                tool_broker_guard_service.evaluate_tool_call(
                    claims=_claims("grant-guard-integ"),
                    tool_key="web-search",
                    action_class="read",
                    now=100.0,
                ).allowed
            )
            blocked = tool_broker_guard_service.evaluate_tool_call(
                claims=_claims("grant-guard-integ"),
                tool_key="browser",
                action_class="read",
                now=101.0,
            )

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.code, "broker_session_rate_limited")


if __name__ == "__main__":
    unittest.main()
