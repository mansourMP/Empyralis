from __future__ import annotations

import unittest

from server_modules import gateway_reason_messages


class GatewayReasonMessagesTests(unittest.TestCase):
    def test_docker_gated_capability_missing_names_docker_specifically(self) -> None:
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing", capability_id="shell.execute",
        )
        self.assertEqual(message, "Docker isn't running on this machine. Start Docker Desktop, then retry.")

        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing", capability_id="filesystem.read_write",
        )
        self.assertIn("Docker", message)

    def test_capability_missing_distinguishes_screen_recording_from_accessibility(self) -> None:
        screen_message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing", capability_id="screenshot.capture",
        )
        accessibility_message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing", capability_id="mouse.click",
        )
        self.assertIn("Screen Recording", screen_message)
        self.assertIn("Accessibility", accessibility_message)
        self.assertNotEqual(screen_message, accessibility_message)

    def test_capability_missing_falls_back_to_naming_the_unknown_capability(self) -> None:
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing", capability_id="some.future.capability",
        )
        self.assertIn("some.future.capability", message)
        self.assertIn("Reconnect", message)

    def test_capability_missing_without_capability_id_still_actionable(self) -> None:
        message = gateway_reason_messages.gateway_reason_message("gateway_capability_missing")
        self.assertTrue(message)
        self.assertIn("Reconnect", message)

    def test_capability_not_ready_names_docker_starting_up(self) -> None:
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_not_ready", capability_id="shell.execute",
        )
        self.assertIn("Docker", message)
        self.assertIn("starting up", message)

    def test_every_documented_reason_token_gets_a_distinct_actionable_message(self) -> None:
        # The exact token list called out in MAN-295's brief — every one of
        # these must resolve to real guidance, and no two of them may
        # collapse back into the same generic sentence (that collapse is
        # the bug this module fixes).
        tokens = [
            "gateway_capability_missing",
            "gateway_registration_missing",
            "gateway_registration_inactive",
            "gateway_device_revoked",
            "gateway_workspace_mismatch",
            "gateway_offline",
            "gateway_heartbeat_stale",
            "gateway_unhealthy",
            "gateway_capability_not_ready",
        ]
        messages = {
            token: gateway_reason_messages.gateway_reason_message(token, capability_id="shell.execute")
            for token in tokens
        }
        for token, message in messages.items():
            with self.subTest(token=token):
                self.assertTrue(message, f"{token} produced an empty message")
                self.assertNotEqual(
                    message,
                    gateway_reason_messages._FALLBACK_MESSAGE,
                    f"{token} silently fell back to the generic message",
                )
        # Distinct tokens must not all collapse onto identical text — that's
        # the exact "Gateway is not ready for this hardware action." bug.
        self.assertEqual(len(set(messages.values())), len(messages))

    def test_registration_level_reasons_mention_the_hardware_page(self) -> None:
        for token in (
            "gateway_registration_missing",
            "gateway_registration_inactive",
            "gateway_device_revoked",
        ):
            with self.subTest(token=token):
                message = gateway_reason_messages.gateway_reason_message(token)
                self.assertIn("Hardware page", message)

    def test_workspace_mismatch_names_the_actual_conflict(self) -> None:
        message = gateway_reason_messages.gateway_reason_message("gateway_workspace_mismatch")
        self.assertIn("different workspace", message)

    def test_offline_and_heartbeat_stale_are_distinct(self) -> None:
        offline = gateway_reason_messages.gateway_reason_message("gateway_offline")
        stale = gateway_reason_messages.gateway_reason_message("gateway_heartbeat_stale")
        self.assertNotEqual(offline, stale)
        self.assertIn("powered on", offline)
        self.assertIn("checked in", stale)

    def test_unknown_reason_token_degrades_safely_to_generic_fallback(self) -> None:
        message = gateway_reason_messages.gateway_reason_message("some_future_reason_token_nobody_taught_this_module")
        self.assertEqual(message, gateway_reason_messages._FALLBACK_MESSAGE)
        # Never leaks the raw token verbatim into the "safe fallback" text.
        self.assertNotIn("some_future_reason_token", message)

    def test_empty_or_none_reason_degrades_safely(self) -> None:
        self.assertEqual(gateway_reason_messages.gateway_reason_message(None), gateway_reason_messages._FALLBACK_MESSAGE)
        self.assertEqual(gateway_reason_messages.gateway_reason_message(""), gateway_reason_messages._FALLBACK_MESSAGE)
        self.assertEqual(gateway_reason_messages.gateway_reason_message("   "), gateway_reason_messages._FALLBACK_MESSAGE)

    def test_never_raises_on_garbage_input(self) -> None:
        # capability_id of a non-string type must not blow up string
        # comparisons/formatting inside the capability-aware builders.
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing", capability_id=12345,
        )
        self.assertTrue(message)
        message = gateway_reason_messages.gateway_reason_message(123, capability_id=None)
        self.assertTrue(message)


if __name__ == "__main__":
    unittest.main()
