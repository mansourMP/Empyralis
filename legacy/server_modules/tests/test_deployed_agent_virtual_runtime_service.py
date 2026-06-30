import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from server_modules import deployed_agent_virtual_runtime_service


class _FakeCountPool:
    def __init__(self, count: int = 0) -> None:
        self._count = count
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return self._count


def _privacy_contract_snapshot(**overrides):
    snapshot = {
        "schema_version": 1,
        "where_it_runs": {"trust_zone": "cloud_managed"},
        "model_provider_data_access": {},
        "screenshots_captured": True,
        "files_accessible": True,
        "terminal_accessible": True,
        "connectors_accessible": {},
        "memory_scope": {},
        "retention_period": {},
        "export_delete_policy": {},
        "audit_log": {"available": True},
        "accepted_at": "2026-05-11T10:00:00Z",
        "accepted_by_user_id": "user-owner",
    }
    snapshot.update(overrides)
    return snapshot


def _computer_safety_contract_snapshot(**overrides):
    snapshot = {
        "schema_version": 1,
        "enabled": True,
        "required_for_mode": True,
        "studio_agent_mode": "cloud_computer_agent",
        "isolation_boundary": "sandbox",
        "inherit_host_environment": False,
        "filesystem_default_access": "session_scoped",
        "domain_allowlist": ["supplier.example", "*.orders.example"],
        "download_install_policy": {
            "downloads_allowed": False,
            "software_install_allowed": False,
        },
        "terminal_command_policy": "review_required",
        "sensitive_action_confirmation_required": True,
        "session_timeout_seconds": 900,
        "max_runtime_seconds": 7200,
        "screenshot_session_recording": {
            "screenshots_enabled": True,
            "recording_policy": "metadata_only",
        },
        "emergency_stop_enabled": True,
        "required_owner_approval_actions": [
            "send_external_messages",
            "make_purchases",
            "delete_data",
            "change_permissions",
            "enter_secrets",
            "install_software",
            "run_unknown_scripts",
        ],
    }
    snapshot.update(overrides)
    return snapshot


def _deployed_agent(**overrides):
    base = {
        "id": "dagent_1",
        "backing_install_id": "ainstall_1",
        "deployment_state": "live",
        "config": {
            "name": "Store Assistant",
            "persona": "Helpful ops assistant",
            "system_prompt": "Help safely.",
            "studio_agent_mode": "cloud_computer_agent",
            "runtime_target": "cloud",
            "computer_automation": {
                "enabled": True,
                "runtime_class": "virtual_browser",
                "allowed_domains": ["supplier.example", "*.orders.example"],
                "max_concurrent_sessions": 2,
                "daily_budget_usd": 25,
                "monthly_budget_usd": 200,
                "requires_owner_approval": True,
                "idle_timeout_seconds": 900,
                "max_session_runtime_seconds": 7200,
                "screenshot_capture_enabled": True,
                "session_recording_policy": "metadata_only",
                "required_owner_approval_actions": [
                    "send_external_messages",
                    "make_purchases",
                    "delete_data",
                    "change_permissions",
                    "enter_secrets",
                    "install_software",
                    "run_unknown_scripts",
                ],
            },
            "commerce_policy": {
                "monthly_cost_cap_usd": 300,
            },
            "tool_policy": {
                "enabled_tools": ["web_search", "http_request"],
            },
            "memory_policy": {
                "memory_enabled": True,
            },
        },
        "metadata": {
            "privacy_contract_snapshot": _privacy_contract_snapshot(),
            "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(),
        },
    }
    override_config = overrides.pop("config", None)
    override_metadata = overrides.pop("metadata", None)
    base.update(overrides)
    if override_config is not None:
        config = dict(base["config"])
        config.update(override_config)
        base["config"] = config
    if override_metadata is not None:
        metadata = dict(base["metadata"])
        metadata.update(override_metadata)
        base["metadata"] = metadata
    return base


def _local_gateway_attachment(**overrides):
    base = {
        "attachment_id": "local_companion:gateway-local-1",
        "attachment_kind": "local_companion",
        "workspace_id": "ws-1",
        "runtime_id": "gateway-local-1",
        "machine_id": "device-local-1",
        "online": True,
        "healthy": True,
        "control_state": "active",
        "gateway_identity": {
            "gateway_id": "gateway-local-1",
            "device_id": "device-local-1",
            "workspace_id": "ws-1",
            "device_trust_state": "verified",
        },
    }
    base.update(overrides)
    return base


class DeployedAgentVirtualRuntimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._service_gate_patch = patch.object(
            deployed_agent_virtual_runtime_service,
            "_enforce_deployed_virtual_runtime_service_decision",
            return_value={"next_action": "build_deployed_agent_virtual_runtime_payload"},
        )
        self._virtual_gate_patch = patch.object(
            deployed_agent_virtual_runtime_service,
            "_enforce_deployed_virtual_runtime_decision",
            return_value={"next_action": "build_deployed_agent_virtual_runtime_payload"},
        )
        self._service_gate_patch.start()
        self._virtual_gate_patch.start()
        self.addCleanup(self._service_gate_patch.stop)
        self.addCleanup(self._virtual_gate_patch.stop)

    def test_builds_cloud_computer_runtime_payload(self):
        payload = deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(
            _deployed_agent()
        )

        self.assertEqual(payload["runtime_choice"], "virtual_browser")
        self.assertEqual(payload["computer_automation"]["runtime_class"], "virtual_browser")
        self.assertEqual(payload["network_policy"]["allowed_domains"], ["supplier.example", "*.orders.example"])
        self.assertTrue(payload["enterprise_controls"]["disable_public_internet_mode"])
        self.assertTrue(payload["session_recording"]["enabled"])
        self.assertEqual(payload["runtime_kill_state"]["deployment_state"], "live")
        self.assertEqual(payload["policy_metadata"]["studio_agent_mode"], "cloud_computer_agent")
        self.assertEqual(payload["policy_metadata"]["privacy_contract_snapshot_version"], 1)
        self.assertEqual(payload["policy_metadata"]["computer_safety_contract_snapshot_version"], 1)

    def test_uses_server_side_policy_not_untrusted_raw_policy_fields(self):
        record = _deployed_agent(
            config={
                "computer_automation": {
                    "enabled": True,
                    "runtime_class": "virtual_browser",
                    "allowed_domains": ["safe.example"],
                    "max_concurrent_sessions": 1,
                    "daily_budget_usd": 10,
                    "monthly_budget_usd": 50,
                },
                "network_policy": {"allowed_domains": ["evil.example"]},
                "session_recording": {"enabled": False},
                "policy_metadata": {"policy_mode": "allow_all"},
            },
            metadata={
                "privacy_contract_snapshot": _privacy_contract_snapshot(),
                "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                    domain_allowlist=["safe.example"],
                ),
                "network_policy": {"allowed_domains": ["evil.example"]},
                "runtime_kill_state": {"deployment_state": "live"},
            },
        )

        payload = deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(record)

        self.assertEqual(payload["network_policy"]["allowed_domains"], ["safe.example"])
        self.assertTrue(payload["session_recording_enabled"])
        self.assertEqual(payload["policy_metadata"]["policy_mode"], "strict")

    def test_maps_kill_switch_and_workspace_emergency_stop(self):
        record = _deployed_agent(
            deployment_state="suspended",
            metadata={
                "kill_switch": {"active": True, "reason": "owner_stop"},
                "workspace_emergency_stop": {"active": True, "reason": "workspace_stop"},
            },
        )

        payload = deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(record)

        self.assertEqual(payload["runtime_kill_state"]["deployment_state"], "suspended")
        self.assertTrue(payload["runtime_kill_state"]["kill_switch_active"])
        self.assertTrue(payload["runtime_kill_state"]["workspace_emergency_stop_active"])
        self.assertEqual(payload["runtime_kill_state"]["kill_switch_reason"], "owner_stop")

    def test_maps_local_runtime_classes_to_local_runtime_choice(self):
        record = _deployed_agent(
            config={
                "studio_agent_mode": "my_computer_agent",
                "commerce_policy": {"monthly_cost_cap_usd": 25},
                "computer_automation": {
                    "enabled": True,
                    "runtime_class": "local_desktop",
                    "allowed_domains": ["intranet.example"],
                    "max_concurrent_sessions": 1,
                    "daily_budget_usd": 5,
                    "monthly_budget_usd": 25,
                },
            },
            metadata={
                "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                    studio_agent_mode="my_computer_agent",
                    domain_allowlist=["intranet.example"],
                )
            },
        )

        payload = deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(record)

        self.assertEqual(payload["runtime_choice"], "local")

    def test_derives_runtime_and_cost_quota_from_backend_config(self):
        payload = deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(
            _deployed_agent()
        )

        self.assertEqual(payload["runtime_quota"]["max_concurrent_sessions"], 2)
        self.assertEqual(payload["cost_quota"]["workspace_monthly_budget_limit"], 300.0)
        self.assertEqual(payload["cost_quota"]["agent_monthly_budget_limit"], 200.0)
        self.assertEqual(payload["cost_quota"]["per_session_runtime_seconds"], 7200)
        self.assertEqual(payload["cost_quota"]["agent_runtime_budget_seconds"], 14400)

    def test_build_payload_rejects_self_hosted_agent(self):
        with self.assertRaisesRegex(ValueError, "self_hosted_agent cannot use cloud runtime payload builder"):
            deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(
                _deployed_agent(
                    config={
                        "studio_agent_mode": "self_hosted_agent",
                        "runtime_target": "self_hosted",
                    }
                )
            )

    def test_requires_privacy_snapshot_for_computer_runtime(self):
        with self.assertRaisesRegex(ValueError, "privacy_contract_snapshot"):
            deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(
                _deployed_agent(metadata={"privacy_contract_snapshot": None})
            )

    def test_requires_computer_safety_snapshot_for_computer_runtime(self):
        with self.assertRaisesRegex(ValueError, "computer_safety_contract_snapshot"):
            deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(
                _deployed_agent(metadata={"computer_safety_contract_snapshot": None})
            )

    def test_requires_budget_allowlist_and_recording_for_computer_runtime(self):
        with self.assertRaisesRegex(ValueError, "allowed_domains"):
            deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(
                _deployed_agent(
                    config={
                        "computer_automation": {
                            "enabled": True,
                            "runtime_class": "virtual_browser",
                            "allowed_domains": [],
                            "daily_budget_usd": 25,
                            "monthly_budget_usd": 200,
                            "session_recording_policy": "metadata_only",
                        }
                    },
                    metadata={
                        "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                            domain_allowlist=[],
                        )
                    },
                )
            )
        with self.assertRaisesRegex(ValueError, "daily_budget_usd"):
            deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(
                _deployed_agent(
                    config={
                        "computer_automation": {
                            "enabled": True,
                            "runtime_class": "virtual_browser",
                            "allowed_domains": ["supplier.example"],
                            "daily_budget_usd": None,
                            "monthly_budget_usd": 200,
                            "session_recording_policy": "metadata_only",
                        }
                    }
                )
            )
        with self.assertRaisesRegex(ValueError, "session recording"):
            deployed_agent_virtual_runtime_service.build_deployed_agent_virtual_runtime_payload(
                _deployed_agent(
                    config={
                        "computer_automation": {
                            "enabled": True,
                            "runtime_class": "virtual_browser",
                            "allowed_domains": ["supplier.example"],
                            "daily_budget_usd": 25,
                            "monthly_budget_usd": 200,
                            "session_recording_policy": "disabled",
                        }
                    },
                    metadata={
                        "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                            screenshot_session_recording={
                                "screenshots_enabled": False,
                                "recording_policy": "disabled",
                            }
                        )
                    },
                )
            )

    def test_ensure_cloud_runtime_session_binding_uses_virtual_runtime_registry(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock(
                return_value={
                    "browser_session": {"browser_session_id": "vcsess_1"},
                    "provider_id": "provider_demo",
                    "provider_kind": "managed_cloud",
                }
            )
            registry = Mock()
            registry.resolve.return_value = runtime
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=_deployed_agent()),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                return await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                    deployed_agent_id="dagent_1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    session_id="sess-1",
                    thread_id="thread-1",
                    channel="telegram",
                    actor={"type": "user", "id": "user-1"},
                    session_metadata={},
                    turn_metadata={"deployed_agent_id": "dagent_1"},
                    machine_target=None,
                ), registry, runtime, append_activity_event

        import asyncio

        binding, registry, runtime, append_activity_event = asyncio.run(_run())
        self.assertEqual(binding["metadata_updates"]["runtime_session_id"], "vcsess_1")
        self.assertEqual(binding["metadata_updates"]["runtime_session_binding"], "cloud_computer_agent")
        registry.resolve.assert_called_once()
        runtime.create_session.assert_awaited_once()
        payload = runtime.create_session.await_args.args[0]
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertEqual(payload["agent_id"], "dagent_1")
        self.assertEqual(payload["metadata"]["runtime_session_binding"], "cloud_computer_agent")
        self.assertEqual(
            append_activity_event.await_args.kwargs["action"],
            "deployed_agent_cloud_runtime_session_created",
        )

    def test_cloud_computer_agent_does_not_silently_fallback_to_inmemory_in_production(self):
        async def _run():
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=_deployed_agent()),
                ),
                patch.dict(
                    "os.environ",
                    {"ORION_ENV": "production", "EMPYRALIS_ALLOW_INMEMORY_RUNTIME": "true"},
                    clear=False,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Cloud Computer runtime provider is not configured"):
                    await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                        turn_metadata={"deployed_agent_id": "dagent_1"},
                    )

        import asyncio

        asyncio.run(_run())

    def test_my_computer_agent_binding_uses_local_gateway_identity(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock(
                return_value={
                    "browser_session": {"browser_session_id": "localsess_1"},
                    "provider_kind": "local_gateway",
                }
            )
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "my_computer_agent",
                    "runtime_target": "local_companion",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "local_desktop",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                        "requires_owner_approval": True,
                        "session_recording_policy": "metadata_only",
                    },
                },
                metadata={
                    "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                        studio_agent_mode="my_computer_agent",
                        domain_allowlist=["intranet.example"],
                    )
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(return_value={"attachments": [_local_gateway_attachment()]}),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                return await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                    deployed_agent_id="dagent_1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    session_id="sess-1",
                    thread_id="thread-1",
                    channel="telegram",
                    actor={"type": "user", "id": "user-1"},
                    session_metadata={},
                    turn_metadata={"deployed_agent_id": "dagent_1"},
                    machine_target=None,
                ), registry, runtime, append_activity_event

        import asyncio

        binding, registry, runtime, append_activity_event = asyncio.run(_run())
        self.assertEqual(binding["metadata_updates"]["runtime_session_id"], "localsess_1")
        self.assertEqual(binding["metadata_updates"]["runtime_session_binding"], "my_computer_agent")
        self.assertTrue(binding["metadata_updates"]["local_gateway_bound"])
        self.assertFalse(binding["metadata_updates"]["virtual_runtime_bound"])
        self.assertEqual(binding["metadata_updates"]["gateway_id"], "gateway-local-1")
        registry.resolve.assert_called_once_with("local", preferred_provider_id=None)
        runtime.create_session.assert_awaited_once()
        payload = runtime.create_session.await_args.args[0]
        self.assertEqual(payload["metadata"]["runtime_session_binding"], "my_computer_agent")
        self.assertEqual(payload["local_gateway_attachment"]["gateway_id"], "gateway-local-1")
        self.assertNotEqual(payload["metadata"]["runtime_session_binding"], "cloud_computer_agent")
        self.assertEqual(
            append_activity_event.await_args.kwargs["action"],
            "deployed_agent_local_gateway_runtime_session_created",
        )
        self.assertEqual(
            append_activity_event.await_args.kwargs["metadata"]["runtime_session_binding"],
            "my_computer_agent",
        )

    def test_my_computer_agent_requires_healthy_paired_gateway(self):
        async def _run():
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "my_computer_agent",
                    "runtime_target": "local_companion",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "local_desktop",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                        "requires_owner_approval": True,
                        "session_recording_policy": "metadata_only",
                    },
                },
                metadata={
                    "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                        studio_agent_mode="my_computer_agent",
                        domain_allowlist=["intranet.example"],
                    )
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(return_value={"attachments": [_local_gateway_attachment(healthy=False)]}),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "healthy paired Gateway"):
                    await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                        turn_metadata={"deployed_agent_id": "dagent_1"},
                    )

        import asyncio

        asyncio.run(_run())

    def test_ensure_cloud_runtime_session_binding_rejects_text_agent(self):
        async def _run():
            with patch.object(
                deployed_agent_virtual_runtime_service.control_plane_repository,
                "get_deployed_agent_by_id",
                new=AsyncMock(
                    return_value=_deployed_agent(
                        config={"studio_agent_mode": "text_agent"},
                    )
                ),
            ):
                with self.assertRaisesRegex(ValueError, "studio_agent_mode"):
                    await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                        turn_metadata={"deployed_agent_id": "dagent_1"},
                    )

        import asyncio

        asyncio.run(_run())

    def test_ensure_cloud_runtime_session_binding_rejects_self_hosted_without_runtime_profile_id(self):
        async def _run():
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(
                        return_value=_deployed_agent(
                            config={
                                "studio_agent_mode": "self_hosted_agent",
                                "runtime_target": "self_hosted",
                                "runtime_profile_id": None,
                                "computer_automation": {
                                    "enabled": True,
                                    "runtime_class": "virtual_code_sandbox",
                                    "allowed_domains": ["safe.example"],
                                    "max_concurrent_sessions": 1,
                                    "daily_budget_usd": 5,
                                    "monthly_budget_usd": 25,
                                    "requires_owner_approval": True,
                                },
                            },
                            metadata={
                                "self_hosted_runtime_binding": {
                                    "runtime_target": "self_host_runtime",
                                    "workspace_id": "ws-1",
                                    "runtime_node_id": "node-1",
                                    "runtime_attachment_id": "attach-1",
                                    "runtime_profile_id": "profile-self-host",
                                    "allowed_capabilities": ["shell.execute"],
                                    "filesystem_scope": "none",
                                    "domain_allowlist": ["safe.example"],
                                    "approval_policy": {"requires_owner_approval": True},
                                    "quota_policy": {"max_concurrent_sessions": 1},
                                },
                                "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                                    studio_agent_mode="self_hosted_agent",
                                    domain_allowlist=["safe.example"],
                                )
                            },
                        )
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "profile-self-host",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": True,
                                    "healthy": True,
                                    "self_hosted_node_status": "online",
                                    "max_concurrent_sessions": 1,
                                }
                            ]
                        }
                    ),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "runtime_profile_id"):
                    await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                        turn_metadata={"deployed_agent_id": "dagent_1"},
                    )

        import asyncio

        asyncio.run(_run())

    def test_ensure_cloud_runtime_session_binding_rejects_self_hosted_unhealthy_target(self):
        async def _run():
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(
                        return_value=_deployed_agent(
                            config={
                                "studio_agent_mode": "self_hosted_agent",
                                "runtime_target": "self_hosted",
                                "runtime_profile_id": "profile-self-host",
                                "computer_automation": {
                                    "enabled": True,
                                    "runtime_class": "virtual_code_sandbox",
                                    "allowed_domains": ["safe.example"],
                                    "max_concurrent_sessions": 1,
                                    "daily_budget_usd": 5,
                                    "monthly_budget_usd": 25,
                                    "requires_owner_approval": True,
                                },
                            },
                            metadata={
                                "self_hosted_runtime_binding": {
                                    "runtime_target": "self_host_runtime",
                                    "workspace_id": "ws-1",
                                    "runtime_node_id": "node-1",
                                    "runtime_attachment_id": "attach-1",
                                    "runtime_profile_id": "profile-self-host",
                                    "allowed_capabilities": ["shell.execute"],
                                    "filesystem_scope": "none",
                                    "domain_allowlist": ["safe.example"],
                                    "approval_policy": {"requires_owner_approval": True},
                                    "quota_policy": {"max_concurrent_sessions": 1},
                                },
                                "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                                    studio_agent_mode="self_hosted_agent",
                                    domain_allowlist=["safe.example"],
                                )
                            },
                        )
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "profile-self-host",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": True,
                                    "healthy": False,
                                    "self_hosted_node_status": "unhealthy",
                                    "max_concurrent_sessions": 1,
                                }
                            ]
                        }
                    ),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "healthy"):
                    await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                        turn_metadata={"deployed_agent_id": "dagent_1"},
                    )

        import asyncio

        asyncio.run(_run())

    def test_ensure_self_hosted_runtime_session_binding_uses_virtual_runtime_registry(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock(
                return_value={
                    "session_id": "shsess_1",
                    "provider_id": "docker_kubernetes",
                    "provider_kind": "self_hosted_sandbox_provider",
                }
            )
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                        "requires_owner_approval": True,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    },
                    "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                        studio_agent_mode="self_hosted_agent",
                        domain_allowlist=["intranet.example"],
                    ),
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "ensure_control_plane_schema",
                    new=AsyncMock(return_value=_FakeCountPool(count=0)),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "rprof-1",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": True,
                                    "healthy": True,
                                    "owner_approved": True,
                                    "self_hosted_node_status": "online",
                                    "max_concurrent_sessions": 1,
                                    "allowed_agent_ids": ["dagent_1"],
                                    "capabilities": ["shell.execute"],
                                }
                            ]
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                binding = await deployed_agent_virtual_runtime_service.ensure_self_hosted_runtime_session_binding(
                    deployed_agent_id="dagent_1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    session_id="sess-1",
                    thread_id="thread-1",
                    channel="web",
                    actor={"type": "user", "id": "user-1"},
                )
                return binding, runtime, append_activity_event

        import asyncio

        binding, runtime, append_activity_event = asyncio.run(_run())
        self.assertEqual(binding["metadata_updates"]["runtime_session_binding"], "self_hosted_agent")
        self.assertEqual(binding["metadata_updates"]["runtime_session_id"], "shsess_1")
        self.assertEqual(binding["metadata_updates"]["runtime_node_id"], "node-1")
        payload = runtime.create_session.await_args.args[0]
        self.assertEqual(payload["runtime_node_id"], "node-1")
        self.assertEqual(payload["policy_metadata"]["self_hosted_runtime_binding"]["runtime_attachment_id"], "attach-1")
        self.assertEqual(
            append_activity_event.await_args.kwargs["action"],
            "deployed_agent_self_hosted_runtime_session_created",
        )

    def test_ensure_self_hosted_runtime_session_binding_rejects_workspace_mismatched_node(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock(return_value={"session_id": "shsess_1"})
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    },
                    "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                        studio_agent_mode="self_hosted_agent",
                        domain_allowlist=["intranet.example"],
                    ),
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "ensure_control_plane_schema",
                    new=AsyncMock(return_value=_FakeCountPool(count=0)),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "rprof-1",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-2",
                                    "node_kind": "mac_mini",
                                    "online": True,
                                    "healthy": True,
                                    "owner_approved": True,
                                    "self_hosted_node_status": "online",
                                    "max_concurrent_sessions": 1,
                                    "allowed_agent_ids": ["dagent_1"],
                                    "capabilities": ["shell.execute"],
                                }
                            ]
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "workspace scope mismatch|outside the requested workspace scope"):
                    await deployed_agent_virtual_runtime_service.ensure_self_hosted_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                    )
                return runtime

        import asyncio

        runtime = asyncio.run(_run())
        runtime.create_session.assert_not_called()

    def test_ensure_self_hosted_runtime_session_binding_rejects_offline_node(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock(return_value={"session_id": "shsess_1"})
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    },
                    "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                        studio_agent_mode="self_hosted_agent",
                        domain_allowlist=["intranet.example"],
                    ),
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "ensure_control_plane_schema",
                    new=AsyncMock(return_value=_FakeCountPool(count=0)),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "rprof-1",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": False,
                                    "healthy": True,
                                    "owner_approved": True,
                                    "self_hosted_node_status": "offline",
                                    "max_concurrent_sessions": 1,
                                    "allowed_agent_ids": ["dagent_1"],
                                    "capabilities": ["shell.execute"],
                                }
                            ]
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "offline"):
                    await deployed_agent_virtual_runtime_service.ensure_self_hosted_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                    )
                return runtime

        import asyncio

        runtime = asyncio.run(_run())
        runtime.create_session.assert_not_called()

    def test_ensure_self_hosted_runtime_session_binding_rejects_missing_required_capabilities(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock(return_value={"session_id": "shsess_1"})
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    },
                    "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                        studio_agent_mode="self_hosted_agent",
                        domain_allowlist=["intranet.example"],
                    ),
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "ensure_control_plane_schema",
                    new=AsyncMock(return_value=_FakeCountPool(count=0)),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "rprof-1",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": True,
                                    "healthy": True,
                                    "owner_approved": True,
                                    "self_hosted_node_status": "online",
                                    "max_concurrent_sessions": 1,
                                    "allowed_agent_ids": ["dagent_1"],
                                    "capabilities": ["browser.navigate"],
                                }
                            ]
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "required capabilities"):
                    await deployed_agent_virtual_runtime_service.ensure_self_hosted_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                    )
                return runtime

        import asyncio

        runtime = asyncio.run(_run())
        runtime.create_session.assert_not_called()

    def test_text_agent_cannot_create_runtime_session_from_crafted_payload(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock(
                return_value={
                    "browser_session": {"browser_session_id": "vcsess_1"},
                    "provider_id": "provider_demo",
                    "provider_kind": "managed_cloud",
                }
            )
            registry = Mock()
            registry.resolve.return_value = runtime
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(
                        return_value=_deployed_agent(
                            config={"studio_agent_mode": "text_agent"},
                        )
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "studio_agent_mode"):
                    await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                        session_metadata={
                            "runtime_session_binding": "cloud_computer_agent",
                            "runtime_session_id": "vcsess_attacker",
                        },
                        turn_metadata={
                            "deployed_agent_id": "dagent_1",
                            "runtime_choice": "virtual_browser",
                            "runtime_provider_id": "provider_attacker",
                        },
                    )
            runtime.create_session.assert_not_called()
            registry.resolve.assert_not_called()

        import asyncio

        asyncio.run(_run())

    def test_execute_bound_cloud_runtime_tool_call_uses_execute_action(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock(
                return_value={
                    "status": "ok",
                    "browser_session": {"browser_session_id": "vcsess_1"},
                    "action_result": {"ok": True, "action": "open_url"},
                }
            )
            registry = Mock()
            registry.resolve.return_value = runtime
            session_ctx = {
                "tenant_id": "tenant-1",
                "agent_turn_request": {
                    "context_hints": {
                        "metadata": {
                            "deployed_agent_id": "dagent_1",
                            "runtime_session_id": "vcsess_1",
                            "runtime_session_binding": "cloud_computer_agent",
                        }
                    }
                },
            }
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=_deployed_agent()),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                return await deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call(
                    connector_id="browser",
                    action_id="navigate",
                    argument_payload={"url": "https://supplier.example"},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx=session_ctx,
                ), runtime, append_activity_event

        import asyncio

        result, runtime, append_activity_event = asyncio.run(_run())
        payload = runtime.execute_action.await_args.args[0]
        self.assertEqual(payload["browser_session_id"], "vcsess_1")
        self.assertEqual(payload["action"], "open_url")
        self.assertEqual(payload["action_args"]["url"], "https://supplier.example")
        self.assertEqual(payload["metadata"]["runtime_session_binding"], "cloud_computer_agent")
        self.assertIn("\"open_url\"", result)
        self.assertEqual(
            append_activity_event.await_args.kwargs["action"],
            "deployed_agent_cloud_runtime_action_admitted",
        )

    def test_execute_bound_cloud_runtime_tool_call_stores_screenshot_proof(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock(
                return_value={
                    "status": "completed",
                    "state": "running",
                    "provider_id": "digitalocean_ssh",
                    "runtime_kind": "digitalocean_ssh_cloud_computer",
                    "artifact": {
                        "artifact_id": "artifact_cloud_1",
                        "artifact_type": "screenshot",
                        "type": "screenshot",
                        "remote_path": "/tmp/empyralis/artifact_cloud_1.png",
                        "url": "https://supplier.example",
                        "title": "Supplier",
                    },
                    "artifacts": [
                        {
                            "artifact_id": "artifact_cloud_1",
                            "artifact_type": "screenshot",
                            "type": "screenshot",
                            "remote_path": "/tmp/empyralis/artifact_cloud_1.png",
                            "url": "https://supplier.example",
                            "title": "Supplier",
                        }
                    ],
                    "streaming_ui": {
                        "current_url": "https://supplier.example",
                        "app_title": "Supplier",
                    },
                    "action_result": {"ok": True, "action": "open_url"},
                }
            )
            runtime.collect_artifact = AsyncMock(
                return_value={
                    "artifact": {
                        "artifact_id": "artifact_cloud_1",
                        "artifact_type": "screenshot",
                        "content_base64": "ZmFrZS1wbmc=",
                        "file_name": "artifact_cloud_1.png",
                        "content_type": "image/png",
                        "remote_path": "/tmp/empyralis/artifact_cloud_1.png",
                        "url": "https://supplier.example",
                        "title": "Supplier",
                        "proof_envelope": {"sha256": "abc123"},
                    }
                }
            )
            registry = Mock()
            registry.resolve.return_value = runtime
            stored_record = Mock()
            stored_record.as_payload.return_value = {
                "artifact_id": "artifact_cloud_1",
                "run_id": "run-1",
                "uri": "artifact://artifact_cloud_1/artifact_cloud_1.png",
                "uri_or_path": "artifact://artifact_cloud_1/artifact_cloud_1.png",
                "kind": "screenshot",
                "content_type": "image/png",
                "byte_size": 8,
                "file_name": "artifact_cloud_1.png",
            }
            session_ctx = {
                "tenant_id": "tenant-1",
                "run_id": "run-1",
                "agent_turn_request": {
                    "context_hints": {
                        "metadata": {
                            "deployed_agent_id": "dagent_1",
                            "runtime_session_id": "vcsess_1",
                            "runtime_session_binding": "cloud_computer_agent",
                            "run_id": "run-1",
                        }
                    }
                },
            }
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=_deployed_agent()),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
                patch.object(
                    deployed_agent_virtual_runtime_service.session_service,
                    "extend_session",
                    new=AsyncMock(),
                ) as extend_session,
                patch.object(
                    deployed_agent_virtual_runtime_service.artifact_service,
                    "store_artifact_bytes",
                    return_value=stored_record,
                ) as store_artifact_bytes,
            ):
                result = await deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call(
                    connector_id="browser",
                    action_id="navigate",
                    argument_payload={"url": "https://supplier.example"},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx=session_ctx,
                )
                return result, runtime, append_activity_event, extend_session, store_artifact_bytes

        import asyncio

        result, runtime, append_activity_event, extend_session, store_artifact_bytes = asyncio.run(_run())
        runtime.collect_artifact.assert_awaited_once()
        self.assertEqual(store_artifact_bytes.call_args.args[0], b"fake-png")
        self.assertEqual(store_artifact_bytes.call_args.kwargs["run_id"], "run-1")
        self.assertEqual(store_artifact_bytes.call_args.kwargs["tenant_id"], "tenant-1")
        self.assertEqual(store_artifact_bytes.call_args.kwargs["workspace_id"], "ws-1")
        self.assertEqual(store_artifact_bytes.call_args.kwargs["agent_install_id"], "ainstall_1")
        self.assertEqual(store_artifact_bytes.call_args.kwargs["artifact_id"], "artifact_cloud_1")
        extend_session.assert_awaited_once()
        proof = extend_session.await_args.kwargs["metadata_updates"]["latest_computer_proof"]
        self.assertEqual(proof["artifact_uri"], "artifact://artifact_cloud_1/artifact_cloud_1.png")
        self.assertEqual(append_activity_event.await_args.kwargs["run_id"], "run-1")
        self.assertEqual(append_activity_event.await_args.kwargs["thread_id"], "thread-1")
        self.assertEqual(append_activity_event.await_args.kwargs["session_key"], "vcsess_1")
        self.assertEqual(
            append_activity_event.await_args.kwargs["artifacts"][0]["uri"],
            "artifact://artifact_cloud_1/artifact_cloud_1.png",
        )
        parsed = json.loads(result)
        self.assertEqual(parsed["computer_proof"]["artifact_id"], "artifact_cloud_1")
        self.assertEqual(parsed["artifact"]["uri"], "artifact://artifact_cloud_1/artifact_cloud_1.png")

    def test_execute_bound_cloud_runtime_tool_call_rejects_missing_runtime_session(self):
        async def _run():
            with patch.object(
                deployed_agent_virtual_runtime_service.control_plane_repository,
                "get_deployed_agent_by_id",
                new=AsyncMock(return_value=_deployed_agent()),
            ):
                return await deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call(
                    connector_id="browser",
                    action_id="navigate",
                    argument_payload={"url": "https://supplier.example"},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={
                        "tenant_id": "tenant-1",
                        "agent_turn_request": {
                            "context_hints": {
                                "metadata": {
                                    "deployed_agent_id": "dagent_1",
                                    "runtime_session_binding": "cloud_computer_agent",
                                }
                            }
                        },
                    },
                )

        import asyncio

        with self.assertRaisesRegex(RuntimeError, "runtime_session_id"):
            asyncio.run(_run())

    def test_execute_bound_cloud_runtime_tool_call_rejects_selector_browser_mutations(self):
        async def _run():
            with patch.object(
                deployed_agent_virtual_runtime_service.control_plane_repository,
                "get_deployed_agent_by_id",
                new=AsyncMock(return_value=_deployed_agent()),
            ):
                return await deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call(
                    connector_id="browser",
                    action_id="fill",
                    argument_payload={"selector": "#email", "value": "x"},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={
                        "tenant_id": "tenant-1",
                        "agent_turn_request": {
                            "context_hints": {
                                "metadata": {
                                    "deployed_agent_id": "dagent_1",
                                    "runtime_session_id": "vcsess_1",
                                    "runtime_session_binding": "cloud_computer_agent",
                                }
                            }
                        },
                    },
                )

        import asyncio

        with self.assertRaisesRegex(RuntimeError, "does not support browser__fill"):
            asyncio.run(_run())

    def test_execute_bound_cloud_runtime_tool_call_emits_policy_override_rejection(self):
        async def _run():
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=_deployed_agent()),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                try:
                    await deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call(
                        connector_id="browser",
                        action_id="navigate",
                        argument_payload={
                            "url": "https://supplier.example",
                            "network_policy": {"allowed_domains": ["evil.example"]},
                        },
                        workspace_id="ws-1",
                        thread_id="thread-1",
                        session_ctx={
                            "tenant_id": "tenant-1",
                            "agent_turn_request": {
                                "context_hints": {
                                    "metadata": {
                                        "deployed_agent_id": "dagent_1",
                                        "runtime_session_id": "vcsess_1",
                                        "runtime_session_binding": "cloud_computer_agent",
                                    }
                                }
                            },
                        },
                    )
                except RuntimeError as exc:
                    return str(exc), append_activity_event
            return "", None

        import asyncio

        error, append_activity_event = asyncio.run(_run())
        self.assertIn("raw policy override", error)
        self.assertEqual(
            append_activity_event.await_args.kwargs["action"],
            "deployed_agent_cloud_runtime_policy_override_rejected",
        )

    def test_execute_bound_cloud_runtime_tool_call_emits_kill_state_rejection(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock(side_effect=RuntimeError("Runtime admission gate rejected session/action: agent_suspended."))
            registry = Mock()
            registry.resolve.return_value = runtime
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=_deployed_agent(deployment_state="suspended")),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                try:
                    await deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call(
                        connector_id="browser",
                        action_id="navigate",
                        argument_payload={"url": "https://supplier.example"},
                        workspace_id="ws-1",
                        thread_id="thread-1",
                        session_ctx={
                            "tenant_id": "tenant-1",
                            "agent_turn_request": {
                                "context_hints": {
                                    "metadata": {
                                        "deployed_agent_id": "dagent_1",
                                        "runtime_session_id": "vcsess_1",
                                        "runtime_session_binding": "cloud_computer_agent",
                                    }
                                }
                            },
                        },
                    )
                except RuntimeError:
                    return append_activity_event.await_args_list
            return []

        import asyncio

        calls = asyncio.run(_run())
        actions = [call.kwargs["action"] for call in calls]
        self.assertIn("deployed_agent_cloud_runtime_action_denied", actions)
        self.assertIn("deployed_agent_cloud_runtime_kill_state_rejected", actions)

    def test_execute_bound_self_hosted_runtime_tool_call_uses_execute_action(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock(
                return_value={
                    "status": "ok",
                    "session_id": "shsess_1",
                    "action_result": {"ok": True, "action": "run_command"},
                }
            )
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    }
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "ensure_control_plane_schema",
                    new=AsyncMock(return_value=_FakeCountPool(count=0)),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "rprof-1",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": True,
                                    "healthy": True,
                                    "owner_approved": True,
                                    "self_hosted_node_status": "online",
                                    "max_concurrent_sessions": 1,
                                    "allowed_agent_ids": ["dagent_1"],
                                    "capabilities": ["shell.execute"],
                                }
                            ]
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                result = await deployed_agent_virtual_runtime_service.execute_bound_self_hosted_runtime_tool_call(
                    connector_id="shell",
                    action_id="exec",
                    argument_payload={"command": "echo hi"},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={
                        "tenant_id": "tenant-1",
                        "agent_turn_request": {
                            "context_hints": {
                                "metadata": {
                                    "deployed_agent_id": "dagent_1",
                                    "runtime_session_id": "shsess_1",
                                    "runtime_session_binding": "self_hosted_agent",
                                }
                            }
                        },
                    },
                )
                return result, runtime, append_activity_event

        import asyncio

        result, runtime, append_activity_event = asyncio.run(_run())
        payload = runtime.execute_action.await_args.args[0]
        self.assertEqual(payload["session_id"], "shsess_1")
        self.assertEqual(payload["runtime_node_id"], "node-1")
        self.assertEqual(payload["action"], "run_command")
        self.assertTrue(payload["local_sandbox_policy"]["enabled"])
        self.assertEqual(payload["local_sandbox_policy"]["filesystem_scope"], "none")
        self.assertEqual(payload["local_sandbox_policy"]["approved_working_directories"], ["/workspace"])
        self.assertFalse(payload["local_sandbox_policy"]["allow_host_env_inheritance"])
        self.assertIn('"run_command"', result)
        self.assertEqual(
            append_activity_event.await_args.kwargs["action"],
            "deployed_agent_self_hosted_runtime_action_admitted",
        )

    def test_execute_bound_self_hosted_runtime_tool_call_rejects_unbound_runtime_required_action(self):
        async def _run():
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    }
                },
            )
            with patch.object(
                deployed_agent_virtual_runtime_service.control_plane_repository,
                "get_deployed_agent_by_id",
                new=AsyncMock(return_value=deployed),
            ):
                return await deployed_agent_virtual_runtime_service.execute_bound_self_hosted_runtime_tool_call(
                    connector_id="browser",
                    action_id="navigate",
                    argument_payload={"url": "https://intranet.example"},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={
                        "tenant_id": "tenant-1",
                        "agent_turn_request": {
                            "context_hints": {
                                "metadata": {
                                    "deployed_agent_id": "dagent_1",
                                    "runtime_session_binding": "self_hosted_agent",
                                }
                            }
                        },
                    },
                )

        import asyncio

        with self.assertRaisesRegex(RuntimeError, "runtime_session_id"):
            asyncio.run(_run())

    def test_execute_bound_self_hosted_runtime_tool_call_blocks_revoked_node_on_next_action(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock(return_value={"status": "ok"})
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    }
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "ensure_control_plane_schema",
                    new=AsyncMock(return_value=_FakeCountPool(count=0)),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "rprof-1",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": False,
                                    "healthy": False,
                                    "owner_approved": True,
                                    "self_hosted_node_status": "revoked",
                                    "max_concurrent_sessions": 1,
                                    "allowed_agent_ids": ["dagent_1"],
                                    "capabilities": ["shell.execute"],
                                }
                            ]
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                with self.assertRaisesRegex(RuntimeError, "revoked"):
                    await deployed_agent_virtual_runtime_service.execute_bound_self_hosted_runtime_tool_call(
                        connector_id="shell",
                        action_id="exec",
                        argument_payload={"command": "echo hi"},
                        workspace_id="ws-1",
                        thread_id="thread-1",
                        session_ctx={
                            "tenant_id": "tenant-1",
                            "agent_turn_request": {
                                "context_hints": {
                                    "metadata": {
                                        "deployed_agent_id": "dagent_1",
                                        "runtime_session_id": "shsess_1",
                                        "runtime_session_binding": "self_hosted_agent",
                                    }
                                }
                            },
                        },
                    )
                return runtime, append_activity_event

        import asyncio

        runtime, append_activity_event = asyncio.run(_run())
        runtime.execute_action.assert_not_called()
        self.assertEqual(
            append_activity_event.await_args.kwargs["action"],
            "deployed_agent_self_hosted_runtime_action_denied",
        )

    def test_execute_bound_self_hosted_runtime_tool_call_rejects_runtime_node_override_payload(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock(return_value={"status": "ok"})
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    }
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "ensure_control_plane_schema",
                    new=AsyncMock(return_value=_FakeCountPool(count=0)),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "rprof-1",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": True,
                                    "healthy": True,
                                    "owner_approved": True,
                                    "self_hosted_node_status": "online",
                                    "max_concurrent_sessions": 1,
                                    "allowed_agent_ids": ["dagent_1"],
                                    "capabilities": ["shell.execute"],
                                }
                            ]
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=AsyncMock(),
                ) as append_activity_event,
            ):
                with self.assertRaisesRegex(RuntimeError, "raw policy override"):
                    await deployed_agent_virtual_runtime_service.execute_bound_self_hosted_runtime_tool_call(
                        connector_id="shell",
                        action_id="exec",
                        argument_payload={
                            "command": "echo hi",
                            "runtime_node_id": "node-attacker",
                        },
                        workspace_id="ws-1",
                        thread_id="thread-1",
                        session_ctx={
                            "tenant_id": "tenant-1",
                            "agent_turn_request": {
                                "context_hints": {
                                    "metadata": {
                                        "deployed_agent_id": "dagent_1",
                                        "runtime_session_id": "shsess_1",
                                        "runtime_session_binding": "self_hosted_agent",
                                    }
                                }
                            },
                        },
                    )
                return runtime, append_activity_event

        import asyncio

        runtime, append_activity_event = asyncio.run(_run())
        runtime.execute_action.assert_not_called()
        self.assertEqual(
            append_activity_event.await_args.kwargs["action"],
            "deployed_agent_self_hosted_runtime_policy_override_rejected",
        )

    def test_execute_bound_self_hosted_runtime_tool_call_ignores_cloud_agent_with_crafted_binding(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock(return_value={"status": "ok"})
            registry = Mock()
            registry.resolve.return_value = runtime
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(
                        return_value=_deployed_agent(
                            config={"studio_agent_mode": "cloud_computer_agent"},
                        )
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
            ):
                result = await deployed_agent_virtual_runtime_service.execute_bound_self_hosted_runtime_tool_call(
                    connector_id="shell",
                    action_id="exec",
                    argument_payload={"command": "echo hi"},
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={
                        "tenant_id": "tenant-1",
                        "agent_turn_request": {
                            "context_hints": {
                                "metadata": {
                                    "deployed_agent_id": "dagent_1",
                                    "runtime_session_id": "shsess_attacker",
                                    "runtime_session_binding": "self_hosted_agent",
                                }
                            }
                        },
                    },
                )
                return result, runtime, registry

        import asyncio

        result, runtime, registry = asyncio.run(_run())
        self.assertIsNone(result)
        runtime.execute_action.assert_not_called()
        registry.resolve.assert_not_called()

    def test_ensure_self_hosted_runtime_session_binding_blocks_node_concurrency_over_limit(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock(return_value={"session_id": "shsess_1"})
            registry = Mock()
            registry.resolve.return_value = runtime
            deployed = _deployed_agent(
                config={
                    "studio_agent_mode": "self_hosted_agent",
                    "runtime_target": "self_host_runtime",
                    "runtime_profile_id": "rprof-1",
                    "computer_automation": {
                        "enabled": True,
                        "runtime_class": "virtual_code_sandbox",
                        "allowed_domains": ["intranet.example"],
                        "max_concurrent_sessions": 1,
                        "daily_budget_usd": 5,
                        "monthly_budget_usd": 25,
                    },
                },
                metadata={
                    "self_hosted_runtime_binding": {
                        "runtime_target": "self_host_runtime",
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-1",
                        "runtime_attachment_id": "attach-1",
                        "runtime_profile_id": "rprof-1",
                        "allowed_capabilities": ["shell.execute"],
                        "filesystem_scope": "none",
                        "domain_allowlist": ["intranet.example"],
                        "approval_policy": {"requires_owner_approval": True},
                        "quota_policy": {"max_concurrent_sessions": 1},
                    },
                    "computer_safety_contract_snapshot": _computer_safety_contract_snapshot(
                        studio_agent_mode="self_hosted_agent",
                        domain_allowlist=["intranet.example"],
                    ),
                },
            )
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "get_deployed_agent_by_id",
                    new=AsyncMock(return_value=deployed),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "ensure_control_plane_schema",
                    new=AsyncMock(return_value=_FakeCountPool(count=1)),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.runtime_attachment_service,
                    "list_workspace_runtime_attachments",
                    new=AsyncMock(
                        return_value={
                            "attachments": [
                                {
                                    "attachment_kind": "self_hosted_business_node",
                                    "runtime_profile_id": "rprof-1",
                                    "runtime_node_id": "node-1",
                                    "workspace_id": "ws-1",
                                    "node_kind": "mac_mini",
                                    "online": True,
                                    "healthy": True,
                                    "owner_approved": True,
                                    "self_hosted_node_status": "online",
                                    "max_concurrent_sessions": 1,
                                    "allowed_agent_ids": ["dagent_1"],
                                    "capabilities": ["shell.execute"],
                                }
                            ]
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "concurrency quota exceeded"):
                    await deployed_agent_virtual_runtime_service.ensure_self_hosted_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                    )
                return runtime

        import asyncio

        runtime = asyncio.run(_run())
        runtime.create_session.assert_not_called()

    def test_terminate_bound_cloud_runtime_session_calls_runtime_terminate(self):
        async def _run():
            runtime = Mock()
            runtime.terminate_session = AsyncMock(return_value={"status": "terminated"})
            registry = Mock()
            registry.resolve.return_value = runtime
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.session_service,
                    "get_session",
                    new=AsyncMock(
                        return_value={
                            "session_id": "sess-1",
                            "tenant_id": "tenant-1",
                            "workspace_id": "ws-1",
                            "metadata": {
                                "deployed_agent_id": "dagent_1",
                                "runtime_session_binding": "cloud_computer_agent",
                                "runtime_session_id": "vcsess_1",
                                "runtime_choice": "virtual_browser",
                                "runtime_provider_id": "provider_demo",
                            },
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
            ):
                return await deployed_agent_virtual_runtime_service.terminate_bound_cloud_runtime_session(
                    session_id="sess-1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                ), runtime

        import asyncio

        result, runtime = asyncio.run(_run())
        payload = runtime.terminate_session.await_args.args[0]
        self.assertEqual(payload["browser_session_id"], "vcsess_1")
        self.assertTrue(payload["manual_terminate"])
        self.assertEqual(result["status"], "terminated")

    def test_terminate_bound_cloud_runtime_session_records_runtime_credit_event(self):
        async def _run():
            runtime = Mock()
            runtime.terminate_session = AsyncMock(return_value={"status": "terminated"})
            registry = Mock()
            registry.resolve.return_value = runtime
            append_activity = AsyncMock(return_value={"id": "activity-1"})
            record_credit = AsyncMock(return_value={"id": "cled_runtime_1"})
            extend_session = AsyncMock()
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.session_service,
                    "get_session",
                    new=AsyncMock(
                        return_value={
                            "session_id": "sess-1",
                            "tenant_id": "tenant-1",
                            "workspace_id": "ws-1",
                            "metadata": {
                                "deployed_agent_id": "dagent_1",
                                "thread_id": "thread-1",
                                "run_id": "run-1",
                                "runtime_session_binding": "cloud_computer_agent",
                                "runtime_session_id": "vcsess_1",
                                "runtime_choice": "virtual_browser",
                                "runtime_provider_id": "provider_demo",
                                "runtime_session_started_at": "2026-05-19T08:00:00Z",
                                "runtime_max_session_seconds": 3600,
                            },
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "_utc_now_iso",
                    return_value="2026-05-19T08:05:00Z",
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.activity_ledger_service,
                    "append_activity_event",
                    new=append_activity,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.control_plane_repository,
                    "record_credit_ledger_event",
                    new=record_credit,
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.session_service,
                    "extend_session",
                    new=extend_session,
                ),
            ):
                return await deployed_agent_virtual_runtime_service.terminate_bound_cloud_runtime_session(
                    session_id="sess-1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                ), append_activity, record_credit, extend_session

        import asyncio

        result, append_activity, record_credit, extend_session = asyncio.run(_run())
        self.assertEqual(result["status"], "terminated")
        append_activity.assert_awaited_once()
        kwargs = append_activity.await_args.kwargs
        self.assertEqual(kwargs["action"], "studio_runtime_computer_runtime_metered")
        self.assertEqual(kwargs["metadata"]["unified_credit_ledger_event"]["credit_type"], "computer_runtime")
        self.assertEqual(kwargs["metadata"]["unified_credit_ledger_event"]["runtime_target"], "sage_cloud_computer")
        self.assertEqual(kwargs["metadata"]["unified_credit_ledger_event"]["provider_usage"]["billable_seconds"], 300.0)
        record_credit.assert_awaited_once()
        self.assertEqual(record_credit.await_args.kwargs["source_table"], "activity_ledger_events")
        self.assertEqual(record_credit.await_args.kwargs["source_event_id"], "activity-1")
        extend_session.assert_awaited_once()
        self.assertEqual(extend_session.await_args.kwargs["metadata_updates"]["runtime_metered_event_id"], "activity-1")

    def test_terminate_bound_self_hosted_runtime_session_calls_runtime_terminate(self):
        async def _run():
            runtime = Mock()
            runtime.terminate_session = AsyncMock(return_value={"status": "terminated"})
            registry = Mock()
            registry.resolve.return_value = runtime
            with (
                patch.object(
                    deployed_agent_virtual_runtime_service.session_service,
                    "get_session",
                    new=AsyncMock(
                        return_value={
                            "session_id": "sess-1",
                            "tenant_id": "tenant-1",
                            "workspace_id": "ws-1",
                            "metadata": {
                                "deployed_agent_id": "dagent_1",
                                "runtime_session_binding": "self_hosted_agent",
                                "runtime_session_id": "shsess_1",
                                "runtime_choice": "virtual_code_sandbox",
                                "runtime_provider_id": "docker_kubernetes",
                                "runtime_node_id": "node-1",
                                "runtime_attachment_id": "attach-1",
                                "runtime_profile_id": "rprof-1",
                            },
                        }
                    ),
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service,
                    "get_runtime_registry",
                    return_value=registry,
                ),
            ):
                return await deployed_agent_virtual_runtime_service.terminate_bound_self_hosted_runtime_session(
                    session_id="sess-1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                ), runtime

        import asyncio

        result, runtime = asyncio.run(_run())
        payload = runtime.terminate_session.await_args.args[0]
        self.assertEqual(payload["session_id"], "shsess_1")
        self.assertEqual(payload["runtime_node_id"], "node-1")
        self.assertTrue(payload["manual_terminate"])
        self.assertEqual(result["status"], "terminated")


if __name__ == "__main__":
    unittest.main()
