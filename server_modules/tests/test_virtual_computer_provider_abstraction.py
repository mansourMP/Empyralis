import base64
import asyncio
import json
import shlex
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

    def test_digitalocean_ssh_runtime_open_url_returns_screenshot_artifact(self):
        calls = []
        container_running = [False]  # mutable state via list

        async def runner(args, timeout_seconds):
            calls.append((list(args), timeout_seconds))
            command = args[-1] if args else ""
            # _start_browser_container: sweep stale containers
            if "docker ps" in command and "empyralis-browser-" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            # _start_browser_container: write browser_loop.mjs
            if "printf" in command and "browser_loop.mjs" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            # _start_browser_container: remove stale ready file
            if "rm -f" in command and "browser_ready" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            # _start_browser_container: docker run -d
            if "docker run -d" in command:
                container_running[0] = True
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="container_id_123")
            # _start_browser_container: wait for browser_ready
            if "test -f" in command and "browser_ready" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="ready")
            # Container alive check — reflect actual state
            if "docker inspect" in command and "empyralis-browser-" in command:
                return DigitalOceanSSHRuntimeCommandResult(
                    returncode=0, stdout="true" if container_running[0] else "false"
                )
            # _send_browser_command: write command file
            if "printf" in command and "cmd_" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            # _send_browser_command: poll for result file
            if "cat" in command and "result_" in command:
                return DigitalOceanSSHRuntimeCommandResult(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": True,
                        "title": "Example Domain",
                        "text": "Example text",
                        "screenshot": "/workspace/artifacts/artifact_test.png",
                        "url": "https://example.com",
                    }),
                )
            # _send_browser_command: sha256sum
            if "sha256sum" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="abc123  screenshot.png\n")
            # _send_browser_command: cleanup result file
            if "rm -f" in command and "result_" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")

        runtime = DigitalOceanSSHVirtualComputerRuntime(
            host="203.0.113.10",
            user="root",
            base_dir="/tmp/empyralis-test",
            browser_image="mcr.microsoft.com/playwright:v1.55.0-noble",
            command_runner=runner,
        )
        asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX,
                    "session_id": "sess-do-browser-1",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                }
            )
        )
        action = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": "sess-do-browser-1",
                    "action": "open_url",
                    "action_args": {"url": "https://example.com"},
                }
            )
        )

        artifact = action.get("artifact") or {}
        self.assertEqual(action.get("status"), "completed")
        self.assertEqual(artifact.get("artifact_type"), "screenshot")
        self.assertEqual(artifact.get("url"), "https://example.com")
        self.assertEqual(artifact.get("title"), "Example Domain")
        # Verify persistent container was started (docker run -d)
        self.assertTrue(any("docker run -d" in call[0][-1] for call in calls))
        # Verify command file was written
        self.assertTrue(any("printf" in call[0][-1] and "cmd_" in call[0][-1] for call in calls))

    def test_digitalocean_ssh_runtime_recovers_artifacts_from_remote_manifest(self):
        remote_files = {}  # maps file path → content
        screenshot_bytes = b"fake-png"

        async def runner(args, timeout_seconds):
            command = args[-1] if args else ""
            # _start_browser_container / _send_browser_command: container checks
            if "docker inspect" in command and "empyralis-browser-" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="true")
            if "docker ps" in command and "empyralis-browser-" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            if "docker run -d" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="container_id")
            if "test -f" in command and "browser_ready" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="ready")
            if "rm -f" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            # _send_browser_command: poll for result → return screenshot artifact
            if "cat" in command and "result_" in command:
                return DigitalOceanSSHRuntimeCommandResult(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": True,
                        "title": "Example Domain",
                        "text": "Example text",
                        "screenshot": "/workspace/artifacts/artifact_test.png",
                        "url": "https://example.com",
                    }),
                )
            if "sha256sum" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="abc123  screenshot.png\n")
            if "base64 -w 0" in command:
                return DigitalOceanSSHRuntimeCommandResult(
                    returncode=0,
                    stdout=base64.b64encode(screenshot_bytes).decode("ascii"),
                )
            # Track printf writes for session manifest / command files
            tokens = shlex.split(command)
            if "printf" in tokens and ">" in tokens:
                try:
                    gt_idx = tokens.index(">")
                    path_val = tokens[gt_idx + 1]
                    printf_idx = tokens.index("printf") if "printf" in tokens else -1
                    if printf_idx >= 0 and printf_idx + 2 < len(tokens):
                        remote_files[path_val] = tokens[printf_idx + 2]
                except (ValueError, IndexError):
                    pass
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            if tokens[:1] == ["cat"] and len(tokens) > 1:
                path = tokens[1]
                remote_files.setdefault(path, json.dumps({"state": "running"}))
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout=remote_files.get(path, "{}"))
            return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")

        runtime = DigitalOceanSSHVirtualComputerRuntime(
            host="203.0.113.10",
            user="root",
            base_dir="/tmp/empyralis-test",
            command_runner=runner,
        )
        asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": RUNTIME_CHOICE_VIRTUAL_BROWSER,
                    "session_id": "sess-do-recover-1",
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                }
            )
        )
        action = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": "sess-do-recover-1",
                    "action": "open_url",
                    "action_args": {"url": "https://example.com"},
                }
            )
        )
        artifact_id = (action.get("artifact") or {}).get("artifact_id")

        fresh_runtime = DigitalOceanSSHVirtualComputerRuntime(
            host="203.0.113.10",
            user="root",
            base_dir="/tmp/empyralis-test",
            command_runner=runner,
        )
        collected = asyncio.run(
            fresh_runtime.collect_artifact(
                {
                    "session_id": "sess-do-recover-1",
                    "artifact_id": artifact_id,
                    "include_content_base64": True,
                }
            )
        )

        self.assertEqual((collected.get("artifact") or {}).get("artifact_id"), artifact_id)
        self.assertEqual(
            base64.b64decode((collected.get("artifact") or {}).get("content_base64")),
            screenshot_bytes,
        )

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


    def test_digitalocean_ssh_runtime_click_type_scroll_via_persistent_browser(self):
        """CLICK, TYPE, and SCROLL all work via the persistent browser container."""
        container_running = [False]
        action_results = []

        async def runner(args, timeout_seconds):
            command = args[-1] if args else ""
            # Container lifecycle
            if "docker ps" in command and "empyralis-browser-" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            if "printf" in command and "browser_loop.mjs" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            if "rm -f" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            if "docker run -d" in command:
                container_running[0] = True
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="container_id")
            if "test -f" in command and "browser_ready" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="ready")
            if "docker inspect" in command and "empyralis-browser-" in command:
                return DigitalOceanSSHRuntimeCommandResult(
                    returncode=0, stdout="true" if container_running[0] else "false"
                )
            if "cd " in command and "npm install" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            # Command processing
            if "printf" in command and "cmd_" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            if "cat" in command and "result_" in command:
                action_results.append(command)
                return DigitalOceanSSHRuntimeCommandResult(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": True, "title": "Test Page",
                        "screenshot": "/workspace/artifacts/test_ss.png", "url": "https://test.example",
                    }),
                )
            if "sha256sum" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="def456  screenshot.png\n")
            return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")

        runtime = DigitalOceanSSHVirtualComputerRuntime(
            host="203.0.113.10", user="root", base_dir="/tmp/empyralis-test",
            command_runner=runner,
        )
        asyncio.run(runtime.create_session({
            "runtime_choice": RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "session_id": "sess-do-browser-2", "workspace_id": "ws-1", "tenant_id": "tenant-1",
        }))

        # Click by selector
        click_result = asyncio.run(runtime.execute_action({
            "session_id": "sess-do-browser-2", "action": "click",
            "action_args": {"selector": "#btn-submit"},
        }))
        self.assertEqual(click_result["status"], "completed")
        self.assertEqual((click_result.get("artifact") or {}).get("artifact_type"), "screenshot")

        # Type text
        type_result = asyncio.run(runtime.execute_action({
            "session_id": "sess-do-browser-2", "action": "type",
            "action_args": {"selector": "input[name=q]", "text": "hello"},
        }))
        self.assertEqual(type_result["status"], "completed")
        self.assertEqual((type_result.get("artifact") or {}).get("url"), "https://test.example")

        # Scroll
        scroll_result = asyncio.run(runtime.execute_action({
            "session_id": "sess-do-browser-2", "action": "scroll",
            "action_args": {"delta_y": 300},
        }))
        self.assertEqual(scroll_result["status"], "completed")

        # Verify the container was started and all 3 actions ran
        self.assertTrue(container_running[0], "Browser container should be running")
        self.assertEqual(len(action_results), 3)

    def test_digitalocean_ssh_runtime_click_without_selector_or_coords_raises(self):
        """CLICK without selector, coordinates, or text fails at the browser level."""
        container_running = [True]

        async def runner(args, timeout_seconds):
            command = args[-1] if args else ""
            if "docker inspect" in command and "empyralis-browser-" in command:
                return DigitalOceanSSHRuntimeCommandResult(
                    returncode=0, stdout="true" if container_running[0] else "false"
                )
            if "printf" in command and "cmd_" in command:
                return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")
            if "cat" in command and "result_" in command:
                return DigitalOceanSSHRuntimeCommandResult(
                    returncode=0,
                    stdout=json.dumps({
                        "ok": False, "error": "click requires selector, (x,y), or text",
                    }),
                )
            return DigitalOceanSSHRuntimeCommandResult(returncode=0, stdout="")

        runtime = DigitalOceanSSHVirtualComputerRuntime(
            host="203.0.113.10", user="root", base_dir="/tmp/empyralis-test",
            command_runner=runner,
        )
        asyncio.run(runtime.create_session({
            "runtime_choice": RUNTIME_CHOICE_VIRTUAL_BROWSER,
            "session_id": "sess-do-browser-3", "workspace_id": "ws-1", "tenant_id": "tenant-1",
        }))

        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(runtime.execute_action({
                "session_id": "sess-do-browser-3", "action": "click",
                "action_args": {},
            }))
        self.assertIn("requires coordinates", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
