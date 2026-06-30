import tempfile
import unittest
from pathlib import Path

from server_modules import direct_chat_metadata_service


class DirectChatMetadataServiceTests(unittest.TestCase):
    def test_build_context_used_tracks_overrides_and_fallback(self) -> None:
        payload = direct_chat_metadata_service.build_context_used(
            workspace_id="default",
            requested_provider="openai",
            effective_provider="gemini",
            requested_model="gpt-5.4",
            effective_model="gemini-2.5-pro",
            reasoning_effort="medium",
            connected_systems=["Google Workspace"],
            tool_capabilities=[{"id": "google_workspace"}],
            prior_messages_used=True,
            history_mode="raw_messages",
            run_created=False,
            fallback_used=True,
            fallback_reason="provider_unavailable",
        )

        self.assertTrue(payload["provider_overridden"])
        self.assertTrue(payload["model_overridden"])
        self.assertEqual(payload["fallback_reason"], "provider_unavailable")

    def test_with_context_used_attaches_context_payload(self) -> None:
        payload = direct_chat_metadata_service.with_context_used(
            {"reply": "hello"},
            {"workspace": "default"},
        )

        self.assertEqual(payload["context_used"]["workspace"], "default")

    def test_heartbeat_pending_tasks_reads_heartbeat_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            heartbeat_path = Path(tmpdir) / "HEARTBEAT.md"
            heartbeat_path.write_text("- [ ] first\n- [ ] second\n- [ ] third\n- [ ] fourth\n", encoding="utf-8")

            tasks = direct_chat_metadata_service.heartbeat_pending_tasks_for_suggestions(
                workspace_context_dir_fn=lambda: Path(tmpdir),
                parse_unchecked_heartbeat_tasks_fn=lambda text: [
                    line.split("] ", 1)[1].strip()
                    for line in text.splitlines()
                    if line.startswith("- [ ] ")
                ],
            )

        self.assertEqual(tasks, ["first", "second", "third"])

    def test_recent_run_prompts_filters_workspace_and_dedupes(self) -> None:
        prompts = direct_chat_metadata_service.recent_run_prompts_for_suggestions(
            "default",
            run_history=[
                {"workspace_id": "default", "user_goal": "  review the current runtime plan  "},
                {"workspace_id": "default", "user_goal": "review the current runtime plan"},
                {"workspace_id": "other", "user_goal": "ignore me"},
                {"workspace_id": "default", "user_goal": "ship the next refactor cut"},
            ],
        )

        self.assertEqual(prompts, ["review the current runtime plan", "ship the next refactor cut"])


if __name__ == "__main__":
    unittest.main()
