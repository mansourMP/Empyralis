from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules import sage_chat_api


class SageChatApiApprovalActivityTests(unittest.IsolatedAsyncioTestCase):
    def test_resolve_tenant_id_uses_workspace_access_entry(self):
        current_user = {
            "workspace_access": {
                "ws_1": {"tenant_id": "tenant_ws_1"},
            }
        }
        resolved = sage_chat_api._resolve_tenant_id(current_user, "ws_1")
        self.assertEqual(resolved, "tenant_ws_1")

    def test_resolve_tenant_id_falls_back_to_default(self):
        resolved = sage_chat_api._resolve_tenant_id({}, "ws_missing")
        self.assertEqual(resolved, "default")

    async def test_emit_approval_activity_appends_timeline_event(self):
        with patch(
            "server_modules.sage_chat_api.activity_ledger_service.append_activity_event",
            new=AsyncMock(),
        ) as mock_append:
            await sage_chat_api._emit_approval_activity(
                action="sage_approval_resolved",
                status="approved",
                approval_token="sap_test",
                tenant_id="tenant_1",
                workspace_id="ws_1",
                actor_user_id="user_1",
                trace_id="trace_1",
                summary="Approved action",
                payload={"resolved_action": "channel_send_draft"},
            )

        mock_append.assert_awaited_once()
        kwargs = mock_append.await_args.kwargs
        self.assertEqual(kwargs["event_class"], "approval")
        self.assertEqual(kwargs["action"], "sage_approval_resolved")
        self.assertEqual(kwargs["trace_id"], "trace_1")
        self.assertEqual(kwargs["status"], "approved")
        self.assertEqual(kwargs["actor_id"], "user_1")
        self.assertEqual(kwargs["payload"]["approval_token"], "sap_test")
        self.assertEqual(kwargs["payload"]["resolved_action"], "channel_send_draft")

    async def test_emit_approval_activity_swallow_failures(self):
        with patch(
            "server_modules.sage_chat_api.activity_ledger_service.append_activity_event",
            new=AsyncMock(side_effect=RuntimeError("write failed")),
        ):
            await sage_chat_api._emit_approval_activity(
                action="sage_approval_executed",
                status="completed",
                approval_token="sap_test",
                tenant_id="tenant_1",
                workspace_id="ws_1",
                actor_user_id="user_1",
                trace_id="trace_1",
            )


if __name__ == "__main__":
    unittest.main()
