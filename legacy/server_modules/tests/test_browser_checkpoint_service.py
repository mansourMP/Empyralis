import unittest

from server_modules import browser_checkpoint_service


class BrowserCheckpointServiceTests(unittest.TestCase):
    def test_attach_browser_checkpoint_updates_run_and_metadata(self):
        run = {"context": {"metadata": {}}}
        metadata = run["context"]["metadata"]

        payload = browser_checkpoint_service.attach_browser_checkpoint(
            run,
            {
                "next_action_index": 4,
                "session_profile": "qa-browser",
                "tabs": [{"id": 1}],
            },
            metadata=metadata,
        )

        self.assertEqual(payload["next_action_index"], 4)
        self.assertEqual(run["browser_checkpoint"]["session_profile"], "qa-browser")
        self.assertTrue(metadata["browser_resume_supported"])
        self.assertEqual(metadata["browser_checkpoint"]["tabs"][0]["id"], 1)

    def test_browser_introspection_snapshot_prefers_latest_browser_action(self):
        run = {
            "browser_checkpoint": {
                "tabs": [{"id": 1}],
                "role_snapshot": [{"role": "button"}],
                "accessibility_snapshot": {"page": "checkpoint"},
            }
        }
        result_data = {
            "outputs": {
                "actions": [
                    {
                        "tool": "browser_automation.interactive",
                        "tabs": [{"id": 2}],
                        "console_entries": [{"message": "warn"}],
                        "network_failures": [{"url": "https://example.com"}],
                        "role_snapshot": [{"role": "textbox"}],
                        "accessibility_snapshot": {"page": "live"},
                    }
                ]
            }
        }

        payload = browser_checkpoint_service.browser_introspection_snapshot(run, result_data)

        self.assertEqual(payload["tabs"][0]["id"], 2)
        self.assertEqual(payload["console_entries"][0]["message"], "warn")
        self.assertEqual(payload["role_snapshot"][0]["role"], "textbox")
        self.assertEqual(payload["accessibility_snapshot"]["page"], "live")

    def test_browser_resume_supported_true_for_checkpoint_even_without_metadata_flag(self):
        self.assertTrue(
            browser_checkpoint_service.browser_resume_supported(
                {"browser_checkpoint": {"next_action_index": 1}},
                {},
            )
        )


if __name__ == "__main__":
    unittest.main()
