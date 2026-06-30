from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server_modules import sage_services_service


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "create_sage_service_entry",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class SageServicesServiceRustGateTests(unittest.TestCase):
    def _workspace_scope(self, root: Path):
        return lambda workspace_id: root / str(workspace_id or "default")

    def test_create_service_entry_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                sage_services_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                sage_services_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision, mock.patch.object(
                sage_services_service.personal_context_engine,
                "publish_event",
                new=mock.AsyncMock(),
            ):
                asyncio.run(sage_services_service.create_service_entry(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    service_id="flashcards",
                    entry={"front": "Capital of France?", "back": "Paris"},
                    actor_user_id="owner-1",
                ))

            state_path = root / "workspace-1" / "sage_services.json"
            self.assertTrue(state_path.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "create_sage_service_entry")
            self.assertEqual(payload["state_class"], "sage_services")
            self.assertEqual(payload["workspace_id"], "workspace-1")
            self.assertEqual(payload["actor_id"], "owner-1")
            self.assertEqual(payload["payload"]["service_id"], "flashcards")

    def test_profile_update_blocks_before_persisting_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "update_sage_service_profile",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                sage_services_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                sage_services_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ), mock.patch.object(
                sage_services_service.personal_context_engine,
                "publish_event",
                new=mock.AsyncMock(),
            ):
                with self.assertRaises(sage_services_service.SageServicesRustGateError):
                    asyncio.run(sage_services_service.update_service_profile(
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        service_id="flashcards",
                        profile={"focus_topic": "biology", "active_deck": "cells"},
                        actor_user_id="owner-1",
                    ))

            self.assertFalse((root / "workspace-1" / "sage_services.json").exists())

    def test_profile_update_blocks_before_persisting_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                sage_services_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                sage_services_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ), mock.patch.object(
                sage_services_service.personal_context_engine,
                "publish_event",
                new=mock.AsyncMock(),
            ):
                with self.assertRaises(sage_services_service.SageServicesRustGateError) as raised:
                    asyncio.run(sage_services_service.update_service_profile(
                        tenant_id="tenant-1",
                        workspace_id="workspace-1",
                        service_id="flashcards",
                        profile={"focus_topic": "biology", "active_deck": "cells"},
                        actor_user_id="owner-1",
                    ))

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse((root / "workspace-1" / "sage_services.json").exists())

    def test_update_delete_and_pin_entry_require_rust_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with mock.patch.object(
                sage_services_service.workspace_context,
                "workspace_scope_dir",
                side_effect=self._workspace_scope(root),
            ), mock.patch.object(
                sage_services_service.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                side_effect=[
                    dict(_ALLOW_DECISION),
                    {**_ALLOW_DECISION, "next_action": "update_sage_service_entry"},
                    {**_ALLOW_DECISION, "next_action": "set_sage_service_entry_pinned"},
                    {**_ALLOW_DECISION, "next_action": "delete_sage_service_entry"},
                ],
            ) as rust_decision, mock.patch.object(
                sage_services_service.personal_context_engine,
                "publish_event",
                new=mock.AsyncMock(),
            ):
                created = asyncio.run(sage_services_service.create_service_entry(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    service_id="flashcards",
                    entry={"front": "One?", "back": "Two"},
                    actor_user_id="owner-1",
                ))
                entry_id = created["service"]["entries"][0]["id"]
                rust_decision.reset_mock()

                asyncio.run(sage_services_service.update_service_entry(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    service_id="flashcards",
                    entry_id=entry_id,
                    entry={"front": "One?", "back": "Updated"},
                    actor_user_id="owner-1",
                ))
                asyncio.run(sage_services_service.set_service_entry_pinned(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    service_id="flashcards",
                    entry_id=entry_id,
                    pinned=True,
                    actor_user_id="owner-1",
                ))
                asyncio.run(sage_services_service.delete_service_entry(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    service_id="flashcards",
                    entry_id=entry_id,
                    actor_user_id="owner-1",
                ))

            operations = [call.kwargs["operation"] for call in rust_decision.call_args_list]
            self.assertIn("update_sage_service_entry", operations)
            self.assertIn("set_sage_service_entry_pinned", operations)
            self.assertIn("delete_sage_service_entry", operations)


if __name__ == "__main__":
    unittest.main()
