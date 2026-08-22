"""Tests for attachment_utils.py's TurnAttachment-shaped attachment context.

Regression coverage for a real crash: build_attachment_context (and
preprocess_image_attachments) used to tolerate BOTH a bare dict and a
TurnAttachment dataclass via getattr()/isinstance() probing (complete with
leftover debug print() calls — evidence the ambiguity had been hit and
worked around before, rather than fixed). A bare dict reaching
build_attachment_context raised AttributeError the moment it touched
att.metadata (a plain dict has no such attribute) — the same class of bug
_load_attachment_context had in agent_turn_runtime_service.py. The fix
settles the contract: both functions now require List[TurnAttachment]
unambiguously; conversion happens once, at the caller boundary
(direct_chat_generation_service.py's resolve_agent_turn_request call — see
its own test coverage in test_direct_chat_generation_service.py).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import attachment_utils
from server_modules.agent_turn import TurnAttachment


def _run(coro):
    return asyncio.run(coro)


class BuildAttachmentContextTests(unittest.TestCase):
    def test_empty_attachments_returns_empty_string(self):
        self.assertEqual(attachment_utils.build_attachment_context("ws-1", []), "")

    def test_text_attachment_content_is_inlined(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            attachments_dir = Path(tmp_dir)
            (attachments_dir / "safe-notes.txt").write_text("the file says hello", encoding="utf-8")

            with patch(
                "server_modules.workspace_context.workspace_attachments_dir",
                return_value=attachments_dir,
            ):
                context = attachment_utils.build_attachment_context(
                    "ws-1",
                    [
                        TurnAttachment(
                            kind="file",
                            uri="https://files.example.com/safe-notes.txt",
                            name="notes.txt",
                            metadata={
                                "safe_filename": "safe-notes.txt",
                                "content_type": "text/plain",
                                "size": 20,
                            },
                        )
                    ],
                )

        self.assertIn("--- notes.txt ---", context)
        self.assertIn("the file says hello", context)
        self.assertIn("[Attached Files]", context)

    def test_image_attachment_uses_pre_resolved_description(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "server_modules.workspace_context.workspace_attachments_dir",
                return_value=Path(tmp_dir),
            ):
                context = attachment_utils.build_attachment_context(
                    "ws-1",
                    [
                        TurnAttachment(
                            kind="file",
                            uri="https://files.example.com/photo.png",
                            name="photo.png",
                            metadata={
                                "safe_filename": "safe-photo.png",
                                "content_type": "image/png",
                                "description": "A red bicycle leaning on a wall.",
                            },
                        )
                    ],
                )

        self.assertIn("[Image description]: A red bicycle leaning on a wall.", context)

    def test_unsupported_file_type_gets_a_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch(
                "server_modules.workspace_context.workspace_attachments_dir",
                return_value=Path(tmp_dir),
            ):
                context = attachment_utils.build_attachment_context(
                    "ws-1",
                    [
                        TurnAttachment(
                            kind="file",
                            uri="https://files.example.com/archive.zip",
                            name="archive.zip",
                            metadata={"safe_filename": "archive.zip", "content_type": "application/zip", "size": 1024},
                        )
                    ],
                )

        self.assertIn("not supported", context)

    def test_a_bare_dict_raises_instead_of_being_silently_tolerated(self):
        # Documents the settled contract: build_attachment_context is not
        # dual-shape tolerant. A bare dict here is exactly the shape that
        # used to reach it (from a hand-rolled or serialized session_ctx)
        # and silently corrupt error reporting via getattr()/isinstance()
        # guessing. The fix moved the conversion to the caller boundary, not
        # into this function's tolerance.
        with self.assertRaises(AttributeError):
            attachment_utils.build_attachment_context(
                "ws-1",
                [{"filename": "notes.txt", "safe_filename": "safe-notes.txt", "content_type": "text/plain"}],
            )


class PreprocessImageAttachmentsTests(unittest.TestCase):
    def test_noop_without_vision_api_key(self):
        attachment = TurnAttachment(
            kind="file",
            uri="https://files.example.com/photo.png",
            name="photo.png",
            metadata={"safe_filename": "safe-photo.png", "content_type": "image/png"},
        )
        with patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("EMPYRALIS_VISION_API_KEY", None)
            _run(attachment_utils.preprocess_image_attachments("ws-1", [attachment]))

        self.assertNotIn("description", attachment.metadata)

    def test_skips_non_image_attachments(self):
        attachment = TurnAttachment(
            kind="file",
            uri="https://files.example.com/notes.txt",
            name="notes.txt",
            metadata={"safe_filename": "safe-notes.txt", "content_type": "text/plain"},
        )
        with patch.dict("os.environ", {"EMPYRALIS_VISION_API_KEY": "test-key"}):
            _run(attachment_utils.preprocess_image_attachments("ws-1", [attachment]))

        self.assertNotIn("description", attachment.metadata)


if __name__ == "__main__":
    unittest.main()
