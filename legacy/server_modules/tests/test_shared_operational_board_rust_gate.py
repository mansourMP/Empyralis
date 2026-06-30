import unittest
from unittest import mock

from fastapi import HTTPException

from server_modules import shared_operational_board_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class SharedOperationalBoardRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_next_action_blocks_board_file_write(self):
        fake_path = mock.Mock()
        with mock.patch.object(
            shared_operational_board_service,
            "_read_board_records",
            return_value=[],
        ), mock.patch.object(
            shared_operational_board_service,
            "_board_log_path",
            return_value=fake_path,
        ), mock.patch.object(
            shared_operational_board_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "write_shared_operational_board_entry",
                "next_action": "write_state",
            },
        ):
            with self.assertRaises(HTTPException) as raised:
                await shared_operational_board_service.write_shared_operational_board_entry(
                    "ws-1",
                    tenant_id="tenant-1",
                    actor_permission="publish_update",
                    action="publish_update",
                    actor={"id": "owner-1", "type": "user"},
                    entry_kind="handoff_note",
                    title="Handoff",
                    body="Next step is ready.",
                )

        self.assertEqual(raised.exception.status_code, 423)
        fake_path.open.assert_not_called()

    async def test_rust_denial_blocks_board_file_write(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "owner_access_denied",
                "operation": "write_shared_operational_board_entry",
            },
            command="runtime-state-store-decision",
        )
        fake_path = mock.Mock()
        with mock.patch.object(
            shared_operational_board_service,
            "_read_board_records",
            return_value=[],
        ), mock.patch.object(
            shared_operational_board_service,
            "_board_log_path",
            return_value=fake_path,
        ), mock.patch.object(
            shared_operational_board_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ):
            with self.assertRaises(HTTPException) as raised:
                await shared_operational_board_service.write_shared_operational_board_entry(
                    "ws-1",
                    tenant_id="tenant-1",
                    actor_permission="publish_update",
                    action="publish_update",
                    actor={"id": "owner-1", "type": "user"},
                    entry_kind="handoff_note",
                    title="Handoff",
                    body="Next step is ready.",
                )

        self.assertEqual(raised.exception.status_code, 423)
        fake_path.open.assert_not_called()
