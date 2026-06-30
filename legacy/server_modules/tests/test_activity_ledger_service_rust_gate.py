import unittest
from unittest import mock

from server_modules import activity_ledger_service
from server_modules.rust_runtime_kernel_client import RustKernelDecisionError


class ActivityLedgerServiceRustGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_activity_event_calls_rust_before_repository_append(self):
        with mock.patch.object(
            activity_ledger_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "append_activity_ledger_event",
                "next_action": "append_activity_ledger_event",
            },
        ) as rust_gate, mock.patch.object(
            activity_ledger_service.control_plane_repository,
            "append_activity_ledger_event",
            new=mock.AsyncMock(return_value={"id": "event-1"}),
        ) as append_event:
            result = await activity_ledger_service.append_activity_event(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                actor_type="user",
                actor_id="owner-1",
                event_class="memory_update",
                title="Memory updated",
                summary="A memory entry changed.",
            )

        rust_gate.assert_called_once()
        command, payload = rust_gate.call_args.args
        self.assertEqual(command, "runtime-state-store-decision")
        self.assertEqual(payload["operation"], "append_activity_ledger_event")
        self.assertEqual(payload["state_class"], "activity_ledger")
        self.assertEqual(payload["workspace_id"], "ws-1")
        append_event.assert_awaited_once()
        self.assertEqual(result["id"], "event-1")

    async def test_append_activity_event_rust_denial_blocks_repository_append(self):
        denied = RustKernelDecisionError(
            {
                "ok": False,
                "decision": "block",
                "reason": "workspace_access_denied",
                "operation": "append_activity_ledger_event",
            },
            command="runtime-state-store-decision",
        )
        with mock.patch.object(
            activity_ledger_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            side_effect=denied,
        ), mock.patch.object(
            activity_ledger_service.control_plane_repository,
            "append_activity_ledger_event",
            new=mock.AsyncMock(),
        ) as append_event:
            with self.assertRaises(RuntimeError):
                await activity_ledger_service.append_activity_event(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    actor_type="user",
                    actor_id="owner-1",
                    event_class="memory_update",
                    title="Memory updated",
                    summary="A memory entry changed.",
                )

        append_event.assert_not_awaited()

    async def test_append_activity_event_unexpected_rust_next_action_blocks_repository_append(self):
        with mock.patch.object(
            activity_ledger_service.rust_runtime_kernel_client,
            "run_runtime_kernel_enforced",
            return_value={
                "ok": True,
                "decision": "allow",
                "reason": "runtime_state_store_policy_satisfied",
                "operation": "append_activity_ledger_event",
                "next_action": "write_state",
            },
        ), mock.patch.object(
            activity_ledger_service.control_plane_repository,
            "append_activity_ledger_event",
            new=mock.AsyncMock(),
        ) as append_event:
            with self.assertRaises(RuntimeError) as raised:
                await activity_ledger_service.append_activity_event(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    actor_type="user",
                    actor_id="owner-1",
                    event_class="memory_update",
                    title="Memory updated",
                    summary="A memory entry changed.",
                )

        self.assertIn("unexpected next_action", str(raised.exception))
        append_event.assert_not_awaited()
