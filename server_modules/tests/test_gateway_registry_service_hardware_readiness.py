import unittest

from server_modules import gateway_registry_service


class ShellFullAccessLocallyEnabledPayloadTests(unittest.TestCase):
    """Hardware-readiness gap: gateway_registration_public_payload's
    runtime_access_mode/runtime_access_label have only ever reported what the
    SERVER authorized for this gateway (set at pairing time) — never whether
    the box operator's own local full_access opt-in
    (EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED) is actually on right now.
    A customer could believe full_access is live when the local half was
    never enabled, or vice versa. This covers the new top-level
    shell_full_access_locally_enabled field this payload now derives from
    the gateway's own self-reported capability_readiness (see
    empyralis-gateway/src/cloud/heartbeat-payload.ts, which threads
    config.shellFullAccessLocallyEnabled onto every heartbeat)."""

    @staticmethod
    def _registration(capability_readiness=None, runtime_access_mode="full_access"):
        metadata = {"runtime_access_mode": runtime_access_mode}
        if capability_readiness is not None:
            metadata["capability_readiness"] = capability_readiness
        return {
            "gateway_id": "gateway-1",
            "device_id": "device-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "status": "active",
            "device_trust_state": "verified",
            "metadata": metadata,
            "capabilities": [],
        }

    def test_reports_true_when_the_gateway_has_heartbeated_it_enabled(self) -> None:
        registration = self._registration({"shell_full_access_locally_enabled": True})
        payload = gateway_registry_service.gateway_registration_public_payload(registration)
        self.assertIs(payload["shell_full_access_locally_enabled"], True)
        # The server-authorized half must stay independently visible — this
        # is exactly the "distinguishable, not conflated" requirement.
        self.assertEqual(payload["runtime_access_mode"], "full_access")
        self.assertEqual(payload["runtime_access_label"], "Full Access")

    def test_reports_false_when_the_gateway_has_heartbeated_it_disabled(self) -> None:
        # The exact "believe it's on when it isn't" case: server authorizes
        # full_access for this box, but the box operator never flipped the
        # local env var. Must read false, not be swallowed or defaulted true.
        registration = self._registration({"shell_full_access_locally_enabled": False})
        payload = gateway_registry_service.gateway_registration_public_payload(registration)
        self.assertIs(payload["shell_full_access_locally_enabled"], False)
        self.assertEqual(payload["runtime_access_mode"], "full_access")

    def test_reports_none_when_the_gateway_has_never_reported_it(self) -> None:
        # An older gateway build, or one that hasn't heartbeated since this
        # field shipped — must read as "unknown" (None), never guessed.
        registration = self._registration(capability_readiness=None)
        payload = gateway_registry_service.gateway_registration_public_payload(registration)
        self.assertIsNone(payload["shell_full_access_locally_enabled"])

    def test_reports_none_when_capability_readiness_is_present_but_lacks_the_field(self) -> None:
        registration = self._registration({"service_statuses": {"docker": "ready"}})
        payload = gateway_registry_service.gateway_registration_public_payload(registration)
        self.assertIsNone(payload["shell_full_access_locally_enabled"])

    def test_malformed_non_boolean_value_degrades_to_none_not_a_crash(self) -> None:
        registration = self._registration({"shell_full_access_locally_enabled": "yes"})
        payload = gateway_registry_service.gateway_registration_public_payload(registration)
        self.assertIsNone(payload["shell_full_access_locally_enabled"])


if __name__ == "__main__":
    unittest.main()
