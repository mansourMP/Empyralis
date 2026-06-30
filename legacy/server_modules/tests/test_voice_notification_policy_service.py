from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import voice_notification_policy_service as service


def _run(coro):
    return asyncio.run(coro)


class VoiceNotificationPolicyServiceTests(unittest.TestCase):
    def test_voice_transcript_builds_policy_envelope(self):
        envelope = service.build_voice_task_policy_envelope(
            workspace_id="ws-1",
            tenant_id="tenant-1",
            transcript="Please summarize my calendar",
            source_channel="mobile_voice",
        )

        self.assertEqual(envelope.normalized_message, "Please summarize my calendar")
        self.assertEqual(envelope.as_policy_dict()["approvals_can_be_requested"], True)
        self.assertEqual(envelope.as_policy_dict()["approvals_can_be_resolved"], False)
        self.assertEqual(envelope.as_policy_dict()["surface"], "voice")

    def test_voice_transcript_redacts_secrets(self):
        envelope = service.build_voice_task_policy_envelope(
            workspace_id="ws-1",
            transcript="Use sk-testsecret123456789 for this task",
        )

        self.assertNotIn("sk-testsecret123456789", envelope.normalized_message)
        self.assertIn("[redacted-secret]", envelope.normalized_message)

    def test_voice_approval_token_is_blocked(self):
        envelope = service.build_voice_task_policy_envelope(
            workspace_id="ws-1",
            transcript="Approve sap_abc1234567890 now",
        )

        self.assertTrue(envelope.approval_resolution_blocked)
        self.assertEqual(envelope.as_policy_dict()["blocked_approval_count"], 1)

    def test_voice_approval_intent_is_blocked_without_token(self):
        envelope = service.build_voice_task_policy_envelope(
            workspace_id="ws-1",
            transcript="Yes, go ahead and send that pending email",
        )

        self.assertTrue(envelope.approval_resolution_blocked)

    def test_normal_voice_task_routes_to_sage_turn(self):
        fake_result = service.SageTurnResult(
            message="Reply",
            trace_id="trace-1",
            provider="deepseek",
            model="deepseek-chat",
        )
        with (
            patch("server_modules.voice_notification_policy_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
            patch("server_modules.voice_notification_policy_service.security_audit_service.emit_security_audit_event"),
            patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock(return_value=fake_result)) as mock_execute,
        ):
            result = _run(service.execute_voice_sage_task(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                transcript="Draft a note for tomorrow",
                current_user={"user_id": "user-1"},
            ))

        self.assertEqual(result["message"], "Reply")
        self.assertEqual(result["voice_policy"]["approvals_can_be_resolved"], False)
        kwargs = mock_execute.call_args.kwargs
        self.assertEqual(kwargs["surface"], "voice")
        self.assertEqual(kwargs["mode"], service.SAGE_MODE)

    def test_voice_approval_attempt_does_not_call_sage_turn(self):
        with (
            patch("server_modules.voice_notification_policy_service.activity_ledger_service.append_activity_event", new=AsyncMock()) as mock_activity,
            patch("server_modules.voice_notification_policy_service.security_audit_service.emit_security_audit_event") as mock_audit,
            patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock()) as mock_execute,
        ):
            result = _run(service.execute_voice_sage_task(
                workspace_id="ws-1",
                tenant_id="tenant-1",
                transcript="Approve sap_abc1234567890 and send it",
                current_user={"user_id": "user-1"},
            ))

        self.assertEqual(result["error"], service.VOICE_APPROVAL_BLOCKED_ERROR)
        self.assertEqual(result["voice_policy"]["approval_resolution_blocked"], True)
        mock_execute.assert_not_called()
        self.assertEqual(mock_audit.call_args.kwargs["status"], "blocked")
        self.assertEqual(mock_activity.await_args.kwargs["event_class"], "blocked_action")

    def test_approval_notification_payload_cannot_inline_approve(self):
        payload = service.build_safe_approval_notification_payload(
            workspace_id="ws-1",
            tenant_id="tenant-1",
            trace_id="trace-1",
            action="send_email",
            description="Send email using token=abc123",
            approval_token="sap_abc1234567890",
        )

        self.assertEqual(payload["data"]["canApproveInline"], False)
        self.assertEqual(payload["data"]["approvalSurface"], "open_app")
        self.assertNotIn("sap_abc1234567890", str(payload))
        self.assertNotIn("abc123", str(payload))


if __name__ == "__main__":
    unittest.main()
