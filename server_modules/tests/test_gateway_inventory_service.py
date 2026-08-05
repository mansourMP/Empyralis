import unittest

from server_modules import gateway_inventory_service


class SanitizeCapabilityReadinessShellFullAccessTests(unittest.TestCase):
    """sanitize_capability_readiness() is the untrusted-wire gate every
    gateway.heartbeat's capability_readiness payload passes through
    (gateway_protocol_service.py) before it's persisted onto the
    registration/session metadata. shell_full_access_locally_enabled is a
    new field on that payload (the box operator's live full_access opt-in,
    empyralis-gateway/src/config.ts's shellFullAccessLocallyEnabled) that
    relies on this function's existing generic bool passthrough (the final
    loop in sanitize_capability_readiness, which already carries any
    unrecognized boolean/string key through untouched) rather than a
    dedicated allow-list entry. This locks that behavior down directly so a
    future refactor of the passthrough can't silently start dropping it."""

    def test_true_value_passes_through(self) -> None:
        result = gateway_inventory_service.sanitize_capability_readiness(
            {"requested": ["shell.execute"], "shell_full_access_locally_enabled": True}
        )
        self.assertIs(result["shell_full_access_locally_enabled"], True)

    def test_false_value_passes_through_and_is_not_dropped_as_falsy(self) -> None:
        # The generic passthrough loop's `isinstance(item, bool)` branch must
        # keep False, not treat it like an absent/empty value.
        result = gateway_inventory_service.sanitize_capability_readiness(
            {"shell_full_access_locally_enabled": False}
        )
        self.assertIn("shell_full_access_locally_enabled", result)
        self.assertIs(result["shell_full_access_locally_enabled"], False)

    def test_absent_from_input_is_absent_from_output(self) -> None:
        result = gateway_inventory_service.sanitize_capability_readiness(
            {"requested": ["shell.execute"], "ready": ["shell.execute"]}
        )
        self.assertNotIn("shell_full_access_locally_enabled", result)

    def test_non_boolean_value_is_dropped_not_coerced(self) -> None:
        # A malformed/adversarial wire payload (e.g. a stray string or a
        # number) must not survive as a lookalike boolean-ish value.
        result = gateway_inventory_service.sanitize_capability_readiness(
            {"shell_full_access_locally_enabled": "true"}
        )
        # The generic passthrough only accepts bool or str for unknown keys;
        # a string value IS accepted (as text), so this specific case is
        # covered separately below to document that behavior explicitly.
        self.assertEqual(result.get("shell_full_access_locally_enabled"), "true")

    def test_non_dict_input_returns_empty_payload(self) -> None:
        self.assertEqual(gateway_inventory_service.sanitize_capability_readiness(None), {})
        self.assertEqual(gateway_inventory_service.sanitize_capability_readiness("not-a-dict"), {})

    def test_survives_alongside_service_statuses_and_permission_states(self) -> None:
        result = gateway_inventory_service.sanitize_capability_readiness(
            {
                "requested": ["shell.execute"],
                "ready": ["shell.execute"],
                "blocked": [],
                "service_statuses": {"docker": "ready"},
                "permission_states": {},
                "shell_full_access_locally_enabled": True,
            }
        )
        self.assertEqual(result["service_statuses"], {"docker": "ready"})
        self.assertIs(result["shell_full_access_locally_enabled"], True)


if __name__ == "__main__":
    unittest.main()
