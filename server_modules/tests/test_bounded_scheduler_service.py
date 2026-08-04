import unittest
from unittest.mock import AsyncMock, patch
import importlib
from datetime import datetime, timezone
from typing import Any, Dict

from server_modules import bounded_scheduler_service, fleet_tools


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


if __name__ == "__main__":
    unittest.main()
