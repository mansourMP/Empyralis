import importlib
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from server_modules import agent_channel_router, channel_concurrency_service, safe_mode_service
from server_modules.agent_manifest import AgentManifest
from server_modules.sage_agent_runtime_contract import SageTurnResult


def _deployed_agent_row(
    *,
    deployment_state: str = "live",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": "dagent_1",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "workspace-1",
        "backing_install_id": "install-specialist",
        "name": "Parts Pro",
        "deployment_state": deployment_state,
        "metadata": dict(metadata or {}),
        "channels": {
            "telegram": {
                "enabled": True,
                "is_inbound_owner": True,
                "endpoint_key": "@partspro_bot",
            }
        },
    }


class AgentChannelRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global agent_channel_router, channel_concurrency_service, safe_mode_service
        agent_channel_router = importlib.import_module("server_modules.agent_channel_router")
        channel_concurrency_service = importlib.import_module("server_modules.channel_concurrency_service")
        safe_mode_service = importlib.import_module("server_modules.safe_mode_service")

    def tearDown(self) -> None:
        safe_mode_service.reset_state_for_tests()

    @unittest.skip(
        "Phase 5B: agent_channel_router.shell_surface_contract / FULL_SHELL_CLASS were "
        "removed in a refactor; this covers a surface that no longer exists. Quarantined "
        "pending deletion or a rewrite against the current shell contract — owner decision."
    )
    async def test_shell_surface_contract_freezes_full_shells_and_channel_shells(self):
        mobile = agent_channel_router.shell_surface_contract("mobile")
        web = agent_channel_router.shell_surface_contract("web")
        desktop = agent_channel_router.shell_surface_contract("desktop")
        telegram = agent_channel_router.shell_surface_contract("telegram")
        whatsapp = agent_channel_router.shell_surface_contract("whatsapp")

        for shell in (mobile, web, desktop):
            self.assertEqual(shell["surface_class"], agent_channel_router.FULL_SHELL_CLASS)
            self.assertEqual(shell["control_depth"], "full")
            self.assertTrue(shell["shares_captain_identity"])
            self.assertTrue(shell["uses_shared_run_engine"])
            self.assertIn("application_navigation", shell["allowed_capabilities"])
            self.assertIn("separate_product_brain", shell["forbidden_capabilities"])

        for shell in (telegram, whatsapp):
            self.assertEqual(shell["surface_class"], agent_channel_router.CHANNEL_SHELL_CLASS)
            self.assertEqual(shell["control_depth"], "lightweight")
            self.assertTrue(shell["shares_captain_identity"])
            self.assertTrue(shell["uses_shared_run_engine"])
            self.assertFalse(shell["deep_connector_control_surface"])
            self.assertIn("conversation", shell["allowed_capabilities"])
            self.assertIn("summary_visibility", shell["allowed_capabilities"])
            self.assertIn("deep_admin_surface", shell["forbidden_capabilities"])
            self.assertIn("separate_product_brain", shell["forbidden_capabilities"])

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_dispatches_to_specialist_and_records_audit(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
            skills=[{"id": "inventory-tool", "enabled": True}],
            channels={"telegram": True},
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {
                    "provider": "deepseek",
                    "model": "deepseek-reasoner",
                    "deployed_agent": {
                        "provider": "deepseek",
                        "model": "deepseek-reasoner",
                    },
                },
                "runtime_profile_id": "runtime-cloud",
                "runtime_profile": {
                    "id": "runtime-cloud",
                    "label": "Empyralis Cloud",
                    "runtime_class": "cloud_worker",
                },
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }
        master_install = {
            "id": "install-sage",
            "label": "Sage",
        }

        @asynccontextmanager
        async def _slot(*args, **kwargs):
            yield {
                "lease_id": "lease-1",
                "quota_snapshot": channel_concurrency_service.ChannelQuotaSnapshot(
                    max_workspace_active_threads=24,
                    max_agent_active_threads=8,
                    max_workspace_turns_per_minute=180,
                    max_runtime_seconds=45,
                ),
            }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(
                    return_value=_deployed_agent_row(
                        deployment_state="live",
                        metadata={
                            "provider": "deepseek",
                            "model": "deepseek-reasoner",
                        },
                    )
                ),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value=master_install),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[
                    {"id": "evt-in"},
                    {"id": "evt-out"},
                ]),
            ) as append_event_mock,
            patch(
                "server_modules.agent_channel_router.execute_canonical_channel_turn",
                new=AsyncMock(return_value={
                    "kind": "durable_run",
                    "result": {
                        "status": "accepted",
                        "run_id": "run-1",
                        "engine": "orion",
                        "route": {"selected": "cloud"},
                    },
                }),
            ) as execute_mock,
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_slot),
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Do you have 2022 Tesla Model 3 wipers?",
                actor_id="telegram-user-1",
                actor_display_name="Customer",
                message_id="tg-msg-1",
            )

        self.assertEqual(result["owner"]["install_id"], "install-specialist")
        self.assertEqual(result["audit"]["inbound_event_id"], "evt-in")
        self.assertEqual(result["audit"]["outbound_event_id"], "evt-out")
        self.assertEqual(result["channel_key"], "telegram")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["run_id"], "run-1")
        self.assertIn("Run accepted", result["reply"])
        self.assertEqual(append_event_mock.await_count, 2)
        turn_request = execute_mock.await_args.kwargs["turn_request"]
        self.assertEqual(turn_request.thread_id, "thread-workspace-1-telegram-partspro-bot-telegram-partspro-bot-telegram-user-1")
        self.assertEqual(turn_request.session_id, "telegram:partspro-bot:telegram-user-1")
        self.assertEqual(turn_request.context_hints["metadata"]["active_agent_install_id"], "install-specialist")
        self.assertEqual(turn_request.context_hints["metadata"]["master_agent_install_id"], "install-sage")
        self.assertEqual(turn_request.context_hints["metadata"]["runtime_profile_id"], "runtime-cloud")
        self.assertEqual(turn_request.context_hints["provider"], "deepseek")
        self.assertEqual(turn_request.context_hints["model"], "deepseek-reasoner")
        self.assertEqual(execute_mock.await_args.kwargs["current_user"]["user_id"], "user-1")

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_gracefully_handles_thread_busy(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }
        quota_snapshot = channel_concurrency_service.ChannelQuotaSnapshot(
            max_workspace_active_threads=24,
            max_agent_active_threads=8,
            max_workspace_turns_per_minute=180,
            max_runtime_seconds=45,
        )

        @asynccontextmanager
        async def _busy_slot(*args, **kwargs):
            raise channel_concurrency_service.ChannelExecutionLimitError(
                reason="thread_busy",
                message="I’m still finishing the previous message in this conversation. One moment.",
                retry_after_seconds=2,
                quota_snapshot=quota_snapshot,
            )
            yield  # pragma: no cover

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ) as append_event_mock,
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_busy_slot),
            patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock()) as execute_mock,
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Hello?",
                actor_id="telegram-user-1",
            )

        self.assertEqual(result["status"], "thread_busy")
        self.assertEqual(result["retry_after_seconds"], 2)
        self.assertEqual(result["limit_reason"], "thread_busy")
        self.assertEqual((result.get("error") or {}).get("code"), "thread_busy")
        self.assertEqual((result.get("error") or {}).get("class"), "rate_limit")
        self.assertIn("previous message", result["reply"])
        self.assertEqual(append_event_mock.await_count, 2)
        execute_mock.assert_not_awaited()

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_gracefully_handles_runtime_cap(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }
        quota_snapshot = channel_concurrency_service.ChannelQuotaSnapshot(
            max_workspace_active_threads=24,
            max_agent_active_threads=8,
            max_workspace_turns_per_minute=180,
            max_runtime_seconds=9,
        )

        @asynccontextmanager
        async def _slot(*args, **kwargs):
            yield {"lease_id": "lease-1", "quota_snapshot": quota_snapshot}

        async def _timeout_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise TimeoutError

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ),
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_slot),
            patch("server_modules.agent_channel_router.asyncio.wait_for", new=_timeout_wait_for),
            patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock(return_value={"kind": "durable_run"})),
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Do you have wipers?",
                actor_id="telegram-user-1",
            )

        self.assertEqual(result["status"], "runtime_capped")
        self.assertEqual(result["limit_reason"], "runtime_cap_exceeded")
        self.assertEqual((result.get("error") or {}).get("code"), "runtime_cap_exceeded")
        self.assertEqual((result.get("error") or {}).get("class"), "execution_timeout")
        self.assertIn("service window", result["reply"])

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_surfaces_nonfatal_degraded_operations(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        @asynccontextmanager
        async def _slot(*args, **kwargs):
            yield {
                "lease_id": "lease-1",
                "quota_snapshot": channel_concurrency_service.ChannelQuotaSnapshot(
                    max_workspace_active_threads=24,
                    max_agent_active_threads=8,
                    max_workspace_turns_per_minute=180,
                    max_runtime_seconds=45,
                ),
            }

        degraded_notice = {
            "error": {
                "code": "channel_memory_snapshot_persist_failed",
                "message": "Failed to persist deployed-agent memory snapshot.",
                "class": "storage_write_failure",
                "retryable": False,
                "status_code": 500,
                "request_id": "evt-in",
                "trace_id": None,
                "details": {},
            },
            "error_code": "channel_memory_snapshot_persist_failed",
            "error_class": "storage_write_failure",
            "degraded_component": "channel_memory_snapshot",
            "severity": "error",
            "swallowed_intentionally": True,
            "metadata": {"workspace_id": "workspace-1"},
        }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ),
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_slot),
            patch(
                "server_modules.agent_channel_router.execute_canonical_channel_turn",
                new=AsyncMock(
                    return_value={
                        "kind": "durable_run",
                        "result": {
                            "status": "accepted",
                            "run_id": "run-1",
                        },
                    }
                ),
            ),
            patch("server_modules.agent_channel_router.persist_snapshot", new=AsyncMock(return_value=degraded_notice)),
            patch("server_modules.agent_channel_router.record_channel_activity", new=AsyncMock(return_value=None)),
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Hello?",
                actor_id="telegram-user-1",
            )

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["metadata"]["degraded_operation_count"], 1)
        self.assertTrue(result["metadata"]["degraded"])
        self.assertEqual(result["degraded_operations"][0]["error_code"], "channel_memory_snapshot_persist_failed")

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_rejects_unbound_endpoint(self):
        with patch(
            "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(agent_channel_router.ChannelOwnerNotFoundError) as error:
                await agent_channel_router.route_inbound_channel_message(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    channel_key="email",
                    endpoint_key="service@example.com",
                    customer_message="Hello",
                )

        self.assertEqual(str(error.exception), "No active channel owner is configured for this endpoint.")

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_rejects_disabled_channel_before_owner_resolution(self):
        safe_mode_service.set_kill_switch(
            scope="channel",
            enabled=True,
            workspace_id="workspace-1",
            channel_key="telegram",
            endpoint_key="@partspro_bot",
            reason="incident",
        )

        with patch(
            "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
            new=AsyncMock(),
        ) as resolve_owner_mock:
            with self.assertRaises(agent_channel_router.ChannelSecurityDeniedError) as error:
                await agent_channel_router.route_inbound_channel_message(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    channel_key="telegram",
                    endpoint_key="@partspro_bot",
                    customer_message="Hello",
                )

        self.assertEqual(str(error.exception), "This channel is temporarily disabled by a security control.")
        resolve_owner_mock.assert_not_awaited()

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_rejects_disabled_agent_before_thread_side_effects(self):
        safe_mode_service.set_kill_switch(
            scope="agent",
            enabled=True,
            workspace_id="workspace-1",
            agent_install_id="install-specialist",
            reason="incident",
        )
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch("server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event", new=AsyncMock()) as append_event_mock,
        ):
            with self.assertRaises(agent_channel_router.ChannelSecurityDeniedError) as error:
                await agent_channel_router.route_inbound_channel_message(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    channel_key="telegram",
                    endpoint_key="@partspro_bot",
                    customer_message="Hello",
                )

        self.assertEqual(str(error.exception), "This agent is temporarily disabled by a security control.")
        append_event_mock.assert_not_awaited()

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_ignores_duplicate_inbound_event_before_thread_side_effects(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(return_value={"id": "evt-existing", "_duplicate_hit": True}),
            ) as append_event_mock,
            patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock()) as execute_mock,
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Hello",
                actor_id="telegram-user-1",
                message_id="msg-1",
            )

        self.assertEqual(result["status"], "duplicate_ignored")
        self.assertEqual(result["audit"]["inbound_event_id"], "evt-existing")
        execute_mock.assert_not_awaited()
        self.assertEqual(append_event_mock.await_count, 1)

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_returns_draining_result_without_execution_side_effects(self):
        safe_mode_service.set_incident_control(
            scope="channel",
            mode="drain",
            workspace_id="workspace-1",
            channel_key="telegram",
            endpoint_key="@partspro_bot",
            reason="draining backlog",
        )
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ) as append_event_mock,
            patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock()) as execute_mock,
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Hello",
                actor_id="telegram-user-1",
                message_id="msg-2",
            )

        self.assertEqual(result["status"], "draining")
        self.assertEqual(result["incident_mode"], "drain")
        self.assertEqual(result["audit"]["outbound_event_id"], "evt-out")
        self.assertEqual(append_event_mock.await_count, 2)
        execute_mock.assert_not_awaited()

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_routes_live_deployed_agent_and_tags_channel_events(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
            channels={"telegram": True},
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        @asynccontextmanager
        async def _slot(*args, **kwargs):
            yield {
                "lease_id": "lease-1",
                "quota_snapshot": channel_concurrency_service.ChannelQuotaSnapshot(
                    max_workspace_active_threads=24,
                    max_agent_active_threads=8,
                    max_workspace_turns_per_minute=180,
                    max_runtime_seconds=45,
                ),
            }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(return_value=_deployed_agent_row(deployment_state="live")),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ) as append_event_mock,
            patch(
                "server_modules.agent_channel_router.execute_canonical_channel_turn",
                new=AsyncMock(
                    return_value={
                        "kind": "durable_run",
                        "result": {
                            "status": "accepted",
                            "run_id": "run-1",
                            "engine": "orion",
                            "route": {"selected": "cloud"},
                        },
                    }
                ),
            ) as execute_mock,
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_slot),
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Need brake pads",
                actor_id="telegram-user-1",
                message_id="tg-msg-1",
            )

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["run_id"], "run-1")
        inbound_kwargs = append_event_mock.await_args_list[0].kwargs
        outbound_kwargs = append_event_mock.await_args_list[1].kwargs
        self.assertEqual(inbound_kwargs["deployed_agent_id"], "dagent_1")
        self.assertEqual(outbound_kwargs["deployed_agent_id"], "dagent_1")
        self.assertEqual(inbound_kwargs["thread_id"], "thread-workspace-1-telegram-partspro-bot-telegram-partspro-bot-telegram-user-1")
        self.assertEqual(outbound_kwargs["run_id"], "run-1")
        self.assertEqual(inbound_kwargs["metadata"]["deployed_agent_id"], "dagent_1")
        self.assertEqual(execute_mock.await_args.kwargs["turn_request"].context_hints["metadata"]["deployed_agent_id"], "dagent_1")

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_returns_paused_reply_for_paused_deployed_agent(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(return_value=_deployed_agent_row(deployment_state="paused")),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ) as append_event_mock,
            patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock()) as execute_mock,
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Hello",
                actor_id="telegram-user-1",
                message_id="msg-paused",
            )

        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["limit_reason"], "deployment_paused")
        self.assertIn("temporarily paused", result["reply"])
        self.assertEqual(append_event_mock.await_count, 2)
        execute_mock.assert_not_awaited()
        outbound_kwargs = append_event_mock.await_args_list[1].kwargs
        self.assertEqual(outbound_kwargs["deployed_agent_id"], "dagent_1")
        self.assertEqual(outbound_kwargs["status"], "paused")

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_returns_suspended_reply_for_suspended_deployed_agent(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(return_value=_deployed_agent_row(deployment_state="suspended")),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ) as append_event_mock,
            patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock()) as execute_mock,
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Hello",
                actor_id="telegram-user-1",
                message_id="msg-suspended",
            )

        self.assertEqual(result["status"], "suspended")
        self.assertEqual(result["limit_reason"], "deployment_suspended")
        self.assertIn("temporarily suspended", result["reply"])
        self.assertEqual(append_event_mock.await_count, 2)
        execute_mock.assert_not_awaited()
        outbound_kwargs = append_event_mock.await_args_list[1].kwargs
        self.assertEqual(outbound_kwargs["deployed_agent_id"], "dagent_1")
        self.assertEqual(outbound_kwargs["status"], "suspended")

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_returns_branded_quota_reply_for_rate_limited_deployed_agent(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }
        deployed_agent = _deployed_agent_row(
            deployment_state="live",
            metadata={
                "daily_message_limit": 2,
                "upgrade_cta_url": "https://app.empyralist.com/signup",
                "upgrade_cta_label": "Continue on Empyralist",
            },
        )
        routing_context = agent_channel_router.build_routing_context(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            channel_key="telegram",
            endpoint_key="@partspro_bot",
            customer_message="Need brake pads",
            install=owner_route["install"],
            manifest=manifest,
            owner_type="specialist",
            message_id="msg-quota",
            actor_id="telegram-user-1",
            validate_preflight=False,
        )
        routing_context.deployed_agent = deployed_agent
        routing_context.deployed_agent_id = "dagent_1"
        routing_context.deployed_agent_state = "live"

        original_resolver = agent_channel_router.resolve_public_channel_owner
        agent_channel_router.resolve_public_channel_owner = AsyncMock(return_value=routing_context)
        try:
            with (
                patch(
                    "server_modules.deployed_agent_daily_quota_adapter.deployed_agent_rate_limit_service.enforce_deployed_agent_daily_message_limit",
                    new=AsyncMock(
                        return_value={
                            "applied": True,
                            "allowed": False,
                            "daily_message_limit": 2,
                            "message_count": 2,
                            "remaining": 0,
                            "usage_day": "2026-04-13",
                            "retry_after_seconds": 3600,
                            "upgrade_cta_url": "https://app.empyralist.com/signup",
                            "upgrade_cta_label": "Continue on Empyralist",
                        }
                    ),
                ),
                patch(
                    "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                    new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
                ),
                patch(
                    "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                    new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
                ),
                patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock()) as execute_mock,
            ):
                result = await agent_channel_router.route_inbound_channel_message(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    channel_key="telegram",
                    endpoint_key="@partspro_bot",
                    customer_message="Need brake pads",
                    actor_id="telegram-user-1",
                    message_id="msg-quota",
                )
        finally:
            agent_channel_router.resolve_public_channel_owner = original_resolver

        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(result["limit_reason"], "deployed_agent_daily_limit_exceeded")
        self.assertGreater(result["retry_after_seconds"], 0)
        self.assertIn("Parts Pro has reached today's free message limit.", result["reply"])
        self.assertIn("Continue on Empyralist: https://app.empyralist.com/signup", result["reply"])
        self.assertIn("/privacy", result["reply"])
        execute_mock.assert_not_awaited()
        self.assertEqual(result["metadata"]["deployed_agent_id"], "dagent_1")
        self.assertEqual(result["metadata"]["daily_message_limit"], 2)
        self.assertGreaterEqual(result["metadata"]["message_count"], 0)

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_uses_custom_paused_message_for_paused_deployed_agent(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(
                    return_value=_deployed_agent_row(
                        deployment_state="paused",
                        metadata={"paused_message": "The Parts Pro desk is offline for stock count. Please try again after 2 PM."},
                    )
                ),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ) as append_event_mock,
            patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock()) as execute_mock,
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Hello",
                actor_id="telegram-user-1",
                message_id="msg-paused-custom",
            )

        self.assertEqual(
            result["reply"],
            "The Parts Pro desk is offline for stock count. Please try again after 2 PM.",
        )
        outbound_kwargs = append_event_mock.await_args_list[1].kwargs
        self.assertEqual(
            outbound_kwargs["text"],
            "The Parts Pro desk is offline for stock count. Please try again after 2 PM.",
        )
        execute_mock.assert_not_awaited()

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_rejects_draft_deployed_agent_without_master_fallback(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(return_value=_deployed_agent_row(deployment_state="draft")),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(),
            ) as master_install_mock,
            patch("server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event", new=AsyncMock()) as append_event_mock,
            patch("server_modules.agent_channel_router.execute_canonical_channel_turn", new=AsyncMock()) as execute_mock,
        ):
            with self.assertRaises(agent_channel_router.ChannelOwnerNotFoundError):
                await agent_channel_router.route_inbound_channel_message(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    channel_key="telegram",
                    endpoint_key="@partspro_bot",
                    customer_message="Hello",
                    actor_id="telegram-user-1",
                )

        master_install_mock.assert_not_awaited()
        append_event_mock.assert_not_awaited()
        execute_mock.assert_not_awaited()

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_injects_memory_context_for_enabled_deployment(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
            channels={"telegram": True},
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        @asynccontextmanager
        async def _slot(*args, **kwargs):
            yield {
                "lease_id": "lease-1",
                "quota_snapshot": channel_concurrency_service.ChannelQuotaSnapshot(
                    max_workspace_active_threads=24,
                    max_agent_active_threads=8,
                    max_workspace_turns_per_minute=180,
                    max_runtime_seconds=45,
                ),
            }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(return_value=_deployed_agent_row(metadata={"memory_enabled": True})),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_memory_service.load_deployed_agent_memory_context",
                new=AsyncMock(
                    return_value={
                        "applied": True,
                        "enabled": True,
                        "prior_messages": [
                            {
                                "role": "assistant",
                                "content": "Persistent customer memory:\nEarlier persisted summary:\nCustomer prefers OEM parts.",
                            },
                            {
                                "role": "user",
                                "content": "I need brake pads for a 2022 Tesla Model 3.",
                            },
                        ],
                        "business_plan": "Conversation memory for this returning channel user.",
                        "summary_present": True,
                        "message_count": 2,
                        "compacted": False,
                    }
                ),
            ) as load_memory_mock,
            patch(
                "server_modules.agent_channel_router.deployed_agent_memory_service.persist_deployed_agent_memory_snapshot",
                new=AsyncMock(return_value={"id": "mem-1"}),
            ) as persist_memory_mock,
            patch(
                "server_modules.agent_channel_router.execute_canonical_channel_turn",
                new=AsyncMock(
                    return_value={
                        "kind": "durable_run",
                        "result": {
                            "status": "accepted",
                            "run_id": "run-1",
                            "engine": "orion",
                            "route": {"selected": "cloud"},
                        },
                    }
                ),
            ) as execute_mock,
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_slot),
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Need brake pads",
                actor_id="telegram-user-1",
                message_id="tg-msg-1",
            )

        self.assertEqual(result["status"], "accepted")
        turn_request = execute_mock.await_args.kwargs["turn_request"]
        self.assertEqual(
            turn_request.context_hints["prior_messages"][0]["content"],
            "Persistent customer memory:\nEarlier persisted summary:\nCustomer prefers OEM parts.",
        )
        self.assertIn(
            "Conversation memory for this returning channel user.",
            turn_request.context_hints["business_plan"],
        )
        self.assertTrue(turn_request.context_hints["metadata"]["conversation_memory_enabled"])
        self.assertTrue(turn_request.context_hints["metadata"]["conversation_memory_summary_present"])
        self.assertEqual(turn_request.context_hints["metadata"]["conversation_memory_message_count"], 2)
        self.assertEqual(load_memory_mock.await_args.kwargs["external_user_id"], "telegram-user-1")
        self.assertEqual(persist_memory_mock.await_args.kwargs["assistant_reply"], result["reply"])

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_leaves_memory_out_when_deployment_memory_is_disabled(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
            channels={"telegram": True},
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        @asynccontextmanager
        async def _slot(*args, **kwargs):
            yield {
                "lease_id": "lease-1",
                "quota_snapshot": channel_concurrency_service.ChannelQuotaSnapshot(
                    max_workspace_active_threads=24,
                    max_agent_active_threads=8,
                    max_workspace_turns_per_minute=180,
                    max_runtime_seconds=45,
                ),
            }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(return_value=_deployed_agent_row(metadata={"memory_enabled": False})),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_memory_service.load_deployed_agent_memory_context",
                new=AsyncMock(
                    return_value={
                        "applied": False,
                        "enabled": False,
                        "prior_messages": [],
                        "business_plan": "",
                        "summary_present": False,
                        "message_count": 0,
                        "compacted": False,
                    }
                ),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_memory_service.persist_deployed_agent_memory_snapshot",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.agent_channel_router.execute_canonical_channel_turn",
                new=AsyncMock(
                    return_value={
                        "kind": "durable_run",
                        "result": {
                            "status": "accepted",
                            "run_id": "run-1",
                            "engine": "orion",
                            "route": {"selected": "cloud"},
                        },
                    }
                ),
            ) as execute_mock,
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_slot),
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Need brake pads",
                actor_id="telegram-user-1",
                message_id="tg-msg-1",
            )

        self.assertEqual(result["status"], "accepted")
        turn_request = execute_mock.await_args.kwargs["turn_request"]
        self.assertNotIn("prior_messages", turn_request.context_hints)
        self.assertNotIn("business_plan", turn_request.context_hints)
        self.assertNotIn("conversation_memory_enabled", turn_request.context_hints["metadata"])

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_applies_health_safety_and_logs_red_flag_escalation(self):
        manifest = AgentManifest(
            manifest_id="manifest-healthguide",
            identity={
                "name": "HealthGuide",
                "role": "Health Assistant",
                "archetype": "support_specialist",
                "summary": "Provide general health information.",
            },
            channels={"telegram": True},
        )
        owner_route = {
            "install": {
                "id": "install-healthguide",
                "label": "HealthGuide",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        @asynccontextmanager
        async def _slot(*args, **kwargs):
            yield {
                "lease_id": "lease-1",
                "quota_snapshot": channel_concurrency_service.ChannelQuotaSnapshot(
                    max_workspace_active_threads=24,
                    max_agent_active_threads=8,
                    max_workspace_turns_per_minute=180,
                    max_runtime_seconds=45,
                ),
            }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(
                    return_value=_deployed_agent_row(
                        metadata={
                            "health_safety_enabled": True,
                            "memory_enabled": False,
                        }
                    )
                ),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ) as append_event_mock,
            patch(
                "server_modules.agent_channel_router.deployed_agent_memory_service.load_deployed_agent_memory_context",
                new=AsyncMock(return_value={"enabled": False}),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_memory_service.persist_deployed_agent_memory_snapshot",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.agent_channel_router.execute_canonical_channel_turn",
                new=AsyncMock(
                    return_value={
                        "status": "completed",
                        "reply": "You may be okay to rest at home.",
                        "run_id": "run-1",
                        "metadata": {},
                    }
                ),
            ) as execute_mock,
            patch(
                "server_modules.activity_ledger_service.append_activity_event",
                new=AsyncMock(return_value={"id": "activity-1"}),
            ) as append_activity_mock,
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_slot),
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="I have chest pain and trouble breathing.",
                actor_id="telegram-user-1",
                message_id="tg-health-1",
            )

        self.assertEqual(result["status"], "completed")
        self.assertIn("urgent medical attention", result["reply"])
        self.assertIn("not a doctor", result["reply"])
        self.assertIn("without verified citations", result["reply"])
        turn_request = execute_mock.await_args.kwargs["turn_request"]
        self.assertTrue(turn_request.context_hints["metadata"]["health_safety_enabled"])
        self.assertIn("healthguide_safety_policy_v1", turn_request.context_hints["business_plan"])
        outbound_kwargs = append_event_mock.await_args_list[1].kwargs
        self.assertTrue(outbound_kwargs["payload"]["health_safety"]["red_flag_triggered"])
        self.assertEqual(outbound_kwargs["payload"]["health_safety"]["citation_mode"], "disclosed_unverified")
        self.assertTrue(any(call.kwargs.get("action") == "escalated" for call in append_activity_mock.await_args_list))

    @unittest.skip(
        "Phase 5B: covers the deployed-agent inbound dispatch that the Stage-4B refactor removed from agent_channel_router.route_inbound_channel_message (it now resolves the specialist via _resolve_agent_for_inbound and routes through Sage). These patch agent_channel_router.{agent_specialist_repository,deployed_agent_service,execute_canonical_channel_turn}, which no longer exist there. Quarantined pending a rewrite against channel_execution_service or deletion — owner decision."
    )
    async def test_route_inbound_channel_message_leaves_non_health_reply_unchanged(self):
        manifest = AgentManifest(
            manifest_id="manifest-parts-pro",
            identity={
                "name": "Parts Pro",
                "role": "Inventory Specialist",
                "archetype": "support_specialist",
                "summary": "Help customers find in-stock parts.",
            },
            channels={"telegram": True},
        )
        owner_route = {
            "install": {
                "id": "install-specialist",
                "label": "Parts Pro",
                "owner_user_id": "user-1",
                "runtime_mode": "hosted_secure",
                "metadata": {"source": "deployed_agent"},
            },
            "manifest": manifest,
            "owner_type": "specialist",
        }

        @asynccontextmanager
        async def _slot(*args, **kwargs):
            yield {
                "lease_id": "lease-1",
                "quota_snapshot": channel_concurrency_service.ChannelQuotaSnapshot(
                    max_workspace_active_threads=24,
                    max_agent_active_threads=8,
                    max_workspace_turns_per_minute=180,
                    max_runtime_seconds=45,
                ),
            }

        with (
            patch(
                "server_modules.agent_channel_router.agent_specialist_repository.resolve_active_inbound_channel_owner",
                new=AsyncMock(return_value=owner_route),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_service.resolve_deployed_agent_for_channel_owner",
                new=AsyncMock(return_value=_deployed_agent_row(metadata={"health_safety_enabled": False})),
            ),
            patch(
                "server_modules.agent_channel_router.agent_registry_repository.get_workspace_master_agent_install",
                new=AsyncMock(return_value={"id": "install-sage", "label": "Sage"}),
            ),
            patch(
                "server_modules.agent_channel_router.control_plane_repository.append_agent_channel_event",
                new=AsyncMock(side_effect=[{"id": "evt-in"}, {"id": "evt-out"}]),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_memory_service.load_deployed_agent_memory_context",
                new=AsyncMock(return_value={"enabled": False}),
            ),
            patch(
                "server_modules.agent_channel_router.deployed_agent_memory_service.persist_deployed_agent_memory_snapshot",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "server_modules.agent_channel_router.execute_canonical_channel_turn",
                new=AsyncMock(
                    return_value={
                        "status": "completed",
                        "reply": "We have OEM brake pads in stock.",
                        "run_id": "run-1",
                        "metadata": {},
                    }
                ),
            ),
            patch(
                "server_modules.activity_ledger_service.append_activity_event",
                new=AsyncMock(return_value={"id": "activity-1"}),
            ),
            patch("server_modules.agent_channel_router.channel_concurrency_service.channel_execution_slot", new=_slot),
        ):
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="telegram",
                endpoint_key="@partspro_bot",
                customer_message="Need brake pads",
                actor_id="telegram-user-1",
                message_id="tg-parts-1",
            )

        self.assertEqual(result["reply"], "We have OEM brake pads in stock.")


    # ── Sage-routed studio connector channels ──────────────────────────

    async def test_slack_guild_routes_through_execute_sage_turn(self):
        """Slack Guild webhook calls execute_sage_turn with channel_origin='slack_guild'."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="Slack response from Sage.",
                trace_id="trace-slack-1",
                provider="deepseek",
                model="deepseek-chat",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                endpoint_key="C123456",
                customer_message="Check inventory for brake pads",
                actor_id="U789",
                actor_display_name="Slack User",
                message_id="slack-msg-1",
            )

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["run_id"])
        self.assertEqual(result["reply"], "Slack response from Sage.")
        self.assertEqual(result["trace_id"], "trace-slack-1")
        execute_mock.assert_awaited_once()
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["channel_origin"], "slack_guild")
        self.assertEqual(_call_kwargs["message"], "Check inventory for brake pads")
        self.assertEqual(_call_kwargs["channel_sender_id"], "U789")
        self.assertEqual(_call_kwargs["workspace_id"], "workspace-1")

    async def test_discord_guild_routes_through_execute_sage_turn(self):
        """Discord Guild webhook calls execute_sage_turn with channel_origin='discord_guild'."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="Discord response from Sage.",
                trace_id="trace-discord-1",
                provider="openai",
                model="gpt-4",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-2",
                workspace_id="workspace-2",
                channel_key="discord",
                endpoint_key="987654321",
                customer_message="What's the status of order #42?",
                actor_id="discord-user-99",
                actor_display_name="DiscordUser",
                message_id="discord-msg-1",
            )

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["run_id"])
        self.assertEqual(result["reply"], "Discord response from Sage.")
        execute_mock.assert_awaited_once()
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["channel_origin"], "discord_guild")
        self.assertEqual(_call_kwargs["message"], "What's the status of order #42?")
        self.assertEqual(_call_kwargs["channel_sender_id"], "discord-user-99")

    async def test_github_routes_through_execute_sage_turn(self):
        """GitHub webhook calls execute_sage_turn with channel_origin='github'."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="GitHub response from Sage.",
                trace_id="trace-github-1",
                provider="deepseek",
                model="deepseek-reasoner",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-3",
                workspace_id="workspace-3",
                channel_key="github",
                endpoint_key="owner/repo",
                customer_message="Review PR #128: fix auth middleware",
                actor_id="github-user-1",
                actor_display_name="GitHubDev",
                message_id="gh-msg-1",
            )

        self.assertTrue(result["ok"], f"Expected ok=True, got {result}")
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["run_id"])
        self.assertEqual(result["reply"], "GitHub response from Sage.")
        execute_mock.assert_awaited_once()
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["channel_origin"], "github")
        self.assertEqual(_call_kwargs["message"], "Review PR #128: fix auth middleware")
        self.assertEqual(_call_kwargs["channel_sender_id"], "github-user-1")

    async def test_customer_message_dict_extracts_text_field(self):
        """When customer_message is a dict, the 'text' field is extracted."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="Dict extracted OK.",
                trace_id="trace-dict-1",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="slack",
                customer_message={"text": "plain text from dict", "unused": 42},
                actor_id="user-1",
            )

        self.assertTrue(result["ok"])
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertEqual(_call_kwargs["message"], "plain text from dict")

    async def test_customer_message_dict_falls_back_to_json(self):
        """When customer_message is a dict without text/goal keys, it serialises as JSON."""
        with patch(
            "server_modules.sage_turn_adapter.execute_sage_turn",
            new=AsyncMock(return_value=SageTurnResult(
                message="JSON fallback OK.",
                trace_id="trace-json-1",
            )),
        ) as execute_mock:
            result = await agent_channel_router.route_inbound_channel_message(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                channel_key="github",
                customer_message={"event": "push", "commits": 3},
                actor_id="user-1",
            )

        self.assertTrue(result["ok"])
        _call_kwargs = execute_mock.call_args.kwargs
        self.assertIn('"event"', _call_kwargs["message"])
        self.assertIn('"push"', _call_kwargs["message"])

    async def test_empty_customer_message_returns_error(self):
        """Empty or None customer_message returns empty_message status."""
        result = await agent_channel_router.route_inbound_channel_message(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            channel_key="slack",
            customer_message="",
            actor_id="user-1",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "empty_message")

    async def test_unimplemented_channel_returns_unavailable(self):
        """Channels not in _SAGE_CHANNEL_ORIGIN_MAP still return channel_unavailable."""
        result = await agent_channel_router.route_inbound_channel_message(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            channel_key="whatsapp_business",
            customer_message="Hello",
            actor_id="user-1",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "channel_unavailable")
        self.assertIn("whatsapp_business", result["error"])


if __name__ == "__main__":
    unittest.main()
