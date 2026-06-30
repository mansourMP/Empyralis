import asyncio
import unittest

from server_modules.virtual_computer_runtime import (
    IDENTITY_CLASS_AGENT_VIRTUAL,
    IDENTITY_CLASS_BUSINESS_WORKSPACE,
    IDENTITY_CLASS_EPHEMERAL_TASK,
    IDENTITY_CLASS_USER_LOCAL,
    InMemoryVirtualComputerRuntime,
    LOGIN_STATE_CREDENTIAL_BROKER,
    LOGIN_STATE_UNAUTHENTICATED,
    LOGIN_STATE_USER_INTERACTIVE,
)


class VirtualComputerIdentitySplitTests(unittest.TestCase):
    def test_virtual_session_blocks_local_cookie_reuse(self):
        runtime = InMemoryVirtualComputerRuntime()

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.create_session(
                    {
                        "runtime_choice": "virtual_browser",
                        "reuse_local_cookies": True,
                    }
                )
            )

    def test_virtual_session_blocks_existing_session_attach_mode(self):
        runtime = InMemoryVirtualComputerRuntime()

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.create_session(
                    {
                        "runtime_choice": "virtual_browser",
                        "browser_session_mode": "existing_session_attach",
                    }
                )
            )

    def test_virtual_session_defaults_to_agent_virtual_and_unauthenticated(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        identity = created.get("identity_context") or {}

        self.assertEqual(identity.get("identity_class"), IDENTITY_CLASS_AGENT_VIRTUAL)
        self.assertEqual(identity.get("login_state"), LOGIN_STATE_UNAUTHENTICATED)
        self.assertFalse(identity.get("cookie_reuse_allowed"))

    def test_virtual_session_uses_credential_broker_login_state_when_grant_present(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_code_sandbox",
                    "credential_grant_id": "grant_123",
                }
            )
        )
        identity = created.get("identity_context") or {}

        self.assertEqual(identity.get("login_state"), LOGIN_STATE_CREDENTIAL_BROKER)
        self.assertEqual(identity.get("credential_grant_id"), "grant_123")

    def test_local_session_defaults_to_user_local_identity(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "local"}))
        identity = created.get("identity_context") or {}

        self.assertEqual(identity.get("identity_class"), IDENTITY_CLASS_USER_LOCAL)
        self.assertEqual(identity.get("login_state"), LOGIN_STATE_USER_INTERACTIVE)
        self.assertTrue(identity.get("cookie_reuse_allowed"))

    def test_business_and_ephemeral_identity_class_overrides(self):
        runtime = InMemoryVirtualComputerRuntime()

        business = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_desktop",
                    "business_workspace_session": True,
                }
            )
        )
        self.assertEqual((business.get("identity_context") or {}).get("identity_class"), IDENTITY_CLASS_BUSINESS_WORKSPACE)

        ephemeral = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_code_sandbox",
                    "ephemeral_task": True,
                }
            )
        )
        self.assertEqual((ephemeral.get("identity_context") or {}).get("identity_class"), IDENTITY_CLASS_EPHEMERAL_TASK)


if __name__ == "__main__":
    unittest.main()
