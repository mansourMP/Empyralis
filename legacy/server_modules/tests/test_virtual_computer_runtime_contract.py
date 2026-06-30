import asyncio
import unittest

from server_modules import runtime_policy
from server_modules.virtual_computer_runtime import (
    InMemoryVirtualComputerRuntime,
    RUNTIME_INTERFACE_ID,
    RUNTIME_METHODS,
    RUNTIME_STATES,
    VirtualComputerRuntimeRegistry,
    contract_descriptor,
)


class VirtualComputerRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_policy._init()

    def test_runtime_policy_route_includes_contract_metadata(self):
        route = runtime_policy.decide_execution_target({"runtime_choice": "virtual_browser"})

        self.assertEqual(route.get("runtime_contract_interface"), runtime_policy.RUNTIME_CONTRACT_INTERFACE_ID)
        self.assertEqual(route.get("runtime_contract_kind"), runtime_policy.RUNTIME_CONTRACT_KIND_VIRTUAL_COMPUTER)
        self.assertEqual(route.get("runtime_contract_methods"), runtime_policy.RUNTIME_CONTRACT_METHODS)
        self.assertEqual(route.get("runtime_contract_states"), runtime_policy.RUNTIME_CONTRACT_STATES)

    def test_contract_descriptor_shape(self):
        descriptor = contract_descriptor("virtual_code_sandbox")

        self.assertEqual(descriptor.get("runtime_contract_interface"), RUNTIME_INTERFACE_ID)
        self.assertEqual(descriptor.get("runtime_contract_methods"), RUNTIME_METHODS)
        self.assertEqual(descriptor.get("runtime_contract_states"), RUNTIME_STATES)
        self.assertEqual(descriptor.get("runtime_contract_kind"), "virtual_computer_runtime")

    def test_in_memory_runtime_supports_standard_methods(self):
        runtime = InMemoryVirtualComputerRuntime()

        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")
        self.assertTrue(session_id)
        self.assertEqual(created.get("state"), "running")

        paused = asyncio.run(runtime.pause_session({"session_id": session_id}))
        self.assertEqual(paused.get("state"), "paused")

        resumed = asyncio.run(runtime.resume_session({"session_id": session_id}))
        self.assertEqual(resumed.get("state"), "running")

        action = asyncio.run(
            runtime.execute_action(
                {"session_id": session_id, "action": "open_url", "action_args": {"url": "https://example.com"}}
            )
        )
        self.assertEqual(action.get("status"), "completed")

        streamed = asyncio.run(runtime.stream_screenshot({"session_id": session_id}))
        artifact_id = (streamed.get("artifact") or {}).get("artifact_id")
        self.assertTrue(artifact_id)

        collected = asyncio.run(runtime.collect_artifact({"session_id": session_id, "artifact_id": artifact_id}))
        self.assertEqual((collected.get("artifact") or {}).get("artifact_id"), artifact_id)

        snapshot = asyncio.run(runtime.snapshot_session({"session_id": session_id}))
        self.assertEqual(snapshot.get("status"), "snapshotted")

        terminated = asyncio.run(runtime.terminate_session({"session_id": session_id}))
        self.assertEqual(terminated.get("state"), "terminated")

    def test_registry_routes_local_vs_virtual(self):
        local_runtime = InMemoryVirtualComputerRuntime()
        virtual_runtime = InMemoryVirtualComputerRuntime()
        registry = VirtualComputerRuntimeRegistry(local_runtime=local_runtime, virtual_runtime=virtual_runtime)

        self.assertIs(registry.resolve("local"), local_runtime)
        self.assertIs(registry.resolve("virtual_browser"), virtual_runtime)


if __name__ == "__main__":
    unittest.main()
