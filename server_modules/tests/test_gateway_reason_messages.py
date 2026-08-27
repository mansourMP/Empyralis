from __future__ import annotations

import unittest

from server_modules import gateway_reason_messages


class GatewayReasonMessagesTests(unittest.TestCase):
    # CORRECTED CONTRACT — the assertion this test used to make
    # (gateway_capability_missing for shell.execute ALWAYS names Docker,
    # unconditionally) encoded a real, live-proven bug and must never be
    # restored. Production: agent "Vale" was placed on gateway
    # gateway_a1c6b043 ("Mansur's Mac"), active, verified, heartbeating,
    # advertising shell.execute among 42 capabilities, with its OWN
    # reported metadata.capability_readiness.service_statuses.docker ==
    # "ready" — and this function still told the founder, across several
    # days, to go start Docker Desktop. It could never have helped, because
    # Docker was never the problem: gateway_capability_missing means the
    # capability was never REQUESTED at all (see gateway_execution_service.
    # _has_gateway_capability), a cause entirely independent of Docker's
    # own readiness. A confidently wrong, specific instruction is worse
    # than an honest "I don't know exactly why" — CLAUDE.md's outcome-
    # honesty law, applied to a diagnostic message rather than a mutation
    # result. The corrected contract: the Docker sentence is EVIDENCE-
    # GATED on the gateway's own reported service_statuses, never guessed
    # from the capability id alone.
    def test_capability_missing_without_evidence_never_blames_docker(self) -> None:
        # No service_statuses passed at all — the common case for most
        # callers today. Must NOT claim Docker is the cause; must still be
        # actionable.
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing", capability_id="shell.execute",
        )
        self.assertNotIn("Docker", message)
        self.assertNotIn("Start Docker Desktop", message)
        self.assertTrue(message)

    def test_capability_missing_with_docker_reported_ready_never_blames_docker(self) -> None:
        # The exact live-proven false-positive: the gateway's own most
        # recent heartbeat says Docker IS ready. The message must not
        # contradict evidence the platform itself already has.
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing",
            capability_id="shell.execute",
            service_statuses={"docker": "ready"},
        )
        self.assertNotIn("Docker", message)
        self.assertNotIn("Start Docker Desktop", message)

    def test_capability_missing_with_docker_confirmed_offline_names_it_specifically(self) -> None:
        # The positive case: when the gateway's own reported status
        # actually confirms Docker is the cause, the specific, actionable
        # sentence is correct and should still be used. Platform evidence
        # supplied (macOS), matching how the real call sites pass it —
        # see the platform-awareness tests below for what happens without it.
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing",
            capability_id="shell.execute",
            service_statuses={"docker": "offline"},
            platform="darwin",
        )
        self.assertEqual(message, "Docker isn't running on this machine. Start Docker Desktop, then retry.")

        for capability_id in ("shell.execute", "filesystem.read_write"):
            with self.subTest(capability_id=capability_id):
                message = gateway_reason_messages.gateway_reason_message(
                    "gateway_capability_missing",
                    capability_id=capability_id,
                    service_statuses={"docker": "missing"},
                    platform="darwin",
                )
                self.assertIn("Docker", message)

    def test_capability_missing_docker_message_is_platform_aware(self) -> None:
        """A Linux VPS owner cannot follow "Start Docker Desktop" — Docker
        Desktop is a macOS/Windows app and does not exist on Linux. The
        founder's own report: "Production Gateway", a Linux VPS, told to
        start an app that isn't there. Reported live in Configure ▸ Hardware
        / relayed verbatim to the person by an agent over Telegram."""
        cases = {
            "darwin": "Docker isn't running on this machine. Start Docker Desktop, then retry.",
            "macos": "Docker isn't running on this machine. Start Docker Desktop, then retry.",
            "win32": "Docker isn't running on this machine. Start Docker Desktop, then retry.",
            "windows": "Docker isn't running on this machine. Start Docker Desktop, then retry.",
            "linux": (
                "Docker isn't running on this machine. Start the Docker service "
                "(for example, `sudo systemctl start docker`), then retry."
            ),
        }
        for platform, expected in cases.items():
            with self.subTest(platform=platform):
                message = gateway_reason_messages.gateway_reason_message(
                    "gateway_capability_missing",
                    capability_id="shell.execute",
                    service_statuses={"docker": "offline"},
                    platform=platform,
                )
                self.assertEqual(message, expected)
                # Never named on Linux — that's the whole defect.
                if platform == "linux":
                    self.assertNotIn("Desktop", message)

    def test_capability_missing_docker_message_with_no_platform_evidence_names_no_app(self) -> None:
        """Omitting platform is not the same mistake as guessing wrong —
        it degrades to the honest, platform-neutral sentence rather than
        asserting "Desktop" on a box that might be a headless Linux VPS."""
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing",
            capability_id="shell.execute",
            service_statuses={"docker": "offline"},
        )
        self.assertEqual(message, "Docker isn't running on this machine. Start Docker, then retry.")
        self.assertNotIn("Desktop", message)

    def test_capability_not_ready_docker_message_names_no_app_on_any_platform(self) -> None:
        """The "still starting up, wait" message never names an app at all
        — platform evidence has nothing to change here, on any platform."""
        for platform in ("darwin", "linux", "win32", None):
            with self.subTest(platform=platform):
                message = gateway_reason_messages.gateway_reason_message(
                    "gateway_capability_not_ready",
                    capability_id="shell.execute",
                    # "degraded" is a real _DOCKER_CONFIRMED_NOT_READY_STATUSES
                    # member — this drives the Docker-specific "starting up"
                    # branch, not the generic capability-not-ready fallback.
                    service_statuses={"docker": "degraded"},
                    platform=platform,
                )
                self.assertIn("Wait a moment", message)
                self.assertIn("Docker is starting up", message)
                self.assertNotIn("Desktop", message)

    def test_capability_missing_with_docker_status_unknown_never_blames_docker(self) -> None:
        # "unknown" is not evidence of anything — the gateway hasn't
        # reported a usable status for this service. Treated the same as
        # no evidence at all, never as a positive signal either way.
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_missing",
            capability_id="shell.execute",
            service_statuses={"docker": "unknown"},
        )
        self.assertNotIn("Docker", message)

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

    def test_capability_not_ready_without_evidence_never_blames_docker(self) -> None:
        # Same evidence discipline as gateway_capability_missing above.
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_not_ready", capability_id="shell.execute",
        )
        self.assertNotIn("Docker", message)
        self.assertTrue(message)

    def test_capability_not_ready_with_docker_confirmed_starting_names_it(self) -> None:
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_not_ready",
            capability_id="shell.execute",
            service_statuses={"docker": "degraded"},
        )
        self.assertIn("Docker", message)
        self.assertIn("starting up", message)

    def test_capability_not_ready_with_docker_reported_ready_never_blames_docker(self) -> None:
        message = gateway_reason_messages.gateway_reason_message(
            "gateway_capability_not_ready",
            capability_id="shell.execute",
            service_statuses={"docker": "ready"},
        )
        self.assertNotIn("Docker", message)

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
