"""Phase J: Ledger expansion integration tests.

Channel sends, file writes, shell execution — one test per category
with hard redaction verified.
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


if __name__ == "__main__":
    unittest.main()
