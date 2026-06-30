"""Tests for server_modules.transparency_settings_service."""

from __future__ import annotations

import unittest

from server_modules.transparency_settings_service import (
    TransparencySettings,
    get_transparency_settings,
    put_transparency_settings,
    effective_visibility,
)


class TransparencySettingsTests(unittest.TestCase):
    def test_default_settings_are_safe(self):
        s = TransparencySettings.safe_defaults("ws-1")
        self.assertTrue(s.is_valid())
        self.assertEqual(s.customer_mode, "minimal")
        self.assertEqual(s.default_mode, "standard")

    def test_customer_mode_rejects_standard(self):
        s = TransparencySettings(customer_mode="standard")
        errors = s.validate()
        self.assertTrue(len(errors) > 0)

    def test_customer_mode_rejects_full(self):
        s = TransparencySettings(customer_mode="full")
        errors = s.validate()
        self.assertTrue(len(errors) > 0)

    def test_customer_mode_rejects_enterprise(self):
        s = TransparencySettings(customer_mode="enterprise")
        errors = s.validate()
        self.assertTrue(len(errors) > 0)

    def test_customer_mode_accepts_minimal(self):
        s = TransparencySettings(customer_mode="minimal")
        self.assertTrue(s.is_valid())

    def test_customer_mode_accepts_off(self):
        s = TransparencySettings(customer_mode="off")
        self.assertTrue(s.is_valid())

    def test_admin_can_use_enterprise(self):
        s = TransparencySettings(default_mode="enterprise")
        self.assertTrue(s.is_valid())

    def test_serialize_roundtrip(self):
        s = TransparencySettings(
            workspace_id="ws-r",
            default_mode="full",
            sage_mode="standard",
            studio_test_mode="full",
            customer_mode="off",
            show_trace_ids=False,
        )
        d = s.to_dict()
        s2 = TransparencySettings.from_dict(d)
        self.assertEqual(s2.workspace_id, "ws-r")
        self.assertEqual(s2.default_mode, "full")
        self.assertEqual(s2.customer_mode, "off")
        self.assertFalse(s2.show_trace_ids)

    def test_invalid_mode_falls_back(self):
        s = TransparencySettings.from_dict({"default_mode": "xyz_invalid"})
        self.assertEqual(s.default_mode, "standard")

    def test_put_and_get(self):
        s = TransparencySettings.safe_defaults("ws-store")
        put_transparency_settings(s)
        retrieved = get_transparency_settings("ws-store")
        self.assertEqual(retrieved.default_mode, "standard")

    def test_missing_workspace_returns_defaults(self):
        s = get_transparency_settings("ws-nonexistent")
        self.assertEqual(s.default_mode, "standard")
        self.assertTrue(s.is_valid())

    def test_customer_safe_defaults_are_minimal(self):
        s = TransparencySettings.customer_safe_defaults("ws-cust")
        self.assertEqual(s.customer_mode, "off")
        self.assertFalse(s.show_trace_ids)
        self.assertFalse(s.show_tool_names)

    def test_put_invalid_raises(self):
        s = TransparencySettings(customer_mode="full")
        with self.assertRaises(ValueError):
            put_transparency_settings(s)

    def test_effective_visibility_customer(self):
        s = TransparencySettings.safe_defaults("ws-ev")
        mode = effective_visibility(s, surface="chat", audience="customer")
        self.assertEqual(mode, "minimal")

    def test_effective_visibility_owner_chat(self):
        s = TransparencySettings.safe_defaults("ws-ev2")
        mode = effective_visibility(s, surface="chat", audience="owner")
        self.assertEqual(mode, "standard")

    def test_effective_visibility_studio_test(self):
        s = TransparencySettings.safe_defaults("ws-ev3")
        mode = effective_visibility(s, surface="studio_test", audience="owner")
        self.assertEqual(mode, "full")

    def test_settings_never_allow_raw_cot(self):
        # The model has no raw_cot field by design
        s = TransparencySettings.safe_defaults("ws-cot")
        d = s.to_dict()
        self.assertNotIn("raw_chain_of_thought", d)
        self.assertNotIn("raw_cot", d)


if __name__ == "__main__":
    unittest.main()
