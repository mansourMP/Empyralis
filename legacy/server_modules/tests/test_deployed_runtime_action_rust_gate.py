import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from server_modules import deployed_agent_virtual_runtime_service
from server_modules.tests.test_deployed_agent_virtual_runtime_service import _deployed_agent


class DeployedRuntimeActionRustGateTests(unittest.TestCase):
    def test_cloud_runtime_action_uses_rust_runtime_action(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock(
                return_value={
                    "status": "ok",
                    "browser_session": {"browser_session_id": "vcsess_1"},
                    "action_result": {"ok": True, "action": "download_artifact"},
                }
            )
            registry = Mock()
            registry.resolve.return_value = runtime

            def rust_decision(command, payload, **kwargs):
                if command == "deployed-virtual-runtime-service-decision":
                    return {"ok": True, "decision": "allow", "next_action": payload.get("operation")}
                if command == "runtime-action-decision":
                    return {
                        "ok": True,
                        "decision": "allow",
                        "next_action": "execute_cloud_runtime_action",
                        "runtime_action": "download_artifact",
                    }
                raise AssertionError(f"unexpected Rust command {command}")

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
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
                    "run_runtime_kernel_enforced",
                    side_effect=rust_decision,
                ),
            ):
                result = await deployed_agent_virtual_runtime_service.execute_bound_cloud_runtime_tool_call(
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
                return result, runtime

        result, runtime = asyncio.run(_run())
        payload = runtime.execute_action.await_args.args[0]
        self.assertEqual(payload["action"], "download_artifact")
        self.assertIn("\"download_artifact\"", result)

    def test_cloud_runtime_action_rust_denial_blocks_execute_action(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock()
            registry = Mock()
            registry.resolve.return_value = runtime
            denied = deployed_agent_virtual_runtime_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "runtime_action_not_supported",
                },
                command="runtime-action-decision",
            )

            def rust_decision(command, payload, **kwargs):
                if command == "deployed-virtual-runtime-service-decision":
                    return {"ok": True, "decision": "allow", "next_action": payload.get("operation")}
                if command == "runtime-action-decision":
                    raise denied
                raise AssertionError(f"unexpected Rust command {command}")

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
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
                    "run_runtime_kernel_enforced",
                    side_effect=rust_decision,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Rust runtime action blocked execute_runtime_action"):
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
                            }
                        },
                    )
            return runtime

        runtime = asyncio.run(_run())
        runtime.execute_action.assert_not_awaited()

    def test_cloud_runtime_action_wrong_next_action_blocks_execute_action(self):
        async def _run():
            runtime = Mock()
            runtime.execute_action = AsyncMock()
            registry = Mock()
            registry.resolve.return_value = runtime

            def rust_decision(command, payload, **kwargs):
                if command == "deployed-virtual-runtime-service-decision":
                    return {"ok": True, "decision": "allow", "next_action": payload.get("operation")}
                if command == "runtime-action-decision":
                    return {
                        "ok": True,
                        "decision": "allow",
                        "next_action": "execute_self_hosted_runtime_action",
                        "runtime_action": "download_artifact",
                    }
                raise AssertionError(f"unexpected Rust command {command}")

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
                ),
                patch.object(
                    deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
                    "run_runtime_kernel_enforced",
                    side_effect=rust_decision,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:execute_self_hosted_runtime_action"):
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
                            }
                        },
                    )
            return runtime

        runtime = asyncio.run(_run())
        runtime.execute_action.assert_not_awaited()
