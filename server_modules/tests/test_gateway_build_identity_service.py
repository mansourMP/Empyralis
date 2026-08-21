"""A self-update may only be advertised when its success could be OBSERVED.

The centrepiece here is test_man331_literal_fix_is_refused_because_it_would_
loop_forever. MAN-331 proposes setting EMPYRALIS_GATEWAY_LATEST_VERSION in
production; doing that literally, against the fleet as it exists today, makes
every box download and reinstall the same build on every status poll forever,
because nothing the update changes is anything the box reports back:

    GATEWAY_VERSION = "0.1.0"  hardcoded in index.ts, never bumped in the
                               entire repo history (`git log -S` -> 1 commit)
    publish channel = "latest" release-gateway-linux.yml publishes a push to
                               main under agent-computer/latest/

    0.1.0 --update--> 0.1.0 --update--> 0.1.0 --update--> ...

These tests pin the refusal, and they pin the DISTINCTION between refusals:
"nothing published" and "there is a newer build but installing it would loop"
send an operator to fix completely different things, so they may never
collapse into one flat False.
"""
from __future__ import annotations

import unittest

from server_modules import gateway_build_identity_service as identity
from server_modules import gateway_self_update_service


def _registration(**metadata) -> dict:
    return {"platform": "linux-x64", "metadata": dict(metadata)}


class GatewayUpdateAdvertisementPlanTests(unittest.TestCase):
    def test_man331_literal_fix_is_refused_because_it_would_loop_forever(self) -> None:
        """The exact production shape: a hand-set numeric 'latest' version,
        against a box whose build carries no fingerprint. The version
        comparison says 'newer' and is WRONG — the published build reports
        0.1.0 too, so this would re-fire on every poll, on every box."""
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint=None,
            version_is_newer=True,
        )
        self.assertFalse(plan["update_available"])
        self.assertEqual(
            plan["refusal_code"], identity.REFUSAL_UPDATE_WOULD_BE_UNOBSERVABLE
        )
        self.assertIn("could not be told apart", plan["reason"])

    def test_publish_channel_name_is_named_as_uncomparable_not_silently_ignored(self) -> None:
        """Setting the variable to the channel the workflow actually publishes
        under ('latest') is the other way an operator resolves MAN-331. It can
        never compare as newer, so today it reads as a working feature that
        happens to find no updates. It must say so instead."""
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="latest",
            current_fingerprint="aaaaaaaaaaaaaaaa",
            version_is_newer=False,
        )
        self.assertFalse(plan["update_available"])
        self.assertEqual(
            plan["refusal_code"], identity.REFUSAL_UNCOMPARABLE_PUBLISHED_VERSION
        )

    def test_nothing_published_is_a_different_fact_from_an_unobservable_update(self) -> None:
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version=None,
            current_fingerprint=None,
            version_is_newer=False,
        )
        self.assertFalse(plan["update_available"])
        self.assertEqual(plan["refusal_code"], identity.REFUSAL_NO_PUBLISHED_BUILD)
        self.assertNotEqual(
            plan["refusal_code"], identity.REFUSAL_UPDATE_WOULD_BE_UNOBSERVABLE
        )

    def test_loop_brake_refuses_a_build_that_already_installed_and_changed_nothing(self) -> None:
        """The after-the-fact catch. A box was updated, came back running
        byte-identical code, and the version still says 'newer'. Offering it
        again is the loop; this is where it stops."""
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint="deadbeefdeadbeef",
            last_update_fingerprint="deadbeefdeadbeef",
            version_is_newer=True,
        )
        self.assertFalse(plan["update_available"])
        self.assertEqual(
            plan["refusal_code"], identity.REFUSAL_PREVIOUS_UPDATE_CHANGED_NOTHING
        )

    def test_loop_brake_outranks_a_newer_published_fingerprint(self) -> None:
        """Ordering is load-bearing: even with a published fingerprint that
        differs, a box that demonstrably did not change last time must not be
        told to try the same thing again."""
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint="deadbeefdeadbeef",
            published_fingerprint="1111111111111111",
            last_update_fingerprint="deadbeefdeadbeef",
            version_is_newer=True,
        )
        self.assertFalse(plan["update_available"])
        self.assertEqual(
            plan["refusal_code"], identity.REFUSAL_PREVIOUS_UPDATE_CHANGED_NOTHING
        )

    def test_differing_fingerprints_advertise_an_update_even_when_versions_match(self) -> None:
        """The signal the version cannot carry: both sides call themselves
        0.1.0, and they are genuinely different builds. This is the 2026-08-18
        production incident, made detectable."""
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.1.0",
            current_fingerprint="0000000000000000",
            published_fingerprint="1111111111111111",
            version_is_newer=False,
        )
        self.assertTrue(plan["update_available"])
        self.assertIsNone(plan["refusal_code"])

    def test_matching_fingerprints_are_up_to_date_with_no_refusal(self) -> None:
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint="abcabcabcabcabca",
            published_fingerprint="abcabcabcabcabca",
            version_is_newer=True,
        )
        self.assertFalse(plan["update_available"])
        self.assertIsNone(plan["refusal_code"])

    def test_version_comparability_agrees_with_the_comparator_actually_used(self) -> None:
        """The expected set and the actual set come from different modules on
        purpose — is_comparable_version() lives here, is_newer_gateway_version()
        lives in the dispatch module, and a drift between them would mean this
        layer refuses things the comparator would have accepted (or worse, the
        reverse). Asserted rather than trusted."""
        for candidate in ("latest", "", "stable", "v", "main"):
            self.assertFalse(
                identity.is_comparable_version(candidate),
                f"{candidate!r} should be uncomparable",
            )
            self.assertFalse(
                gateway_self_update_service.is_newer_gateway_version("0.1.0", candidate),
                f"{candidate!r} must never compare as newer",
            )
        for candidate in ("0.2.0", "1.0", "v2.3.4"):
            self.assertTrue(identity.is_comparable_version(candidate))


class GatewayUpdateStatusLivePathTests(unittest.TestCase):
    """'Code exists' is not 'reachable on the live path' — these drive
    gateway_update_status(), the function the Hardware page's gateway list
    actually calls, not the planner underneath it."""

    def setUp(self) -> None:
        self._saved = {}
        for key in (
            "EMPYRALIS_GATEWAY_LATEST_VERSION",
            "EMPYRALIS_GATEWAY_LATEST_BUILD_FINGERPRINT",
        ):
            self._saved[key] = __import__("os").environ.pop(key, None)

    def tearDown(self) -> None:
        import os

        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_status_refuses_the_man331_configuration_on_the_live_path(self) -> None:
        import os

        os.environ["EMPYRALIS_GATEWAY_LATEST_VERSION"] = "0.2.0"
        status = gateway_self_update_service.gateway_update_status(
            _registration(gateway_version="0.1.0")
        )
        self.assertFalse(status["gateway_update_available"])
        self.assertEqual(
            status["gateway_update_refusal_code"],
            identity.REFUSAL_UPDATE_WOULD_BE_UNOBSERVABLE,
        )
        # A refused update must not hand out an artifact URL to install.
        self.assertIsNone(status["latest_gateway_artifact_url"])

    def test_status_surfaces_the_fingerprint_the_box_reported(self) -> None:
        status = gateway_self_update_service.gateway_update_status(
            _registration(gateway_version="0.1.0", gateway_build_fingerprint="feedfacefeedface")
        )
        self.assertEqual(status["gateway_build_fingerprint"], "feedfacefeedface")

    def test_status_reports_a_drifted_build_when_the_published_fingerprint_differs(self) -> None:
        import os

        os.environ["EMPYRALIS_GATEWAY_LATEST_BUILD_FINGERPRINT"] = "1111111111111111"
        status = gateway_self_update_service.gateway_update_status(
            _registration(gateway_version="0.1.0", gateway_build_fingerprint="0000000000000000")
        )
        self.assertTrue(status["gateway_update_available"])
        self.assertIsNone(status["gateway_update_refusal_code"])


class DesktopAppManagedGatewayTests(unittest.TestCase):
    """A gateway that ships inside Empyralis.app is updated BY THE APP.

    Advertising a gateway self-update to one of those boxes is MAN-331 in its
    purest form: the update would download, install into
    <stateDir>/../gateway-releases, report success — and the next start would
    run the same build out of the .app bundle, forever, because both launch
    paths point into the bundle.
    """

    def test_a_desktop_box_is_refused_and_told_why(self) -> None:
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint="aaaa",
            published_fingerprint="bbbb",
            version_is_newer=True,
            desktop_managed=True,
        )
        self.assertFalse(plan["update_available"])
        self.assertEqual(
            plan["refusal_code"], identity.REFUSAL_DESKTOP_APP_MANAGES_GATEWAY
        )
        # The reason is what a customer reads on the Hardware page, so it has
        # to name the real update path rather than a mechanism.
        self.assertIn("Empyralis app", plan["reason"])
        for mechanism in ("launchctl", "systemctl", "ExecStart", "symlink", "tarball"):
            self.assertNotIn(mechanism, plan["reason"])

    def test_it_outranks_the_launch_path_refusal(self) -> None:
        """ORDER, and it is the whole reason this code exists.

        A desktop box's login item deliberately starts the gateway from inside
        the .app, so gateway-launch-updatability.ts reports "not_updatable" —
        a true answer to the wrong question. Answering with
        LAUNCH_PATH_NOT_UPDATABLE would put a block of launchctl commands in
        front of someone whose machine is working perfectly.
        """
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint="aaaa",
            published_fingerprint="bbbb",
            version_is_newer=True,
            launch_updatability="not_updatable",
            desktop_managed=True,
        )
        self.assertEqual(
            plan["refusal_code"], identity.REFUSAL_DESKTOP_APP_MANAGES_GATEWAY
        )

    def test_every_existing_box_is_unaffected(self) -> None:
        """The blast radius is exactly the boxes that set the marker.

        `desktop_managed` defaults to False and no VPS gateway reports it, so
        every existing box plans byte-identically to before this parameter
        existed.
        """
        common = dict(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint="aaaa",
            published_fingerprint="bbbb",
            version_is_newer=True,
        )
        self.assertEqual(
            identity.plan_gateway_update_advertisement(**common),
            identity.plan_gateway_update_advertisement(**common, desktop_managed=False),
        )
        self.assertTrue(
            identity.plan_gateway_update_advertisement(**common)["update_available"]
        )

    def test_absent_metadata_is_never_read_as_desktop_managed(self) -> None:
        """Absent must mean False here, not unknown.

        Every box in the fleet predates this field, and reading a missing
        value as desktop-managed would silently stop gateway updates for all
        of them at once.
        """
        self.assertFalse(identity.is_desktop_managed({}))
        self.assertFalse(identity.is_desktop_managed({"metadata": {}}))
        self.assertFalse(identity.is_desktop_managed({"metadata": None}))
        # Only a real boolean true counts — the connect handler coerces it, so
        # a stray string must not be promoted here either.
        self.assertFalse(
            identity.is_desktop_managed({"metadata": {"gateway_desktop_managed": "1"}})
        )
        self.assertTrue(
            identity.is_desktop_managed({"metadata": {"gateway_desktop_managed": True}})
        )

    def test_the_live_path_reads_it_from_the_registration(self) -> None:
        """Reachability, not behaviour: gateway_update_status() is what the
        Hardware page calls, and a planner nothing passes this to would be
        the 'built, tested, and never wired' shape one level up."""
        status = gateway_self_update_service.gateway_update_status(
            {
                "platform": "darwin-aarch64",
                "metadata": {
                    "gateway_version": "0.1.0",
                    "gateway_build_fingerprint": "aaaa",
                    "gateway_desktop_managed": True,
                },
            }
        )
        self.assertFalse(status["gateway_update_available"])
        self.assertEqual(
            status["gateway_update_refusal_code"],
            identity.REFUSAL_DESKTOP_APP_MANAGES_GATEWAY,
        )
        self.assertIsNone(status["latest_gateway_artifact_url"])


if __name__ == "__main__":
    unittest.main()
