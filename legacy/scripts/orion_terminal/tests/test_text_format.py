from __future__ import annotations

import unittest

from scripts.orion_terminal.text_format import compact_summary_line, mask_credential_label, preview_text


class TextFormatTests(unittest.TestCase):
    def test_preview_text_truncates(self) -> None:
        self.assertEqual(preview_text("abcdef", 4), "abc…")

    def test_compact_summary_keyword_match(self) -> None:
        line = compact_summary_line("Execution Plan 1) Ordered plan ...")
        self.assertEqual(line, "Execution plan generated.")

    def test_mask_credential_label_hides_sensitive(self) -> None:
        label = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
        masked = mask_credential_label(label)
        self.assertTrue(masked.startswith("sk-pro"))
        self.assertIn("...", masked)


if __name__ == "__main__":
    unittest.main()

