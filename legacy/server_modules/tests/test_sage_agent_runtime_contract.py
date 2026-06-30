from __future__ import annotations

import unittest

from server_modules.sage_agent_runtime_contract import (
    SAGE_MODE,
    SAGE_RUNTIME_KIND,
    SAGE_ALLOWED_SURFACES,
    SAGE_RESPONSE_KEYS,
    SageTurnContract,
    SageTurnResult,
    normalize_sage_mode,
    normalize_sage_surface,
    is_sage_mode,
    build_sage_policy_context,
)


class SageContractNormalizationTests(unittest.TestCase):
    def test_normalize_sage_mode_accepts_owner_sage(self):
        self.assertEqual(normalize_sage_mode("owner_sage"), SAGE_MODE)

    def test_normalize_sage_mode_accepts_aliases(self):
        self.assertEqual(normalize_sage_mode("sage"), SAGE_MODE)
        self.assertEqual(normalize_sage_mode("owner"), SAGE_MODE)
        self.assertEqual(normalize_sage_mode("personal"), SAGE_MODE)

    def test_normalize_sage_mode_rejects_unknown(self):
        with self.assertRaises(ValueError):
            normalize_sage_mode("customer_live")
        with self.assertRaises(ValueError):
            normalize_sage_mode("studio_agent")
        with self.assertRaises(ValueError):
            normalize_sage_mode("")

    def test_normalize_sage_surface_accepts_allowed(self):
        self.assertEqual(normalize_sage_surface("chat"), "chat")
        self.assertEqual(normalize_sage_surface("mobile"), "mobile")
        self.assertEqual(normalize_sage_surface("web"), "web")
        self.assertEqual(normalize_sage_surface("desktop"), "desktop")
        self.assertEqual(normalize_sage_surface("voice"), "voice")

    def test_normalize_sage_surface_defaults_empty(self):
        self.assertEqual(normalize_sage_surface(""), "chat")

    def test_normalize_sage_surface_rejects_unknown(self):
        with self.assertRaises(ValueError):
            normalize_sage_surface("email")

    def test_is_sage_mode_detects_valid(self):
        self.assertTrue(is_sage_mode("owner_sage"))
        self.assertTrue(is_sage_mode("sage"))

    def test_is_sage_mode_rejects_invalid(self):
        self.assertFalse(is_sage_mode("customer_live"))
        self.assertFalse(is_sage_mode(""))

    def test_build_sage_policy_context(self):
        ctx = build_sage_policy_context()
        self.assertEqual(ctx["sage_mode"], SAGE_MODE)
        self.assertEqual(ctx["runtime_kind"], SAGE_RUNTIME_KIND)

    def test_sage_turn_contract_defaults(self):
        contract = SageTurnContract(
            workspace_id="ws-1",
            tenant_id="t-1",
            message="hello",
        )
        self.assertEqual(contract.mode, SAGE_MODE)
        self.assertEqual(contract.surface, "chat")

    def test_sage_turn_result_as_dict(self):
        result = SageTurnResult(
            message="reply",
            used_context=["sage_memory"],
            blocked_tools=[{"skill_id": "x", "label": "X", "action_class": "write"}],
            approvals_required=[{"type": "tool_action", "skill_id": "x", "label": "X", "reason": "test"}],
            trace_id="trace-1",
            provider="openai",
        )
        d = result.as_dict()
        for key in SAGE_RESPONSE_KEYS:
            self.assertIn(key, d)
        self.assertEqual(d["approvals_required"], result.approvals_required)
        self.assertEqual(d["blocked_tools"], result.blocked_tools)

    def test_sage_turn_result_defaults(self):
        result = SageTurnResult(message="ok")
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.blocked_tools, [])
        self.assertEqual(result.approvals_required, [])
        self.assertEqual(result.memory_updates, [])


if __name__ == "__main__":
    unittest.main()
