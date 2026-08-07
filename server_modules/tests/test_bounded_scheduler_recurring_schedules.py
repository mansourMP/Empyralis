"""Recurring schedules ("every morning at 9am") -- unit cover for the pure
cron-parsing/next-fire-time logic and the _fire_recurring_schedule gating
decisions, all mocked (no DB required; see test_recurring_schedules_
integration.py for the real-Postgres end-to-end proof of the same table and
functions).

Three things this file proves that the DB integration suite intentionally
does NOT, because they don't need a database at all:
  1. A valid cron parses; an invalid one fails LOUD (SchedulerPolicyError),
     never a silent None -- this is the exact bug fleet_tools._parse_when
     used to have (see fleet_tools.py's own updated docstring).
  2. compute_next_cron_fire_at is correct across a DST spring-forward
     transition AND a month rollover, in the WORKSPACE's configured
     timezone -- not UTC, not the server's own zone.
  3. _fire_recurring_schedule's gating (per-schedule daily wake cap,
     expiry/max_occurrences, and missed-occurrence catch-up avoidance)
     makes the right call without ever touching Postgres -- every
     repository/kernel call is mocked.
"""

from __future__ import annotations

import importlib
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from server_modules import bounded_scheduler_service, fleet_tools


class CronParsingTests(unittest.TestCase):
    def setUp(self):
        global bounded_scheduler_service
        bounded_scheduler_service = importlib.import_module("server_modules.bounded_scheduler_service")

    def test_valid_five_field_cron_parses(self):
        self.assertEqual(bounded_scheduler_service.parse_cron_expression("0 9 * * *"), "0 9 * * *")
        self.assertEqual(bounded_scheduler_service.parse_cron_expression("  */15 * * * *  "), "*/15 * * * *")

    def test_blank_cron_fails_loud(self):
        with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError):
            bounded_scheduler_service.parse_cron_expression("")
        with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError):
            bounded_scheduler_service.parse_cron_expression("   ")

    def test_wrong_field_count_fails_loud_with_specific_message(self):
        with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError) as ctx:
            bounded_scheduler_service.parse_cron_expression("* * * *")
        self.assertIn("5 fields", str(ctx.exception))

        with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError):
            # Seconds/year extensions (7-field) are explicitly not supported.
            bounded_scheduler_service.parse_cron_expression("0 0 9 * * * *")

    def test_invalid_field_values_fail_loud(self):
        with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError):
            bounded_scheduler_service.parse_cron_expression("99 9 * * *")  # minute out of range
        with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError):
            bounded_scheduler_service.parse_cron_expression("not a cron at all")

    def test_never_silently_returns_none(self):
        """The exact regression this feature fixes: an invalid/unparseable
        cron string used to fall through to `return None` inside
        fleet_tools._parse_when with no error at all. parse_cron_expression
        must always either return a string or raise -- never return None."""
        for bad in ["", "garbage", "* * * *", "60 25 32 13 8"]:
            with self.assertRaises(bounded_scheduler_service.SchedulerPolicyError):
                bounded_scheduler_service.parse_cron_expression(bad)


class NextFireTimeTests(unittest.TestCase):
    def setUp(self):
        global bounded_scheduler_service
        bounded_scheduler_service = importlib.import_module("server_modules.bounded_scheduler_service")

    def _policy(self, timezone_name: str) -> "bounded_scheduler_service.SchedulerPolicyBounds":
        return bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
            timezone_name=timezone_name,
        )

    def test_next_fire_uses_workspace_timezone_not_utc(self):
        """'Every morning at 9am' means 9am in the WORKSPACE's timezone.
        Tokyo is UTC+9 with no DST -- 9am JST is unambiguously 00:00 UTC,
        a clean sanity check with no daylight-saving noise."""
        policy = self._policy("Asia/Tokyo")
        after = datetime(2026, 5, 1, 1, 0, tzinfo=timezone.utc)  # 10am JST
        next_fire = bounded_scheduler_service.compute_next_cron_fire_at(
            "0 9 * * *", policy, after=after,
        )
        # Already past 9am JST on May 1 -> next fire is May 2, 9am JST == 00:00 UTC May 2.
        self.assertEqual(next_fire, datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc))

    def test_next_fire_is_correct_across_a_dst_spring_forward_transition(self):
        """America/New_York, cron '0 9 * * *' (9am daily). US DST in 2026
        starts Sunday March 8 (clocks jump 2am -> 3am, EST/UTC-5 becomes
        EDT/UTC-4). A naive fixed-UTC-offset implementation would compute
        14:00 UTC for "9am" on BOTH sides of the transition; the real
        answer shifts by exactly one hour, because the wall-clock time
        (9am) is what the cron expression actually means, not a frozen UTC
        instant."""
        policy = self._policy("America/New_York")

        before_transition = datetime(2026, 3, 5, 15, 0, tzinfo=timezone.utc)  # March 5, 10am EST
        next_fire_before = bounded_scheduler_service.compute_next_cron_fire_at(
            "0 9 * * *", policy, after=before_transition,
        )
        # March 6, 9am EST = 14:00 UTC (UTC-5, before the spring-forward).
        self.assertEqual(next_fire_before, datetime(2026, 3, 6, 14, 0, tzinfo=timezone.utc))

        after_transition = datetime(2026, 3, 9, 14, 0, tzinfo=timezone.utc)  # March 9, 10am EDT
        next_fire_after = bounded_scheduler_service.compute_next_cron_fire_at(
            "0 9 * * *", policy, after=after_transition,
        )
        # March 10, 9am EDT = 13:00 UTC (UTC-4, after the spring-forward) --
        # ONE HOUR EARLIER in UTC than the pre-transition case, despite the
        # cron expression never changing. This is the DST-correctness proof.
        self.assertEqual(next_fire_after, datetime(2026, 3, 10, 13, 0, tzinfo=timezone.utc))

        # Sanity: the actual local wall-clock hour is 9am on both sides.
        self.assertEqual(next_fire_before.astimezone(ZoneInfo("America/New_York")).hour, 9)
        self.assertEqual(next_fire_after.astimezone(ZoneInfo("America/New_York")).hour, 9)

    def test_next_fire_rolls_over_a_month_and_a_year_correctly(self):
        """cron '0 0 1 * *' (midnight on the 1st of every month), starting
        from the last day of a 30-day month AND from New Year's Eve --
        exactly the class of edge case a hand-rolled "add N days" parser
        gets wrong (April has 30 days, not 31; December rolls into a new
        year, not month 13)."""
        policy = self._policy("UTC")

        after_april_30 = datetime(2026, 4, 30, 23, 0, tzinfo=timezone.utc)
        next_fire = bounded_scheduler_service.compute_next_cron_fire_at(
            "0 0 1 * *", policy, after=after_april_30,
        )
        self.assertEqual(next_fire, datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc))

        after_dec_31 = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)
        next_fire_year = bounded_scheduler_service.compute_next_cron_fire_at(
            "0 0 1 * *", policy, after=after_dec_31,
        )
        self.assertEqual(next_fire_year, datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc))


class BoundedLifetimeTests(unittest.TestCase):
    def setUp(self):
        global bounded_scheduler_service
        bounded_scheduler_service = importlib.import_module("server_modules.bounded_scheduler_service")

    def test_no_bounds_supplied_defaults_to_90_days(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        max_occurrences, expires_at = bounded_scheduler_service._clamp_recurring_schedule_bounds(
            now_utc=now, max_occurrences=None, expires_at=None,
        )
        self.assertIsNone(max_occurrences)
        self.assertEqual(expires_at, now + timedelta(days=bounded_scheduler_service.DEFAULT_RECURRING_SCHEDULE_LIFETIME_DAYS))

    def test_explicit_expires_at_beyond_hard_ceiling_is_clamped(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        requested = now + timedelta(days=10_000)  # absurdly far out
        _, expires_at = bounded_scheduler_service._clamp_recurring_schedule_bounds(
            now_utc=now, max_occurrences=None, expires_at=requested,
        )
        self.assertEqual(expires_at, now + timedelta(days=bounded_scheduler_service.RECURRING_SCHEDULE_HARD_MAX_LIFETIME_DAYS))

    def test_explicit_max_occurrences_beyond_hard_ceiling_is_clamped(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        max_occurrences, expires_at = bounded_scheduler_service._clamp_recurring_schedule_bounds(
            now_utc=now, max_occurrences=1_000_000, expires_at=None,
        )
        self.assertEqual(max_occurrences, bounded_scheduler_service.RECURRING_SCHEDULE_HARD_MAX_OCCURRENCES)
        # An occurrence bound with no explicit expiry still gets a lifetime
        # backstop -- a rarely-firing schedule shouldn't stay "active" forever.
        self.assertEqual(expires_at, now + timedelta(days=bounded_scheduler_service.RECURRING_SCHEDULE_HARD_MAX_LIFETIME_DAYS))

    def test_reasonable_explicit_bounds_pass_through_unclamped(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        requested_expiry = now + timedelta(days=14)
        max_occurrences, expires_at = bounded_scheduler_service._clamp_recurring_schedule_bounds(
            now_utc=now, max_occurrences=5, expires_at=requested_expiry,
        )
        self.assertEqual(max_occurrences, 5)
        self.assertEqual(expires_at, requested_expiry)


class ParseWhenRejectsCronTests(unittest.TestCase):
    """fleet_tools._parse_when's own fix: a cron-SHAPED `when` now raises a
    specific, actionable ValueError instead of silently falling through to
    `return None` (the exact bug the build brief flagged)."""

    def test_cron_shaped_string_raises_with_redirect(self):
        with self.assertRaises(ValueError) as ctx:
            fleet_tools._parse_when("*/5 * * * *")
        self.assertIn("schedule_recurring_task", str(ctx.exception))

    def test_valid_cron_also_raises_not_silently_accepted_as_a_datetime(self):
        with self.assertRaises(ValueError):
            fleet_tools._parse_when("0 9 * * *")

    def test_genuine_gibberish_still_returns_none(self):
        self.assertIsNone(fleet_tools._parse_when("whenever, I guess"))

    def test_relative_and_iso_forms_still_work(self):
        self.assertIsNotNone(fleet_tools._parse_when("in 30 minutes"))
        self.assertIsNotNone(fleet_tools._parse_when("2026-07-04T09:00:00Z"))

    def test_schedule_task_surfaces_the_cron_redirect_as_ok_false(self):
        import asyncio

        result = asyncio.run(
            fleet_tools.schedule_task(
                workspace_id="ws-1",
                agent_id="agent-1",
                when="0 9 * * *",
                instruction="Do the thing.",
            )
        )
        self.assertFalse(result["ok"])
        self.assertIn("schedule_recurring_task", result["error"])


class FireRecurringScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        global bounded_scheduler_service
        bounded_scheduler_service = importlib.import_module("server_modules.bounded_scheduler_service")

    def _policy(self):
        return bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=0,
            quiet_hours_end=0,  # disabled -- start == end means never active
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
            timezone_name="UTC",
        )

    def _schedule(self, **overrides):
        base = {
            "id": "recur_abc123",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "agent_id": "agent-1",
            "cron_expression": "*/5 * * * *",
            "summary": "Ping.",
            "payload": {"instruction": "Ping.", "agent_id": "agent-1", "authority_tier": "audience"},
            "requested_by": "owner",
            "next_fire_at": bounded_scheduler_service._utc_now() - timedelta(minutes=1),
            "occurrence_count": 0,
            "max_occurrences": None,
            "expires_at": None,
        }
        base.update(overrides)
        return base

    async def test_skips_when_daily_cap_reached_and_never_calls_persist_wakeup(self):
        policy = self._policy()
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=bounded_scheduler_service.max_recurring_wakes_per_day()),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(),
            ) as persist_mock,
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.update_agent_recurring_schedule",
                new=AsyncMock(return_value={"id": "recur_abc123"}),
            ) as update_mock,
        ):
            outcome = await bounded_scheduler_service._fire_recurring_schedule(self._schedule())

        self.assertEqual(outcome["action"], "skipped_daily_cap")
        persist_mock.assert_not_called()
        update_mock.assert_awaited_once()
        self.assertEqual(update_mock.await_args.kwargs["metadata_patch"]["last_skip_reason"], "recurring_daily_wake_cap")
        # Even when skipped, next_fire_at must still advance -- otherwise
        # the schedule would be "due" again on every single scan tick
        # forever, hammering the daily-cap check in a tight loop.
        self.assertIsNotNone(update_mock.await_args.kwargs["next_fire_at"])

    async def test_expires_when_past_expires_at_and_never_calls_persist_wakeup(self):
        policy = self._policy()
        past_expiry = bounded_scheduler_service._utc_now() - timedelta(days=1)
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(),
            ) as persist_mock,
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.update_agent_recurring_schedule",
                new=AsyncMock(return_value={"id": "recur_abc123", "status": "expired"}),
            ) as update_mock,
        ):
            outcome = await bounded_scheduler_service._fire_recurring_schedule(
                self._schedule(expires_at=past_expiry),
            )

        self.assertEqual(outcome["action"], "expired")
        persist_mock.assert_not_called()
        update_mock.assert_awaited_once_with(
            tenant_id="tenant-1", workspace_id="workspace-1", schedule_id="recur_abc123", status="expired",
        )

    async def test_expires_when_max_occurrences_reached(self):
        policy = self._policy()
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(),
            ) as persist_mock,
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.update_agent_recurring_schedule",
                new=AsyncMock(return_value={"id": "recur_abc123", "status": "expired"}),
            ),
        ):
            outcome = await bounded_scheduler_service._fire_recurring_schedule(
                self._schedule(max_occurrences=3, occurrence_count=3),
            )

        self.assertEqual(outcome["action"], "expired")
        persist_mock.assert_not_called()

    async def test_fires_and_advances_next_fire_at_and_occurrence_count(self):
        policy = self._policy()
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(return_value={"id": "wake-99", "status": "pending"}),
            ) as persist_mock,
            patch(
                "server_modules.bounded_scheduler_service._trigger_ambient_monitor",
                return_value={"ok": True},
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.update_agent_recurring_schedule",
                new=AsyncMock(return_value={"id": "recur_abc123"}),
            ) as update_mock,
            patch(
                "server_modules.bounded_scheduler_service._apply_policy_to_due_at",
                wraps=bounded_scheduler_service._apply_policy_to_due_at,
            ) as apply_policy_mock,
        ):
            outcome = await bounded_scheduler_service._fire_recurring_schedule(self._schedule())

        self.assertEqual(outcome["action"], "fired")
        self.assertEqual(outcome["wake_request_id"], "wake-99")
        persist_mock.assert_awaited_once()
        self.assertEqual(persist_mock.await_args.kwargs["trigger_kind"], "recurring")
        # Quiet hours are NOT skipped for a recurring fire -- unlike task_
        # assigned/task_commented (which skip them because a human is
        # demonstrably awake right now), a recurring fire is an ambient
        # trigger, exactly what quiet hours exist to gate.
        self.assertEqual(apply_policy_mock.call_args.kwargs["skip_quiet_hours"], False)
        update_mock.assert_awaited_once()
        self.assertEqual(update_mock.await_args.kwargs["occurrence_count"], 1)
        self.assertIsNotNone(update_mock.await_args.kwargs["last_fired_at"])

    async def test_missed_occurrences_do_not_cause_a_catch_up_storm(self):
        """If the process was down and a schedule's next_fire_at is days in
        the past, the recomputed next_fire_at must be anchored to NOW, not
        to the stale slot -- otherwise every missed tick would need to be
        caught up one by one."""
        policy = self._policy()
        stale_schedule = self._schedule(
            next_fire_at=bounded_scheduler_service._utc_now() - timedelta(days=2),
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(return_value={"id": "wake-1", "status": "pending"}),
            ),
            patch("server_modules.bounded_scheduler_service._trigger_ambient_monitor", return_value={"ok": True}),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.update_agent_recurring_schedule",
                new=AsyncMock(return_value={"id": "recur_abc123"}),
            ) as update_mock,
        ):
            await bounded_scheduler_service._fire_recurring_schedule(stale_schedule)

        next_fire_at = update_mock.await_args.kwargs["next_fire_at"]
        # New next_fire_at must be close to now (within a few minutes for a
        # */5 cron), not two days in the past and not two days' worth of
        # missed slots queued up.
        self.assertLess(abs((next_fire_at - bounded_scheduler_service._utc_now()).total_seconds()), 600)


if __name__ == "__main__":
    unittest.main()
