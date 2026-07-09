import asyncio
import unittest
from unittest.mock import patch

from server_modules.virtual_computer_runtime import (
    DigitalOceanSSHRuntimeCommandResult,
    DigitalOceanSSHVirtualComputerRuntime,
    InMemoryVirtualComputerRuntime,
    PROVIDER_CAPABILITY_KEYS,
    PROVIDER_ID_BROWSERBASE,
    PROVIDER_ID_DAYTONA,
    PROVIDER_ID_DIGITALOCEAN_SSH,
    PROVIDER_ID_DOCKER_KUBERNETES,
    RUNTIME_CHOICE_VIRTUAL_BROWSER,
    RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
    SelfHostedNodeVirtualComputerRuntime,
    VirtualComputerRuntimeRegistry,
    default_virtual_computer_provider_registry,
)


class _FakeSelfHostedDelegateRuntime:
    async def create_session(self, payload):
        return {"session_id": "sess_self_hosted", "payload": dict(payload)}

    async def resume_session(self, payload):
        return {"session_id": str(payload.get("session_id") or ""), "status": "resumed"}

    async def pause_session(self, payload):
        return {"session_id": str(payload.get("session_id") or ""), "status": "paused"}

    async def terminate_session(self, payload):
        return {"session_id": str(payload.get("session_id") or ""), "status": "terminated"}

    async def execute_action(self, payload):
        return {"session_id": str(payload.get("session_id") or ""), "status": "completed"}

    async def stream_screenshot(self, payload):
        return {"session_id": str(payload.get("session_id") or ""), "status": "streaming"}

    async def collect_artifact(self, payload):
        return {"session_id": str(payload.get("session_id") or ""), "status": "collected"}

    async def snapshot_session(self, payload):
        return {"session_id": str(payload.get("session_id") or ""), "status": "snapshotted"}

    async def export_audit_report(self, payload):
        return {"session_id": str(payload.get("session_id") or ""), "audit_report": {"event_count": 0}}


class VirtualComputerProviderAbstractionTests(unittest.TestCase):
    def test_default_provider_registry_exposes_capability_schema(self):
        registry = default_virtual_computer_provider_registry()
        specs = registry.list_provider_specs()

        self.assertTrue(specs)
        for spec in specs:
            capabilities = spec.get("capabilities") if isinstance(spec.get("capabilities"), dict) else {}
            for key in PROVIDER_CAPABILITY_KEYS:
                self.assertIn(key, capabilities)

    def test_virtual_browser_defaults_to_real_runtime_not_stub(self):
        provider_registry = default_virtual_computer_provider_registry()
        runtime_registry = VirtualComputerRuntimeRegistry(
            local_runtime=InMemoryVirtualComputerRuntime(),
            virtual_runtime=InMemoryVirtualComputerRuntime(),
            provider_registry=provider_registry,
        )

        runtime = runtime_registry.resolve(RUNTIME_CHOICE_VIRTUAL_BROWSER)

        # Browserbase is a stub (no runtime_factory) — the registry must prefer
        # DigitalOcean SSH which has a proven, working runtime_factory.
        self.assertIsInstance(runtime._runtime, DigitalOceanSSHVirtualComputerRuntime)
        self.assertEqual(runtime._provider_spec.provider_id, PROVIDER_ID_DIGITALOCEAN_SSH)

    def test_production_cloud_computer_prefers_real_runtime_over_stub(self):
        provider_registry = default_virtual_computer_provider_registry()
        runtime_registry = VirtualComputerRuntimeRegistry(
            local_runtime=InMemoryVirtualComputerRuntime(),
            virtual_runtime=InMemoryVirtualComputerRuntime(),
            provider_registry=provider_registry,
        )

        with patch.dict(
            "os.environ",
            {"ORION_ENV": "production"},
            clear=False,
        ):
            # The DO runtime has a factory — it must be selected even in production.
            # No InMemory block error because a real (non-InMemory) runtime wins.
            runtime = runtime_registry.resolve(RUNTIME_CHOICE_VIRTUAL_BROWSER)
            # Should be a real provider, not InMemory (the stub lost).
            self.assertNotIsInstance(runtime._runtime, InMemoryVirtualComputerRuntime)

    def test_dev_cloud_computer_prefers_real_runtime_even_with_inmemory_allowed(self):
        provider_registry = default_virtual_computer_provider_registry()
        runtime_registry = VirtualComputerRuntimeRegistry(
            local_runtime=InMemoryVirtualComputerRuntime(),
            virtual_runtime=InMemoryVirtualComputerRuntime(),
            provider_registry=provider_registry,
        )

        with patch.dict(
            "os.environ",
            {"ORION_ENV": "local", "EMPYRALIS_ALLOW_INMEMORY_RUNTIME": "true"},
            clear=False,
        ):
            runtime = runtime_registry.resolve(RUNTIME_CHOICE_VIRTUAL_BROWSER)

        # Even with InMemory allowed, a real factory-backed runtime (DO) must win
        # over a stub (Browserbase).  Stubs are never chosen over proven runtimes.
        self.assertIsInstance(runtime._runtime, DigitalOceanSSHVirtualComputerRuntime)
        self.assertEqual(runtime._provider_spec.provider_id, PROVIDER_ID_DIGITALOCEAN_SSH)

    def test_provider_can_be_swapped_without_contract_change(self):
        provider_registry = default_virtual_computer_provider_registry()
        runtime_registry = VirtualComputerRuntimeRegistry(
            local_runtime=InMemoryVirtualComputerRuntime(),
            virtual_runtime=InMemoryVirtualComputerRuntime(),
            provider_registry=provider_registry,
        )

        runtime = runtime_registry.resolve(
            RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
            preferred_provider_id=PROVIDER_ID_DAYTONA,
        )
        created = asyncio.run(runtime.create_session({"runtime_choice": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX}))
        action = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": created.get("session_id"),
                    "action": "run_command",
                    "approval_id": "appr_provider_swap",
                    "risk_policy": {"red_policy": "owner_approval"},
                    "policy_metadata": {"owner_role": "owner", "owner_is_admin": True},
                    "action_args": {"command": "echo hello"},
                }
            )
        )

        self.assertEqual(created.get("provider_id"), PROVIDER_ID_DAYTONA)
        self.assertEqual(action.get("provider_id"), PROVIDER_ID_DAYTONA)
        self.assertEqual(action.get("runtime_contract_interface"), "virtual_computer_runtime.v1")

    def test_digitalocean_provider_uses_real_ssh_runtime_not_inmemory(self):
        provider_registry = default_virtual_computer_provider_registry()
        runtime_registry = VirtualComputerRuntimeRegistry(
            local_runtime=InMemoryVirtualComputerRuntime(),
            virtual_runtime=InMemoryVirtualComputerRuntime(),
            provider_registry=provider_registry,
        )

        runtime = runtime_registry.resolve(
            RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
            preferred_provider_id=PROVIDER_ID_DIGITALOCEAN_SSH,
        )

        self.assertIsInstance(runtime._runtime, DigitalOceanSSHVirtualComputerRuntime)
        browser_runtime = runtime_registry.resolve(
            RUNTIME_CHOICE_VIRTUAL_BROWSER,
            preferred_provider_id=PROVIDER_ID_DIGITALOCEAN_SSH,
        )
        self.assertIsInstance(browser_runtime._runtime, DigitalOceanSSHVirtualComputerRuntime)

    def test_digitalocean_ssh_runtime_creates_executes_and_terminates_session(self):
        calls = []

        async def runner(args, timeout_seconds):
            calls.append((list(args), timeout_seconds))
            command = args[-1]
            if "docker run" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="hello from cloud\n")
            return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")

        runtime = DigitalOceanSSHVirtualComputerRuntime(
            host="203.0.113.10",
            user="root",
            base_dir="/tmp/empyralis-test",
            docker_image="alpine:3.20",
            command_runner=runner,
        )
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
                    "session_id": "sess-do-1",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                }
            )
        )
        action = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": "sess-do-1",
                    "action": "run_command",
                    "approval_id": "appr-do-1",
                    "risk_policy": {"red_policy": "owner_approval"},
                    "policy_metadata": {"owner_role": "owner", "owner_is_admin": True},
                    "action_args": {"command": "echo hello from cloud"},
                }
            )
        )
        terminated = asyncio.run(runtime.terminate_session({"session_id": "sess-do-1"}))

        self.assertEqual(created.get("runtime_kind"), "digitalocean_ssh_cloud_computer")
        self.assertEqual(action.get("status"), "completed")
        self.assertIn("hello from cloud", (action.get("action_result") or {}).get("stdout") or "")
        self.assertEqual(terminated.get("state"), "terminated")
        self.assertTrue(any("mkdir -p" in call[0][-1] for call in calls))
        self.assertTrue(any("docker run" in call[0][-1] for call in calls))
        self.assertTrue(any("--user $(id -u):$(id -g)" in call[0][-1] for call in calls))
        self.assertTrue(any("rm -rf" in call[0][-1] for call in calls))

    def test_self_hosted_provider_cannot_fallback_to_in_memory_runtime(self):
        provider_registry = default_virtual_computer_provider_registry()
        adapter = provider_registry.select_provider(
            runtime_choice=RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
            preferred_provider_id=PROVIDER_ID_DOCKER_KUBERNETES,
        )

        runtime = adapter.build_runtime(fallback_runtime=InMemoryVirtualComputerRuntime())
        self.assertNotIsInstance(runtime, InMemoryVirtualComputerRuntime)

    def test_self_hosted_runtime_requires_runtime_node_binding(self):
        runtime = SelfHostedNodeVirtualComputerRuntime(runtime=_FakeSelfHostedDelegateRuntime())
        with self.assertRaisesRegex(RuntimeError, "self_hosted_runtime_binding"):
            asyncio.run(runtime.create_session({"runtime_choice": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX}))

        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
                    "workspace_id": "ws-1",
                    "policy_metadata": {
                        "self_hosted_runtime_binding": {
                            "runtime_target": "self_host_runtime",
                            "workspace_id": "ws-1",
                            "runtime_node_id": "node-123",
                            "runtime_profile_id": "rprof-123",
                            "runtime_attachment_id": "attach-123",
                        }
                    },
                }
            )
        )
        self.assertTrue(created.get("self_hosted"))
        self.assertEqual(created.get("runtime_kind"), "self_hosted_node_runtime")
        self.assertEqual(created.get("runtime_node_id"), "node-123")
        self.assertEqual(created.get("workspace_id"), "ws-1")

        action = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": created.get("session_id"),
                    "action": "wait",
                    "action_args": {"duration_ms": 100},
                }
            )
        )
        self.assertEqual(action.get("runtime_kind"), "self_hosted_node_runtime")
        self.assertEqual(action.get("runtime_node_id"), "node-123")

    def test_self_hosted_runtime_rejects_raw_runtime_node_override(self):
        runtime = SelfHostedNodeVirtualComputerRuntime(runtime=_FakeSelfHostedDelegateRuntime())
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
                    "workspace_id": "ws-1",
                    "policy_metadata": {
                        "self_hosted_runtime_binding": {
                            "runtime_target": "self_host_runtime",
                            "workspace_id": "ws-1",
                            "runtime_node_id": "node-123",
                            "runtime_profile_id": "rprof-123",
                            "runtime_attachment_id": "attach-123",
                        }
                    },
                }
            )
        )
        with self.assertRaisesRegex(RuntimeError, "runtime_node_id override"):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": created.get("session_id"),
                        "workspace_id": "ws-1",
                        "runtime_node_id": "node-attacker",
                        "action": "wait",
                        "action_args": {"duration_ms": 100},
                    }
                )
            )



if __name__ == "__main__":
    unittest.main()
