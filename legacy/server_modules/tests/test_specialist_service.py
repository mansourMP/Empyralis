import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import runtime_attachment_service, specialist_service


def _install_payload() -> dict:
    return {
        "id": "install-support",
        "label": "Support Specialist",
        "status": "active",
        "enabled": True,
        "runtime_mode": "local_secure",
        "runtime_profile_id": "profile-local",
        "root_folder_uri": "file:///runtime/support",
        "tool_toggles": {
            "gmail_send": True,
            "calendar_write": False,
        },
        "folder_grants": ["/workspace/support"],
        "connector_bindings": {
            "gmail": {"enabled": True, "credential_id": "cred-gmail", "scope": "workspace"},
            "slack": {"enabled": False, "credential_id": "cred-slack", "scope": "workspace"},
        },
        "runtime_profile": {
            "id": "profile-local",
            "label": "Owner Mac mini",
            "runtime_class": "desktop_companion",
            "placement_mode": "sticky",
            "supported_capabilities": ["filesystem", "network", "background_runs"],
            "allowed_connector_scopes": ["workspace", "install"],
        },
        "metadata": {
            "specialist_mode": "owner_test",
            "agent_manifest": {
                "manifest_id": "manifest-support",
                "identity": {
                    "name": "Support Specialist",
                    "role": "Customer Service",
                    "archetype": "support_specialist",
                    "summary": "Handles customer support and follow-ups.",
                },
                "skills": [
                    {"id": "reply-customer", "enabled": True},
                    {"id": "triage-ticket", "enabled": True},
                ],
            }
        },
        "agent_definition": {
            "agent_kind": "specialist",
            "name": "Support Specialist",
        },
    }


def _memory_payload() -> dict:
    return {
        "summary": {
            "layer_count": 7,
            "accessible_layers": ["profile_memory", "specialist_scoped_memory", "cloud_synced_memory"],
            "item_counts": {"specialist_scoped_memory": 2},
        },
        "boundary_map": {
            "never_sync_by_default": ["local_private_memory"],
        },
        "layers": {
            "profile_memory": {"accessible": True, "scope": "specialist", "summary": {"policy": "install profile only", "count": 1}},
            "specialist_scoped_memory": {"accessible": True, "scope": "specialist", "summary": {"policy": "install-scoped only", "count": 2, "entry_count": 3}},
            "local_private_memory": {"accessible": False, "scope": "specialist", "summary": {"policy": "local only", "local_sources": 4}},
            "cloud_synced_memory": {"accessible": True, "scope": "specialist", "summary": {"policy": "cloud-safe only", "count": 1}},
        },
    }


def _runtime_plan() -> dict:
    return {
        "deployment_mode": "hybrid",
        "selected_attachment": {
            "attachment_id": "local_companion:profile-local",
            "attachment_kind": "local_companion",
            "label": "Owner Mac mini",
            "runtime_id": "runtime-local",
            "machine_id": "machine-local",
            "runtime_class": "desktop_companion",
            "online": True,
            "healthy": True,
            "control_state": "active",
        },
        "execution_target_selected": "local_companion",
        "selection_reason": "Local execution selected the paired local runtime attachment for scoped device work.",
        "privacy_split": {"local_private_memory_stays_local": True},
        "hybrid_rules": {"shared_sage_identity": True},
        "hybrid_policy": {"effective_sync_class": "local_only"},
        "sync_enforcement": {"effective_sync_class": "local_only"},
        "placement_enforcement": {"priority_order": ["privacy", "capabilities"]},
        "degraded_mode": {},
    }


def _timeline_payload() -> dict:
    return {
        "items": [
            {
                "id": "evt-1",
                "ts": "2026-04-10T00:00:00Z",
                "event_class": "specialist_activity",
                "action": "responded",
                "title": "Customer reply sent",
                "summary": "Resolved a refund question.",
                "review_required": False,
                "artifacts": [
                    {
                        "id": "artifact-1",
                        "name": "refund-response.md",
                        "kind": "file",
                        "path": "/workspace/support/refund-response.md",
                        "review_required": True,
                    }
                ],
            }
        ],
        "summary": {
            "review_required_count": 1,
            "by_class": {"specialist_activity": 1},
        },
    }


class SpecialistServiceTests(unittest.TestCase):
    def test_build_specialist_service_contract_keeps_scope_and_runtime_power(self) -> None:
        with (
            patch(
                "server_modules.specialist_service.unified_memory_service.build_specialist_memory_payload",
                new=AsyncMock(return_value=_memory_payload()),
            ),
            patch(
                "server_modules.specialist_service.runtime_attachment_service.resolve_install_runtime_plan",
                new=AsyncMock(return_value=_runtime_plan()),
            ),
            patch(
                "server_modules.specialist_service.activity_ledger_service.list_activity_timeline_payload",
                new=AsyncMock(return_value=_timeline_payload()),
            ),
        ):
            contract = asyncio.run(
                specialist_service.build_specialist_service_contract(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=_install_payload(),
                )
            )

        self.assertEqual(contract["service_kind"], "business_specialist")
        self.assertEqual(contract["operating_mode"]["mode"], "owner_test")
        self.assertFalse(contract["operating_mode"]["prompt_editable"])
        self.assertEqual(contract["operating_mode"]["response_audience"], "owner_operator_preview")
        self.assertFalse(contract["separation_contract"]["inherits_sage_memory_by_default"])
        self.assertTrue(contract["runtime_policy"]["same_runtime_capability_as_platform"])
        self.assertEqual(contract["runtime_policy"]["selection_status"], "ready")
        self.assertEqual(contract["runtime_policy"]["execution_target_selected"], "local_companion")
        self.assertEqual(contract["skill_scope"]["enabled_skills"], ["reply-customer", "triage-ticket"])
        self.assertEqual(contract["tool_scope"]["enabled_tools"], ["gmail_send"])
        self.assertEqual(contract["tool_scope"]["blocked_tools"], ["calendar_write"])
        self.assertEqual(contract["connector_scope"]["enabled_connectors"], ["gmail"])
        self.assertIn("specialist_scoped_memory", contract["memory_scope"]["accessible_layers"])
        self.assertEqual(contract["artifact_scope"]["review_required_count"], 1)
        self.assertEqual(contract["sage_orchestration"]["summary_channel"]["item_count"], 1)
        self.assertEqual(contract["sage_orchestration"]["artifact_channel"]["recent_artifacts"][0]["name"], "refund-response.md")
        summary = specialist_service._projection_summary(contract)
        self.assertEqual(summary["specialist_mode"], "owner_test")
        self.assertFalse(summary["mode_contract"]["config_editable"])

    def test_build_specialist_service_contract_surfaces_runtime_block_without_broadening_scope(self) -> None:
        with (
            patch(
                "server_modules.specialist_service.unified_memory_service.build_specialist_memory_payload",
                new=AsyncMock(return_value=_memory_payload()),
            ),
            patch(
                "server_modules.specialist_service.runtime_attachment_service.resolve_install_runtime_plan",
                new=AsyncMock(
                    side_effect=runtime_attachment_service.RuntimeAttachmentSelectionError(
                        "Selected runtime attachment is offline.",
                        reason="runtime_attachment_offline",
                        enforcement_state={"effective_sync_class": "local_only"},
                    )
                ),
            ),
            patch(
                "server_modules.specialist_service.activity_ledger_service.list_activity_timeline_payload",
                new=AsyncMock(return_value=_timeline_payload()),
            ),
        ):
            contract = asyncio.run(
                specialist_service.build_specialist_service_contract(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=_install_payload(),
                )
            )

        self.assertEqual(contract["runtime_policy"]["selection_status"], "blocked")
        self.assertEqual(contract["runtime_policy"]["selection_error"]["reason"], "runtime_attachment_offline")
        self.assertFalse(contract["separation_contract"]["inherits_other_specialist_memory_by_default"])
        self.assertIn("local_private_memory", contract["memory_scope"]["summary_only_layers"])

    def test_build_specialist_service_contract_surfaces_mcp_scope_for_bound_live_data_tools(self) -> None:
        install = _install_payload()
        install["metadata"]["agent_manifest"]["skills"].append({"id": "mcp:inventory-feed:lookup_stock", "enabled": True})

        with (
            patch(
                "server_modules.specialist_service.unified_memory_service.build_specialist_memory_payload",
                new=AsyncMock(return_value=_memory_payload()),
            ),
            patch(
                "server_modules.specialist_service.runtime_attachment_service.resolve_install_runtime_plan",
                new=AsyncMock(return_value=_runtime_plan()),
            ),
            patch(
                "server_modules.specialist_service.activity_ledger_service.list_activity_timeline_payload",
                new=AsyncMock(return_value=_timeline_payload()),
            ),
            patch(
                "server_modules.specialist_service.mcp_registry_service.list_workspace_mcp_servers",
                return_value=[
                    {
                        "id": "inventory-feed",
                        "label": "Inventory Feed",
                        "transport": "streamable_http",
                        "enabled": True,
                        "tool_count": 1,
                        "skill_ids": ["mcp:inventory-feed:lookup_stock"],
                        "last_synced_at": "2026-04-23T00:00:00Z",
                    }
                ],
            ),
        ):
            contract = asyncio.run(
                specialist_service.build_specialist_service_contract(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                )
            )

        self.assertEqual(contract["mcp_scope"]["bound_skill_ids"], ["mcp:inventory-feed:lookup_stock"])
        self.assertEqual(contract["mcp_scope"]["bound_servers"], ["inventory-feed"])
        self.assertEqual(contract["mcp_scope"]["server_count"], 1)
        summary = specialist_service._projection_summary(contract)
        self.assertEqual(summary["scope_summary"]["mcp_servers"], ["inventory-feed"])


if __name__ == "__main__":
    unittest.main()
