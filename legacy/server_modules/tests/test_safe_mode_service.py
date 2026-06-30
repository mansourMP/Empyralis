import unittest
from unittest.mock import patch

from server_modules import safe_mode_service


class SafeModeServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        safe_mode_service.reset_state_for_tests()

    def test_safe_mode_blocks_only_unsafe_capability_families(self) -> None:
        safe_mode_service.set_safe_mode(enabled=True, reason="incident")

        self.assertTrue(safe_mode_service.is_capability_disabled("computer_control.click"))
        self.assertTrue(safe_mode_service.is_capability_disabled("computer_control.notify"))
        self.assertTrue(safe_mode_service.is_capability_disabled("browser_automation.interactive"))
        self.assertTrue(safe_mode_service.is_capability_disabled("shell.execute"))
        self.assertFalse(safe_mode_service.is_capability_disabled("screenshot.capture"))
        self.assertFalse(safe_mode_service.is_capability_disabled("filesystem.read_write"))

    def test_scoped_safe_mode_and_kill_switches_are_isolated(self) -> None:
        safe_mode_service.set_safe_mode(enabled=True, workspace_id="ws-1", reason="workspace incident")
        safe_mode_service.set_kill_switch(scope="machine", enabled=True, machine_id="machine-1", reason="host issue")
        safe_mode_service.set_kill_switch(scope="capability", enabled=True, capability_id="screenshot.capture", reason="capture freeze")

        self.assertTrue(safe_mode_service.is_capability_disabled("computer_control.click", workspace_id="ws-1"))
        self.assertFalse(safe_mode_service.is_capability_disabled("computer_control.click", workspace_id="ws-2"))
        self.assertTrue(safe_mode_service.is_capability_disabled("filesystem.read_write", machine_id="machine-1"))
        self.assertFalse(safe_mode_service.is_capability_disabled("filesystem.read_write", machine_id="machine-2"))
        self.assertTrue(safe_mode_service.is_capability_disabled("screenshot.capture"))

    def test_tenant_scope_is_included_in_resolution_precedence(self) -> None:
        safe_mode_service.set_safe_mode(enabled=True, tenant_id="tenant-1", reason="tenant incident")
        safe_mode_service.set_kill_switch(scope="workspace", enabled=True, workspace_id="ws-1", reason="workspace freeze")
        safe_mode_service.set_kill_switch(scope="capability", enabled=True, capability_id="computer_control.click", reason="cap freeze")

        resolved = safe_mode_service.resolve_capability_disable_state(
            "computer_control.click",
            tenant_id="tenant-1",
            workspace_id="ws-1",
        )

        self.assertTrue(resolved["disabled"])
        self.assertEqual(resolved["scope"], "capability")
        self.assertEqual(resolved["matched_chain"][0]["scope"], "tenant")
        self.assertEqual(resolved["matched_chain"][-1]["scope"], "capability")

    def test_agent_channel_and_connector_scoped_kill_switches_resolve(self) -> None:
        safe_mode_service.set_kill_switch(scope="agent", enabled=True, workspace_id="ws-1", agent_install_id="install-1", reason="agent incident")
        safe_mode_service.set_kill_switch(scope="channel", enabled=True, workspace_id="ws-1", channel_key="telegram", endpoint_key="@bot", reason="channel incident")
        safe_mode_service.set_kill_switch(scope="connector", enabled=True, workspace_id="ws-1", connector_id="email", credential_id="cred-1", reason="connector incident")

        self.assertTrue(safe_mode_service.is_agent_disabled(workspace_id="ws-1", agent_install_id="install-1"))
        self.assertFalse(safe_mode_service.is_agent_disabled(workspace_id="ws-1", agent_install_id="install-2"))
        self.assertTrue(safe_mode_service.is_channel_disabled(workspace_id="ws-1", channel_key="telegram", endpoint_key="@bot"))
        self.assertFalse(safe_mode_service.is_channel_disabled(workspace_id="ws-1", channel_key="telegram", endpoint_key="@other"))
        self.assertTrue(safe_mode_service.is_connector_disabled(workspace_id="ws-1", connector_id="email", credential_id="cred-1"))
        self.assertFalse(safe_mode_service.is_connector_disabled(workspace_id="ws-1", connector_id="email", credential_id="cred-2"))

    def test_operator_control_report_combines_top_level_and_scoped_controls(self) -> None:
        safe_mode_service.set_kill_switch(scope="machine", enabled=True, machine_id="gw-1", reason="machine incident")
        safe_mode_service.set_kill_switch(scope="agent", enabled=True, workspace_id="ws-1", agent_install_id="install-1", reason="agent incident")
        safe_mode_service.set_kill_switch(scope="channel", enabled=True, workspace_id="ws-1", channel_key="telegram", endpoint_key="@bot", reason="channel incident")
        safe_mode_service.set_kill_switch(scope="capability", enabled=True, capability_id="computer_control.click", reason="capability incident")
        safe_mode_service.set_incident_control(scope="workspace", mode="drain", workspace_id="ws-1", reason="workspace drain")

        report = safe_mode_service.resolve_operator_control_report(
            workspace_id="ws-1",
            agent_install_id="install-1",
            gateway_id="gw-1",
            channel_key="telegram",
            endpoint_key="@bot",
            capability_id="computer_control.click",
            trace_id="trace-1",
        )

        self.assertTrue(report["top_level_emergency_gate"]["blocked"])
        self.assertEqual(report["top_level_emergency_gate"]["scope"], "machine")
        self.assertEqual(report["top_level_emergency_gate"]["trace_id"], "trace-1")
        self.assertTrue(report["safe_mode_service"]["machine_policy"]["kill_switch"]["active"])
        self.assertTrue(report["safe_mode_service"]["agent"]["active"])
        self.assertTrue(report["safe_mode_service"]["channel"]["kill_switch"]["active"])
        self.assertTrue(report["safe_mode_service"]["capability"]["disabled"])
        self.assertEqual(report["safe_mode_service"]["workspace_incident"]["mode"], "drain")

    def test_revoke_and_rotate_connector_access_record_rotation_metadata(self) -> None:
        revoked = safe_mode_service.revoke_connector_access(
            workspace_id="ws-1",
            connector_id="email",
            credential_id="cred-old",
            reason="compromised",
        )
        rotated = safe_mode_service.rotate_connector_access(
            workspace_id="ws-1",
            connector_id="email",
            previous_credential_id="cred-old",
            replacement_credential_id="cred-new",
            reason="rotated",
        )

        revoked_state = safe_mode_service.resolve_connector_disable_state(
            workspace_id="ws-1",
            connector_id="email",
            credential_id="cred-old",
        )

        self.assertEqual(revoked["action"], "revoked")
        self.assertEqual(rotated["action"], "rotated")
        self.assertTrue(revoked_state["active"])
        self.assertEqual(revoked_state["matched_chain"][-1]["metadata"]["rotated_to_credential_id"], "cred-new")

    def test_incident_controls_resolve_workspace_channel_and_connector_modes(self) -> None:
        safe_mode_service.set_incident_control(
            scope="workspace",
            mode="pause",
            workspace_id="ws-1",
            reason="workspace pause",
        )
        safe_mode_service.set_incident_control(
            scope="channel",
            mode="drain",
            workspace_id="ws-1",
            channel_key="telegram",
            endpoint_key="@bot",
            reason="channel drain",
        )
        safe_mode_service.set_incident_control(
            scope="connector",
            mode="reject",
            workspace_id="ws-1",
            connector_id="email",
            credential_id="cred-1",
            reason="connector reject",
        )

        workspace_state = safe_mode_service.resolve_workspace_incident_state(workspace_id="ws-1")
        channel_state = safe_mode_service.resolve_channel_incident_state(workspace_id="ws-1", channel_key="telegram", endpoint_key="@bot")
        connector_state = safe_mode_service.resolve_connector_incident_state(workspace_id="ws-1", connector_id="email", credential_id="cred-1")

        self.assertTrue(workspace_state["active"])
        self.assertEqual(workspace_state["mode"], "pause")
        self.assertEqual(channel_state["mode"], "drain")
        self.assertEqual(channel_state["scope"], "channel")
        self.assertEqual(connector_state["mode"], "reject")
        self.assertEqual(connector_state["scope"], "connector")

    def test_workspace_controls_survive_local_reset_when_durable_store_is_available(self) -> None:
        durable_store: dict[tuple[str, str, str, str, str], dict] = {}

        async def fake_upsert_security_control_state(**kwargs):
            key = (
                kwargs["tenant_id"],
                kwargs["workspace_id"],
                kwargs["scope_type"],
                kwargs["control_kind"],
                kwargs["scope_key"],
            )
            durable_store[key] = {
                "id": durable_store.get(key, {}).get("id", f"sctl-{len(durable_store) + 1}"),
                "tenant_id": kwargs["tenant_id"],
                "workspace_id": kwargs["workspace_id"],
                "scope_type": kwargs["scope_type"],
                "control_kind": kwargs["control_kind"],
                "scope_key": kwargs["scope_key"],
                "agent_install_id": kwargs.get("agent_install_id"),
                "channel_key": kwargs.get("channel_key"),
                "endpoint_key": kwargs.get("endpoint_key"),
                "connector_id": kwargs.get("connector_id"),
                "credential_id": kwargs.get("credential_id"),
                "enabled": kwargs["enabled"],
                "status": kwargs["status"],
                "reason": kwargs["reason"],
                "metadata": kwargs.get("metadata") or {},
            }
            return durable_store[key]

        async def fake_list_security_control_states(**kwargs):
            return [
                dict(value)
                for value in durable_store.values()
                if value["tenant_id"] == kwargs["tenant_id"]
                and value["workspace_id"] == kwargs["workspace_id"]
                and (not kwargs.get("active_only") or bool(value.get("enabled")))
            ]

        async def fake_clear_security_control_state_for_tests():
            durable_store.clear()

        with (
            patch("server_modules.safe_mode_service.control_plane_repository.upsert_security_control_state", side_effect=fake_upsert_security_control_state),
            patch("server_modules.safe_mode_service.control_plane_repository.list_security_control_states", side_effect=fake_list_security_control_states),
            patch("server_modules.safe_mode_service.control_plane_repository.clear_security_control_state_for_tests", side_effect=fake_clear_security_control_state_for_tests),
        ):
            safe_mode_service.set_kill_switch(
                scope="workspace",
                enabled=True,
                tenant_id="tenant-1",
                workspace_id="ws-1",
                reason="workspace freeze",
            )
            safe_mode_service.revoke_connector_access(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                connector_id="email",
                credential_id="cred-1",
                reason="compromised",
            )
            safe_mode_service.set_incident_control(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                scope="workspace",
                mode="drain",
                reason="draining",
            )

            safe_mode_service.reset_state_for_tests(clear_durable=False)

            self.assertTrue(
                safe_mode_service.resolve_machine_policy_status(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                )["kill_switch"]["active"]
            )
            self.assertTrue(
                safe_mode_service.resolve_connector_disable_state(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    connector_id="email",
                    credential_id="cred-1",
                )["active"]
            )
            self.assertEqual(
                safe_mode_service.resolve_workspace_incident_state(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                )["mode"],
                "drain",
            )


if __name__ == "__main__":
    unittest.main()
