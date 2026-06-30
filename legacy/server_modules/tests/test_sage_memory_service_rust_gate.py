import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import sage_memory_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class SageMemoryServiceRustGateTests(unittest.TestCase):
    def test_upsert_memory_entry_calls_rust_before_save(self):
        state = {"version": 1, "entries": []}
        with mock.patch.object(
            sage_memory_service,
            "_read_state",
            return_value=state,
        ), mock.patch.object(
            sage_memory_service,
            "_save_state",
            return_value=state,
        ) as save_state, mock.patch.object(
            sage_memory_service,
            "list_sage_memory",
            return_value={"entries": []},
        ), mock.patch.object(
            sage_memory_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "upsert_sage_memory_entry",
                "next_action": "write_sage_memory_entry",
            },
        ) as rust_gate:
            result = sage_memory_service.upsert_memory_entry(
                workspace_id="ws-1",
                category="safe_general",
                title="Preference",
                content="Use concise answers.",
                actor_user_id="owner-1",
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "runtime-state-store-decision")
        self.assertEqual(payload["operation"], "upsert_sage_memory_entry")
        self.assertEqual(payload["state_class"], "sage_memory")
        self.assertEqual(payload["workspace_id"], "ws-1")
        self.assertEqual(payload["actor_id"], "owner-1")
        save_state.assert_called_once()
        self.assertEqual(result["entry"]["category"], "safe_general")

    def test_upsert_memory_entry_wrong_rust_action_blocks_save(self):
        state = {"version": 1, "entries": []}
        with mock.patch.object(
            sage_memory_service,
            "_read_state",
            return_value=state,
        ), mock.patch.object(
            sage_memory_service,
            "_save_state",
        ) as save_state, mock.patch.object(
            sage_memory_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "upsert_sage_memory_entry",
                "next_action": "write_profile_api_file",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                sage_memory_service.upsert_memory_entry(
                    workspace_id="ws-1",
                    category="safe_general",
                    title="Preference",
                    content="Use concise answers.",
                    actor_user_id="owner-1",
                )

        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(
            raised.exception.detail["reason"],
            "unexpected_next_action:write_profile_api_file",
        )
        save_state.assert_not_called()

    def test_delete_memory_entry_rust_denial_blocks_save(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "retention_lock_blocks_delete",
                "operation": "delete_sage_memory_entry",
            },
            command="runtime-state-store-decision",
        )
        state = {
            "version": 1,
            "entries": [
                {
                    "id": "entry-1",
                    "category": "safe_general",
                    "title": "Preference",
                    "content": "Use concise answers.",
                    "history": [],
                }
            ],
        }
        with mock.patch.object(
            sage_memory_service,
            "_read_state",
            return_value=state,
        ), mock.patch.object(
            sage_memory_service,
            "_save_state",
        ) as save_state, mock.patch.object(
            sage_memory_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ):
            with self.assertRaises(HTTPException) as raised:
                sage_memory_service.delete_memory_entry(
                    workspace_id="ws-1",
                    entry_id="entry-1",
                    actor_user_id="owner-1",
                )

        self.assertEqual(raised.exception.status_code, 423)
        save_state.assert_not_called()
