"""Tests for MAN-134's metering half: agent_computer_metering_service.py.

Two things this file proves:

  (A) agent_computer_hourly_credit_debit_plan is a pure function with the
      exact hour-bucket/request_id math the ticket specifies
      (request_id = f"agent_computer:{vps_id}:{hour_bucket}"), and it
      returns [] rather than inventing a price for an unpriced size.

  (B) THE DOUBLE-RUN IDEMPOTENCY PROOF the ticket asks for: running
      run_agent_computer_metering_sweep() twice against the same VPS record
      (one completed hour of runtime) must call the real debit primitive
      EXACTLY ONCE, not twice with the second short-circuiting to
      "already_recorded" -- see CLAUDE.md's "a money path needs a call-COUNT
      assertion" entry (test_default_engine_credit_debit.py's own == 1
      assertion is the reference this mirrors). meter_one_agent_computer's
      high-water-mark bookkeeping (persisted via a fake update_vps_record)
      is what makes call_count == 1 achievable across two SEPARATE sweep
      invocations, rather than relying solely on the debit primitive's own
      request_id dedup (which would still produce call_count == 2, with the
      second call merely being a no-op).
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from server_modules import agent_computer_metering_service as metering
from server_modules import agent_computers_repository


def _run(coro):
    return asyncio.run(coro)


class AgentComputerHourlyCreditDebitPlanTests(unittest.TestCase):
    """Pure, DB-free plan function -- mirrors
    runtime_usage_credit_debit_plan's own test shape."""

    def test_unpriced_size_never_produces_a_plan(self):
        plans = metering.agent_computer_hourly_credit_debit_plan(
            vps_id="vps-1",
            provider="digitalocean",
            size="s-999vcpu-not-a-real-size",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            created_at="2026-08-01T00:00:00Z",
            now="2026-08-01T05:00:00Z",
        )
        self.assertEqual(plans, [])

    def test_unknown_provider_never_produces_a_plan(self):
        plans = metering.agent_computer_hourly_credit_debit_plan(
            vps_id="vps-1",
            provider="hetzner",
            size="s-1vcpu-2gb",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            created_at="2026-08-01T00:00:00Z",
            now="2026-08-01T05:00:00Z",
        )
        self.assertEqual(plans, [])

    def test_less_than_one_hour_elapsed_produces_no_plan(self):
        plans = metering.agent_computer_hourly_credit_debit_plan(
            vps_id="vps-1",
            provider="digitalocean",
            size="s-1vcpu-2gb",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            created_at="2026-08-01T00:00:00Z",
            now="2026-08-01T00:45:00Z",
        )
        self.assertEqual(plans, [])

    def test_three_complete_hours_produce_three_buckets_with_stable_request_ids(self):
        plans = metering.agent_computer_hourly_credit_debit_plan(
            vps_id="vps-abc",
            provider="digitalocean",
            size="s-1vcpu-2gb",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            created_at="2026-08-01T00:00:00Z",
            now="2026-08-01T03:30:00Z",
        )
        self.assertEqual(len(plans), 3)
        self.assertEqual(
            [p["request_id"] for p in plans],
            [
                "agent_computer:vps-abc:0",
                "agent_computer:vps-abc:1",
                "agent_computer:vps-abc:2",
            ],
        )
        for plan in plans:
            self.assertGreater(plan["credits_to_charge"], 0)
            self.assertEqual(plan["source"], "agent_computer_hourly")

    def test_last_billed_hour_bucket_skips_already_billed_buckets(self):
        plans = metering.agent_computer_hourly_credit_debit_plan(
            vps_id="vps-abc",
            provider="digitalocean",
            size="s-1vcpu-2gb",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            created_at="2026-08-01T00:00:00Z",
            now="2026-08-01T03:30:00Z",
            last_billed_hour_bucket=1,
        )
        self.assertEqual([p["request_id"] for p in plans], ["agent_computer:vps-abc:2"])

    def test_all_buckets_already_billed_produces_no_plan(self):
        plans = metering.agent_computer_hourly_credit_debit_plan(
            vps_id="vps-abc",
            provider="digitalocean",
            size="s-1vcpu-2gb",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            created_at="2026-08-01T00:00:00Z",
            now="2026-08-01T03:30:00Z",
            last_billed_hour_bucket=2,
        )
        self.assertEqual(plans, [])

    def test_repeated_call_with_same_inputs_produces_the_same_request_ids(self):
        """Stability proof: the same (vps_id, created_at, now) always yields
        the same request_id set -- this is what makes the debit primitive's
        own idempotency effective at all."""
        kwargs = dict(
            vps_id="vps-stable",
            provider="digitalocean",
            size="s-2vcpu-4gb",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            created_at="2026-08-01T00:00:00Z",
            now="2026-08-01T02:15:00Z",
        )
        first = metering.agent_computer_hourly_credit_debit_plan(**kwargs)
        second = metering.agent_computer_hourly_credit_debit_plan(**kwargs)
        self.assertEqual(
            [p["request_id"] for p in first],
            [p["request_id"] for p in second],
        )


class _FakeVpsRecordStore:
    """A tiny in-memory stand-in for agent_computers_repository, just
    enough surface for meter_one_agent_computer / run_agent_computer_
    metering_sweep to drive: list_active_vps_for_metering + update_vps_record
    that actually MERGES metadata, mirroring the real repository's
    documented merge behavior (metadata || $4::jsonb)."""

    def __init__(self, records):
        self._records = {r["vps_id"]: dict(r) for r in records}
        self.update_calls = 0

    async def list_active_vps_for_metering(self):
        return [dict(r) for r in self._records.values()]

    async def update_vps_record(self, vps_id, *, metadata_updates=None, **_kwargs):
        self.update_calls += 1
        record = self._records.get(vps_id)
        if record is None:
            return None
        merged_metering = dict(record.get("metering") or {})
        merged_metering.update((metadata_updates or {}).get("metering") or {})
        record["metering"] = merged_metering
        return dict(record)


class DoubleRunIdempotencyTests(unittest.TestCase):
    """THE proof the ticket asks for: run the sweep twice, assert the real
    debit primitive was called exactly once."""

    def _one_hour_old_vps_record(self):
        created_at = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=5)).isoformat().replace("+00:00", "Z")
        return {
            "vps_id": "vps-idempotency-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "connected",
            "provider": "digitalocean",
            "size": "s-1vcpu-2gb",
            "created_at": created_at,
            "metering": {},
        }

    def test_running_the_sweep_twice_debits_exactly_once(self):
        fake_store = _FakeVpsRecordStore([self._one_hour_old_vps_record()])
        mock_debit = AsyncMock(return_value={"ok": True, "credits_debited": 2, "debited_usd": 0.02})

        with (
            patch.object(agent_computers_repository, "list_active_vps_for_metering", fake_store.list_active_vps_for_metering),
            patch.object(agent_computers_repository, "update_vps_record", fake_store.update_vps_record),
            patch.object(metering.control_plane_repository, "debit_workspace_credits_for_turn_atomic", mock_debit),
        ):
            first_result = _run(metering.run_agent_computer_metering_sweep())
            second_result = _run(metering.run_agent_computer_metering_sweep())

        self.assertEqual(mock_debit.call_count, 1)
        self.assertEqual(first_result.vps_billed, 1)
        self.assertEqual(second_result.vps_billed, 0)
        # The high-water mark was actually persisted (not just computed and
        # discarded) -- this is WHY the second sweep skipped the bucket.
        self.assertEqual(fake_store.update_calls, 1)
        self.assertEqual(
            fake_store._records["vps-idempotency-1"]["metering"]["last_billed_hour_bucket"], 0
        )

    def test_two_complete_hours_then_a_second_sweep_only_bills_the_new_one(self):
        record = self._one_hour_old_vps_record()
        # Back-date created_at so TWO hours are complete on the first sweep.
        record["created_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=2, minutes=5)
        ).isoformat().replace("+00:00", "Z")
        fake_store = _FakeVpsRecordStore([record])
        mock_debit = AsyncMock(return_value={"ok": True, "credits_debited": 2, "debited_usd": 0.02})

        with (
            patch.object(agent_computers_repository, "list_active_vps_for_metering", fake_store.list_active_vps_for_metering),
            patch.object(agent_computers_repository, "update_vps_record", fake_store.update_vps_record),
            patch.object(metering.control_plane_repository, "debit_workspace_credits_for_turn_atomic", mock_debit),
        ):
            first_result = _run(metering.run_agent_computer_metering_sweep())
            # Re-run immediately (no time passed): must be a pure no-op.
            second_result = _run(metering.run_agent_computer_metering_sweep())

        # First sweep bills both completed hours (buckets 0 and 1).
        self.assertEqual(mock_debit.call_count, 2)
        self.assertEqual(first_result.vps_billed, 1)
        self.assertEqual(second_result.vps_billed, 0)


class UnpricedSizeReportingTests(unittest.TestCase):
    def test_unpriced_size_is_reported_not_billed(self):
        record = {
            "vps_id": "vps-unpriced-1",
            "workspace_id": "ws-1",
            "tenant_id": "tenant-1",
            "status": "connected",
            "provider": "digitalocean",
            "size": "s-999vcpu-not-a-real-size",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "metering": {},
        }
        fake_store = _FakeVpsRecordStore([record])
        mock_debit = AsyncMock(return_value={"ok": True, "credits_debited": 2, "debited_usd": 0.02})

        with (
            patch.object(agent_computers_repository, "list_active_vps_for_metering", fake_store.list_active_vps_for_metering),
            patch.object(agent_computers_repository, "update_vps_record", fake_store.update_vps_record),
            patch.object(metering.control_plane_repository, "debit_workspace_credits_for_turn_atomic", mock_debit),
        ):
            result = _run(metering.run_agent_computer_metering_sweep())

        mock_debit.assert_not_called()
        self.assertEqual(result.vps_billed, 0)
        self.assertEqual(len(result.unpriced), 1)
        self.assertEqual(result.unpriced[0]["vps_id"], "vps-unpriced-1")


if __name__ == "__main__":
    unittest.main()
