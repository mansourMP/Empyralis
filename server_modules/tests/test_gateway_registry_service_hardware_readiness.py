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


class ExecutionBlockedConnectionStatusTests(unittest.TestCase):
    """Connectivity and execution readiness are two different facts. A box
    can have a perfectly live WSS session and a fresh heartbeat while its
    Docker sandbox is not ready (e.g. a box still on a pre-fix installer/
    image with no Docker at all — see deploy/packer/scripts/60-docker.sh),
    which means shell.execute/filesystem.read_write — the founder's stated
    launch bar for what a box must be able to do — would fail. Reporting
    that box as "online" is exactly the collapsed-two-facts-into-one-light
    bug CLAUDE.md already documents for the OpenClaw outbound socket.
    connection_status must read "execution_blocked" instead, and ONLY when
    it would otherwise have been "online" — a box that's degraded/
    reconnecting/revoked/offline already has a more urgent, honest reason
    for that label."""

    @staticmethod
    def _online_registration(capability_readiness=None):
        # session_status "connected" is threaded through
        # gateway_state_repository.get_latest_gateway_session, which this
        # unit test does not have a live session table to back — reaching
        # connection_status == "online" through the real function requires
        # mocking that lookup. See the connection_status branch tests below,
        # which patch it directly.
        metadata = {}
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

    @staticmethod
    def _online_session(capability_readiness):
        from datetime import datetime, timezone

        return {
            "session_id": "session-1",
            "status": "connected",
            "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"capability_readiness": capability_readiness} if capability_readiness else {},
        }

    def test_online_box_with_blocked_shell_execute_reads_execution_blocked(self) -> None:
        from unittest.mock import patch

        from server_modules import gateway_registry_service

        session = self._online_session(
            {"requested": ["shell.execute", "filesystem.read_write"], "ready": [], "blocked": ["shell.execute", "filesystem.read_write"]}
        )
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=session,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(
                self._online_registration()
            )
        self.assertEqual(payload["connection_status"], "execution_blocked")

    def test_online_box_with_ready_shell_execute_stays_online(self) -> None:
        from unittest.mock import patch

        from server_modules import gateway_registry_service

        session = self._online_session(
            {"requested": ["shell.execute", "filesystem.read_write"], "ready": ["shell.execute", "filesystem.read_write"], "blocked": []}
        )
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=session,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(
                self._online_registration()
            )
        self.assertEqual(payload["connection_status"], "online")

    def test_no_capability_readiness_reported_yet_stays_online_not_guessed_blocked(self) -> None:
        # An older gateway build, or one that hasn't heartbeated with this
        # field yet. Must never be guessed as blocked — that would report a
        # real capability gap that hasn't actually been observed.
        from unittest.mock import patch

        from server_modules import gateway_registry_service

        session = self._online_session(None)
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=session,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(
                self._online_registration()
            )
        self.assertEqual(payload["connection_status"], "online")

    def test_already_degraded_box_is_not_relabeled_execution_blocked(self) -> None:
        # A box with a stale heartbeat is already reporting an honest,
        # more urgent reason ("degraded") — folding execution readiness in
        # here would obscure which fact is actually true.
        from unittest.mock import patch

        from datetime import datetime, timezone, timedelta

        from server_modules import gateway_registry_service

        stale_session = {
            "session_id": "session-1",
            "status": "connected",
            "last_heartbeat_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
            "metadata": {
                "capability_readiness": {
                    "requested": ["shell.execute", "filesystem.read_write"],
                    "ready": [],
                    "blocked": ["shell.execute", "filesystem.read_write"],
                }
            },
        }
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=stale_session,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(
                self._online_registration()
            )
        self.assertEqual(payload["connection_status"], "degraded")


class FleetWideServiceReadinessTests(unittest.TestCase):
    """The fleet-wide answer to "which boxes lack Docker or OpenClaw" —
    service_readiness on gateway_registration_public_payload, reachable in
    ONE call via GET /gateway/registrations (list_workspace_gateways calls
    this per box), instead of an N+1 loop over each box's own /doctor
    endpoint.

    This also proves the underlying staleness fix: service_inventory used
    to only ever be read off registration.metadata (refreshed only by the
    rare gateway.state.update frame — usually empty/stale in production,
    a check deriving its expectations from a field nothing keeps
    populated), never off the CONTINUOUS gateway.heartbeat stream the
    live session actually carries it on — the identical staleness gap
    capability_readiness already had, fixed the identical way."""

    @staticmethod
    def _registration(stale_service_inventory=None):
        metadata: dict = {}
        if stale_service_inventory is not None:
            metadata["service_inventory"] = stale_service_inventory
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

    @staticmethod
    def _session_with_live_inventory(service_inventory):
        from datetime import datetime, timezone

        return {
            "session_id": "session-1",
            "status": "connected",
            "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"service_inventory": service_inventory} if service_inventory else {},
        }

    def test_docker_and_openclaw_both_ready(self) -> None:
        from unittest.mock import patch

        from server_modules import gateway_registry_service

        session = self._session_with_live_inventory(
            [
                {"id": "docker", "status": "ready", "detected": True},
                {"id": "openclaw", "status": "ready", "detected": True},
            ]
        )
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=session,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(
                self._registration()
            )
        self.assertEqual(payload["service_readiness"]["docker"], {"detected": True, "status": "ready", "ready": True})
        self.assertEqual(payload["service_readiness"]["openclaw"], {"detected": True, "status": "ready", "ready": True})

    def test_docker_missing_openclaw_ready_are_reported_separately(self) -> None:
        # The exact shape the coordinator's brief asks for: two DIFFERENT
        # facts, not collapsed into one light.
        from unittest.mock import patch

        from server_modules import gateway_registry_service

        session = self._session_with_live_inventory(
            [
                {"id": "docker", "status": "missing", "detected": False},
                {"id": "openclaw", "status": "ready", "detected": True},
            ]
        )
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=session,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(
                self._registration()
            )
        self.assertFalse(payload["service_readiness"]["docker"]["ready"])
        self.assertEqual(payload["service_readiness"]["docker"]["status"], "missing")
        self.assertTrue(payload["service_readiness"]["openclaw"]["ready"])

    def test_no_inventory_ever_reported_reads_unknown_not_guessed(self) -> None:
        from unittest.mock import patch

        from server_modules import gateway_registry_service

        session = self._session_with_live_inventory(None)
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=session,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(
                self._registration()
            )
        self.assertEqual(payload["service_readiness"]["docker"]["status"], "unknown")
        self.assertFalse(payload["service_readiness"]["docker"]["ready"])
        self.assertEqual(payload["service_readiness"]["openclaw"]["status"], "unknown")

    def test_live_heartbeat_inventory_wins_over_stale_registration_metadata(self) -> None:
        # THE staleness fix, proved directly: the registration's own
        # metadata.service_inventory (only ever refreshed by the rare
        # gateway.state.update frame) says docker is missing; the live
        # session's heartbeat-sourced copy says it's ready. The live copy
        # must win — it is what the box is ACTUALLY reporting right now.
        from unittest.mock import patch

        from server_modules import gateway_registry_service

        stale_registration = self._registration(
            stale_service_inventory=[{"id": "docker", "status": "missing", "detected": False}]
        )
        live_session = self._session_with_live_inventory(
            [{"id": "docker", "status": "ready", "detected": True}]
        )
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=live_session,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(stale_registration)
        self.assertTrue(payload["service_readiness"]["docker"]["ready"])

    def test_no_live_session_falls_back_to_registration_metadata_not_a_crash(self) -> None:
        # An older gateway build, or a box that has never connected since
        # this shipped — no live session at all. Must degrade to whatever
        # the registration's own (possibly stale) metadata says, never
        # raise and never silently report "ready".
        from unittest.mock import patch

        from server_modules import gateway_registry_service

        registration = self._registration(
            stale_service_inventory=[{"id": "docker", "status": "ready", "detected": True}]
        )
        with patch(
            "server_modules.gateway_state_repository.get_latest_gateway_session",
            return_value=None,
        ):
            payload = gateway_registry_service.gateway_registration_public_payload(registration)
        self.assertTrue(payload["service_readiness"]["docker"]["ready"])


if __name__ == "__main__":
    unittest.main()
