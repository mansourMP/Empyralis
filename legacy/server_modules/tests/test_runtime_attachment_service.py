import asyncio
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import entitlements_service, hybrid_policy_service, runtime_attachment_service


class RuntimeAttachmentServiceTests(unittest.TestCase):
    def test_list_workspace_runtime_attachments_reads_profiles_without_hot_path_seeding(self) -> None:
        runtime_attachment_service._RUNTIME_ATTACHMENTS_CACHE.clear()
        with (
            patch(
                "server_modules.runtime_attachment_service.agent_registry_repository.list_runtime_profiles",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "profile-cloud",
                            "slug": "empyralis-cloud",
                            "label": "Empyralis Cloud",
                            "runtime_class": "cloud_worker",
                            "status": "active",
                        }
                    ]
                ),
            ) as profiles_mock,
            patch(
                "server_modules.runtime_attachment_service.run_state_repository.list_fleet_workers",
                new=AsyncMock(return_value=[]),
            ),
        ):
            inventory = asyncio.run(
                runtime_attachment_service.list_workspace_runtime_attachments(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual(inventory["deployment_mode"], "cloud_only")
        self.assertEqual(profiles_mock.await_args.kwargs["seed_if_missing"], False)

    def test_list_workspace_runtime_targets_reuses_snapshot_for_identical_inventory(self) -> None:
        runtime_attachment_service._RUNTIME_TARGETS_CACHE.clear()
        inventory = {
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "_snapshot_version": "runtime-snapshot-1",
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "online": True,
                    "healthy": True,
                    "supports_runtime_modes": ["hosted_secure"],
                }
            ],
        }

        with patch(
            "server_modules.runtime_attachment_service.build_workspace_runtime_targets",
            wraps=runtime_attachment_service.build_workspace_runtime_targets,
        ) as build_mock:
            first = asyncio.run(
                runtime_attachment_service.list_workspace_runtime_targets(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    inventory=inventory,
                )
            )
            second = asyncio.run(
                runtime_attachment_service.list_workspace_runtime_targets(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    inventory=inventory,
                )
            )

        self.assertEqual(first["default_target_id"], second["default_target_id"])
        self.assertEqual(build_mock.call_count, 1)

    def test_list_workspace_runtime_attachments_reports_hybrid_topology(self) -> None:
        runtime_profiles = [
            {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "label": "Empyralis Cloud",
                "runtime_class": "cloud_worker",
                "status": "active",
                "runtime_id": "runtime-cloud",
            },
            {
                "id": "profile-local",
                "slug": "my-local-mac",
                "label": "My Local Mac",
                "runtime_class": "desktop_companion",
                "status": "active",
                "runtime_id": "runtime-local",
                "machine_id": "machine-local",
            },
        ]
        fleet_workers = [
            {
                "worker_id": "runtime-local",
                "runtime_id": "runtime-local",
                "machine_id": "machine-local",
                "runtime_type": "local_companion",
                "display_name": "Mansur Mac mini",
                "online": True,
                "status": "idle",
                "control_state": "active",
                "execution_targets": ["local_companion"],
                "capabilities": ["computer_control", "file_access"],
            }
        ]

        inventory = asyncio.run(
            runtime_attachment_service.list_workspace_runtime_attachments(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_profiles=runtime_profiles,
                fleet_workers=fleet_workers,
            )
        )

        self.assertEqual(inventory["deployment_mode"], "hybrid")
        self.assertEqual(len(inventory["attachments"]), 2)
        self.assertEqual(inventory["attachments"][0]["attachment_kind"], "managed_cloud")
        self.assertEqual(inventory["attachments"][1]["attachment_kind"], "local_companion")
        self.assertTrue(inventory["attachments"][1]["healthy"])

    def test_list_workspace_runtime_attachments_supports_local_only_topology(self) -> None:
        runtime_profiles = [
            {
                "id": "profile-local",
                "slug": "my-local-mac",
                "label": "My Local Mac",
                "runtime_class": "desktop_companion",
                "status": "active",
                "runtime_id": "runtime-local",
                "machine_id": "machine-local",
            }
        ]
        fleet_workers = [
            {
                "worker_id": "runtime-local",
                "runtime_id": "runtime-local",
                "machine_id": "machine-local",
                "runtime_type": "local_companion",
                "display_name": "Local Box",
                "online": True,
                "status": "idle",
                "control_state": "active",
                "execution_targets": ["local_companion"],
            }
        ]

        inventory = asyncio.run(
            runtime_attachment_service.list_workspace_runtime_attachments(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_profiles=runtime_profiles,
                fleet_workers=fleet_workers,
            )
        )

        self.assertEqual(inventory["deployment_mode"], "local_only")
        self.assertEqual([item["attachment_kind"] for item in inventory["attachments"]], ["local_companion"])

    def test_list_workspace_runtime_attachments_includes_paired_gateway_truth(self) -> None:
        runtime_attachment_service._RUNTIME_ATTACHMENTS_CACHE.clear()
        with (
            patch(
                "server_modules.runtime_attachment_service.agent_registry_repository.list_runtime_profiles",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.runtime_attachment_service.run_state_repository.list_fleet_workers",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "server_modules.runtime_attachment_service.gateway_state_repository.list_workspace_gateway_registrations",
                return_value=[
                    {
                        "gateway_id": "gateway-local-1",
                        "device_id": "device-local-1",
                        "tenant_id": "tenant-1",
                        "workspace_id": "workspace-1",
                        "user_id": "user-1",
                        "status": "active",
                        "device_trust_state": "verified",
                        "display_name": "Mansur Mac",
                        "platform": "macos-arm64",
                        "metadata": {"health_state": "online"},
                        "capabilities": ["screen.read"],
                        "journal_cursor": 0,
                        "checkpoint_cursor": 0,
                        "created_at": "2026-04-22T12:00:00Z",
                        "updated_at": "2026-04-22T12:00:00Z",
                        "last_seen_at": "2026-04-22T12:00:00Z",
                        "last_heartbeat_at": "2026-04-22T12:00:00Z",
                        "token_rotated_at": "2026-04-22T12:00:00Z",
                        "revoked_at": None,
                        "revoked_reason": None,
                    }
                ],
            ),
            patch(
                "server_modules.runtime_attachment_service.gateway_state_repository.GATEWAY_STATE_DB_FILE",
                Path(__file__),
            ),
            patch(
                "server_modules.runtime_attachment_service.auth.get_user_device_link",
                return_value={
                    "device_id": "device-local-1",
                    "user_id": "user-1",
                    "workspace_id": "workspace-1",
                    "channel": "local_runtime_companion",
                    "status": "active",
                    "trust_state": "verified",
                    "metadata": {"gateway_id": "gateway-local-1"},
                },
            ),
        ):
            inventory = asyncio.run(
                runtime_attachment_service.list_workspace_runtime_attachments(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        self.assertEqual(inventory["deployment_mode"], "local_only")
        self.assertEqual(len(inventory["attachments"]), 1)
        attachment = inventory["attachments"][0]
        self.assertEqual(attachment["attachment_kind"], "local_companion")
        self.assertEqual(attachment["gateway_identity"]["gateway_id"], "gateway-local-1")
        self.assertEqual(attachment["gateway_identity"]["device_id"], "device-local-1")
        self.assertTrue(attachment["trust_model"]["gateway_identity_bound"])

    def test_gateway_attachment_projects_heartbeat_ready_capabilities(self) -> None:
        runtime_attachment_service._RUNTIME_ATTACHMENTS_CACHE.clear()
        registration = {
            "gateway_id": "gateway-local-1",
            "device_id": "device-local-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "status": "active",
            "device_trust_state": "verified",
            "display_name": "Mansur Mac",
            "platform": "macos-arm64",
            "metadata": {
                "health_state": "online",
                "capability_readiness": {
                    "requested": ["browser.session.start", "external_agent_proxy"],
                    "ready": ["browser.session.start", "external_agent_proxy"],
                },
            },
            "capabilities": ["browser.session.start"],
            "last_seen_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_heartbeat_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        with (
            patch("server_modules.runtime_attachment_service.agent_registry_repository.list_runtime_profiles", new=AsyncMock(return_value=[])),
            patch("server_modules.runtime_attachment_service.run_state_repository.list_fleet_workers", new=AsyncMock(return_value=[])),
            patch(
                "server_modules.runtime_attachment_service.gateway_state_repository.list_workspace_gateway_registrations",
                return_value=[registration],
            ),
            patch("server_modules.runtime_attachment_service.gateway_state_repository.GATEWAY_STATE_DB_FILE", Path(__file__)),
            patch("server_modules.gateway_protocol_service.gateway_connection_is_live", return_value=True),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "connection_status": "online",
                    "heartbeat_fresh": True,
                    "reported_health_state": "online",
                    "heartbeat_age_seconds": 1,
                    "latest_session_status": "connected",
                },
            ),
            patch("server_modules.runtime_attachment_service.auth.get_user_device_link", return_value=None),
        ):
            inventory = asyncio.run(
                runtime_attachment_service.list_workspace_runtime_attachments(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        attachment = inventory["attachments"][0]
        self.assertIn("external_agent_proxy", attachment["capabilities"])
        self.assertIn("external_agent_proxy", attachment["capability_readiness"]["ready"])

    def test_gateway_attachment_requires_live_fresh_session_for_this_device(self) -> None:
        runtime_attachment_service._RUNTIME_ATTACHMENTS_CACHE.clear()
        registration = {
            "gateway_id": "gateway-local-1",
            "device_id": "device-local-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "status": "active",
            "device_trust_state": "verified",
            "display_name": "Mansur Mac",
            "platform": "macos-arm64",
            "metadata": {
                "health_state": "online",
                "capability_readiness": {"ready": ["screen.read"]},
            },
            "capabilities": ["screen.read"],
            "last_seen_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_heartbeat_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        with (
            patch("server_modules.runtime_attachment_service.agent_registry_repository.list_runtime_profiles", new=AsyncMock(return_value=[])),
            patch("server_modules.runtime_attachment_service.run_state_repository.list_fleet_workers", new=AsyncMock(return_value=[])),
            patch(
                "server_modules.runtime_attachment_service.gateway_state_repository.list_workspace_gateway_registrations",
                return_value=[registration],
            ),
            patch("server_modules.runtime_attachment_service.gateway_state_repository.GATEWAY_STATE_DB_FILE", Path(__file__)),
            patch("server_modules.gateway_protocol_service.gateway_connection_is_live", return_value=False),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "connection_status": "online",
                    "heartbeat_fresh": True,
                    "reported_health_state": "online",
                    "heartbeat_age_seconds": 1,
                    "latest_session_status": "connected",
                },
            ),
            patch("server_modules.runtime_attachment_service.auth.get_user_device_link", return_value=None),
        ):
            inventory = asyncio.run(
                runtime_attachment_service.list_workspace_runtime_attachments(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        attachment = inventory["attachments"][0]
        self.assertFalse(attachment["online"])
        self.assertFalse(attachment["healthy"])
        self.assertEqual(attachment["status_reason"], "gateway_offline")
        self.assertFalse(attachment["target_selection"]["selectable"])

    def test_gateway_attachment_reports_online_when_target_selection_is_fresh(self) -> None:
        runtime_attachment_service._RUNTIME_ATTACHMENTS_CACHE.clear()
        registration = {
            "gateway_id": "gateway-local-1",
            "device_id": "device-local-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "status": "active",
            "device_trust_state": "verified",
            "display_name": "Mansur Mac",
            "platform": "macos-arm64",
            "metadata": {
                "health_state": "online",
                "capability_readiness": {"ready": ["screen.read"]},
            },
            "capabilities": ["screen.read"],
            "last_seen_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_heartbeat_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        with (
            patch("server_modules.runtime_attachment_service.agent_registry_repository.list_runtime_profiles", new=AsyncMock(return_value=[])),
            patch("server_modules.runtime_attachment_service.run_state_repository.list_fleet_workers", new=AsyncMock(return_value=[])),
            patch(
                "server_modules.runtime_attachment_service.gateway_state_repository.list_workspace_gateway_registrations",
                return_value=[registration],
            ),
            patch("server_modules.runtime_attachment_service.gateway_state_repository.GATEWAY_STATE_DB_FILE", Path(__file__)),
            patch("server_modules.gateway_protocol_service.gateway_connection_is_live", return_value=True),
            patch(
                "server_modules.gateway_registry_service.gateway_registration_public_payload",
                return_value={
                    "connection_status": "online",
                    "heartbeat_fresh": True,
                    "reported_health_state": "online",
                    "heartbeat_age_seconds": 1,
                    "latest_session_status": "connected",
                },
            ),
            patch("server_modules.runtime_attachment_service.auth.get_user_device_link", return_value=None),
        ):
            inventory = asyncio.run(
                runtime_attachment_service.list_workspace_runtime_attachments(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            )

        attachment = inventory["attachments"][0]
        self.assertTrue(attachment["online"])
        self.assertTrue(attachment["healthy"])
        self.assertEqual(attachment["target_selection"]["connection_status"], "online")

    def test_list_workspace_runtime_attachments_maps_worker_only_local_runtime_to_local_companion(self) -> None:
        inventory = asyncio.run(
            runtime_attachment_service.list_workspace_runtime_attachments(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_profiles=[],
                fleet_workers=[
                    {
                        "runtime_id": "worker-local-1",
                        "runtime_type": "local",
                        "display_name": "Worker Local 1",
                        "online": True,
                        "status": "idle",
                        "control_state": "active",
                        "execution_targets": ["local"],
                        "capabilities": ["filesystem.read_write", "local.worker"],
                    }
                ],
            )
        )

        self.assertEqual(inventory["deployment_mode"], "local_only")
        self.assertEqual(len(inventory["attachments"]), 1)
        self.assertEqual(inventory["attachments"][0]["attachment_kind"], "local_companion")
        self.assertEqual(inventory["attachments"][0]["runtime_class"], "desktop_companion")
        self.assertEqual(
            inventory["attachments"][0]["supports_runtime_modes"],
            ["local_secure", "privileged_device"],
        )

    def test_list_workspace_runtime_attachments_infers_online_from_recent_worker_heartbeat(self) -> None:
        inventory = asyncio.run(
            runtime_attachment_service.list_workspace_runtime_attachments(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_profiles=[],
                fleet_workers=[
                    {
                        "runtime_id": "worker-local-2",
                        "runtime_type": "local",
                        "display_name": "Worker Local 2",
                        "online": False,
                        "status": "busy",
                        "control_state": "active",
                        "execution_targets": ["local"],
                        "capabilities": ["filesystem.read_write", "local.worker"],
                        "lease_seconds": 30,
                        "last_heartbeat_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    }
                ],
            )
        )

        self.assertTrue(inventory["attachments"][0]["online"])
        self.assertTrue(inventory["attachments"][0]["healthy"])

    def test_build_workspace_runtime_targets_projects_cloud_default_for_hybrid_workspace(self) -> None:
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "online": True,
                    "healthy": True,
                    "supports_runtime_modes": ["hosted_secure"],
                },
                {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                    "online": True,
                    "healthy": True,
                    "supports_runtime_modes": ["local_secure", "privileged_device"],
                },
            ],
        }

        payload = runtime_attachment_service.build_workspace_runtime_targets(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            inventory=inventory,
        )

        self.assertEqual(payload["default_target_id"], "cloud_default")
        self.assertEqual(payload["routing_contract"]["mobile_entry_mode"], "platform_first")
        self.assertFalse(payload["routing_contract"]["direct_mobile_lan_default"])
        targets = {item["target_id"]: item for item in payload["targets"]}
        self.assertTrue(targets["cloud_default"]["default_for_workspace"])
        self.assertEqual(targets["cloud_default"]["label"], "Cloud")
        self.assertEqual(targets["cloud_default"]["public_label"], "Cloud")
        self.assertEqual(targets["cloud_default"]["status"], "live")
        self.assertEqual(targets["local_companion"]["connection_mode"], "platform_relay")
        self.assertEqual(targets["local_companion"]["canonical_target_id"], "user_device_gateway")
        self.assertEqual(targets["local_companion"]["label"], "This Device")
        self.assertEqual(targets["local_companion"]["public_label"], "This Device")
        self.assertEqual(targets["local_companion"]["runtime_fabric_target"], "user_device_gateway")
        self.assertEqual(targets["local_companion"]["hardware_edge"], "empyralis_gateway")
        self.assertTrue(targets["local_companion"]["runtime_session_required"])
        self.assertEqual(targets["local_companion"]["default_runtime_access_mode"], "full_access")
        self.assertFalse(targets["local_companion"]["direct_mobile_connection_required"])
        local_modes = {item["id"]: item for item in targets["local_companion"]["execution_modes"]}
        self.assertTrue(local_modes["full_access"]["available"])
        self.assertTrue(local_modes["full_access"]["requires_owner_approval"])
        self.assertEqual(
            payload["routing_contract"]["canonical_runtime_targets"],
            [
                "cloud_default",
                "user_device_gateway",
                "empyralis_cloud_computer",
                "self_hosted_node",
            ],
        )
        self.assertTrue(payload["routing_contract"]["hardware_actions_require_runtime_session"])
        self.assertTrue(payload["routing_contract"]["hardware_actions_emit_transparency_events"])
        self.assertTrue(payload["routing_contract"]["legacy_local_companion_deprecated_for_machine_control"])
        self.assertEqual(
            payload["routing_contract"]["public_runtime_target_labels"],
            {
                "cloud_default": "Cloud",
                "user_device_gateway": "This Device",
                "empyralis_cloud_computer": "Cloud Computer",
                "self_hosted_node": "Server/VPS",
            },
        )

    def test_public_runtime_target_labels_hide_legacy_target_ids(self) -> None:
        self.assertEqual(runtime_attachment_service.public_runtime_target_label("cloud_default"), "Cloud")
        self.assertEqual(runtime_attachment_service.public_runtime_target_label("local_companion"), "This Device")
        self.assertEqual(runtime_attachment_service.public_runtime_target_label("user_device_gateway"), "This Device")
        self.assertEqual(runtime_attachment_service.public_runtime_target_label("sage_cloud_computer"), "Cloud Computer")
        self.assertEqual(runtime_attachment_service.public_runtime_target_label("empyralis_cloud_computer"), "Cloud Computer")
        self.assertEqual(runtime_attachment_service.public_runtime_target_label("self_host_runtime"), "Server/VPS")
        self.assertEqual(runtime_attachment_service.public_runtime_target_label("self_hosted_node"), "Server/VPS")

    def test_list_workspace_runtime_attachments_maps_cloud_computer_profile(self) -> None:
        inventory = asyncio.run(
            runtime_attachment_service.list_workspace_runtime_attachments(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_profiles=[
                    {
                        "id": "profile-cloud",
                        "slug": "empyralis-cloud",
                        "label": "Empyralis Cloud",
                        "runtime_class": "cloud_worker",
                        "status": "active",
                    },
                    {
                        "id": "profile-cloud-computer",
                        "slug": "sage-cloud-computer",
                        "label": "Sage Cloud Computer",
                        "runtime_class": "cloud_computer",
                        "status": "active",
                        "runtime_id": "cloud-computer-1",
                        "supported_capabilities": ["cloud.computer", "browser.automation", "terminal.exec"],
                    },
                ],
                fleet_workers=[
                    {
                        "worker_id": "cloud-computer-1",
                        "runtime_id": "cloud-computer-1",
                        "runtime_type": "cloud_computer",
                        "display_name": "Sage Cloud Computer Session",
                        "online": True,
                        "status": "idle",
                        "control_state": "active",
                        "execution_targets": ["cloud_computer"],
                        "capabilities": ["cloud.computer", "browser.automation", "terminal.exec"],
                    }
                ],
            )
        )

        self.assertEqual(inventory["deployment_mode"], "cloud_only")
        attachments = {item["attachment_kind"]: item for item in inventory["attachments"]}
        self.assertEqual(attachments["cloud_computer"]["runtime_class"], "cloud_computer")
        self.assertTrue(attachments["cloud_computer"]["healthy"])
        self.assertEqual(attachments["cloud_computer"]["trust_model"]["trust_tier"], "hosted_cloud_computer")
        self.assertTrue(attachments["cloud_computer"]["lifecycle"]["metered"])
        self.assertTrue(attachments["cloud_computer"]["privacy_posture"]["personal_device_access_requires_gateway"])

    def test_build_workspace_runtime_targets_projects_cloud_computer_as_optional_metered_target(self) -> None:
        payload = runtime_attachment_service.build_workspace_runtime_targets(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            inventory={
                "deployment_mode": "cloud_only",
                "attachments": [
                    {
                        "attachment_id": "managed_cloud:profile-cloud",
                        "attachment_kind": "managed_cloud",
                        "online": True,
                        "healthy": True,
                        "supports_runtime_modes": ["hosted_secure"],
                    },
                    {
                        "attachment_id": "cloud_computer:profile-cloud-computer",
                        "attachment_kind": "cloud_computer",
                        "online": True,
                        "healthy": True,
                        "supports_runtime_modes": ["hosted_secure"],
                    },
                ],
            },
        )

        self.assertEqual(payload["default_target_id"], "cloud_default")
        self.assertTrue(payload["routing_contract"]["cloud_computer_requires_explicit_selection"])
        self.assertTrue(payload["routing_contract"]["cloud_computer_metered"])
        self.assertFalse(payload["routing_contract"]["agent_definition_allocates_runtime"])
        self.assertTrue(payload["routing_contract"]["runtime_placement_separate_from_computer_automation"])
        self.assertEqual(payload["routing_contract"]["studio_agent_computer_automation_default"], "disabled")
        self.assertTrue(payload["routing_contract"]["registered_agents_active_agents_queue_when_over_capacity"])
        targets = {item["target_id"]: item for item in payload["targets"]}
        self.assertTrue(targets["sage_cloud_computer"]["available"])
        self.assertEqual(targets["sage_cloud_computer"]["label"], "Cloud Computer")
        self.assertEqual(targets["sage_cloud_computer"]["canonical_target_id"], "empyralis_cloud_computer")
        self.assertTrue(targets["sage_cloud_computer"]["metered"])
        self.assertTrue(targets["sage_cloud_computer"]["requires_explicit_selection"])
        self.assertFalse(targets["sage_cloud_computer"]["default_for_workspace"])
        cloud_modes = {item["id"]: item for item in targets["sage_cloud_computer"]["execution_modes"]}
        self.assertTrue(cloud_modes["autopilot"]["available"])
        self.assertTrue(cloud_modes["full_access"]["available"])
        self.assertEqual(payload["routing_contract"]["full_access_scope"], "dedicated_runtime_targets")

    def test_build_workspace_runtime_targets_never_defaults_to_cloud_computer(self) -> None:
        payload = runtime_attachment_service.build_workspace_runtime_targets(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            inventory={
                "deployment_mode": "cloud_only",
                "attachments": [
                    {
                        "attachment_id": "cloud_computer:profile-cloud-computer",
                        "attachment_kind": "cloud_computer",
                        "online": True,
                        "healthy": True,
                        "supports_runtime_modes": ["hosted_secure"],
                    },
                ],
            },
        )

        self.assertEqual(payload["default_target_id"], "cloud_default")
        targets = {item["target_id"]: item for item in payload["targets"]}
        self.assertTrue(targets["sage_cloud_computer"]["available"])
        self.assertFalse(targets["sage_cloud_computer"]["default_for_workspace"])

    def test_runtime_usage_credit_event_meters_cloud_computer_runtime(self) -> None:
        event = runtime_attachment_service.build_runtime_usage_credit_event(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            surface="sage",
            runtime_target="sage_cloud_computer",
            session_id="session-1",
            started_at="2026-05-20T00:00:00Z",
            ended_at="2026-05-20T00:05:00Z",
            active_seconds=300,
            billable_seconds=240,
            estimated_cost_usd=0.08,
            thread_id="thread-1",
            metadata={"domain_allowlist": ["example.com"]},
        )

        self.assertEqual(event["credit_type"], "computer_runtime")
        self.assertEqual(event["credit_item_type"], "virtual_desktop_minutes")
        self.assertEqual(event["credit_quantity"], 4.0)
        self.assertEqual(event["runtime_target"], "sage_cloud_computer")
        self.assertEqual(event["canonical_runtime_target"], "empyralis_cloud_computer")
        self.assertEqual(event["runtime_fabric_target"], "empyralis_cloud_computer")
        self.assertEqual(event["surface"], "sage")
        self.assertEqual(event["thread_id"], "thread-1")
        self.assertEqual(event["metadata"]["runtime_type"], "cloud_computer")
        self.assertEqual(event["metadata"]["canonical_runtime_target"], "empyralis_cloud_computer")
        unified = event["metadata"]["unified_credit_ledger_event"]
        self.assertEqual(unified["surface"], "sage")
        self.assertEqual(unified["credit_type"], "computer_runtime")
        self.assertEqual(unified["runtime_target"], "sage_cloud_computer")
        self.assertEqual(unified["payer"], "platform_credits")
        self.assertEqual(unified["thread_id"], "thread-1")
        self.assertIn("payments", event["metadata"]["sensitive_actions_require_confirmation"])

    def test_runtime_usage_credit_event_accepts_canonical_runtime_target_alias(self) -> None:
        event = runtime_attachment_service.build_runtime_usage_credit_event(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            surface="sage",
            runtime_target="empyralis_cloud_computer",
            session_id="session-1",
            started_at="2026-05-20T00:00:00Z",
            ended_at="2026-05-20T00:02:00Z",
            active_seconds=120,
            billable_seconds=60,
        )

        self.assertEqual(event["runtime_target"], "sage_cloud_computer")
        self.assertEqual(event["canonical_runtime_target"], "empyralis_cloud_computer")
        self.assertEqual(event["metadata"]["runtime_fabric_target"], "empyralis_cloud_computer")

    def test_runtime_usage_credit_event_requires_session_and_valid_billable_seconds(self) -> None:
        with self.assertRaises(ValueError) as missing_session:
            runtime_attachment_service.build_runtime_usage_credit_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                surface="studio",
                runtime_target="sage_cloud_computer",
                session_id="",
                started_at="2026-05-20T00:00:00Z",
                ended_at="2026-05-20T00:05:00Z",
                active_seconds=300,
                billable_seconds=240,
            )
        self.assertIn("session_id", str(missing_session.exception))

        with self.assertRaises(ValueError) as invalid_billable:
            runtime_attachment_service.build_runtime_usage_credit_event(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                surface="studio",
                runtime_target="sage_cloud_computer",
                session_id="session-1",
                started_at="2026-05-20T00:00:00Z",
                ended_at="2026-05-20T00:05:00Z",
                active_seconds=120,
                billable_seconds=240,
            )
        self.assertIn("billable_seconds", str(invalid_billable.exception))

    def test_build_workspace_runtime_targets_projects_self_host_without_identity_fork(self) -> None:
        inventory = {
            "deployment_mode": "self_hosted_business",
            "attachments": [
                {
                    "attachment_id": "self_hosted_business_node:profile-self-host",
                    "attachment_kind": "self_hosted_business_node",
                    "workspace_id": "workspace-1",
                    "runtime_node_id": "runtime-self-host",
                    "node_kind": "mac_mini",
                    "self_hosted_node_status": "online",
                    "owner_approved": True,
                    "max_concurrent_sessions": 2,
                    "capabilities": ["shell.execute", "file_access"],
                    "online": True,
                    "healthy": True,
                    "supports_runtime_modes": ["hosted_secure"],
                }
            ],
        }

        payload = runtime_attachment_service.build_workspace_runtime_targets(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            inventory=inventory,
        )

        self.assertEqual(payload["default_target_id"], "self_host_runtime")
        self.assertTrue(payload["routing_contract"]["supports_self_host_without_identity_fork"])
        targets = {item["target_id"]: item for item in payload["targets"]}
        self.assertTrue(targets["self_host_runtime"]["default_for_workspace"])
        self.assertTrue(targets["self_host_runtime"]["workspace_scoped_identity"])
        self.assertEqual(targets["self_host_runtime"]["execution_target"], "cloud")

    def test_list_workspace_runtime_attachments_treats_managed_cloud_plus_self_host_as_hybrid(self) -> None:
        runtime_profiles = [
            {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "label": "Empyralis Cloud",
                "runtime_class": "cloud_worker",
                "status": "active",
                "runtime_id": "runtime-cloud",
            },
            {
                "id": "profile-enterprise",
                "slug": "workspace-secure-node",
                "label": "Workspace Secure Node",
                "runtime_class": "self_hosted_business_node",
                "status": "active",
                "runtime_id": "runtime-enterprise",
                "machine_id": "machine-enterprise",
            },
        ]

        inventory = asyncio.run(
            runtime_attachment_service.list_workspace_runtime_attachments(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_profiles=runtime_profiles,
                fleet_workers=[],
            )
        )

        self.assertEqual(inventory["deployment_mode"], "hybrid")
        self.assertEqual(
            [item["attachment_kind"] for item in inventory["attachments"]],
            ["managed_cloud", "self_hosted_business_node"],
        )

    def test_build_workspace_runtime_targets_keeps_cloud_default_when_self_host_is_optional(self) -> None:
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "online": True,
                    "healthy": True,
                    "supports_runtime_modes": ["hosted_secure"],
                },
                {
                    "attachment_id": "self_hosted_business_node:profile-enterprise",
                    "attachment_kind": "self_hosted_business_node",
                    "workspace_id": "workspace-1",
                    "runtime_node_id": "runtime-enterprise",
                    "node_kind": "linux_server",
                    "self_hosted_node_status": "online",
                    "owner_approved": True,
                    "max_concurrent_sessions": 2,
                    "capabilities": ["shell.execute", "file_access"],
                    "online": True,
                    "healthy": True,
                    "supports_runtime_modes": ["hosted_secure"],
                },
            ],
        }

        payload = runtime_attachment_service.build_workspace_runtime_targets(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            inventory=inventory,
        )

        self.assertEqual(payload["default_target_id"], "cloud_default")
        self.assertEqual(payload["routing_contract"]["business_default_mode"], "cloud_first")
        self.assertTrue(payload["routing_contract"]["business_private_runtime_optional"])
        self.assertTrue(payload["routing_contract"]["self_host_runtime_requires_explicit_selection"])
        targets = {item["target_id"]: item for item in payload["targets"]}
        self.assertTrue(targets["cloud_default"]["default_for_workspace"])
        self.assertFalse(targets["self_host_runtime"]["default_for_workspace"])

    def test_resolve_install_runtime_plan_selects_local_attachment_for_local_secure(self) -> None:
        install = {
            "runtime_mode": "local_secure",
            "runtime_profile": {
                "id": "profile-local",
                "slug": "my-local-mac",
                "runtime_class": "desktop_companion",
                "runtime_id": "runtime-local",
                "machine_id": "machine-local",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "my-local-mac",
                    "allowed_runtime_classes": ["desktop_companion"],
                }
            },
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                    "trust_model": {"trust_tier": "hosted_cloud_computer"},
                },
                {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                    "runtime_class": "desktop_companion",
                    "runtime_profile_id": "profile-local",
                    "runtime_profile_slug": "my-local-mac",
                    "runtime_id": "runtime-local",
                    "machine_id": "machine-local",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
            ],
        }

        plan = asyncio.run(
            runtime_attachment_service.resolve_install_runtime_plan(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                install=install,
                inventory=inventory,
                workspace={"metadata": {"billing": {"plan": "pro"}}},
            )
        )

        self.assertEqual(plan["execution_target_selected"], "local_companion")
        self.assertEqual(plan["machine_target"], "machine-local")
        self.assertEqual(plan["selected_attachment"]["attachment_kind"], "local_companion")
        self.assertTrue(plan["privacy_split"]["local_private_memory_stays_local"])
        self.assertFalse(plan["state_layer_policy"]["cross_install_private_memory_allowed"])
        self.assertEqual(plan["state_layer_policy"]["artifacts_history"]["cross_install_exchange_mode"], "artifacts_only")

    def test_resolve_install_runtime_plan_rejects_offline_local_attachment(self) -> None:
        install = {
            "runtime_mode": "local_secure",
            "runtime_profile": {
                "id": "profile-local",
                "runtime_class": "desktop_companion",
                "runtime_id": "runtime-local",
                "machine_id": "machine-local",
            },
            "agent_definition_version": {"placement_manifest": {"allowed_runtime_classes": ["desktop_companion"]}},
        }
        inventory = {
            "deployment_mode": "local_only",
            "attachments": [
                {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                    "runtime_class": "desktop_companion",
                    "runtime_profile_id": "profile-local",
                    "runtime_id": "runtime-local",
                    "machine_id": "machine-local",
                    "online": False,
                    "healthy": False,
                    "control_state": "active",
                }
            ],
        }

        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            asyncio.run(
                runtime_attachment_service.resolve_install_runtime_plan(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                    inventory=inventory,
                )
            )

        self.assertEqual(str(ctx.exception), "Selected runtime attachment is offline.")

    def test_resolve_install_runtime_plan_rejects_local_attachment_when_capability_not_ready(self) -> None:
        install = {
            "runtime_mode": "local_secure",
            "runtime_profile": {
                "id": "profile-local",
                "runtime_class": "desktop_companion",
                "runtime_id": "runtime-local",
                "machine_id": "machine-local",
            },
            "agent_definition_version": {"placement_manifest": {"allowed_runtime_classes": ["desktop_companion"]}},
        }
        inventory = {
            "deployment_mode": "local_only",
            "attachments": [
                {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                    "runtime_class": "desktop_companion",
                    "runtime_profile_id": "profile-local",
                    "runtime_id": "runtime-local",
                    "machine_id": "machine-local",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                    "capabilities": ["shell.execute"],
                    "capability_readiness": {
                        "passive_services": ["postgres"],
                        "service_statuses": {"postgres": "ready"},
                    },
                }
            ],
        }

        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            asyncio.run(
                runtime_attachment_service.resolve_install_runtime_plan(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                    inventory=inventory,
                    policy_context={"required_capabilities": ["shell.execute"]},
                )
            )

        self.assertEqual(ctx.exception.reason, "gateway_capability_not_ready")

    def test_resolve_install_runtime_plan_allows_requested_machine_for_unprofiled_local_worker(self) -> None:
        install = {
            "runtime_mode": "local_secure",
            "runtime_profile": {
                "id": "profile-local",
                "runtime_class": "desktop_companion",
            },
            "agent_definition_version": {"placement_manifest": {"allowed_runtime_classes": ["desktop_companion"]}},
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
                {
                    "attachment_id": "local_companion:phase52-proof-worker",
                    "attachment_kind": "local_companion",
                    "runtime_class": "desktop_companion",
                    "runtime_profile_id": None,
                    "runtime_id": "phase52-proof-worker",
                    "machine_id": "phase52-proof-worker",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
            ],
        }

        plan = asyncio.run(
            runtime_attachment_service.resolve_install_runtime_plan(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                install=install,
                inventory=inventory,
                requested_machine_target="phase52-proof-worker",
            )
        )

        self.assertEqual(plan["execution_target_selected"], "local_companion")
        self.assertEqual(plan["machine_target"], "phase52-proof-worker")
        self.assertEqual(plan["selected_attachment"]["attachment_id"], "local_companion:phase52-proof-worker")

    def test_resolve_install_runtime_plan_supports_self_hosted_secure_node(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-enterprise",
                "slug": "workspace-secure-node",
                "runtime_class": "self_hosted_business_node",
                "runtime_id": "runtime-enterprise",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "workspace-secure-node",
                    "allowed_runtime_classes": ["self_hosted_business_node"],
                }
            },
        }
        inventory = {
            "deployment_mode": "self_hosted_business",
            "attachments": [
                {
                    "attachment_id": "self_hosted_business_node:profile-enterprise",
                    "attachment_kind": "self_hosted_business_node",
                    "runtime_class": "self_hosted_business_node",
                    "runtime_profile_id": "profile-enterprise",
                    "runtime_profile_slug": "workspace-secure-node",
                    "runtime_id": "runtime-enterprise",
                    "workspace_id": "workspace-1",
                    "runtime_node_id": "runtime-enterprise",
                    "node_kind": "linux_server",
                    "self_hosted_node_status": "online",
                    "owner_approved": True,
                    "max_concurrent_sessions": 2,
                    "capabilities": ["shell.execute", "file_access"],
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                }
            ],
        }

        plan = asyncio.run(
            runtime_attachment_service.resolve_install_runtime_plan(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                install=install,
                inventory=inventory,
                workspace={"metadata": {"billing": {"plan": "pro"}}},
            )
        )

        self.assertEqual(plan["selected_attachment"]["attachment_kind"], "self_hosted_business_node")
        self.assertEqual(plan["execution_target_selected"], "cloud")
        self.assertEqual(plan["entitlements"]["enforcement_target"], "self_hosted")

    def test_list_workspace_runtime_attachments_projects_self_hosted_node_registry_fields(self) -> None:
        runtime_profiles = [
            {
                "id": "profile-enterprise",
                "slug": "workspace-secure-node",
                "label": "Workspace Secure Node",
                "runtime_class": "self_hosted_business_node",
                "status": "active",
                "runtime_id": "runtime-enterprise",
                "metadata": {
                    "owner_user_id": "user-owner",
                    "public_key": "pk-node-123",
                    "allowed_agent_ids": ["agent-1", "agent-2"],
                    "max_concurrent_sessions": 3,
                    "root_policy": {"filesystem": "workspace_scoped"},
                    "owner_approved": True,
                },
            }
        ]
        fleet_workers = [
            {
                "worker_id": "runtime-enterprise",
                "runtime_id": "runtime-enterprise",
                "runtime_type": "self_hosted_business_node",
                "display_name": "Mansur Mac mini",
                "online": True,
                "status": "idle",
                "control_state": "active",
                "execution_targets": ["cloud"],
                "capabilities": ["shell.execute", "computer_control"],
                "last_heartbeat_at": "2026-05-11T01:02:03Z",
                "metadata": {
                    "node_kind": "mac_mini",
                },
            }
        ]

        inventory = asyncio.run(
            runtime_attachment_service.list_workspace_runtime_attachments(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                runtime_profiles=runtime_profiles,
                fleet_workers=fleet_workers,
            )
        )
        self_hosted = next(
            item for item in inventory["attachments"] if item.get("attachment_kind") == "self_hosted_business_node"
        )
        self.assertEqual(self_hosted["runtime_node_id"], "runtime-enterprise")
        self.assertEqual(self_hosted["workspace_id"], "workspace-1")
        self.assertEqual(self_hosted["owner_user_id"], "user-owner")
        self.assertEqual(self_hosted["node_kind"], "mac_mini")
        self.assertEqual(self_hosted["self_hosted_node_status"], "online")
        self.assertEqual(self_hosted["heartbeat_at"], "2026-05-11T01:02:03Z")
        self.assertEqual(self_hosted["public_key"], "pk-node-123")
        self.assertEqual(self_hosted["allowed_agent_ids"], ["agent-1", "agent-2"])
        self.assertEqual(self_hosted["max_concurrent_sessions"], 3)
        self.assertEqual(self_hosted["root_policy"], {"filesystem": "workspace_scoped"})
        self.assertTrue(self_hosted["owner_approved"])

    def test_ensure_self_hosted_node_gate_rejects_revoked_node(self) -> None:
        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            runtime_attachment_service.ensure_self_hosted_node_gate(
                attachment={
                    "attachment_kind": "self_hosted_business_node",
                    "workspace_id": "workspace-1",
                    "runtime_node_id": "node-1",
                    "node_kind": "mac_mini",
                    "self_hosted_node_status": "revoked",
                    "online": True,
                    "healthy": True,
                    "owner_approved": True,
                    "capabilities": ["shell.execute"],
                    "max_concurrent_sessions": 1,
                },
                workspace_id="workspace-1",
                required_capabilities=["shell.execute"],
            )
        self.assertEqual(ctx.exception.reason, "self_hosted_node_revoked")

    def test_ensure_self_hosted_node_gate_rejects_missing_required_capability(self) -> None:
        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            runtime_attachment_service.ensure_self_hosted_node_gate(
                attachment={
                    "attachment_kind": "self_hosted_business_node",
                    "workspace_id": "workspace-1",
                    "runtime_node_id": "node-1",
                    "node_kind": "linux_server",
                    "self_hosted_node_status": "online",
                    "online": True,
                    "healthy": True,
                    "owner_approved": True,
                    "capabilities": ["shell.execute"],
                    "max_concurrent_sessions": 1,
                },
                workspace_id="workspace-1",
                required_capabilities=["computer_control"],
            )
        self.assertEqual(ctx.exception.reason, "self_hosted_node_capability_mismatch")

    def test_ensure_self_hosted_node_gate_rejects_not_owner_approved(self) -> None:
        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            runtime_attachment_service.ensure_self_hosted_node_gate(
                attachment={
                    "attachment_kind": "self_hosted_business_node",
                    "workspace_id": "workspace-1",
                    "runtime_node_id": "node-1",
                    "node_kind": "linux_server",
                    "self_hosted_node_status": "online",
                    "online": True,
                    "healthy": True,
                    "owner_approved": False,
                    "capabilities": ["shell.execute"],
                    "max_concurrent_sessions": 1,
                },
                workspace_id="workspace-1",
                required_capabilities=["shell.execute"],
            )
        self.assertEqual(ctx.exception.reason, "self_hosted_node_not_owner_approved")

    def test_resolve_install_runtime_plan_supports_cloud_computer_profile(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud-computer",
                "slug": "sage-cloud-computer",
                "runtime_class": "cloud_computer",
                "runtime_id": "cloud-computer-1",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "sage-cloud-computer",
                    "allowed_runtime_classes": ["cloud_computer"],
                }
            },
        }
        inventory = {
            "deployment_mode": "cloud_only",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
                {
                    "attachment_id": "cloud_computer:profile-cloud-computer",
                    "attachment_kind": "cloud_computer",
                    "runtime_class": "cloud_computer",
                    "runtime_profile_id": "profile-cloud-computer",
                    "runtime_profile_slug": "sage-cloud-computer",
                    "runtime_id": "cloud-computer-1",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                    "trust_model": {"trust_tier": "hosted_cloud_computer"},
                },
            ],
        }

        plan = asyncio.run(
            runtime_attachment_service.resolve_install_runtime_plan(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                install=install,
                inventory=inventory,
                workspace={"metadata": {"billing": {"plan": "pro"}}},
            )
        )

        self.assertEqual(plan["selected_attachment"]["attachment_kind"], "cloud_computer")
        self.assertEqual(plan["selected_attachment"]["trust_model"]["trust_tier"], "hosted_cloud_computer")
        self.assertEqual(plan["execution_target_selected"], "cloud_computer")

    def test_resolve_install_runtime_plan_does_not_auto_select_cloud_computer(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
        }
        inventory = {
            "deployment_mode": "cloud_only",
            "attachments": [
                {
                    "attachment_id": "cloud_computer:profile-cloud-computer",
                    "attachment_kind": "cloud_computer",
                    "runtime_class": "cloud_computer",
                    "runtime_profile_id": "profile-cloud-computer",
                    "runtime_profile_slug": "sage-cloud-computer",
                    "runtime_id": "cloud-computer-1",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                    "trust_model": {"trust_tier": "hosted_cloud_computer"},
                },
            ],
        }

        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            asyncio.run(
                runtime_attachment_service.resolve_install_runtime_plan(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                    inventory=inventory,
                    workspace={"metadata": {"billing": {"plan": "pro"}}},
                )
            )

        self.assertEqual(ctx.exception.reason, "no_hosted_runtime_attachment_available")

    def test_resolve_install_runtime_plan_keeps_managed_cloud_default_when_self_host_is_optional(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "allowed_runtime_classes": ["cloud_worker", "self_hosted_business_node"],
                }
            },
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "runtime_id": "runtime-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
                {
                    "attachment_id": "self_hosted_business_node:profile-enterprise",
                    "attachment_kind": "self_hosted_business_node",
                    "runtime_class": "self_hosted_business_node",
                    "runtime_profile_id": "profile-enterprise",
                    "runtime_profile_slug": "workspace-secure-node",
                    "runtime_id": "runtime-enterprise",
                    "workspace_id": "workspace-1",
                    "runtime_node_id": "runtime-enterprise",
                    "node_kind": "linux_server",
                    "self_hosted_node_status": "online",
                    "owner_approved": True,
                    "max_concurrent_sessions": 2,
                    "capabilities": ["shell.execute", "file_access"],
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
            ],
        }

        plan = asyncio.run(
            runtime_attachment_service.resolve_install_runtime_plan(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                install=install,
                inventory=inventory,
                workspace={"metadata": {"billing": {"plan": "pro"}}},
            )
        )

        self.assertEqual(plan["selected_attachment"]["attachment_kind"], "managed_cloud")
        self.assertEqual(
            plan["placement_enforcement"]["preferred_attachment_kinds"],
            ["managed_cloud", "cloud_computer", "self_hosted_business_node"],
        )

    def test_resolve_install_runtime_plan_rejects_hosted_quota_exhaustion(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "empyralis-cloud",
                    "allowed_runtime_classes": ["cloud_worker"],
                }
            },
        }
        inventory = {
            "deployment_mode": "cloud_only",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                }
            ],
        }
        workspace = {
            "metadata": {
                "billing": {
                    "plan": "pro",
                    "entitlement_usage": {"hosted_runtime_minutes_monthly": 1500},
                }
            }
        }

        with self.assertRaises(entitlements_service.EntitlementQuotaExceededError) as ctx:
            asyncio.run(
                runtime_attachment_service.resolve_install_runtime_plan(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                    inventory=inventory,
                    workspace=workspace,
                )
            )

        self.assertEqual(ctx.exception.reason, "hosted_runtime_minutes_exhausted")

    def test_resolve_install_runtime_plan_fails_closed_when_local_only_memory_is_requested_for_hosted(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "empyralis-cloud",
                    "allowed_runtime_classes": ["cloud_worker"],
                }
            },
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
                {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                    "runtime_class": "desktop_companion",
                    "runtime_profile_id": "profile-local",
                    "runtime_profile_slug": "my-local-mac",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
            ],
        }

        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            asyncio.run(
                runtime_attachment_service.resolve_install_runtime_plan(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                    inventory=inventory,
                    policy_context={
                        "requested_memory_layers": ["local_private_memory"],
                    },
                )
            )

        self.assertEqual(ctx.exception.reason, "local_runtime_required_for_hosted_execution")

    def test_resolve_install_runtime_plan_rejects_invalid_summary_bridge_payloads(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "empyralis-cloud",
                    "allowed_runtime_classes": ["cloud_worker"],
                }
            },
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                }
            ],
        }

        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            asyncio.run(
                runtime_attachment_service.resolve_install_runtime_plan(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                    inventory=inventory,
                    policy_context={
                        "requested_memory_layers": ["episodic_memory"],
                        "sync_class": "summary_bridge_only",
                        "summary_payload_classes": ["raw_private_file_content_without_explicit_share"],
                        "last_safe_summary_available": True,
                    },
                )
            )

        self.assertEqual(ctx.exception.reason, "summary_bridge_payload_not_allowed")

    def test_resolve_install_runtime_plan_marks_local_offline_summary_bridge_degraded_mode(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "empyralis-cloud",
                    "allowed_runtime_classes": ["cloud_worker"],
                }
            },
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
                {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                    "runtime_class": "desktop_companion",
                    "runtime_profile_id": "profile-local",
                    "runtime_profile_slug": "my-local-mac",
                    "online": False,
                    "healthy": False,
                    "control_state": "active",
                },
            ],
        }

        plan = asyncio.run(
            runtime_attachment_service.resolve_install_runtime_plan(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                install=install,
                inventory=inventory,
                workspace={"metadata": {"billing": {"plan": "pro"}}},
                policy_context={
                    "requested_memory_layers": ["local_private_memory"],
                    "sync_class": "summary_bridge_only",
                    "summary_payload_classes": ["bounded_local_private_summaries"],
                    "last_safe_summary_available": True,
                },
            )
        )

        self.assertEqual(plan["execution_target_selected"], "cloud")
        self.assertEqual(plan["sync_enforcement"]["effective_sync_class"], "summary_bridge_only")
        self.assertEqual(plan["degraded_mode"]["state"], "local_offline_summary_bridge")
        self.assertTrue(plan["degraded_mode"]["last_safe_summary_only"])
        self.assertEqual(plan["state_layer_policy"]["local_private_memory_access"], "cloud_safe_summaries_only")

    def test_resolve_install_runtime_plan_uses_persisted_summary_bridge_status_for_offline_fallback(self) -> None:
        install = {
            "id": "install-1",
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "empyralis-cloud",
                    "allowed_runtime_classes": ["cloud_worker"],
                }
            },
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
                {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                    "runtime_class": "desktop_companion",
                    "runtime_profile_id": "profile-local",
                    "runtime_profile_slug": "my-local-mac",
                    "online": False,
                    "healthy": False,
                    "control_state": "active",
                },
            ],
        }

        with patch.object(
            runtime_attachment_service.memory_service,
            "hybrid_summary_bridge_status",
            return_value={
                "record_count": 1,
                "available_payload_classes": ["bounded_local_private_summaries"],
                "last_safe_summary_available": True,
                "latest_published_at": "2026-04-10T00:00:00Z",
                "latest_last_safe_at": "2026-04-10T00:00:00Z",
            },
        ):
            plan = asyncio.run(
                runtime_attachment_service.resolve_install_runtime_plan(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                    inventory=inventory,
                    workspace={"metadata": {"billing": {"plan": "pro"}}},
                    policy_context={
                        "requested_memory_layers": ["local_private_memory"],
                        "sync_class": "summary_bridge_only",
                        "summary_payload_classes": ["bounded_local_private_summaries"],
                    },
                )
            )

        self.assertEqual(plan["degraded_mode"]["state"], "local_offline_summary_bridge")
        self.assertTrue(plan["sync_enforcement"]["summary_bridge_status"]["last_safe_summary_available"])

    def test_resolve_install_runtime_plan_fails_closed_when_offline_summary_bridge_has_no_safe_snapshot(self) -> None:
        install = {
            "id": "install-1",
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "empyralis-cloud",
                    "allowed_runtime_classes": ["cloud_worker"],
                }
            },
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
                {
                    "attachment_id": "local_companion:profile-local",
                    "attachment_kind": "local_companion",
                    "runtime_class": "desktop_companion",
                    "runtime_profile_id": "profile-local",
                    "runtime_profile_slug": "my-local-mac",
                    "online": False,
                    "healthy": False,
                    "control_state": "active",
                },
            ],
        }

        with patch.object(
            runtime_attachment_service.memory_service,
            "hybrid_summary_bridge_status",
            return_value={
                "record_count": 0,
                "available_payload_classes": [],
                "last_safe_summary_available": False,
                "latest_published_at": None,
                "latest_last_safe_at": None,
            },
        ):
            with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
                asyncio.run(
                    runtime_attachment_service.resolve_install_runtime_plan(
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        install=install,
                        inventory=inventory,
                        policy_context={
                            "requested_memory_layers": ["local_private_memory"],
                            "sync_class": "summary_bridge_only",
                            "summary_payload_classes": ["bounded_local_private_summaries"],
                        },
                    )
                )

        self.assertEqual(ctx.exception.reason, "summary_bridge_snapshot_unavailable")

    def test_resolve_install_runtime_plan_prefers_self_hosted_when_customer_controlled_compute_is_required(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-enterprise",
                "slug": "workspace-secure-node",
                "runtime_class": "self_hosted_business_node",
                "runtime_id": "runtime-enterprise",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "allowed_runtime_classes": ["cloud_worker", "self_hosted_business_node"],
                }
            },
        }
        inventory = {
            "deployment_mode": "hybrid",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
                {
                    "attachment_id": "self_hosted_business_node:profile-enterprise",
                    "attachment_kind": "self_hosted_business_node",
                    "runtime_class": "self_hosted_business_node",
                    "runtime_profile_id": "profile-enterprise",
                    "runtime_profile_slug": "workspace-secure-node",
                    "workspace_id": "workspace-1",
                    "runtime_node_id": "runtime-enterprise",
                    "node_kind": "linux_server",
                    "self_hosted_node_status": "online",
                    "owner_approved": True,
                    "max_concurrent_sessions": 2,
                    "capabilities": ["shell.execute", "file_access"],
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                },
            ],
        }

        plan = asyncio.run(
            runtime_attachment_service.resolve_install_runtime_plan(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                install=install,
                inventory=inventory,
                policy_context={"customer_controlled_compute_required": True},
            )
        )

        self.assertEqual(plan["selected_attachment"]["attachment_kind"], "self_hosted_business_node")
        self.assertEqual(
            plan["placement_enforcement"]["preferred_attachment_kinds"],
            ["self_hosted_business_node", "managed_cloud", "cloud_computer"],
        )

    def test_resolve_install_runtime_plan_requires_explicit_opt_in_for_document_sync(self) -> None:
        install = {
            "runtime_mode": "hosted_secure",
            "runtime_profile": {
                "id": "profile-cloud",
                "slug": "empyralis-cloud",
                "runtime_class": "cloud_worker",
                "runtime_id": "runtime-cloud",
            },
            "agent_definition_version": {
                "placement_manifest": {
                    "preferred_runtime_slug": "empyralis-cloud",
                    "allowed_runtime_classes": ["cloud_worker"],
                }
            },
        }
        inventory = {
            "deployment_mode": "cloud_only",
            "attachments": [
                {
                    "attachment_id": "managed_cloud:profile-cloud",
                    "attachment_kind": "managed_cloud",
                    "runtime_class": "cloud_worker",
                    "runtime_profile_id": "profile-cloud",
                    "runtime_profile_slug": "empyralis-cloud",
                    "online": True,
                    "healthy": True,
                    "control_state": "active",
                }
            ],
        }

        with self.assertRaises(runtime_attachment_service.RuntimeAttachmentSelectionError) as ctx:
            asyncio.run(
                runtime_attachment_service.resolve_install_runtime_plan(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    install=install,
                    inventory=inventory,
                    policy_context={
                        "requested_memory_layers": ["notes_documents_retrieval"],
                        "sync_class": "explicit_opt_in",
                    },
                )
            )

        self.assertEqual(ctx.exception.reason, "explicit_opt_in_required")


class HybridPolicyServiceTests(unittest.TestCase):
    def test_evaluate_hybrid_runtime_policy_reports_local_cloud_availability_and_identity(self) -> None:
        state = hybrid_policy_service.evaluate_hybrid_runtime_policy(
            runtime_mode="hosted_secure",
            policy_context={
                "requested_memory_layers": ["app_event_history"],
            },
            attachments=[
                {
                    "attachment_kind": "managed_cloud",
                    "online": True,
                    "control_state": "active",
                },
                {
                    "attachment_kind": "local_companion",
                    "online": True,
                    "control_state": "active",
                },
            ],
        )

        self.assertEqual(state["effective_sync_class"], "sync_allowed")
        self.assertTrue(state["placement"]["availability"]["local_online"])
        self.assertTrue(state["placement"]["availability"]["managed_cloud_online"])
        self.assertFalse(state["placement"]["availability"]["cloud_computer_online"])
        self.assertEqual(state["shared_identity"]["sage_identity"], "shared")
