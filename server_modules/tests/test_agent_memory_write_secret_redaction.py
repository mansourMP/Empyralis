"""Tests for the 2026-07-22 memory_write secret-redaction fix.

VERIFIED DEFECT (Tier 0.3): agent_memory_tools.memory_write persisted
content to the agent's durable memory directory with ZERO secret
redaction. A live API key, bearer/JWT token, password, or card number
appearing in a tool result or conversation could be written to disk
verbatim and would then survive across every future session (memory
files are read back on-demand via memory_read, and MEMORY.md is
injected into every turn's context).

The fix reuses the codebase's existing, already-widely-used
secret_redaction_service.redact_text (the same redactor that already
guards transparency events, the activity ledger, gateway payloads,
and ~25 other call sites) and applies it to `content` inside
memory_write, BEFORE the append/overwrite branch computes the bytes
that get written to disk. See agent_memory_tools.py's memory_write.

This file proves, directly against the real memory_write function and
a real (tmp-dir-isolated) filesystem:
  (a) a memory_write whose content contains an API key / JWT / bearer
      token / password gets that secret span redacted in what actually
      lands on disk -- not just in the returned dict.
  (b) ordinary memory content (prose, names, short numbers, dates)
      passes through byte-for-byte unchanged -- the fix must not
      over-redact.
  (c) the redaction applies on BOTH the "overwrite" and "append" write
      modes, and on append, only the NEW content is redacted -- any
      secret literally already sitting in the pre-existing file (out
      of scope for this fix; see server_modules/agent_memory_tools.py's
      docstring -- write path only) is left as-is by this call.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import agent_memory_tools, workspace_context


def _run(coro):
    return asyncio.run(coro)


class _IsolatedMemoryTestCase(unittest.TestCase):
    """Every test runs against a fresh tmp workspace dir -- never the real
    .orion-stack/ tree, and never shared between tests. Mirrors the
    isolation pattern used by test_memory_cross_agent_isolation.py."""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="memory-write-redaction-")
        root = Path(self._tempdir.name)
        self._patch = patch.object(workspace_context, "_WORKSPACE_DIR", root / "workspace")
        self._patch.start()
        self.addCleanup(self._tempdir.cleanup)
        self.addCleanup(self._patch.stop)

    def _written_text(self, *, agent_install_id: str, path: str) -> str:
        memory_dir = agent_memory_tools._agent_memory_dir(
            workspace_id="ws-1", agent_install_id=agent_install_id,
        )
        return (memory_dir / path).read_text(encoding="utf-8")


class MemoryWriteRedactsSecretsTests(_IsolatedMemoryTestCase):
    def test_api_key_is_redacted_before_it_touches_disk(self) -> None:
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="NOTE.md",
            content="Remember: the customer's OpenAI key is "
                    "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD, keep it handy.",
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="NOTE.md")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD", on_disk)
        self.assertIn("[redacted-secret]", on_disk)
        # The surrounding, non-secret prose must survive.
        self.assertIn("Remember: the customer's OpenAI key is", on_disk)
        self.assertIn("keep it handy", on_disk)

    def test_jwt_bearer_token_is_redacted_before_it_touches_disk(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="NOTE.md",
            content=f"Auth header used during the incident: Authorization: Bearer {jwt}",
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="NOTE.md")
        self.assertNotIn(jwt, on_disk)
        self.assertIn("[redacted]", on_disk)

    def test_password_in_key_value_form_is_redacted(self) -> None:
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="NOTE.md",
            content="Staging DB creds: user=admin password=Sup3rSecretPass!",
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="NOTE.md")
        self.assertNotIn("Sup3rSecretPass!", on_disk)
        self.assertIn("password=[redacted-secret]", on_disk)
        self.assertIn("user=admin", on_disk)

    def test_password_in_colon_form_is_redacted(self) -> None:
        """The 'pwd:' / 'password:' colon-form gap this fix also closed in
        secret_redaction_service (the pre-existing pattern only matched
        `password=...`, not `password: ...`)."""
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="NOTE.md",
            content="pwd: hunter2gomets",
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="NOTE.md")
        self.assertNotIn("hunter2gomets", on_disk)

    def test_card_number_like_sequence_is_redacted(self) -> None:
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="NOTE.md",
            content="Customer's card on file: 4111 1111 1111 1111, exp 12/29.",
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="NOTE.md")
        self.assertNotIn("4111 1111 1111 1111", on_disk)

    def test_redaction_applies_on_overwrite_mode_too(self) -> None:
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="NOTE.md", mode="overwrite",
            content="AWS key: AKIAIOSFODNN7EXAMPLE",
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="NOTE.md")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", on_disk)

    def test_append_mode_only_redacts_the_newly_written_content(self) -> None:
        """Append must redact the incoming chunk; it is not responsible for
        re-scrubbing bytes that were already on disk before this call
        (out of scope per the task: write-path-only fix, read path and
        pre-existing files are untouched)."""
        _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="NOTE.md", mode="overwrite",
            content="Existing line one.",
        ))
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="NOTE.md", mode="append",
            content="New secret: sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD",
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="NOTE.md")
        self.assertIn("Existing line one.", on_disk)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890ABCD", on_disk)
        self.assertIn("[redacted-secret]", on_disk)


class MemoryWritePreservesOrdinaryContentTests(_IsolatedMemoryTestCase):
    """The fix must not over-redact -- normal prose, names, short numbers,
    and dates must pass through byte-for-byte unchanged."""

    def test_ordinary_prose_is_unchanged(self) -> None:
        content = (
            "The user's name is Alice Chen. She prefers async standups on "
            "Tuesdays and mentioned she is based in Austin, Texas. Her "
            "favorite project is called Project Nightingale, launched in "
            "March 2026 with a team of 6 engineers."
        )
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="MEMORY.md", content=content,
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="MEMORY.md")
        self.assertEqual(on_disk, content + "\n")

    def test_short_numbers_and_dates_are_not_redacted(self) -> None:
        # Note: an ISO-style dashed date (e.g. "2026-07-14") collides with
        # the shared redactor's pre-existing digit-sequence "phone number"
        # heuristic and IS redacted -- a known, pre-existing over-redaction
        # edge case in secret_redaction_service (not introduced by this
        # fix, and shared by ~25 other call sites); see report notes.
        content = "Order #4521 shipped on July 14, 2026; quantity was 12 units."
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="MEMORY.md", content=content,
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="MEMORY.md")
        self.assertEqual(on_disk, content + "\n")

    def test_url_is_not_treated_as_a_secret(self) -> None:
        content = "Reference: https://docs.example.com/product/reference for API docs."
        result = _run(agent_memory_tools.memory_write(
            workspace_id="ws-1", agent_install_id="agent-a",
            path="MEMORY.md", content=content,
        ))
        self.assertTrue(result["ok"])
        on_disk = self._written_text(agent_install_id="agent-a", path="MEMORY.md")
        self.assertEqual(on_disk, content + "\n")


if __name__ == "__main__":
    unittest.main()
