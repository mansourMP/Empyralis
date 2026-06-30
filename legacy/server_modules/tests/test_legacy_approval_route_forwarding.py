import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import runs_history


class _ApprovalPayload:
    def __init__(self, decision: str, note: str = "", approval_scope: str | None = None) -> None:
        self.decision = decision
        self.note = note
        self.approval_scope = approval_scope

    def validate_fields(self) -> None:
        return None


class LegacyApprovalRouteForwardingTests(unittest.TestCase):
    def test_list_pending_approvals_forwards_to_runtime_helper(self):
        current_user = {"user_id": "user-1"}

        with patch.object(
            runs_history.runtime_run_approval_service,
            "list_pending_approvals_payload",
            return_value={"items": [{"approval_id": "approval-1"}], "count": 1},
        ) as helper:
            payload = asyncio.run(
                runs_history.list_pending_approvals(
                    workspace_id="ws-1",
                    limit=5,
                    current_user=current_user,
                )
            )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["approval_id"], "approval-1")
        self.assertEqual(helper.call_args.kwargs["workspace_id"], "ws-1")
        self.assertEqual(helper.call_args.kwargs["current_user"], current_user)

    def test_list_cognitive_approvals_maps_runtime_items_to_legacy_shape(self):
        with patch.object(
            runs_history.runtime_run_approval_service,
            "list_pending_approvals_payload",
            return_value={
                "items": [
                    {
                        "approval_id": "approval-1",
                        "run_id": "run-1",
                        "workspace_id": "ws-1",
                        "summary": "Approve send",
                        "status": "pending",
                        "action": "email_send",
                    }
                ],
                "count": 1,
            },
        ):
            payload = asyncio.run(runs_history.list_cognitive_approvals(limit=5))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "runtime")
        self.assertEqual(payload["items"][0]["event_id"], "approval-1")
        self.assertEqual(payload["items"][0]["objective_id"], "run-1")

    def test_resolve_cognitive_approval_forwards_to_runtime_resolver(self):
        with patch.object(
            runs_history.runtime_run_approval_service,
            "resolve_standalone_approval_with_runtime_defaults",
            return_value={
                "approval_id": "approval-1",
                "run_id": "run-1",
                "resolution": "approved",
                "actor": "user",
                "reason": "ok",
                "correlation_id": "corr-1",
                "outbox_event": {"event_id": "evt-1"},
            },
        ) as helper:
            payload = asyncio.run(
                runs_history.resolve_cognitive_approval(
                    "approval-1",
                    _ApprovalPayload("approve", "ok"),
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["event_id"], "approval-1")
        self.assertEqual(payload["status"], "approved")
        self.assertEqual(helper.call_args.kwargs["payload"]["resolution"], "approved")

    def test_resolve_approval_bridges_gateway_hardware_approval(self):
        gateway_result = {
            "status": "executed",
            "approval": {
                "approval_id": "gapproval-1",
                "request_payload": {
                    "runtime_session_binding": "sage_hardware_action",
                    "runtime_session_id": "hrs-1",
                },
            },
            "execution": {"result": {"summary": "Ran command."}},
        }
        with (
            patch.object(
                runs_history.gateway_approval_service,
                "get_gateway_tool_approval",
                return_value={
                    "approval_id": "gapproval-1",
                    "gateway_id": "gw-1",
                    "run_id": "run-1",
                },
            ),
            patch.object(
                runs_history.gateway_state_repository,
                "get_gateway_registration",
                return_value={"gateway_id": "gw-1"},
            ),
            patch.object(
                runs_history.gateway_approval_service,
                "resolve_gateway_tool_approval",
                new=AsyncMock(return_value=gateway_result),
            ) as resolve_gateway,
            patch.object(
                runs_history.hardware_action_broker_service,
                "record_gateway_approval_resolution",
                new=AsyncMock(return_value={**gateway_result, "runtime_session": {"state": "ready"}}),
            ) as record_resolution,
        ):
            payload = asyncio.run(runs_history.resolve_approval("gapproval-1", _ApprovalPayload("approved", "ok")))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "gateway")
        self.assertEqual(payload["status"], "approved")
        resolve_gateway.assert_awaited_once()
        record_resolution.assert_awaited_once()

    def test_resolve_approval_bridges_runtime_hardware_approval(self):
        with (
            patch.object(
                runs_history.gateway_approval_service,
                "get_gateway_tool_approval",
                return_value=None,
            ),
            patch.object(
                runs_history.hardware_action_broker_service,
                "get_runtime_hardware_approval",
                new=AsyncMock(
                    return_value={
                        "approval_id": "cloudapproval-1",
                        "workspace_id": "ws-1",
                        "run_id": "run-1",
                    }
                ),
            ) as get_hardware,
            patch.object(
                runs_history.hardware_action_broker_service,
                "resolve_runtime_hardware_approval",
                new=AsyncMock(
                    return_value={
                        "status": "completed",
                        "runtime_session": {"state": "ready", "run_id": "run-1"},
                    }
                ),
            ) as resolve_hardware,
        ):
            payload = asyncio.run(
                runs_history.resolve_approval(
                    "cloudapproval-1",
                    _ApprovalPayload("approved", "ok", approval_scope="session"),
                    workspace_id="ws-1",
                    current_user={"user_id": "user-1"},
                )
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "hardware_runtime")
        self.assertEqual(payload["status"], "approved")
        get_hardware.assert_awaited_once()
        resolve_hardware.assert_awaited_once()
        self.assertEqual(resolve_hardware.await_args.kwargs["approval_scope"], "session")


if __name__ == "__main__":
    unittest.main()
