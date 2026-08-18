"""A box that cannot receive an update must say so, and say what fixes it.

MAN-355's forward half (`d3d42ce11`) only reaches a box nothing supervises
yet: `SUPERVISOR_PRESENCE_CHECK.detect` short-circuits the moment a
systemd/launchd env hint is present, so an ALREADY-supervised box carrying a
mis-pinned unit never runs the writer that was fixed. Production is exactly
that box, and it cannot repair itself — measured read-only on 165.227.25.201,
not assumed:

    Uid 995 (empyralis-gw)   /etc/systemd/system is root:root 0755
                             `test -w` as that user: NOT WRITABLE
    ProtectSystem=strict     `/` is mounted `ro` inside the unit's own mount
                             namespace, so even root inside it cannot write
    NoNewPrivileges=true     no setuid, no sudo, no escalation

So the product's obligation is the honest one: refuse the update, name the
fact, and hand over the one line a human has to apply. These tests pin all
three, plus the direction every unknown has to fall in — a wrong "unknown"
costs a signal, a wrong refusal takes updates away from a healthy box, and
every box in the fleet reports nothing here until it is rebuilt.
"""
from __future__ import annotations

import unittest

from server_modules import gateway_build_identity_service as identity


def _launch_metadata(**overrides) -> dict:
    report = {
        "status": "not_updatable",
        "supervisor": "systemd",
        "unitPath": "/etc/systemd/system/empyralis-gateway-channels.service",
        "launchCommand": "/usr/bin/node /opt/empyralis-app/empyralis-gateway/dist/index.js",
        "expectedEntrypoint": "/var/lib/empyralis-gw/gateway-releases/current/gateway/dist/index.js",
        "blockers": [
            {"code": "launch_path_outside_release_layout", "detail": "starts from a fixed place"},
            {"code": "supervisor_will_not_restart_on_clean_exit", "detail": "would stay off"},
        ],
        "unknownReason": None,
        "repair": {
            "launcherPath": "/var/lib/empyralis-gw/state/launch/run-gateway",
            "execStartLine": "ExecStart=/var/lib/empyralis-gw/state/launch/run-gateway",
            "verified": True,
            "failureReason": None,
        },
    }
    report.update(overrides)
    return report


def _registration(**metadata) -> dict:
    return {"platform": "linux-x64", "metadata": dict(metadata)}


class LaunchPathRefusalTests(unittest.TestCase):
    def test_a_box_that_cannot_receive_an_update_is_refused_with_its_own_code(self) -> None:
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint="aaaaaaaaaaaaaaaa",
            published_fingerprint="bbbbbbbbbbbbbbbb",
            version_is_newer=True,
            launch_updatability="not_updatable",
        )
        self.assertFalse(plan["update_available"])
        self.assertEqual(plan["refusal_code"], identity.REFUSAL_LAUNCH_PATH_NOT_UPDATABLE)

    def test_the_launch_refusal_outranks_every_other_refusal_including_the_loop_brake(self) -> None:
        """`previous_update_changed_nothing` is what a mis-pinned box eventually
        reports — it is the SYMPTOM. Naming it first sends an operator to stare
        at build fingerprints while the fix is one line in a service file."""
        plan = identity.plan_gateway_update_advertisement(
            current_version="0.1.0",
            latest_version="0.2.0",
            current_fingerprint="aaaaaaaaaaaaaaaa",
            last_update_fingerprint="aaaaaaaaaaaaaaaa",
            version_is_newer=True,
            launch_updatability="not_updatable",
        )
        self.assertEqual(plan["refusal_code"], identity.REFUSAL_LAUNCH_PATH_NOT_UPDATABLE)

    def test_every_not_not_updatable_value_leaves_todays_behaviour_exactly_as_it_was(self) -> None:
        """The whole fleet reports nothing here until it is rebuilt. Refusing on
        an absent, unknown or unrecognised value would take updates away from
        every healthy box at once — a far worse bug than the one being fixed."""
        for value in (None, "", "unknown", "updatable", "something_new"):
            with self.subTest(launch_updatability=value):
                plan = identity.plan_gateway_update_advertisement(
                    current_version="0.1.0",
                    latest_version="0.2.0",
                    current_fingerprint="aaaaaaaaaaaaaaaa",
                    published_fingerprint="bbbbbbbbbbbbbbbb",
                    version_is_newer=True,
                    launch_updatability=value,
                )
                self.assertTrue(plan["update_available"])
                self.assertIsNone(plan["refusal_code"])

    def test_the_refusal_reason_names_the_fact_and_never_reuses_another_reason(self) -> None:
        reason = identity._REFUSAL_REASONS[identity.REFUSAL_LAUNCH_PATH_NOT_UPDATABLE]
        self.assertIn("fixed location", reason)
        others = [
            text
            for code, text in identity._REFUSAL_REASONS.items()
            if code != identity.REFUSAL_LAUNCH_PATH_NOT_UPDATABLE
        ]
        self.assertNotIn(reason, others)


class LaunchReportReadingTests(unittest.TestCase):
    def test_a_gateway_too_old_to_report_reads_as_unknown_not_as_a_problem(self) -> None:
        report = identity.launch_report(_registration(gateway_version="0.1.0"))
        self.assertEqual(report["status"], identity.LAUNCH_STATUS_UNKNOWN)
        self.assertFalse(report["reported"])
        self.assertIsNone(identity.plan_gateway_launch_repair(report))

    def test_a_junk_payload_cannot_produce_a_refusal(self) -> None:
        for junk in ("not a dict", 7, [], {"status": 12}):
            with self.subTest(junk=junk):
                report = identity.launch_report(
                    _registration(gateway_launch_updatability=junk)
                )
                self.assertNotEqual(report["status"], identity.LAUNCH_STATUS_NOT_UPDATABLE)

    def test_the_report_carries_the_verbatim_command_an_operator_will_recognise(self) -> None:
        report = identity.launch_report(
            _registration(gateway_launch_updatability=_launch_metadata())
        )
        self.assertEqual(report["status"], identity.LAUNCH_STATUS_NOT_UPDATABLE)
        self.assertEqual(
            report["launch_command"],
            "/usr/bin/node /opt/empyralis-app/empyralis-gateway/dist/index.js",
        )
        self.assertEqual(
            report["unit_path"], "/etc/systemd/system/empyralis-gateway-channels.service"
        )
        self.assertEqual([b["code"] for b in report["blockers"]].count("launch_path_outside_release_layout"), 1)


class LaunchRepairPlanTests(unittest.TestCase):
    def test_a_ready_repair_is_a_drop_in_that_fixes_BOTH_blockers_in_one_edit(self) -> None:
        plan = identity.plan_gateway_launch_repair(
            identity.launch_report(_registration(gateway_launch_updatability=_launch_metadata()))
        )
        assert plan is not None
        self.assertEqual(plan["state"], "ready")
        joined = "\n".join(plan["commands"])
        # A drop-in, never a sed over the shipped unit: reverting is deleting
        # one file, and `ExecStart=` reset works whether the unit declares one
        # ExecStart or several.
        self.assertIn(
            "/etc/systemd/system/empyralis-gateway-channels.service.d", joined
        )
        self.assertIn("ExecStart=\n", joined)
        self.assertIn("ExecStart=/var/lib/empyralis-gw/state/launch/run-gateway", joined)
        # Restart=always is ADDED, not substituted — production's unit says
        # `on-failure`, under which the clean exit an update ends with would
        # leave the box switched off rather than starting the new build.
        self.assertIn("Restart=always", joined)
        self.assertIn("systemctl daemon-reload", joined)
        self.assertIn("systemctl restart empyralis-gateway-channels.service", joined)

    def test_an_unproven_launcher_is_never_handed_out_as_an_ExecStart(self) -> None:
        """Recommending a launcher the box could not run is how a stale box
        becomes a dead one — nobody can SSH in to undo it."""
        plan = identity.plan_gateway_launch_repair(
            identity.launch_report(
                _registration(
                    gateway_launch_updatability=_launch_metadata(
                        repair={
                            "launcherPath": "/var/lib/empyralis-gw/state/launch/run-gateway",
                            "execStartLine": "ExecStart=/var/lib/empyralis-gw/state/launch/run-gateway",
                            "verified": False,
                            "failureReason": "the launcher could not be run — EACCES",
                        }
                    )
                )
            )
        )
        assert plan is not None
        self.assertEqual(plan["state"], "unverified")
        self.assertNotIn("commands", plan)
        self.assertIn("EACCES", plan["detail"])

    def test_a_box_with_no_repair_at_all_still_reports_the_problem(self) -> None:
        plan = identity.plan_gateway_launch_repair(
            identity.launch_report(
                _registration(gateway_launch_updatability=_launch_metadata(repair=None))
            )
        )
        assert plan is not None
        self.assertEqual(plan["state"], "unverified")
        self.assertEqual(
            plan["current_launch_command"],
            "/usr/bin/node /opt/empyralis-app/empyralis-gateway/dist/index.js",
        )

    def test_a_healthy_box_is_offered_no_repair_at_all(self) -> None:
        for status in ("updatable", "unknown"):
            with self.subTest(status=status):
                plan = identity.plan_gateway_launch_repair(
                    identity.launch_report(
                        _registration(
                            gateway_launch_updatability=_launch_metadata(status=status)
                        )
                    )
                )
                self.assertIsNone(plan)

    def test_a_mac_box_is_not_handed_systemctl_commands(self) -> None:
        plan = identity.plan_gateway_launch_repair(
            identity.launch_report(
                _registration(
                    gateway_launch_updatability=_launch_metadata(
                        supervisor="launchd",
                        unitPath="/Users/someone/Library/LaunchAgents/ai.empyralis.agent-computer.plist",
                    )
                )
            )
        )
        assert plan is not None
        joined = "\n".join(plan["commands"])
        self.assertNotIn("systemctl", joined)
        self.assertIn("launchctl", joined)


class UpdateStatusPayloadTests(unittest.TestCase):
    def test_the_status_payload_carries_the_refusal_AND_the_fix_for_it(self) -> None:
        """A person told 'this computer cannot receive updates' and given
        nothing to do about it has been informed of a dead end."""
        from server_modules import gateway_self_update_service

        status = gateway_self_update_service.gateway_update_status(
            _registration(
                gateway_version="0.1.0",
                gateway_build_fingerprint="aaaaaaaaaaaaaaaa",
                gateway_launch_updatability=_launch_metadata(),
            )
        )
        self.assertFalse(status["gateway_update_available"])
        self.assertEqual(
            status["gateway_update_refusal_code"],
            identity.REFUSAL_LAUNCH_PATH_NOT_UPDATABLE,
        )
        self.assertIsNotNone(status["gateway_launch_repair"])
        self.assertEqual(status["gateway_launch_repair"]["state"], "ready")

    def test_a_healthy_box_carries_no_repair_block(self) -> None:
        from server_modules import gateway_self_update_service

        status = gateway_self_update_service.gateway_update_status(
            _registration(gateway_version="0.1.0", gateway_build_fingerprint="aaaa")
        )
        self.assertIsNone(status["gateway_launch_repair"])


if __name__ == "__main__":
    unittest.main()
