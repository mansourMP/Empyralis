import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from server_modules import deployed_agent_virtual_runtime_service
from server_modules.tests.test_deployed_agent_virtual_runtime_service import _deployed_agent


class DeployedRuntimeTerminationRustGateTests(unittest.TestCase):
    def test_cloud_runtime_termination_calls_rust_before_runtime_terminate(self):
        async def _run():
            calls = []
            runtime = Mock()

            async def terminate_session(payload):
                calls.append("terminate_session")
                return {"status": "terminated"}

            runtime.terminate_session = AsyncMock(side_effect=terminate_session)
            registry = Mock()
            registry.resolve.return_value = runtime

            def rust_decision(command, payload, **kwargs):
                calls.append(command)
                if command == "deployed-virtual-runtime-service-decision":
                    self.assertEqual(payload["operation"], "terminate_cloud_session")
                    self.assertEqual(payload["deployed_agent_id"], "dagent_1")
                    self.assertEqual(payload["runtime_session_id"], "vcsess_1")
                    return {"ok": True, "decision": "allow", "next_action": "terminate_and_meter_runtime_session"}
                raise AssertionError(f"unexpected Rust command {command}")

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
                    deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
                    "run_runtime_kernel_enforced",
                    side_effect=rust_decision,
                ),
            ):
                return await deployed_agent_virtual_runtime_service.terminate_bound_cloud_runtime_session(
                    session_id="sess-1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                ), runtime, calls

        result, runtime, calls = asyncio.run(_run())
        self.assertEqual(result["status"], "terminated")
        self.assertEqual(calls[0], "deployed-virtual-runtime-service-decision")
        self.assertLess(calls.index("deployed-virtual-runtime-service-decision"), calls.index("terminate_session"))
        runtime.terminate_session.assert_awaited_once()

    def test_cloud_runtime_termination_rust_denial_blocks_runtime_terminate(self):
        async def _run():
            runtime = Mock()
            runtime.terminate_session = AsyncMock()
            registry = Mock()
            registry.resolve.return_value = runtime
            denied = deployed_agent_virtual_runtime_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "workspace_id_missing",
                },
                command="deployed-virtual-runtime-service-decision",
            )

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
                            },
                        }
                    ),
                ),
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
                    deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
                    "run_runtime_kernel_enforced",
                    side_effect=denied,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Rust deployed virtual-runtime service blocked terminate_cloud_session"):
                    await deployed_agent_virtual_runtime_service.terminate_bound_cloud_runtime_session(
                        session_id="sess-1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                    )
            return runtime

        runtime = asyncio.run(_run())
        runtime.terminate_session.assert_not_awaited()
