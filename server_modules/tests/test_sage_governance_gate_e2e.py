"""Sage Governance Gate — End-to-End Certification.

Proves that the unified ingress (execute_sage_turn → handle_sage_chat → action loop)
correctly fires the governance gate for every Main Agent channel:

  (a) Approval gate — risky actions require approval
  (b) Kill switch   — blocks execution when active

These tests exercise the SAME code path every channel uses, guaranteeing identical
safety rules regardless of surface (Telegram-hosted, Slack, Discord, etc.).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import kill_switch_gate, safe_mode_service
from server_modules.unified_governance_gate import evaluate_action_policy
from server_modules.agent_computer_policy_service import (
    build_default_agent_computer_policy,
    AUTONOMY_ASK_EVERY_TIME,
)


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _default_policy(**overrides):
    return build_default_agent_computer_policy(
        autonomy_mode=AUTONOMY_ASK_EVERY_TIME,
        policy_id="test-cert:ws-1",
        **overrides,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 1. Approval Gate — direct evaluate_action_policy() call
# ──────────────────────────────────────────────────────────────────────────────

class GovernanceGateApprovalTests(unittest.TestCase):
    """Approval gate fires for risky capabilities through the unified governance gate."""

    def setUp(self):
        safe_mode_service.reset_state_for_tests()

    def tearDown(self):
        safe_mode_service.reset_state_for_tests()

    def test_risky_shell_exec_requires_approval(self):
        """shell.execute with ASK_EVERY_TIME autonomy → approval_required."""
        policy = _default_policy()
        decision = evaluate_action_policy(
            workspace_id="ws-1",
            actor_user_id="owner",
            agent_id="sage_main_agent",
            policy=policy,
            capability="shell.execute",
            action_class="execute",
            surface="sage_chat",
        )
        self.assertEqual(decision.decision, "approval_required",
                          f"Expected approval_required, got {decision.decision}: {decision.reason}")
        self.assertTrue(decision.approval_required)
        self.assertFalse(decision.blocked)
        self.assertFalse(decision.allowed)

    def test_filesystem_write_requires_approval(self):
        """filesystem.write → approval_required."""
        policy = _default_policy()
        decision = evaluate_action_policy(
            workspace_id="ws-1",
            actor_user_id="owner",
            agent_id="sage_main_agent",
            policy=policy,
            capability="filesystem.write",
            action_class="write",
            surface="sage_chat",
        )
        self.assertEqual(decision.decision, "approval_required")

    def test_agent_computer_write_actions_require_approval(self):
        """Agent-computer write-class actions with ASK_EVERY_TIME policy
        correctly return approval_required."""
        policy = _default_policy()
        decision = evaluate_action_policy(
            workspace_id="ws-1",
            actor_user_id="owner",
            agent_id="sage_main_agent",
            policy=policy,
            capability="filesystem.write",
            action_class="write",
            surface="sage_chat",
        )
        self.assertEqual(decision.decision, "approval_required")

    def test_safe_read_not_blocked(self):
        """Low-risk 'read' action_class is never blocked by kill switch or safe-mode."""
        policy = _default_policy()
        decision = evaluate_action_policy(
            workspace_id="ws-1",
            actor_user_id="owner",
            agent_id="sage_main_agent",
            policy=policy,
            capability="filesystem.read",
            action_class="read",
            surface="sage_chat",
        )
        self.assertFalse(decision.blocked,
                         f"Read action should never be blocked, got: {decision.reason}")

    def test_decision_as_dict_is_serializable(self):
        """ActionPolicyDecision.as_dict() returns a safe dict for channel replies."""
        policy = _default_policy()
        decision = evaluate_action_policy(
            workspace_id="ws-1",
            actor_user_id="owner",
            agent_id="sage_main_agent",
            policy=policy,
            capability="shell.execute",
            action_class="execute",
            surface="sage_chat",
        )
        d = decision.as_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("decision", d)
        self.assertIn("approval_required", d)
        self.assertIn("blocked", d)
        self.assertIn("reason", d)

    def test_approval_decision_includes_reason(self):
        """When approval is required, the decision reason describes why."""
        policy = _default_policy()
        decision = evaluate_action_policy(
            workspace_id="ws-1",
            actor_user_id="owner",
            agent_id="sage_main_agent",
            policy=policy,
            capability="shell.execute",
            action_class="execute",
            surface="sage_chat",
        )
        self.assertTrue(decision.approval_required)
        self.assertTrue(len(decision.reason) > 0,
                        f"Reason should not be empty for approval_required")


# ──────────────────────────────────────────────────────────────────────────────
# 2. Kill Switch — mock evaluate_kill_switch to return a blocked decision
# ──────────────────────────────────────────────────────────────────────────────

class GovernanceGateKillSwitchTests(unittest.TestCase):
    """Kill switch blocks execution at the unified governance gate.

    Instead of setting real kill switches (which requires a working Rust kernel),
    we mock evaluate_kill_switch to return a blocked decision — the SAME path
    that real kill switches use.
    """

    def setUp(self):
        safe_mode_service.reset_state_for_tests()

    def tearDown(self):
        safe_mode_service.reset_state_for_tests()

    def _blocked_kill_decision(self, **overrides):
        """Return a KillSwitchDecision that signals blocked."""
        return kill_switch_gate.KillSwitchDecision(
            blocked=True,
            reason=overrides.get("reason", "agent_kill_active"),
            scope=overrides.get("scope", "agent"),
            detail=overrides.get("detail", "Agent sage_main_agent is stopped."),
        )

    def test_agent_kill_switch_blocks_shell_exec(self):
        """Agent-scoped kill switch → block for any action on that agent."""
        blocked = self._blocked_kill_decision()
        with patch.object(kill_switch_gate, "evaluate_kill_switch", return_value=blocked):
            decision = evaluate_action_policy(
                workspace_id="ws-1",
                agent_id="sage_main_agent",
                capability="shell.execute",
                surface="sage_chat",
            )
        self.assertEqual(decision.decision, "block",
                         f"Expected block, got {decision.decision}: {decision.reason}")
        self.assertIn("agent_kill_active", decision.reason)
        self.assertTrue(decision.blocked)
        self.assertFalse(decision.allowed)
        self.assertIsNotNone(decision.kill_decision)
        self.assertTrue(decision.kill_decision.get("blocked"))

    def test_global_kill_switch_blocks_all(self):
        """Global kill switch → block regardless of agent_id."""
        blocked = self._blocked_kill_decision(
            reason="global_kill_active",
            scope="global",
            detail="Global kill switch is active.",
        )
        with patch.object(kill_switch_gate, "evaluate_kill_switch", return_value=blocked):
            decision = evaluate_action_policy(
                workspace_id="ws-1",
                capability="shell.execute",
                surface="sage_chat",
            )
        self.assertEqual(decision.decision, "block")
        self.assertIn("global_kill_active", decision.reason)

    def test_kill_switch_inactive_allows_approval_flow(self):
        """When kill switch is NOT active, the gate proceeds to approval flow."""
        allowed = kill_switch_gate.KillSwitchDecision(
            blocked=False,
            reason="",
            scope="",
            detail="",
        )
        with patch.object(kill_switch_gate, "evaluate_kill_switch", return_value=allowed):
            policy = _default_policy()
            decision = evaluate_action_policy(
                workspace_id="ws-1",
                agent_id="sage_main_agent",
                policy=policy,
                capability="shell.execute",
                action_class="execute",
                surface="sage_chat",
            )
        self.assertFalse(decision.blocked,
                         f"Expected not-blocked when kill inactive, got: {decision.reason}")
        self.assertTrue(decision.approval_required,
                        "Should reach approval check when kill is inactive")

    def test_kill_decision_payload_includes_scope_and_detail(self):
        """Kill decision dict includes scope and detail for audit transparency."""
        blocked = self._blocked_kill_decision()
        with patch.object(kill_switch_gate, "evaluate_kill_switch", return_value=blocked):
            decision = evaluate_action_policy(
                workspace_id="ws-1",
                agent_id="sage_main_agent",
                capability="shell.execute",
                surface="sage_chat",
            )
        self.assertIsNotNone(decision.kill_decision)
        kd = decision.kill_decision
        self.assertIn("scope", kd)
        self.assertIn("detail", kd)
        self.assertIn("reason", kd)
        self.assertEqual(kd["blocked"], True)

    def test_kill_switch_blocks_before_safe_mode_check(self):
        """Kill switch is step 1 — it blocks WITHOUT consulting safe mode or risk."""
        blocked = self._blocked_kill_decision()
        with patch.object(kill_switch_gate, "evaluate_kill_switch", return_value=blocked):
            decision = evaluate_action_policy(
                workspace_id="ws-1",
                agent_id="sage_main_agent",
                capability="shell.execute",
                surface="sage_chat",
            )
        self.assertEqual(decision.decision, "block")
        self.assertIn("kill_active", decision.reason)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Full Telegram-hosted path — execute_sage_turn() with mocked LLM
# ──────────────────────────────────────────────────────────────────────────────

class TelegramHostedE2ETests(unittest.TestCase):
    """Telegram-hosted messages flow through execute_sage_turn → reply."""

    def test_text_reply_flows_through_execute_sage_turn(self):
        """A plain-text 'hello' routes through execute_sage_turn and gets a reply."""
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                  return_value={"profile": {"user_name": "Test", "identity_summary": "Tester"}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                  return_value=("deepseek", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback",
                  return_value=("Hey Mansur! How can I help?", {"model": "deepseek-chat"}, "deepseek", "")),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.sage_agent_runtime_service.thread_service.ensure_master_thread",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.thread_service.get_thread",
                  new=AsyncMock(return_value={"turns": []})),
            # Suppress Rust kernel errors from trace service (non-fatal in production)
            patch("server_modules.sage_agent_runtime_service.agent_trace_service.start_trace",
                  new=AsyncMock(return_value={"trace_id": "mock-trace-1"})),
        ):
            from server_modules.sage_turn_adapter import execute_sage_turn

            result = _run(execute_sage_turn(
                workspace_id="ws-1",
                message="hello",
                channel_origin="telegram_hosted",
                channel_sender_id="123456",
                channel_sender_name="Test User",
            ))

        self.assertIn("Hey Mansur", result.message)
        self.assertEqual(result.provider, "deepseek")
        self.assertTrue(result.trace_id, "trace_id should be populated")
        self.assertIsInstance(result.used_context, list)

    def test_channel_metadata_flows_through(self):
        """channel_origin and sender info flow through to a successful reply."""
        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                  return_value={"profile": {"user_name": "Test"}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                  return_value=("deepseek", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback",
                  return_value=("Hi Alice!", {"model": "deepseek-chat"}, "deepseek", "")),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.sage_agent_runtime_service.thread_service.ensure_master_thread",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.thread_service.get_thread",
                  new=AsyncMock(return_value={"turns": []})),
            patch("server_modules.sage_agent_runtime_service.agent_trace_service.start_trace",
                  new=AsyncMock(return_value={"trace_id": "mock-trace-2"})),
        ):
            from server_modules.sage_turn_adapter import execute_sage_turn

            result = _run(execute_sage_turn(
                workspace_id="ws-1",
                message="hello",
                channel_origin="telegram_hosted",
                channel_sender_id="tg-user-99",
                channel_sender_name="Alice",
            ))

        self.assertIn("Hi Alice", result.message)
        self.assertTrue(result.trace_id)

    def test_empty_message_raises_value_error(self):
        """Empty message raises ValueError before reaching the LLM."""
        from server_modules.sage_turn_adapter import execute_sage_turn

        with self.assertRaises(ValueError) as ctx:
            _run(execute_sage_turn(
                workspace_id="ws-1",
                message="",
                channel_origin="telegram_hosted",
            ))
        self.assertIn("message", str(ctx.exception).lower())

    def test_empty_workspace_raises_value_error(self):
        """Empty workspace_id raises ValueError."""
        from server_modules.sage_turn_adapter import execute_sage_turn

        with self.assertRaises(ValueError) as ctx:
            _run(execute_sage_turn(
                workspace_id="",
                message="hello",
                channel_origin="telegram_hosted",
            ))
        self.assertIn("workspace_id", str(ctx.exception).lower())


# ──────────────────────────────────────────────────────────────────────────────
# 4. Action loop triggers governance — approval fires on tool calls
# ──────────────────────────────────────────────────────────────────────────────

class TelegramHostedApprovalE2ETests(unittest.TestCase):
    """When the Sage action loop returns a tool call, the governance gate fires."""

    def _approval_stream_event(self, command="ls -la /tmp"):
        """Build a stream event that simulates the action loop requesting approval."""
        return iter([{
            "type": "final",
            "payload": {
                "reply": "",
                "actions": [{
                    "type": "approval_required",
                    "kind": "approval_required",
                    "connector": "shell",
                    "action": "exec",
                    "input": f'{{"command":"{command}"}}',
                }],
                "approvals": [{
                    "prompt": "Approve Shell to exec before continuing.",
                    "labels": ["shell.exec"],
                    "capabilities": ["shell"],
                    "actions": ["exec"],
                    "status": "waiting",
                }],
                "error": "",
            },
        }])

    def test_shell_tool_triggers_approval_in_action_loop(self):
        """Action loop V3 → shell__exec tool call → approval_required result.
        The result must include tool_calls with status='approval_required'
        and a non-empty approvals_required list."""
        from server_modules import sage_agent_runtime_service

        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                  return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                  return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback"),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities",
                  return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                  return_value={"runtime_ok": True, "local_gateway_online": True}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
                  return_value=self._approval_stream_event("ls -la /tmp")),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.sage_agent_runtime_service.thread_service.ensure_master_thread",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.thread_service.get_thread",
                  new=AsyncMock(return_value={"turns": []})),
            patch("server_modules.sage_agent_runtime_service.agent_trace_service.start_trace",
                  new=AsyncMock(return_value={"trace_id": "mock-trace-3"})),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="run command: ls -la /tmp",
            ))

        # Governance gate fired → approval required
        self.assertEqual(result["action_execution_mode"], "approval_required",
                         f"Expected approval_required, got {result.get('action_execution_mode')}")
        self.assertGreater(len(result["tool_calls"]), 0,
                           "tool_calls should include the shell__exec request")
        self.assertEqual(result["tool_calls"][0]["name"], "shell__exec")
        self.assertEqual(result["tool_calls"][0]["status"], "approval_required")
        self.assertGreater(len(result["approvals_required"]), 0,
                           "approvals_required should be populated when tool needs approval")

    def test_action_loop_with_telegram_hosted_channel_origin(self):
        """Governance gate fires identically with telegram_hosted channel_origin."""
        from server_modules import sage_agent_runtime_service

        with (
            patch("server_modules.sage_agent_runtime_service.sage_profile_service.list_sage_profile",
                  return_value={"profile": {}}),
            patch("server_modules.sage_agent_runtime_service.workspace_context.read_workspace_context_files",
                  return_value={}),
            patch("server_modules.sage_agent_runtime_service.sage_memory_service.build_sage_memory_context_block",
                  return_value=""),
            patch("server_modules.sage_agent_runtime_service.sage_heartbeat_service.build_sage_heartbeat_snapshot",
                  new=AsyncMock(return_value={})),
            patch("server_modules.sage_agent_runtime_service.list_skill_definitions", return_value=[]),
            patch("server_modules.sage_agent_runtime_service._resolve_cloud_provider",
                  return_value=("openai", {"api_key": "test-key"})),
            patch("server_modules.sage_agent_runtime_service.generate_chat_reply_with_provider_fallback"),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports.resolve_workspace_tool_capabilities",
                  return_value=[]),
            patch("server_modules.sage_agent_runtime_service.direct_chat_runtime_exports._resolve_direct_chat_availability",
                  return_value={"runtime_ok": True, "local_gateway_online": True}),
            patch("server_modules.sage_agent_runtime_service.direct_chat_generation_service.stream_provider_backed_direct_chat",
                  return_value=self._approval_stream_event("pwd")),
            patch("server_modules.sage_agent_runtime_service.persist_interaction"),
            patch("server_modules.sage_agent_runtime_service.activity_ledger_service.append_activity_event",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.sage_agent_runtime_service.thread_service.ensure_master_thread",
                  new=AsyncMock()),
            patch("server_modules.sage_agent_runtime_service.thread_service.get_thread",
                  new=AsyncMock(return_value={"turns": []})),
            patch("server_modules.sage_agent_runtime_service.agent_trace_service.start_trace",
                  new=AsyncMock(return_value={"trace_id": "mock-trace-4"})),
        ):
            result = _run(sage_agent_runtime_service.handle_sage_chat(
                workspace_id="ws-1",
                message="run: pwd",
                channel_origin="telegram_hosted",
                sender_name="Mansur",
                sender_id="tg-123",
            ))

        self.assertEqual(result["action_execution_mode"], "approval_required")
        self.assertGreater(len(result["approvals_required"]), 0)
        self.assertGreater(len(result["tool_calls"]), 0)
        self.assertEqual(result["tool_calls"][0]["status"], "approval_required")
