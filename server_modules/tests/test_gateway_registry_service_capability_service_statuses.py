import unittest

from server_modules import gateway_registry_service


class CapabilityServiceStatusesTests(unittest.TestCase):
    """gateway_reason_messages.py's Docker-message evidence gate reads
    service_statuses through this helper — pins that it actually extracts
    what gateway_registration_public_payload folds in from the LIVE
    capability_readiness (MAN-313's own live-merge), and degrades safely
    on anything malformed rather than raising (this only ever feeds a
    human-facing failure message)."""

    @staticmethod
    def _registration(capability_readiness=None):
        metadata = {}
        if capability_readiness is not None:
            metadata["capability_readiness"] = capability_readiness
        return {
            "gateway_id": "gateway-css-1",
            "device_id": "device-css-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "status": "active",
            "device_trust_state": "verified",
            "metadata": metadata,
            "capabilities": [],
        }

    def test_extracts_the_real_service_statuses(self) -> None:
        registration = self._registration({"service_statuses": {"docker": "ready", "postgres": "offline"}})
        statuses = gateway_registry_service.capability_service_statuses(registration)
        self.assertEqual(statuses.get("docker"), "ready")
        self.assertEqual(statuses.get("postgres"), "offline")

    def test_missing_capability_readiness_returns_empty_dict(self) -> None:
        registration = self._registration(None)
        self.assertEqual(gateway_registry_service.capability_service_statuses(registration), {})

    def test_capability_readiness_present_but_no_service_statuses_returns_empty_dict(self) -> None:
        registration = self._registration({"ready": ["shell.execute"]})
        self.assertEqual(gateway_registry_service.capability_service_statuses(registration), {})

    def test_none_registration_returns_empty_dict_not_an_exception(self) -> None:
        self.assertEqual(gateway_registry_service.capability_service_statuses(None), {})

    def test_garbage_registration_returns_empty_dict_not_an_exception(self) -> None:
        self.assertEqual(gateway_registry_service.capability_service_statuses({}), {})
        self.assertEqual(gateway_registry_service.capability_service_statuses("not-a-dict"), {})


if __name__ == "__main__":
    unittest.main()
