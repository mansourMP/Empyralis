import asyncio
import unittest

from server_modules.virtual_computer_runtime import InMemoryVirtualComputerRuntime


class VirtualComputerNetworkBrowserSecurityTests(unittest.TestCase):
    def test_url_deny_policy_blocks_domain(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "network_browser_policy": {"denied_domains": ["evil.example"]},
                }
            )
        )
        session_id = created.get("session_id")
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "open_url",
                        "action_args": {"url": "https://evil.example/path"},
                    }
                )
            )

    def test_private_ip_and_metadata_endpoints_are_blocked_by_default(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "open_url",
                        "action_args": {"url": "http://127.0.0.1:8080"},
                    }
                )
            )
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "open_url",
                        "action_args": {"url": "http://169.254.169.254/latest/meta-data"},
                    }
                )
            )

    def test_phishing_detection_blocks_suspicious_login_page(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "open_url",
                        "action_args": {"url": "https://secure-login-portal.example/signin-now"},
                    }
                )
            )

    def test_auto_download_requires_approval(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "isolation_profile": {"file_transfer_enabled": True},
                }
            )
        )
        session_id = created.get("session_id")
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "download_artifact",
                        "action_args": {"url": "https://example.com/public.csv", "auto_download": True},
                    }
                )
            )

        approved = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "download_artifact",
                    "approval_id": "appr_download",
                    "action_args": {"url": "https://example.com/public.csv", "auto_download": True},
                }
            )
        )
        self.assertEqual((approved.get("action_result") or {}).get("action"), "download_artifact")

    def test_domain_bound_credential_injection(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "login_state": "credential_broker_grant",
                    "credential_grant_id": "grant_1",
                    "credential_scope_domain": "example.com",
                }
            )
        )
        session_id = created.get("session_id")

        allowed = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "open_url",
                    "approval_id": "appr_credential_domain_1",
                    "action_args": {"url": "https://accounts.example.com/login"},
                }
            )
        )
        self.assertEqual((allowed.get("action_result") or {}).get("action"), "open_url")

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "open_url",
                        "action_args": {"url": "https://evil.com/login"},
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
