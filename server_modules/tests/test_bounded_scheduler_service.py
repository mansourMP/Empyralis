import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict

from server_modules import bounded_scheduler_service, db as db_module, fleet_tools


class BoundedSchedulerServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        global bounded_scheduler_service

        bounded_scheduler_service = importlib.import_module("server_modules.bounded_scheduler_service")

    def test_max_wakes_per_task_per_day_default(self):
        """STEP 6 numeric backstop (agent-identity plan): the named,
        easily-tunable constant the per-task wake ceiling reads. Not yet
        wired to the wake-on-mention trigger (a future wave), but the
        constant + accessor + enforcement point (schedule_task_assigned_
        wakeup) are live now."""
        self.assertEqual(bounded_scheduler_service.DEFAULT_MAX_WAKES_PER_TASK_PER_DAY, 24)
        self.assertEqual(
            bounded_scheduler_service.max_wakes_per_task_per_day(),
            bounded_scheduler_service.DEFAULT_MAX_WAKES_PER_TASK_PER_DAY,
        )

    @patch.dict("os.environ", {"EMPYRALIS_MAX_WAKES_PER_TASK_PER_DAY": "5"})
    def test_max_wakes_per_task_per_day_respects_env_override(self):
        self.assertEqual(bounded_scheduler_service.max_wakes_per_task_per_day(), 5)

    @patch.dict("os.environ", {"EMPYRALIS_MAX_WAKES_PER_TASK_PER_DAY": "0"})
    def test_max_wakes_per_task_per_day_floors_at_one(self):
        """A misconfigured 0 (or negative) override can never mean
        "unlimited" -- it floors at 1."""
        self.assertEqual(bounded_scheduler_service.max_wakes_per_task_per_day(), 1)

    def test_resolve_scheduler_policy_uses_entitlement_defaults(self):
        policy = bounded_scheduler_service.resolve_scheduler_policy(
            workspace={"metadata": {"billing": {"plan": "free"}}},
            master_install={"metadata": {}},
        )

        self.assertEqual(policy.plan_tier, "free")
        self.assertEqual(policy.max_event_triggers_per_hour, 2)
        self.assertEqual(policy.max_self_proposed_per_hour, 1)
        self.assertEqual(policy.max_runtime_seconds, 15)

    async def test_maybe_schedule_event_trigger_ignores_low_priority_events(self):
        result = await bounded_scheduler_service.maybe_schedule_event_trigger(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            event={
                "id": "evt-1",
                "event_type": "flashcards_created",
                "source_app": "study",
                "summary": "12 flashcards were created.",
                "priority": 20,
                "scope": {"audience": ["sage"]},
            },
        )

        self.assertIsNone(result)

    async def test_propose_self_wakeup_denies_privileged_without_approval(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service._persist_wakeup",
                new=AsyncMock(return_value={"id": "wake-1", "status": "denied"}),
            ) as persist_mock,
        ):
            result = await bounded_scheduler_service.propose_self_wakeup(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                summary="Check whether the user still needs a sleep reminder.",
                reason="night_review",
                policy_context={"requires_privileged_runtime": True},
            )

        self.assertFalse(result["accepted"])
        self.assertEqual(result["wake_request"]["status"], "denied")
        self.assertTrue(persist_mock.await_args.kwargs["approval_required"])

    async def test_propose_self_wakeup_accepts_immediate_request_and_triggers_monitor(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=0,
            quiet_hours_end=0,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
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
                new=AsyncMock(return_value={"id": "wake-2", "status": "pending"}),
            ),
            patch(
                "server_modules.bounded_scheduler_service._trigger_ambient_monitor",
                return_value={"ok": True},
            ) as trigger_mock,
        ):
            result = await bounded_scheduler_service.propose_self_wakeup(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                summary="Check whether the reply backlog needs a nudge.",
                reason="message_followup",
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["wake_request"]["id"], "wake-2")
        trigger_mock.assert_called_once_with("workspace-1")

    async def test_build_wakeup_execution_bundle_uses_context_goals_and_preferences(self):
        """Same-tier heartbeat + wake request: HEARTBEAT.md is always owner,
        and an explicitly owner-tier wake request joins that same group —
        one turn, one blended message, exactly like the pre-tier-grouping
        behavior for the (common) case where everything actually is the same
        tier."""
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(
                    return_value=(
                        {"metadata": {"goals": ["Prepare for finals"]}},
                        {"id": "install-sage", "metadata": {}},
                        policy,
                    )
                ),
            ),
            patch(
                "server_modules.personal_context_engine.list_events",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "evt-1",
                            "source_app": "calendar",
                            "summary": "Calendar conflict detected for Math Final.",
                        }
                    ]
                ),
            ),
            patch(
                "server_modules.bounded_scheduler_service.workspace_context.read_workspace_context_file",
                return_value="Prefer brief reminders after 9 PM.",
            ),
        ):
            result = await bounded_scheduler_service.build_wakeup_execution_bundle(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                heartbeat_tasks=["Review notification queue"],
                wake_requests=[{
                    "id": "wake-1",
                    "trigger_kind": "self_proposed",
                    "summary": "Reply is still pending for Alex.",
                    "payload": {"authority_tier": "owner"},
                }],
            )

        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["authority_tier"], "owner")
        self.assertEqual(group["scheduler_mode"], "mixed")
        self.assertIn("Prepare for finals", group["message"])
        self.assertIn("Reply is still pending for Alex.", group["message"])
        self.assertIn("Calendar conflict detected for Math Final.", group["message"])
        self.assertIn("Prefer brief reminders", group["message"])
        self.assertEqual(result["unattributed_wake_request_ids"], [])

    async def test_build_wakeup_execution_bundle_never_blends_tiers(self):
        """The anti-laundering fix: an owner-configured heartbeat checklist
        and an audience-scheduled wake request (fleet_tools.schedule_task,
        inherit_tier'd from an audience-tier turn) must land in SEPARATE
        groups — never one blended message/turn."""
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23, quiet_hours_end=7, max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2, max_runtime_seconds=20, minimum_battery_percent=20,
            require_network_online=False, require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch("server_modules.personal_context_engine.list_events", new=AsyncMock(return_value=[])),
            patch("server_modules.bounded_scheduler_service.workspace_context.read_workspace_context_file", return_value=""),
        ):
            result = await bounded_scheduler_service.build_wakeup_execution_bundle(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                heartbeat_tasks=["Check the inbox backlog"],
                wake_requests=[
                    {
                        "id": "wake-audience",
                        "trigger_kind": "self_proposed",
                        "summary": "Follow up with the customer about their order.",
                        "payload": {"authority_tier": "audience"},
                    },
                    {
                        "id": "wake-owner",
                        "trigger_kind": "self_proposed",
                        "summary": "Draft the weekly investor update.",
                        "payload": {"authority_tier": "owner"},
                    },
                ],
            )

        groups_by_tier = {g["authority_tier"]: g for g in result["groups"]}
        self.assertEqual(set(groups_by_tier.keys()), {"owner", "audience"})

        owner_group = groups_by_tier["owner"]
        self.assertIn("Check the inbox backlog", owner_group["message"])
        self.assertIn("Draft the weekly investor update", owner_group["message"])
        self.assertNotIn("customer about their order", owner_group["message"])
        self.assertEqual(owner_group["wake_request_ids"], ["wake-owner"])

        audience_group = groups_by_tier["audience"]
        self.assertIn("customer about their order", audience_group["message"])
        self.assertNotIn("Check the inbox backlog", audience_group["message"])
        self.assertNotIn("investor update", audience_group["message"])
        self.assertEqual(audience_group["wake_request_ids"], ["wake-audience"])
        self.assertEqual(audience_group["heartbeat_tasks"], [])

        self.assertEqual(result["unattributed_wake_request_ids"], [])

    async def test_schedule_task_wake_request_groups_and_executes_as_audience_end_to_end(self):
        """Closes the loop for the fleet__schedule_task dispatch wiring: an
        agent's ACTUAL schedule_task() call (not a hand-written fixture)
        produces a wake-request payload that later groups and would execute
        as audience — the same tier the initiating turn carried, never
        upgraded on the way through."""
        captured_payload = {}
        captured_summary = {}

        async def _fake_propose_self_wakeup(**kwargs):
            captured_payload.update(kwargs.get("payload") or {})
            captured_summary["value"] = kwargs.get("summary")
            return {"accepted": True, "wake_request": {"id": "wake-scheduled"}}

        with patch(
            "server_modules.bounded_scheduler_service.propose_self_wakeup",
            new=_fake_propose_self_wakeup,
        ):
            schedule_result = await fleet_tools.schedule_task(
                workspace_id="workspace-1",
                agent_id="agent-x",
                actor_id="agent-x",
                when="in 30 minutes",
                instruction="Follow up with the customer about their order.",
                tenant_id="tenant-1",
                authority_tier="audience",
            )
        self.assertTrue(schedule_result["ok"])
        self.assertEqual(captured_payload["authority_tier"], "audience")

        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23, quiet_hours_end=7, max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2, max_runtime_seconds=20, minimum_battery_percent=20,
            require_network_online=False, require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch("server_modules.personal_context_engine.list_events", new=AsyncMock(return_value=[])),
            patch("server_modules.bounded_scheduler_service.workspace_context.read_workspace_context_file", return_value=""),
        ):
            bundle = await bounded_scheduler_service.build_wakeup_execution_bundle(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                heartbeat_tasks=["Owner's unrelated checklist item"],
                # Mirrors exactly what claim_due_scheduler_wake_requests would
                # hand back for the row schedule_task just proposed: the id
                # it minted, the trigger_kind propose_self_wakeup stamps for
                # self-proposed rows, and the summary/payload captured above.
                wake_requests=[{
                    "id": "wake-scheduled",
                    "trigger_kind": "self_proposed",
                    "summary": captured_summary["value"],
                    "payload": captured_payload,
                }],
            )

        groups_by_tier = {g["authority_tier"]: g for g in bundle["groups"]}
        self.assertIn("audience", groups_by_tier)
        audience_group = groups_by_tier["audience"]
        self.assertEqual(audience_group["wake_request_ids"], ["wake-scheduled"])
        self.assertIn("customer about their order", audience_group["message"])
        # The owner's unrelated heartbeat checklist item must never appear in
        # (or execute as part of) the audience-tier group.
        self.assertNotIn("unrelated checklist", audience_group["message"])
        self.assertEqual(audience_group["heartbeat_tasks"], [])
        self.assertEqual(bundle["unattributed_wake_request_ids"], [])

    async def test_build_wakeup_execution_bundle_legacy_tierless_request_never_owner(self):
        """A wake request with no authority_tier in its payload and a
        trigger_kind that isn't the documented no-tier case (event_trigger)
        represents either a legacy pre-fix row or an unrecognized producer —
        it must fail safe to audience, NEVER owner, and get flagged as
        unattributed so the gap stays visible."""
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23, quiet_hours_end=7, max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2, max_runtime_seconds=20, minimum_battery_percent=20,
            require_network_online=False, require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch("server_modules.personal_context_engine.list_events", new=AsyncMock(return_value=[])),
            patch("server_modules.bounded_scheduler_service.workspace_context.read_workspace_context_file", return_value=""),
            patch(
                "server_modules.bounded_scheduler_service._ledger_unattributed_wake_request",
                new=AsyncMock(),
            ) as ledger_mock,
        ):
            result = await bounded_scheduler_service.build_wakeup_execution_bundle(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                heartbeat_tasks=[],
                wake_requests=[{"id": "wake-legacy", "trigger_kind": "self_proposed", "summary": "Pre-fix row, no payload tier."}],
            )

        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["authority_tier"], "audience")
        self.assertEqual(result["unattributed_wake_request_ids"], ["wake-legacy"])
        ledger_mock.assert_awaited_once()

    async def test_build_wakeup_execution_bundle_event_trigger_is_audience_not_unattributed(self):
        """event_trigger rows (context-engine, no live sender) resolve to
        audience by design — this is a documented default, not a gap, so it
        must NOT be flagged unattributed."""
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23, quiet_hours_end=7, max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2, max_runtime_seconds=20, minimum_battery_percent=20,
            require_network_online=False, require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch("server_modules.personal_context_engine.list_events", new=AsyncMock(return_value=[])),
            patch("server_modules.bounded_scheduler_service.workspace_context.read_workspace_context_file", return_value=""),
        ):
            result = await bounded_scheduler_service.build_wakeup_execution_bundle(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                heartbeat_tasks=[],
                # No payload at all -- maybe_schedule_event_trigger stamps
                # authority_tier explicitly now, but older rows won't have
                # it; trigger_kind alone must still resolve this safely.
                wake_requests=[{"id": "wake-evt", "trigger_kind": "event_trigger", "summary": "Detected a calendar conflict."}],
            )

        self.assertEqual(result["groups"][0]["authority_tier"], "audience")
        self.assertEqual(result["unattributed_wake_request_ids"], [])

    async def test_scheduler_status_snapshot_reports_policy_jobs_and_queue(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=23,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.list_agent_scheduler_wake_requests",
                new=AsyncMock(side_effect=[[{"id": "wake-1"}], [{"id": "wake-2"}]]),
            ),
            patch(
                "server_modules.bounded_scheduler_service.ambient_monitor_status",
                return_value={"registered": True, "heartbeat": {"ok": True}},
            ),
            patch(
                "server_modules.runs_core.list_schedules",
                new=AsyncMock(return_value={"items": [{"id": "job-1"}, {"id": "job-2"}]}),
            ),
        ):
            result = await bounded_scheduler_service.scheduler_status_snapshot(
                tenant_id="tenant-1",
                workspace_id="workspace-1",
            )

        self.assertEqual(result["policy"]["plan_tier"], "standard")
        self.assertTrue(result["ambient_monitor"]["registered"])
        self.assertEqual(result["exact_jobs"]["count"], 2)
        self.assertEqual(result["wake_queue"]["pending_count"], 1)
        self.assertEqual(result["wake_queue"]["claimed_count"], 1)

    def test_quiet_hours_status_snapshot_reports_active_window(self):
        """MAN-294: `policy` below leaves timezone_name at its default
        (DEFAULT_SCHEDULER_TIMEZONE, "UTC"), so `now_utc` must actually fall
        inside 22:00-07:00 UTC for this to be a real assertion. Before the
        fix, _is_within_quiet_hours used the HOST MACHINE's local timezone
        (bare .astimezone()), so this test's original fixture --
        15:30 UTC, nowhere near 22:00-07:00 in ANY timezone actually used in
        this window's own arithmetic -- only ever passed by accident,
        because the CI/dev box's own local zone happened to shift 15:30 UTC
        into the window. That was the bug, disguised as a passing test:
        changing the box's TZ would have flipped this test's result without
        touching a line of source. 23:30 UTC is unambiguously inside
        22:00-07:00 in the explicit UTC default this test now exercises, so
        the result no longer depends on where this test happens to run."""
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=22,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )

        snapshot = bounded_scheduler_service.quiet_hours_status_snapshot(
            policy=policy,
            now_utc=datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc),
        )

        self.assertTrue(snapshot["active"])
        self.assertIn("Quiet hours active until", snapshot["label"])
        # The window ends at 07:00 in the policy's own (UTC, here) timezone,
        # not whatever the host machine's zone would have produced.
        self.assertIn("07:00", snapshot["label"])
        self.assertEqual(snapshot["next_allowed_at"], "2026-05-06T07:00:00Z")

    def test_quiet_hours_status_snapshot_reports_inactive_outside_window(self):
        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=22,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )

        snapshot = bounded_scheduler_service.quiet_hours_status_snapshot(
            policy=policy,
            now_utc=datetime(2026, 5, 5, 15, 30, tzinfo=timezone.utc),
        )

        self.assertFalse(snapshot["active"])
        self.assertEqual(snapshot["label"], "Background work can run now")


class SchedulerPolicyTimezoneTests(unittest.TestCase):
    """MAN-294 part 1: no workspace, install, or user anywhere in this
    codebase stores a timezone today (verified by reading workspace
    metadata, the workspace settings routes, and the user profile). Per the
    ruling on this ticket, resolve_scheduler_policy now ALWAYS resolves an
    explicit timezone_name -- DEFAULT_SCHEDULER_TIMEZONE ("UTC") until a
    workspace or install configures scheduler.timezone in its metadata,
    mirroring exactly how scheduler.quiet_hours is already threaded
    through -- rather than the previous implicit, server-clock-dependent
    behavior (bare datetime.astimezone())."""

    def test_defaults_to_utc_when_nothing_configures_a_timezone(self):
        policy = bounded_scheduler_service.resolve_scheduler_policy(
            workspace={"metadata": {}},
            master_install={"metadata": {}},
        )
        self.assertEqual(policy.timezone_name, "UTC")

    def test_workspace_metadata_timezone_is_honored(self):
        policy = bounded_scheduler_service.resolve_scheduler_policy(
            workspace={"metadata": {"scheduler": {"timezone": "Asia/Tokyo"}}},
            master_install={"metadata": {}},
        )
        self.assertEqual(policy.timezone_name, "Asia/Tokyo")

    def test_install_metadata_timezone_wins_over_workspace(self):
        """Matches quiet_hours' own precedence (install over workspace) --
        see _workspace_scheduler_metadata/_install_scheduler_metadata's
        merge order in resolve_scheduler_policy."""
        policy = bounded_scheduler_service.resolve_scheduler_policy(
            workspace={"metadata": {"scheduler": {"timezone": "Asia/Tokyo"}}},
            master_install={"metadata": {"scheduler": {"timezone": "America/Phoenix"}}},
        )
        self.assertEqual(policy.timezone_name, "America/Phoenix")

    def test_unrecognized_timezone_string_falls_back_to_default_not_a_guess(self):
        """A garbled/invalid IANA name must not silently become the server's
        own zone, and must not raise -- it falls back to the same explicit
        DEFAULT_SCHEDULER_TIMEZONE an unconfigured workspace gets."""
        policy = bounded_scheduler_service.resolve_scheduler_policy(
            workspace={"metadata": {"scheduler": {"timezone": "Not/AZone"}}},
            master_install={"metadata": {}},
        )
        self.assertEqual(policy.timezone_name, "UTC")


class QuietHoursTimezoneEvaluationTests(unittest.TestCase):
    """MAN-294 part 1's actual regression coverage: _is_within_quiet_hours
    must evaluate against the POLICY's configured timezone, not the host
    process's. All cases below share one wrap-around window
    (quiet_hours_start=22 > quiet_hours_end=7, i.e. 22:00-07:00), the exact
    shape production hit, and one fixed UTC instant -- only `timezone_name`
    varies between assertions, so any difference in the result is
    attributable to that one field and nothing else (in particular: not to
    whatever timezone this test happens to run in, unlike the pre-fix
    behavior this replaces).

    America/Phoenix and Asia/Tokyo are both deliberately DST-free
    (Arizona does not observe DST; Japan has none), so these results hold on
    every calendar date, not just the ones picked here.
    """

    def _policy(self, *, timezone_name: str) -> bounded_scheduler_service.SchedulerPolicyBounds:
        return bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=22,
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

    def test_same_instant_is_on_opposite_sides_of_the_window_in_two_timezones(self):
        """2026-06-15T14:00:00Z is 23:00 in Tokyo (UTC+9) -- well inside
        22:00-07:00 -- and simultaneously 07:00 in Phoenix (UTC-7) -- the
        window's own end boundary, exclusive, so NOT active. One real
        instant, two workspaces, two different honest answers -- this is
        exactly what a shared server-clock evaluation cannot produce, since
        it can only ever give one answer for everyone at a given UTC
        instant."""
        instant = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
        tokyo_policy = self._policy(timezone_name="Asia/Tokyo")
        phoenix_policy = self._policy(timezone_name="America/Phoenix")

        self.assertTrue(bounded_scheduler_service._is_within_quiet_hours(instant, tokyo_policy))
        self.assertFalse(bounded_scheduler_service._is_within_quiet_hours(instant, phoenix_policy))

    def test_reverse_direction_other_timezone_active_instead(self):
        """2026-06-15T05:00:00Z is 22:00 in Phoenix (UTC-7) -- just inside
        the window's START boundary, inclusive -- and simultaneously 14:00
        in Tokyo (UTC+9) -- nowhere near it. Confirms the previous test
        wasn't a one-off coincidence of which zone happened to be ahead."""
        instant = datetime(2026, 6, 15, 5, 0, tzinfo=timezone.utc)
        tokyo_policy = self._policy(timezone_name="Asia/Tokyo")
        phoenix_policy = self._policy(timezone_name="America/Phoenix")

        self.assertTrue(bounded_scheduler_service._is_within_quiet_hours(instant, phoenix_policy))
        self.assertFalse(bounded_scheduler_service._is_within_quiet_hours(instant, tokyo_policy))

    def test_midnight_wrap_next_allowed_wakeup_time_lands_in_the_right_zone(self):
        """_next_allowed_wakeup_time's own wrap-handling (candidate <= local_
        now -> +1 day) evaluated in a non-UTC, non-server zone: at
        2026-06-15T14:00:00Z (23:00 in Tokyo), the next allowed wakeup is
        Tokyo's own 07:00 the following LOCAL day, converted back to UTC
        (07:00 JST == 2026-06-15T22:00:00Z, still the 15th UTC because JST
        is ahead of UTC) -- not 07:00 UTC, and not 07:00 in whatever zone
        the test runner's machine is in."""
        instant = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
        tokyo_policy = self._policy(timezone_name="Asia/Tokyo")

        next_allowed = bounded_scheduler_service._next_allowed_wakeup_time(instant, tokyo_policy)

        self.assertEqual(next_allowed, datetime(2026, 6, 15, 22, 0, tzinfo=timezone.utc))


class QuietHoursSkipForExplicitHumanActionTests(unittest.IsolatedAsyncioTestCase):
    """MAN-294 part 2: schedule_task_assigned_wakeup/schedule_task_commented_
    wakeup must skip the quiet-hours gate entirely (an explicit human action
    is not an ambient trigger); propose_self_wakeup (self_proposed) must
    stay fully gated, exactly as before. Same quiet-hours-active policy and
    the same frozen instant back every case here -- the only thing that
    differs between "due immediately" and "deferred to 07:00" is which
    entry point is under test, not the policy or the clock."""

    def _quiet_hours_active_policy(self) -> bounded_scheduler_service.SchedulerPolicyBounds:
        # 22:00-07:00 UTC. frozen_now (below, 23:30 UTC) sits inside it in
        # the explicit UTC default, so this is deterministic regardless of
        # the host machine's own timezone.
        return bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=22,
            quiet_hours_end=7,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )

    async def test_task_assigned_wake_during_quiet_hours_is_due_immediately(self):
        policy = self._quiet_hours_active_policy()
        frozen_now = datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc)
        captured: Dict[str, Any] = {}

        async def fake_persist(**kwargs):
            captured.update(kwargs)
            return {"id": "wake-immediate", "status": "pending"}

        with (
            patch("server_modules.bounded_scheduler_service._utc_now", return_value=frozen_now),
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch("server_modules.bounded_scheduler_service._persist_wakeup", side_effect=fake_persist),
            patch(
                "server_modules.bounded_scheduler_service._trigger_ambient_monitor",
                return_value={"ok": True},
            ) as trigger_mock,
        ):
            await bounded_scheduler_service.schedule_task_assigned_wakeup(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="agent-1",
                task_id="task-1",
                title="Ship the widget",
            )

        # The production bug, closed: before this fix due_at would have
        # been deferred to 2026-05-06T07:00:00+00:00 (quiet_hours_end).
        self.assertEqual(captured["due_at"], frozen_now)
        self.assertNotIn("policy_delay_reason", captured["metadata"])
        # An immediate due_at also wakes the ambient monitor right away,
        # instead of leaving the assignment silent until the next scan.
        trigger_mock.assert_called_once_with("ws-1")

    async def test_task_commented_wake_during_quiet_hours_is_due_immediately(self):
        policy = self._quiet_hours_active_policy()
        frozen_now = datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc)
        captured: Dict[str, Any] = {}

        async def fake_persist(**kwargs):
            captured.update(kwargs)
            return {"id": "wake-immediate-comment", "status": "pending"}

        with (
            patch("server_modules.bounded_scheduler_service._utc_now", return_value=frozen_now),
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch("server_modules.bounded_scheduler_service._persist_wakeup", side_effect=fake_persist),
            patch(
                "server_modules.bounded_scheduler_service._trigger_ambient_monitor",
                return_value={"ok": True},
            ) as trigger_mock,
        ):
            await bounded_scheduler_service.schedule_task_commented_wakeup(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="agent-1",
                task_id="task-1",
                title="Ship the widget",
                comment_body="try approach B instead",
            )

        self.assertEqual(captured["due_at"], frozen_now)
        self.assertNotIn("policy_delay_reason", captured["metadata"])
        trigger_mock.assert_called_once_with("ws-1")

    async def test_self_proposed_wake_during_quiet_hours_is_still_deferred(self):
        """The control case: self_proposed (an ambient, agent-initiated
        trigger, not a human clicking something) must NOT get the same
        skip -- it stays deferred to the next allowed wakeup, exactly as
        before this fix."""
        policy = self._quiet_hours_active_policy()
        frozen_now = datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc)
        captured: Dict[str, Any] = {}

        async def fake_persist(**kwargs):
            captured.update(kwargs)
            return {"id": "wake-deferred", "status": "pending"}

        with (
            patch("server_modules.bounded_scheduler_service._utc_now", return_value=frozen_now),
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, policy)),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch("server_modules.bounded_scheduler_service._persist_wakeup", side_effect=fake_persist),
            patch(
                "server_modules.bounded_scheduler_service._trigger_ambient_monitor",
                return_value={"ok": True},
            ) as trigger_mock,
        ):
            result = await bounded_scheduler_service.propose_self_wakeup(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                summary="Check on the reply backlog.",
                reason="ambient_followup",
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(captured["due_at"], datetime(2026, 5, 6, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(captured["metadata"]["policy_delay_reason"], "quiet_hours")
        trigger_mock.assert_not_called()


class _FastStopEvent:
    """A threading.Event look-alike whose .wait(timeout) returns
    immediately instead of actually sleeping -- lets the tests below drive
    run_wake_request_scan_forever through several ticks without paying
    the real wake_request_scan_poll_seconds() floor (5s minimum)."""

    def __init__(self) -> None:
        self._flag = False

    def wait(self, timeout=None) -> bool:  # noqa: ARG002 - signature parity with threading.Event
        return self._flag

    def set(self) -> None:
        self._flag = True

    def is_set(self) -> bool:
        return self._flag


class WakeRequestScanLoggingTests(unittest.IsolatedAsyncioTestCase):
    """MAN-292: bounded_scheduler_service.py had two silent swallows in the
    wake-request-scanner daemon -- a bare `except Exception: continue` on
    each tick (run_wake_request_scan_forever), and a per-scope
    run_workspace_heartbeat failure caught into an `outcome` dict that the
    tick-level caller then discards entirely (scan_due_wake_requests_once).
    Both are now logged via LOGGER.exception; these tests prove a raising
    scan produces a real log record at each of those two points."""

    async def test_scan_once_logs_a_per_scope_heartbeat_failure(self):
        def _raising_heartbeat(tasks, metadata):
            raise RuntimeError("boom")

        with (
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository."
                "list_due_agent_scheduler_wake_request_scopes",
                new=AsyncMock(return_value=[{"tenant_id": "tenant-1", "workspace_id": "workspace-1"}]),
            ),
            self.assertLogs("server_modules.bounded_scheduler_service", level="ERROR") as log_ctx,
        ):
            result = await bounded_scheduler_service.scan_due_wake_requests_once(
                run_workspace_heartbeat=_raising_heartbeat,
            )

        # The scan itself still completes and still reports the failure in
        # its return value (unchanged behavior) -- the fix is that the
        # failure is ALSO now logged, not a replacement for the return
        # value.
        self.assertEqual(result["scanned"], 1)
        self.assertFalse(result["results"][0]["result"]["acted"])
        self.assertIn("wake scan failed", result["results"][0]["result"]["summary"])

        self.assertTrue(
            any("heartbeat failed" in message for message in log_ctx.output),
            f"expected a heartbeat-failure log record, got: {log_ctx.output}",
        )
        self.assertTrue(
            any("boom" in message for message in log_ctx.output),
            f"expected the original exception text in the log record, got: {log_ctx.output}",
        )

    def test_run_forever_logs_a_tick_failure_and_keeps_running(self):
        """A tick that raises must be logged (MAN-292) AND must not kill
        the scanner for subsequent ticks (MAN-265's explicit constraint:
        a transient failure must never become a permanent outage)."""
        tick_count = {"n": 0}
        stop_event = _FastStopEvent()

        async def _fake_scan(*, run_workspace_heartbeat):
            tick_count["n"] += 1
            if tick_count["n"] >= 2:
                stop_event.set()
            raise RuntimeError("tick boom")

        with (
            patch.object(bounded_scheduler_service, "scan_due_wake_requests_once", _fake_scan),
            self.assertLogs("server_modules.bounded_scheduler_service", level="ERROR") as log_ctx,
        ):
            bounded_scheduler_service.run_wake_request_scan_forever(
                run_workspace_heartbeat=lambda tasks, metadata: {"acted": False},
                stop_event=stop_event,
                poll_seconds=5,
            )

        # Both ticks ran despite tick 1 raising -- the loop survived.
        self.assertEqual(tick_count["n"], 2)
        tick_failure_logs = [m for m in log_ctx.output if "tick failed" in m]
        self.assertEqual(
            len(tick_failure_logs), 2,
            f"expected one 'tick failed' log record per raising tick, got: {log_ctx.output}",
        )
        self.assertTrue(any("tick boom" in m for m in log_ctx.output))


class WakeRequestScannerPersistentLoopTests(unittest.TestCase):
    """MAN-265: run_wake_request_scan_forever used to open a brand new
    asyncio event loop (asyncio.new_event_loop() / run_until_complete() /
    loop.close()) on EVERY tick via a since-removed `_run_sync` helper.
    server_modules.db.get_pool() caches the Postgres pool keyed by
    id(current_loop) (server_modules/db.py:150) specifically so a
    long-lived worker reuses one pool -- a fresh loop object every tick
    (every wake_request_scan_poll_seconds(), 20s by default) defeated that
    cache: get_pool() saw a "new" loop each time, tore down the "stale"
    pool from the just-closed previous loop, and paid for a fresh
    asyncpg.create_pool(...) call every single tick, forever. Confirmed
    live on production: "Postgres pool initialized -- run state will be
    durable" in ~/.pm2/logs/empyralis-error.log at exact 20-second
    intervals.

    This test proves the actual fix property MAN-265 asked for: across N
    ticks on one persistent loop, asyncpg.create_pool is invoked exactly
    ONCE, not once per tick.
    """

    def test_pool_created_once_across_multiple_ticks(self):
        create_pool_calls: list[str] = []
        loop_ids_seen: list[int] = []
        tick_count = {"n": 0}
        stop_event = _FastStopEvent()

        class _FakePool:
            async def close(self) -> None:
                pass

            def terminate(self) -> None:
                pass

        async def _fake_create_pool(*, dsn, min_size, max_size, command_timeout):
            create_pool_calls.append(dsn)
            return _FakePool()

        async def _fake_scan(*, run_workspace_heartbeat):
            tick_count["n"] += 1
            loop_ids_seen.append(id(asyncio.get_running_loop()))
            # The real scan reaches db.get_pool() indirectly (through
            # run_workspace_heartbeat's own DB access); this stands in for
            # that and is the actual thing MAN-265 is about.
            await db_module.get_pool()
            if tick_count["n"] >= 3:
                stop_event.set()
            return {"scanned": 0, "results": []}

        fake_asyncpg = SimpleNamespace(create_pool=_fake_create_pool)

        with (
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://fake:fake@fake-host/fake_db"}),
            patch.object(db_module, "asyncpg", fake_asyncpg),
            patch.object(db_module, "_POOLS_BY_LOOP", {}),
            patch.object(db_module, "_POOL_INIT_LOCKS_BY_LOOP", {}),
            patch.object(db_module, "_POOL_INIT_FAILED", False),
            patch.object(bounded_scheduler_service, "scan_due_wake_requests_once", _fake_scan),
        ):
            bounded_scheduler_service.run_wake_request_scan_forever(
                run_workspace_heartbeat=lambda tasks, metadata: {"acted": False},
                stop_event=stop_event,
                poll_seconds=5,
            )

        self.assertEqual(tick_count["n"], 3, "expected exactly 3 ticks to have run")
        self.assertEqual(
            len(set(loop_ids_seen)), 1,
            f"expected every tick to run on the SAME event loop object, saw "
            f"{len(set(loop_ids_seen))} distinct loop ids across ticks {loop_ids_seen}",
        )
        self.assertEqual(
            len(create_pool_calls), 1,
            f"expected asyncpg.create_pool to be called exactly ONCE across "
            f"{tick_count['n']} ticks (this is MAN-265's whole point), but it "
            f"was called {len(create_pool_calls)} times",
        )


class DelegationActorAttributionTests(unittest.IsolatedAsyncioTestCase):
    """MAN-304 (3): every delegation-class ledger event this module writes
    used to unconditionally stamp the workspace's own Sage/Operator install
    as the actor -- even for trigger kinds that already have a REAL
    specialist agent's install_id in hand (task_assigned, task_commented, a
    goal's own agent_id, a recurring schedule's own agent_id). The Inbox
    (frontend/app/(account)/w/[workspaceId]/inbox/page.tsx) resolves the
    sender purely from install_id via agentNameByInstall, so a brand-new
    user assigning their own agent a task saw "Sage" -- an entity they
    never created -- as the sender of "Delegated wake request scheduled".

    _resolve_delegation_actor is the one seam every one of these ledger
    writes now goes through."""

    def setUp(self):
        global bounded_scheduler_service

        bounded_scheduler_service = importlib.import_module("server_modules.bounded_scheduler_service")

    def test_resolve_delegation_actor_prefers_the_real_agent_when_given_one(self):
        actor_type, actor_id, install_id = bounded_scheduler_service._resolve_delegation_actor(
            master_install={"id": "install-sage"},
            attributed_agent_install_id="install-specialist-1",
        )
        self.assertEqual(actor_type, "agent")
        self.assertEqual(actor_id, "install-specialist-1")
        self.assertEqual(install_id, "install-specialist-1")

    def test_resolve_delegation_actor_falls_back_to_sage_with_no_agent(self):
        """The genuinely Sage-owned triggers (self-proposed wakeups,
        context-engine event triggers) have no per-agent install_id at all
        -- this is the ONLY case that should still resolve to Sage, and it
        must keep resolving there so those triggers don't regress."""
        actor_type, actor_id, install_id = bounded_scheduler_service._resolve_delegation_actor(
            master_install={"id": "install-sage"},
            attributed_agent_install_id=None,
        )
        self.assertEqual(actor_type, "sage")
        self.assertEqual(actor_id, "install-sage")
        self.assertEqual(install_id, "install-sage")

    def test_resolve_delegation_actor_falls_back_to_system_with_no_master_install_either(self):
        actor_type, actor_id, install_id = bounded_scheduler_service._resolve_delegation_actor(
            master_install=None,
            attributed_agent_install_id=None,
        )
        self.assertEqual(actor_type, "system")
        self.assertEqual(actor_id, "scheduler")
        self.assertIsNone(install_id)

    def test_resolve_delegation_actor_blank_string_is_treated_as_absent(self):
        actor_type, actor_id, install_id = bounded_scheduler_service._resolve_delegation_actor(
            master_install={"id": "install-sage"},
            attributed_agent_install_id="   ",
        )
        self.assertEqual(actor_type, "sage")
        self.assertEqual(install_id, "install-sage")

    async def _persist_wakeup_and_capture_ledger_call(self, *, attributed_agent_install_id):
        """Drives the REAL _persist_wakeup (not a stand-in) so the
        assertion covers the actual seam a customer's write goes through --
        only its three external dependencies (the Rust kernel gate, the
        wake-request repository write, and the activity ledger append) are
        stood in for."""
        captured: Dict[str, Any] = {}

        async def fake_append_activity_event(**kwargs):
            captured.update(kwargs)
            return {"id": "event-1"}

        policy = bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=0,
            quiet_hours_end=0,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )
        with (
            patch.object(
                bounded_scheduler_service,
                "_enforce_session_scheduler_decision",
                return_value={"next_action": "schedule_event_trigger"},
            ),
            patch.object(
                bounded_scheduler_service.control_plane_repository,
                "append_agent_scheduler_wake_request",
                new=AsyncMock(return_value={"id": "wake-1", "status": "pending"}),
            ),
            patch(
                "server_modules.activity_ledger_service.append_activity_event",
                new=fake_append_activity_event,
            ),
        ):
            await bounded_scheduler_service._persist_wakeup(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                master_install={"id": "install-sage"},
                trigger_kind="task_assigned",
                source="project_tasks",
                requested_by="owner",
                reason="task_assigned",
                summary="Task assigned: Ship the widget",
                payload={},
                policy=policy,
                due_at=datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc),
                approval_required=False,
                status="pending",
                denial_reason=None,
                metadata={},
                attributed_agent_install_id=attributed_agent_install_id,
            )
        return captured

    async def test_persist_wakeup_attributes_the_ledger_event_to_the_real_agent(self):
        captured = await self._persist_wakeup_and_capture_ledger_call(
            attributed_agent_install_id="install-specialist-1",
        )
        self.assertEqual(captured["title"], "Delegated wake request scheduled")
        self.assertEqual(captured["actor_type"], "agent")
        self.assertEqual(captured["actor_id"], "install-specialist-1")
        self.assertEqual(captured["install_id"], "install-specialist-1")
        self.assertNotEqual(captured["install_id"], "install-sage")

    async def test_persist_wakeup_still_falls_back_to_sage_when_no_agent_is_attributed(self):
        """Regression guard for the genuinely Sage-owned triggers (self-
        proposed wakeups, context-engine event triggers) -- these must keep
        attributing to Sage exactly as before this fix."""
        captured = await self._persist_wakeup_and_capture_ledger_call(
            attributed_agent_install_id=None,
        )
        self.assertEqual(captured["actor_type"], "sage")
        self.assertEqual(captured["install_id"], "install-sage")

    async def test_schedule_task_assigned_wakeup_attributes_to_the_assignee_not_sage(self):
        """End-to-end from the real call site a brand-new user hits every
        time they assign a task to their own agent."""
        captured: Dict[str, Any] = {}

        async def fake_persist(**kwargs):
            captured.update(kwargs)
            return {"id": "wake-1", "status": "pending"}

        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, self._policy())),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch("server_modules.bounded_scheduler_service._persist_wakeup", side_effect=fake_persist),
            patch("server_modules.bounded_scheduler_service._trigger_ambient_monitor", return_value={"ok": True}),
        ):
            await bounded_scheduler_service.schedule_task_assigned_wakeup(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="install-specialist-1",
                task_id="task-1",
                title="Ship the widget",
            )

        self.assertEqual(captured["attributed_agent_install_id"], "install-specialist-1")
        self.assertNotEqual(captured["attributed_agent_install_id"], "install-sage")

    async def test_schedule_task_commented_wakeup_attributes_to_the_assignee_not_sage(self):
        captured: Dict[str, Any] = {}

        async def fake_persist(**kwargs):
            captured.update(kwargs)
            return {"id": "wake-1", "status": "pending"}

        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, self._policy())),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch("server_modules.bounded_scheduler_service._persist_wakeup", side_effect=fake_persist),
            patch("server_modules.bounded_scheduler_service._trigger_ambient_monitor", return_value={"ok": True}),
        ):
            await bounded_scheduler_service.schedule_task_commented_wakeup(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="install-specialist-1",
                task_id="task-1",
                title="Ship the widget",
                comment_body="try approach B instead",
            )

        self.assertEqual(captured["attributed_agent_install_id"], "install-specialist-1")

    async def test_create_goal_attributes_both_the_wake_and_the_created_event_to_the_agent(self):
        captured_wake: Dict[str, Any] = {}
        captured_ledger_calls: list = []

        async def fake_persist(**kwargs):
            captured_wake.update(kwargs)
            return {"id": "wake-1", "status": "pending"}

        async def fake_append_activity_event(**kwargs):
            captured_ledger_calls.append(kwargs)
            return {"id": "event-1"}

        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, self._policy())),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.append_agent_goal",
                new=AsyncMock(return_value={"id": "goal-1"}),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.update_agent_goal",
                new=AsyncMock(return_value=None),
            ),
            patch("server_modules.bounded_scheduler_service._persist_wakeup", side_effect=fake_persist),
            patch("server_modules.bounded_scheduler_service._trigger_ambient_monitor", return_value={"ok": True}),
            patch(
                "server_modules.activity_ledger_service.append_activity_event",
                new=fake_append_activity_event,
            ),
        ):
            await bounded_scheduler_service.create_goal(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                project_id="project-1",
                agent_id="install-specialist-1",
                goal_text="Negotiate a better rate with the supplier.",
            )

        self.assertEqual(captured_wake["attributed_agent_install_id"], "install-specialist-1")
        goal_created_calls = [c for c in captured_ledger_calls if c.get("title") == "Goal created"]
        self.assertEqual(len(goal_created_calls), 1)
        self.assertEqual(goal_created_calls[0]["actor_type"], "agent")
        self.assertEqual(goal_created_calls[0]["install_id"], "install-specialist-1")

    async def test_create_recurring_schedule_attributes_the_created_event_to_the_agent(self):
        captured_ledger_calls: list = []

        async def fake_append_activity_event(**kwargs):
            captured_ledger_calls.append(kwargs)
            return {"id": "event-1"}

        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, self._policy())),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.append_agent_recurring_schedule",
                new=AsyncMock(return_value={"id": "schedule-1"}),
            ),
            patch(
                "server_modules.activity_ledger_service.append_activity_event",
                new=fake_append_activity_event,
            ),
        ):
            await bounded_scheduler_service.create_recurring_schedule(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="install-specialist-1",
                cron_expression="0 9 * * *",
                instruction="Check the inbox and reply to anything urgent.",
            )

        recurring_created_calls = [
            c for c in captured_ledger_calls if c.get("title") == "Recurring wake schedule created"
        ]
        self.assertEqual(len(recurring_created_calls), 1)
        self.assertEqual(recurring_created_calls[0]["actor_type"], "agent")
        self.assertEqual(recurring_created_calls[0]["install_id"], "install-specialist-1")

    async def test_maybe_schedule_event_trigger_still_attributes_to_sage_unchanged(self):
        """Control case: a context-engine event trigger has no per-agent
        install_id at all and must keep resolving to Sage exactly as before
        this fix -- confirms the fix is additive, not a blanket rename."""
        captured: Dict[str, Any] = {}

        async def fake_persist(**kwargs):
            captured.update(kwargs)
            return {"id": "wake-1", "status": "pending"}

        with (
            patch(
                "server_modules.bounded_scheduler_service._load_scheduler_scope",
                new=AsyncMock(return_value=({"metadata": {}}, {"id": "install-sage", "metadata": {}}, self._policy())),
            ),
            patch(
                "server_modules.bounded_scheduler_service.control_plane_repository.count_agent_scheduler_wake_requests_since",
                new=AsyncMock(return_value=0),
            ),
            patch("server_modules.bounded_scheduler_service._persist_wakeup", side_effect=fake_persist),
            patch("server_modules.bounded_scheduler_service._trigger_ambient_monitor", return_value={"ok": True}),
        ):
            await bounded_scheduler_service.maybe_schedule_event_trigger(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                event={
                    "id": "event-1",
                    "priority": 90,
                    "scope": {"audience": ["sage"]},
                    "event_type": "something_happened",
                    "summary": "Something happened.",
                },
            )

        self.assertNotIn("attributed_agent_install_id", captured)

    def _policy(self) -> "bounded_scheduler_service.SchedulerPolicyBounds":
        return bounded_scheduler_service.SchedulerPolicyBounds(
            quiet_hours_start=0,
            quiet_hours_end=0,
            max_event_triggers_per_hour=4,
            max_self_proposed_per_hour=2,
            max_runtime_seconds=20,
            minimum_battery_percent=20,
            require_network_online=False,
            require_owner_approval_for_privileged_wakeups=True,
            plan_tier="standard",
        )


if __name__ == "__main__":
    unittest.main()
