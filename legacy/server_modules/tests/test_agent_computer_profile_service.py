from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import agent_computer_profile_service as profiles


class AgentComputerProfileServiceTests(unittest.TestCase):
    def _patch_workspace(self, root: Path):
        return patch("server_modules.agent_computer_profile_service.workspace_context.workspace_scope_dir", return_value=root)

    def test_create_profile_persists_machine_identity_without_autonomy_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "ws-1"
            with self._patch_workspace(root):
                profile = profiles.create_agent_computer_profile(
                    {
                        "workspace_id": "ws-1",
                        "owner_user_id": "user-1",
                        "policy_id": "policy-1",
                        "machine_label": "Mansur Mac Mini",
                        "environment_kind": "dedicated_workstation",
                        "gateway_id": "gw-1",
                        "dedicated_to_agent": True,
                        "filesystem_roots": ["/Users/mansur/Agent", "/Users/mansur/Agent"],
                    }
                )

            payload = profile.as_dict()
            self.assertTrue(profile.profile_id.startswith(profiles.PROFILE_ID_PREFIX))
            self.assertEqual(payload["policy_id"], "policy-1")
            self.assertEqual(payload["environment_kind"], "dedicated_workstation")
            self.assertEqual(payload["filesystem_roots"], ["/Users/mansur/Agent"])
            self.assertNotIn("autonomy_mode", payload)
            self.assertTrue((root / "agent_computer_profiles.json").exists())

    def test_profile_rejects_autonomy_mode(self) -> None:
        with self.assertRaises(profiles.AgentComputerProfileError):
            profiles.normalize_agent_computer_profile(
                {
                    "workspace_id": "ws-1",
                    "policy_id": "policy-1",
                    "environment_kind": "personal_computer",
                    "gateway_id": "gw-1",
                    "autonomy_mode": "trusted_workstation",
                }
            )

    def test_personal_computer_requires_gateway_id(self) -> None:
        with self.assertRaises(profiles.AgentComputerProfileError):
            profiles.normalize_agent_computer_profile(
                {
                    "workspace_id": "ws-1",
                    "policy_id": "policy-1",
                    "environment_kind": "personal_computer",
                }
            )

    def test_cloud_profile_does_not_require_gateway(self) -> None:
        profile = profiles.normalize_agent_computer_profile(
            {
                "workspace_id": "ws-1",
                "policy_id": "policy-cloud",
                "environment_kind": "cloud_computer",
                "machine_label": "Cloud Sandbox",
            }
        )

        self.assertIsNone(profile.gateway_id)
        self.assertEqual(profile.environment_kind, "cloud_computer")

    def test_get_profile_is_workspace_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "ws-1"
            with self._patch_workspace(root):
                created = profiles.create_agent_computer_profile(
                    {
                        "workspace_id": "ws-1",
                        "policy_id": "policy-1",
                        "environment_kind": "personal_computer",
                        "gateway_id": "gw-1",
                    }
                )
                found = profiles.get_agent_computer_profile(workspace_id="ws-1", profile_id=created.profile_id)
                missing = profiles.get_agent_computer_profile(workspace_id="ws-2", profile_id=created.profile_id)

            self.assertIsNotNone(found)
            self.assertIsNone(missing)

    def test_list_profiles_returns_only_workspace_records(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "ws-1"
            with self._patch_workspace(root):
                profiles.create_agent_computer_profile(
                    {
                        "workspace_id": "ws-1",
                        "policy_id": "policy-1",
                        "environment_kind": "personal_computer",
                        "gateway_id": "gw-1",
                        "machine_label": "A",
                    }
                )
                profiles.create_agent_computer_profile(
                    {
                        "workspace_id": "ws-1",
                        "policy_id": "policy-2",
                        "environment_kind": "cloud_computer",
                        "machine_label": "B",
                    }
                )
                items = profiles.list_agent_computer_profiles(workspace_id="ws-1")

            self.assertEqual(len(items), 2)
            self.assertEqual([item["machine_label"] for item in items], ["A", "B"])

    def test_update_health_persists_status(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "ws-1"
            with self._patch_workspace(root):
                created = profiles.create_agent_computer_profile(
                    {
                        "workspace_id": "ws-1",
                        "policy_id": "policy-1",
                        "environment_kind": "personal_computer",
                        "gateway_id": "gw-1",
                    }
                )
                updated = profiles.update_agent_computer_profile_health(
                    workspace_id="ws-1",
                    profile_id=created.profile_id,
                    health_state="online",
                    last_seen_at="2026-05-13T00:00:00Z",
                )
                reloaded = profiles.get_agent_computer_profile(workspace_id="ws-1", profile_id=created.profile_id)

            self.assertEqual(updated.health_state, "online")
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.health_state, "online")

    def test_runtime_attachment_builds_local_profile_with_gateway_identity(self) -> None:
        profile = profiles.agent_computer_profile_from_runtime_attachment(
            {
                "attachment_id": "local_companion:gw-1",
                "attachment_kind": "local_companion",
                "label": "Personal Mac",
                "healthy": True,
                "gateway_identity": {
                    "gateway_id": "gw-1",
                    "user_id": "user-1",
                },
            },
            workspace_id="ws-1",
            policy_id="policy-1",
        )

        self.assertEqual(profile.environment_kind, "personal_computer")
        self.assertEqual(profile.gateway_id, "gw-1")
        self.assertEqual(profile.owner_user_id, "user-1")
        self.assertEqual(profile.health_state, "online")

    def test_create_profile_fail_closed_on_write_error(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "ws-1"
            with self._patch_workspace(root), patch(
                "server_modules.agent_computer_profile_service._write_state",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(RuntimeError):
                    profiles.create_agent_computer_profile(
                        {
                            "workspace_id": "ws-1",
                            "policy_id": "policy-1",
                            "environment_kind": "personal_computer",
                            "gateway_id": "gw-1",
                        }
                    )

    def test_create_profile_wrong_rust_action_blocks_state_write(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "ws-1"
            with (
                self._patch_workspace(root),
                patch(
                    "server_modules.agent_computer_profile_service.rust_runtime_kernel_client.runtime_state_store_decision",
                    return_value={"ok": True, "decision": "allow", "next_action": "write_profile_api_file"},
                ) as rust_mock,
            ):
                with self.assertRaises(profiles.AgentComputerProfileRustGateError) as raised:
                    profiles.create_agent_computer_profile(
                        {
                            "workspace_id": "ws-1",
                            "policy_id": "policy-1",
                            "environment_kind": "personal_computer",
                            "gateway_id": "gw-1",
                        }
                    )

        self.assertEqual(str(raised.exception), "unexpected_next_action")
        rust_mock.assert_called_once()
        self.assertFalse((root / "agent_computer_profiles.json").exists())


if __name__ == "__main__":
    unittest.main()
