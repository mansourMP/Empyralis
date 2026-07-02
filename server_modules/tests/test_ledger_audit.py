"""Phase J: Ledger expansion integration tests.

Channel sends, file writes, shell execution — one test per category
with hard redaction verified.

Phase K: Gateway audit trail — outbound + hardware choke points.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import ledger_audit


def _run(coro):
    return asyncio.run(coro)


class LedgerAuditRedactionTests(unittest.TestCase):
    """Hard redaction: ledger never stores raw content."""

    def test_channel_send_never_stores_raw_message(self):
        """Redacted record has NO raw text, only hash + byte count."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(),
        ) as mock_ledger:
            _run(ledger_audit.record_channel_send(
                workspace_id="ws-1",
                channel_key="telegram_hosted",
                remote_jid="user-123456789",
                text="Hello! This is a secret message the user sent.",
                status="sent",
            ))

        call_kwargs = mock_ledger.call_args.kwargs
        redacted = call_kwargs["metadata"]["redacted_args"]

        # Raw text is NEVER in the record
        self.assertNotIn("Hello", call_kwargs["summary"])
        self.assertNotIn("Hello", call_kwargs["title"])
        self.assertNotIn("secret", str(call_kwargs))

        # Redacted data is present
        self.assertEqual(redacted["channel_type"], "telegram_hosted")
        self.assertNotEqual(redacted["recipient_hash"], "none")
        self.assertGreater(redacted["byte_count"], 0)

    def test_file_write_never_stores_raw_content(self):
        """Redacted record has NO file content, only path hash + byte count."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(),
        ) as mock_ledger:
            _run(ledger_audit.record_file_write(
                workspace_id="ws-1",
                path="/home/user/secret_notes.txt",
                byte_count=4096,
                status="written",
            ))

        call_kwargs = mock_ledger.call_args.kwargs
        redacted = call_kwargs["metadata"]["redacted_args"]

        # Raw path is NEVER in the record
        self.assertNotIn("secret_notes", call_kwargs["summary"])
        self.assertNotIn("/home/user", call_kwargs["title"])

        # Redacted data is present
        self.assertNotEqual(redacted["path_hash"], "unknown")
        self.assertEqual(redacted["path_ext"], "txt")
        self.assertEqual(redacted["byte_count"], 4096)

    def test_shell_exec_never_stores_full_command(self):
        """Redacted record has ONLY program name, NOT arguments/secrets."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(),
        ) as mock_ledger:
            _run(ledger_audit.record_shell_exec(
                workspace_id="ws-1",
                command="curl -H 'Authorization: Bearer sk-secret-token' https://api.example.com/data",
                exit_code=0,
                combined_bytes=512,
                status="executed",
            ))

        call_kwargs = mock_ledger.call_args.kwargs
        redacted = call_kwargs["metadata"]["redacted_args"]

        # NEVER: secrets, URLs, arguments
        self.assertNotIn("Bearer", call_kwargs["summary"])
        self.assertNotIn("sk-secret", str(call_kwargs))
        self.assertNotIn("api.example.com", str(call_kwargs))
        self.assertNotIn("Authorization", str(call_kwargs))

        # ONLY: program name (first token)
        self.assertEqual(redacted["program"], "curl")
        self.assertGreater(redacted["token_count"], 1)  # had arguments
        self.assertEqual(redacted["exit_code"], 0)
        self.assertEqual(redacted["byte_count"], 512)

    def test_shell_exec_program_name_basename_only(self):
        """Full paths are reduced to basename."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(),
        ) as mock_ledger:
            _run(ledger_audit.record_shell_exec(
                workspace_id="ws-1",
                command="/usr/bin/python3 -c 'print(123)'",
                exit_code=0,
                combined_bytes=10,
            ))

        redacted = mock_ledger.call_args.kwargs["metadata"]["redacted_args"]
        self.assertEqual(redacted["program"], "python3")
        self.assertNotIn("/usr/bin", str(redacted))


class LedgerAuditIntegrationTests(unittest.TestCase):
    """End-to-end: call the record function, verify exactly one row."""

    def test_channel_send_returns_ledger_row(self):
        """record_channel_send returns a ledger row dict."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(return_value={
                "id": "aevt-channel-001",
                "action": "channel_send",
                "status": "sent",
            }),
        ):
            result = _run(ledger_audit.record_channel_send(
                workspace_id="ws-1",
                channel_key="discord_guild",
                remote_jid="user-abc",
                text="Hello world",
            ))
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "channel_send")

    def test_file_write_returns_ledger_row(self):
        """record_file_write returns a ledger row dict."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(return_value={
                "id": "aevt-file-001",
                "action": "file_write",
                "status": "written",
            }),
        ):
            result = _run(ledger_audit.record_file_write(
                workspace_id="ws-1",
                path="/tmp/output.txt",
                byte_count=100,
            ))
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "file_write")

    def test_shell_exec_returns_ledger_row(self):
        """record_shell_exec returns a ledger row dict."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(return_value={
                "id": "aevt-shell-001",
                "action": "shell_exec",
                "status": "executed",
            }),
        ):
            result = _run(ledger_audit.record_shell_exec(
                workspace_id="ws-1",
                command="ls",
                exit_code=0,
            ))
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "shell_exec")


# ═══════════════════════════════════════════════════════════════════════════
# Phase K: Gateway audit trail tests
# ═══════════════════════════════════════════════════════════════════════════

class GatewayLedgerRedactionTests(unittest.TestCase):
    """Phase K: gateway channel + hardware ledger — hard redaction."""

    def test_gateway_channel_send_never_stores_raw_message(self):
        """Gateway channel ledger: no raw text, phone numbers, or usernames."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(),
        ) as mock_ledger:
            _run(ledger_audit.record_gateway_channel_send(
                workspace_id="ws-1",
                actor_id="agent-001",
                channel_key="telegram",
                remote_jid="+1234567890",
                text="Secret message content",
                status="sent",
            ))

        call_kwargs = mock_ledger.call_args.kwargs

        # NEVER raw content or PII
        self.assertNotIn("Secret", call_kwargs["summary"])
        self.assertNotIn("+1234567890", str(call_kwargs))
        self.assertNotIn("message content", str(call_kwargs))

        # Event class is correct
        self.assertEqual(call_kwargs["event_class"], "gateway_channel")
        self.assertEqual(call_kwargs["action"], "channel_send")
        self.assertEqual(call_kwargs["actor_id"], "agent-001")

        # Redacted data is present
        redacted = call_kwargs["metadata"]["redacted_args"]
        self.assertEqual(redacted["channel_type"], "telegram")
        self.assertNotEqual(redacted["recipient_hash"], "none")
        self.assertGreater(redacted["byte_count"], 0)

    def test_gateway_channel_send_returns_exactly_one_row(self):
        """Fake gateway send → exactly one redacted ledger row."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(return_value={
                "id": "aevt-gw-ch-001",
                "action": "channel_send",
                "status": "sent",
            }),
        ) as mock_ledger:
            result = _run(ledger_audit.record_gateway_channel_send(
                workspace_id="ws-1",
                channel_key="whatsapp",
                remote_jid="user-abc",
                text="Test",
            ))

        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "channel_send")
        mock_ledger.assert_called_once()

    def test_gateway_hardware_invoke_returns_exactly_one_row(self):
        """Fake hardware invoke → exactly one redacted ledger row."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(return_value={
                "id": "aevt-gw-hw-001",
                "action": "shell_execute",
                "status": "executed",
            }),
        ) as mock_ledger:
            result = _run(ledger_audit.record_gateway_hardware_invoke(
                workspace_id="ws-1",
                actor_id="agent-002",
                capability_id="shell.execute",
                arguments={"command": "ls -la /secret"},
                status="executed",
            ))

        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "shell_execute")
        mock_ledger.assert_called_once()

    def test_gateway_hardware_invoke_never_stores_command_args(self):
        """Gateway hardware ledger: no raw command argument VALUES."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(),
        ) as mock_ledger:
            _run(ledger_audit.record_gateway_hardware_invoke(
                workspace_id="ws-1",
                capability_id="shell.execute",
                arguments={"command": "curl -H 'Authorization: Bearer sk-abc' https://evil.com"},
            ))

        call_kwargs = mock_ledger.call_args.kwargs

        # NEVER raw arg VALUES
        self.assertNotIn("Authorization", str(call_kwargs))
        self.assertNotIn("Bearer", str(call_kwargs))
        self.assertNotIn("sk-abc", str(call_kwargs))
        self.assertNotIn("evil.com", str(call_kwargs))

        # Event class and tier are correct
        self.assertEqual(call_kwargs["event_class"], "gateway_hardware")
        self.assertEqual(call_kwargs["metadata"]["execution_tier"], "gateway")

        # Args summary has key names (not values)
        redacted = call_kwargs["metadata"]["redacted_args"]
        self.assertIn("arg_keys", redacted)
        self.assertIn("command", redacted["arg_keys"])  # key name is safe
        self.assertEqual(redacted["capability_id"], "shell.execute")

    def test_gateway_hardware_action_classification(self):
        """Capability_id determines action: shell→shell_execute, file→file_write, etc."""
        cases = [
            ("shell.execute", "shell_execute"),
            ("filesystem.read_write", "file_write"),
            ("file.write", "file_write"),
            ("screenshot.capture", "screenshot"),
            ("browser.session.create", "other"),
        ]
        for cap_id, expected_action in cases:
            with self.subTest(capability_id=cap_id):
                with patch(
                    "server_modules.activity_ledger_service.append_activity_event",
                    new=AsyncMock(return_value={"action": expected_action}),
                ) as mock_ledger:
                    _run(ledger_audit.record_gateway_hardware_invoke(
                        workspace_id="ws-1",
                        capability_id=cap_id,
                    ))
                self.assertEqual(mock_ledger.call_args.kwargs["action"], expected_action)

    def test_gateway_ledger_is_best_effort(self):
        """Gateway ledger functions never raise, even on append failures."""
        with patch(
            "server_modules.activity_ledger_service.append_activity_event",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            # Must not raise
            result = _run(ledger_audit.record_gateway_channel_send(
                workspace_id="ws-1",
                channel_key="telegram",
                remote_jid="user-abc",
                text="test",
            ))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
