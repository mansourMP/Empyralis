import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from server_modules import deployed_agent_virtual_runtime_service
from server_modules.tests.test_deployed_agent_virtual_runtime_service import _deployed_agent


class DeployedRuntimeBindingRustGateTests(unittest.TestCase):
    def test_runtime_binding_helper_wrong_next_action_blocks(self):
        with patch.object(
            deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "next_action": "create_self_hosted_runtime_session",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:create_self_hosted_runtime_session"):
                deployed_agent_virtual_runtime_service._enforce_runtime_binding_decision(
                    operation="ensure_cloud_runtime_session",
                    deployed_agent_id="dagent_1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    session_id="sess-1",
                    thread_id="thread-1",
                    studio_agent_mode="cloud_computer_agent",
                )

    def test_cloud_runtime_binding_calls_rust_before_runtime_create_session(self):
        async def _run():
            calls = []
            runtime = Mock()

            async def create_session(payload):
                calls.append("create_session")
                return {
                    "browser_session": {"browser_session_id": "vcsess_1"},
                    "provider_id": "provider_demo",
                    "provider_kind": "managed_cloud",
                }

            runtime.create_session = AsyncMock(side_effect=create_session)
            registry = Mock()
            registry.resolve.return_value = runtime

            def rust_decision(command, payload, **kwargs):
                calls.append(command)
                if command == "runtime-binding-decision":
                    self.assertEqual(payload["operation"], "ensure_cloud_runtime_session")
                    self.assertEqual(payload["deployed_agent_id"], "dagent_1")
                    self.assertEqual(payload["studio_agent_mode"], "cloud_computer_agent")
                    return {"ok": True, "decision": "allow", "next_action": "create_cloud_runtime_session"}
                if command == "deployed-virtual-runtime-service-decision":
                    return {"ok": True, "decision": "allow", "next_action": payload.get("operation")}
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
                binding = await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                    deployed_agent_id="dagent_1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    session_id="sess-1",
                    thread_id="thread-1",
                    channel="web",
                    actor={"type": "user", "id": "user-1"},
                    session_metadata={},
                    turn_metadata={"deployed_agent_id": "dagent_1"},
                )
            return binding, calls, runtime

        binding, calls, runtime = asyncio.run(_run())

        self.assertEqual(binding["metadata_updates"]["runtime_session_binding"], "cloud_computer_agent")
        self.assertEqual(calls[0], "runtime-binding-decision")
        self.assertLess(calls.index("runtime-binding-decision"), calls.index("create_session"))
        runtime.create_session.assert_awaited_once()

    def test_runtime_binding_rust_denial_blocks_runtime_create_session(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock()
            registry = Mock()
            registry.resolve.return_value = runtime
            denied = deployed_agent_virtual_runtime_service.rust_runtime_kernel_client.RustKernelDecisionError(
                {
                    "ok": False,
                    "decision": "block",
                    "reason": "workspace_id_missing",
                },
                command="runtime-binding-decision",
            )

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
                    deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
                    "run_runtime_kernel_enforced",
                    side_effect=denied,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Rust runtime binding blocked ensure_cloud_runtime_session"):
                    await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                        session_metadata={},
                        turn_metadata={"deployed_agent_id": "dagent_1"},
                    )
            return runtime

        runtime = asyncio.run(_run())
        runtime.create_session.assert_not_awaited()

    def test_runtime_binding_rust_reuse_controls_cloud_session_reuse(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock()
            registry = Mock()
            registry.resolve.return_value = runtime

            def rust_decision(command, payload, **kwargs):
                if command == "runtime-binding-decision":
                    return {
                        "ok": True,
                        "decision": "allow",
                        "next_action": "reuse_runtime_session",
                        "runtime_session_id": "rust-reused-session",
                        "runtime_session_binding": "cloud_computer_agent",
                    }
                if command == "deployed-virtual-runtime-service-decision":
                    return {"ok": True, "decision": "allow", "next_action": payload.get("operation")}
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
                    deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
                    "run_runtime_kernel_enforced",
                    side_effect=rust_decision,
                ),
            ):
                return await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                    deployed_agent_id="dagent_1",
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    session_id="sess-1",
                    thread_id="thread-1",
                    channel="web",
                    actor={"type": "user", "id": "user-1"},
                    session_metadata={"runtime_session_id": "python-session", "runtime_session_binding": "other_binding"},
                    turn_metadata={},
                ), runtime

        binding, runtime = asyncio.run(_run())
        self.assertEqual(binding["metadata_updates"]["runtime_session_id"], "rust-reused-session")
        self.assertEqual(binding["metadata_updates"]["runtime_session_binding"], "cloud_computer_agent")
        runtime.create_session.assert_not_awaited()

    def test_runtime_binding_wrong_next_action_blocks_cloud_session_create(self):
        async def _run():
            runtime = Mock()
            runtime.create_session = AsyncMock()
            registry = Mock()
            registry.resolve.return_value = runtime

            def rust_decision(command, payload, **kwargs):
                if command == "runtime-binding-decision":
                    return {
                        "ok": True,
                        "decision": "allow",
                        "next_action": "create_self_hosted_runtime_session",
                    }
                if command == "deployed-virtual-runtime-service-decision":
                    return {"ok": True, "decision": "allow", "next_action": payload.get("operation")}
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
                    deployed_agent_virtual_runtime_service.rust_runtime_kernel_client,
                    "run_runtime_kernel_enforced",
                    side_effect=rust_decision,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpected_next_action:create_self_hosted_runtime_session"):
                    await deployed_agent_virtual_runtime_service.ensure_cloud_runtime_session_binding(
                        deployed_agent_id="dagent_1",
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        session_id="sess-1",
                        thread_id="thread-1",
                        channel="web",
                        actor={"type": "user", "id": "user-1"},
                        session_metadata={},
                        turn_metadata={"deployed_agent_id": "dagent_1"},
                    )
            return runtime

        runtime = asyncio.run(_run())
        runtime.create_session.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
