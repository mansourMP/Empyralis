import unittest
from unittest import mock

from server_modules import connectors_core


class ConnectorsCoreProcessLifecycleRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_anthropic_cli_login_calls_rust_before_popen(self):
        calls = []

        def rust_allow(**payload):
            calls.append("rust")
            self.assertEqual(payload["operation"], "start")
            self.assertEqual(payload["run_id"], "anthropic-local-cli-login")
            self.assertEqual(payload["status"], "not_started")
            self.assertEqual(payload["provider"], "anthropic")
            self.assertEqual(payload["executable"], "claude")
            return {"ok": True, "decision": "allow", "next_action": "spawn"}

        def popen_stub(*args, **kwargs):
            calls.append("popen")
            return mock.Mock(pid=12345)

        with mock.patch.object(connectors_core.shutil, "which", return_value="/opt/homebrew/bin/claude"), mock.patch.object(
            connectors_core.rust_runtime_kernel_client,
            "process_lifecycle_decision",
            side_effect=rust_allow,
        ) as rust_mock, mock.patch.object(
            connectors_core.subprocess,
            "Popen",
            side_effect=popen_stub,
        ) as popen_mock:
            result = await connectors_core.start_anthropic_local_cli_login()

        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["rust", "popen"])
        rust_mock.assert_called_once()
        popen_mock.assert_called_once()

    async def test_anthropic_cli_login_rust_denial_blocks_popen(self):
        blocked = connectors_core.rust_runtime_kernel_client.RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "kill_switch_blocks_process_start",
            },
            command="process-lifecycle-decision",
        )

        with mock.patch.object(connectors_core.shutil, "which", return_value="/opt/homebrew/bin/claude"), mock.patch.object(
            connectors_core.rust_runtime_kernel_client,
            "process_lifecycle_decision",
            side_effect=blocked,
        ) as rust_mock, mock.patch.object(
            connectors_core.subprocess,
            "Popen",
        ) as popen_mock:
            with self.assertRaises(connectors_core.HTTPException) as raised:
                await connectors_core.start_anthropic_local_cli_login()

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "kill_switch_blocks_process_start")
        rust_mock.assert_called_once()
        popen_mock.assert_not_called()

    async def test_anthropic_cli_login_wrong_rust_action_blocks_popen(self):
        with mock.patch.object(connectors_core.shutil, "which", return_value="/opt/homebrew/bin/claude"), mock.patch.object(
            connectors_core.rust_runtime_kernel_client,
            "process_lifecycle_decision",
            return_value={"ok": True, "decision": "allow", "next_action": "continue"},
        ) as rust_mock, mock.patch.object(
            connectors_core.subprocess,
            "Popen",
        ) as popen_mock:
            with self.assertRaises(connectors_core.HTTPException) as raised:
                await connectors_core.start_anthropic_local_cli_login()

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(raised.exception.detail["reason"], "unexpected_next_action:continue")
        rust_mock.assert_called_once()
        popen_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
