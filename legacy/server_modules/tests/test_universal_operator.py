import os
import unittest
from unittest.mock import AsyncMock, patch

from server_modules.agent_manifest import (
    AgentManifest,
    AgentManifestIdentity,
    AgentManifestSkillBinding,
    SAGE_GLOBAL_MANIFEST,
)
from server_modules import safe_mode_service, tool_broker
from server_modules import universal_operator


def _parts_pro_manifest(*, skills: list[str] | None = None) -> AgentManifest:
    return AgentManifest(
        manifest_id="manifest-parts-pro",
        identity=AgentManifestIdentity(
            name="Parts Pro",
            role="Inventory Specialist",
            archetype="support_specialist",
            summary="Help customers find available parts.",
        ),
        skills=[AgentManifestSkillBinding(id=skill_id, enabled=True) for skill_id in (skills or ["inventory-tool"])],
    )


class UniversalOperatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._env_patcher = patch.dict(os.environ, {"EMPYRALIS_TOOL_BROKER_SECRET": "empyralis-test-broker-secret"}, clear=False)
        self._env_patcher.start()

    def tearDown(self) -> None:
        self._env_patcher.stop()
        safe_mode_service.reset_state_for_tests()

    async def test_execute_customer_turn_uses_skill_registry_when_skill_is_bound(self):
        manifest = _parts_pro_manifest()
        with patch(
            "server_modules.universal_operator.execution_sandbox_service.execute_hosted_customer_turn",
            new=AsyncMock(return_value={
                "status": "ok",
                "reply": "I found 3 Tesla Model 3 Aero Wiper Kit in stock. Price: $24.99.",
                "artifact": {"label": "Inventory tool result"},
                "steps": [{"label": "Sandbox", "status": "done"}],
                "sandbox": {"driver": "sandbox_exec"},
                "critic": {"mode": "pass", "violations": []},
                "needed_skill_id": "inventory-tool",
            }),
        ):
            result = await universal_operator.execute_customer_turn(
                manifest=manifest,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                goal="Do you have 2022 Tesla Model 3 wipers?",
                seed_demo_if_empty=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sandbox"]["driver"], "sandbox_exec")
        self.assertEqual(result["needed_skill_id"], "inventory-tool")

    async def test_execute_customer_turn_in_process_uses_skill_registry_when_skill_is_bound(self):
        manifest = _parts_pro_manifest()
        with patch(
            "server_modules.universal_operator.tool_broker.execute_skill",
            new=AsyncMock(return_value={
                "status": "ok",
                "reply": "I found 3 Tesla Model 3 Aero Wiper Kit in stock. Price: $24.99.",
                "artifact": {"label": "Inventory tool result"},
                "steps": [{"label": "Querying workspace inventory", "detail": "1 live match", "status": "done", "kind": "connector"}],
                "items": [{"sku": "TES-WIPER-M3-2022"}],
            }),
        ):
            result = await universal_operator.execute_customer_turn_in_process(
                manifest=manifest,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                goal="Do you have 2022 Tesla Model 3 wipers?",
                seed_demo_if_empty=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["needed_skill_id"], "inventory-tool")
        self.assertEqual(result["critic"]["mode"], "pass")
        self.assertEqual(result["artifact"]["label"], "Inventory tool result")
        self.assertEqual(result["steps"][5]["label"], "Issuing capability grant")

    async def test_execute_customer_turn_in_process_emits_local_runtime_scope(self):
        manifest = _parts_pro_manifest(skills=["email-access"])
        result = await universal_operator.execute_customer_turn_in_process(
            manifest=manifest,
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            goal="Reply by email",
            runtime_mode="local_secure",
            runtime_profile={
                "id": "profile-1",
                "label": "My Mac",
                "runtime_class": "desktop_companion",
                "root_folder_uri": "/Users/mansur/Documents/Empyralis",
                "metadata": {
                    "allowed_folders": ["/Users/mansur/Desktop"],
                    "allowed_applications": ["Mail", "Safari"],
                },
            },
        )

        self.assertEqual(result["runtime_scope"]["mode"], "local_secure")
        self.assertEqual(
            result["runtime_scope"]["approved_folders"],
            ["/Users/mansur/Documents/Empyralis", "/Users/mansur/Desktop"],
        )
        self.assertEqual(result["runtime_scope"]["approved_applications"], ["Mail", "Safari"])
        self.assertIn("approved folder(s)", result["steps"][3]["detail"])

    async def test_execute_customer_turn_escalates_when_required_skill_is_not_bound(self):
        manifest = _parts_pro_manifest(skills=["email-access"])
        result = await universal_operator.execute_customer_turn_in_process(
            manifest=manifest,
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            goal="Do you have 2022 Tesla Model 3 wipers?",
        )

        self.assertIn("has not bound", result["reply"])
        self.assertEqual(result["needed_skill_id"], "inventory-tool")
        self.assertEqual(result["status"], "denied")

    def test_policy_critic_rewrites_inventory_claim_without_evidence(self):
        manifest = _parts_pro_manifest()
        critic = universal_operator.run_policy_critic(
            manifest=manifest,
            goal="Do you have 2022 Tesla Model 3 wipers?",
            draft_reply="Yes, it is in stock for $24.99.",
            skill_id="inventory-tool",
            skill_result={"status": "no_match", "items": []},
        )

        self.assertEqual(critic["mode"], "rewrite")
        self.assertIn("check the live inventory tool", critic["reply"])

    async def test_execute_customer_turn_requires_approval_for_privileged_runtime(self):
        manifest = _parts_pro_manifest()

        result = await universal_operator.execute_customer_turn(
            manifest=manifest,
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            goal="Open the inventory system on the device.",
            runtime_mode="privileged_device",
            runtime_profile={"id": "profile-1", "label": "Owner Mac"},
            privileged_runtime_approved=False,
        )

        self.assertEqual(result["status"], "approval_required")
        self.assertIn("Owner approval is required", result["reply"])
        self.assertIn("privileged_runtime_approval_required", result["critic"]["violations"])

    def test_issue_capability_token_gives_sage_broader_envelope(self):
        grant = tool_broker.issue_capability_token(
            manifest=SAGE_GLOBAL_MANIFEST,
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            runtime_mode="hosted_secure",
            runtime_scope={"driver": "sandbox_exec", "workspace_kind": "ephemeral"},
        )

        self.assertIn("inventory-tool", grant.claims["allowed_skills"])
        self.assertIn("*", grant.claims["allowed_connector_scopes"])
        self.assertEqual(grant.claims["allowed_action_classes"], ["read", "write", "execute"])

    async def test_execute_customer_turn_blocks_disabled_agent_before_execution(self):
        safe_mode_service.set_kill_switch(
            scope="agent",
            enabled=True,
            workspace_id="workspace-1",
            agent_install_id="install-1",
            reason="incident",
        )
        manifest = _parts_pro_manifest()

        with patch(
            "server_modules.universal_operator.execution_sandbox_service.execute_hosted_customer_turn",
            new=AsyncMock(),
        ) as execute_mock:
            result = await universal_operator.execute_customer_turn(
                manifest=manifest,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                goal="Do you have Tesla wipers?",
                active_agent_install_id="install-1",
            )

        self.assertEqual(result["status"], "security_blocked")
        self.assertIn("disabled", result["reply"])
        execute_mock.assert_not_awaited()

    async def test_execute_customer_turn_blocks_workspace_when_incident_pause_is_active(self):
        safe_mode_service.set_incident_control(
            scope="workspace",
            mode="pause",
            workspace_id="workspace-1",
            reason="incident pause",
        )
        manifest = _parts_pro_manifest()

        with patch(
            "server_modules.universal_operator.execution_sandbox_service.execute_hosted_customer_turn",
            new=AsyncMock(),
        ) as execute_mock:
            result = await universal_operator.execute_customer_turn(
                manifest=manifest,
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                goal="Do you have Tesla wipers?",
            )

        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["incident_mode"], "pause")
        self.assertIn("paused", result["reply"])
        execute_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
