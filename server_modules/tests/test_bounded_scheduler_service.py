import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

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

        self.assertTrue(snapshot["active"])
        self.assertIn("Quiet hours active until", snapshot["label"])
        self.assertTrue(snapshot["next_allowed_at"].endswith("Z"))


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


if __name__ == "__main__":
    unittest.main()
