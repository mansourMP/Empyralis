import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import sage_profile_service


class SageProfileServiceTests(unittest.TestCase):
    def test_bootstrap_answers_progress_and_sync_workspace_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-1"
            with patch("server_modules.sage_profile_service.workspace_context.workspace_scope_dir", return_value=root):
                payload = sage_profile_service.answer_sage_profile_bootstrap(
                    workspace_id="workspace-1",
                    answer="Mansur",
                    actor_user_id="user-1",
                )
                self.assertEqual(payload["profile"]["user_name"], "Mansur")
                self.assertFalse(payload["bootstrap"]["complete"])
                self.assertEqual(payload["bootstrap"]["current_question"]["id"], "identity_summary")

                for answer in (
                    "I lead product and engineering for Empyralis.",
                    "Be direct, concise, and start with the answer.",
                    "Keep my inbox triaged every morning.",
                    "Never send external messages without approval.",
                ):
                    payload = sage_profile_service.answer_sage_profile_bootstrap(
                        workspace_id="workspace-1",
                        answer=answer,
                        actor_user_id="user-1",
                    )

                self.assertTrue(payload["bootstrap"]["complete"])
                self.assertEqual(payload["profile"]["standing_rules"], ["Never send external messages without approval."])
                self.assertTrue((root / "sage_profile.json").exists())
                self.assertIn("Preferred name: Mansur", (root / "USER.md").read_text(encoding="utf-8"))
                self.assertIn("lead product and engineering", (root / "IDENTITY.md").read_text(encoding="utf-8"))
                self.assertIn("Be direct, concise", (root / "SOUL.md").read_text(encoding="utf-8"))
                self.assertIn("Keep my inbox triaged every morning.", (root / "HEARTBEAT.md").read_text(encoding="utf-8"))

    def test_upsert_profile_normalizes_standing_rules_text(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workspace-2"
            with patch("server_modules.sage_profile_service.workspace_context.workspace_scope_dir", return_value=root):
                payload = sage_profile_service.upsert_sage_profile(
                    workspace_id="workspace-2",
                    actor_user_id="user-2",
                    user_name="Owner",
                    identity_summary="Runs the platform.",
                    communication_style="Keep it short.",
                    recurring_responsibility="Check launch blockers daily.",
                    standing_rules_text="- Never send customer emails without approval.\n- Keep secrets out of replies.",
                )

                self.assertEqual(
                    payload["profile"]["standing_rules"],
                    [
                        "Never send customer emails without approval.",
                        "Keep secrets out of replies.",
                    ],
                )
                self.assertTrue(payload["bootstrap"]["complete"])


if __name__ == "__main__":
    unittest.main()
