"""A failed turn is NOT a silence decision — the message must not vanish.

THE DEFECT (live, and shared by every OpenClaw-transported channel — see the
"which path" note below):

    you  -> "check my calendar"        turn 1 starts
    you  -> "and my email"             turn 2 starts alongside it; the
                                       provider is rate-limited / credits are
                                       out / the socket drops
         <- "here's your calendar"     turn 1 answers
                                       turn 2 raised -> _build_error_reply_dict
                                       returned {"text": ""} -> the delivery
                                       seam read that as "the agent had
                                       nothing to say" and wrote the
                                       `<channel>:noreply:` marker.

The marker is the durable record that the agent was asked and DELIBERATELY
said nothing, and it is the ONLY thing the redelivery guard at the top of
_deliver_local_bridge_personal_reply reads. `channel.inbound` is
at-least-once on every leg (ws-client's replayable outbox,
local-bridge-runtime's in-memory seen-set, the OpenClaw bridge plugin's
durable BoundedRetryQueue), so that marker is what turns "the platform failed
to answer this" into "this message is answered, never send it again". The
email ask is gone forever, and nobody — not the sender, not the owner, not the
audit ledger — is told anything different from an ordinary quiet turn.

WHICH PATH THIS IS. The seams exercised here are the cloud-side ones every
channel crosses, first-party and OpenClaw alike:
_deliver_local_bridge_personal_reply is inherited unchanged by
_OpenClawPersonalChannelHandler (via _LocalBridgePersonalChannelHandler), and
channel_adapter.filter_channel_outbound_reply — now reached through
resolve_channel_reply_outcome — is the send-boundary choke point for all of
them. This is not outgoing first-party channel work.

WHICH ENGINE. None. The defect and the fix both live strictly DOWNSTREAM of
turn execution: every test here stubs the turn at
personal_channel_sage_bridge_service.build_personal_channel_reply_async or at
agent_turn_adapter.execute_sage_turn, so _resolve_turn_engine_id is never
called and the legacy/SDK engine split cannot silently decide the result.

WHAT MUST NOT REGRESS. Suppression is unchanged for a stranger: every code in
CHANNEL_OWNER_SAFE_CODES is STILL in CHANNEL_SUPPRESSED_TEXTS, and the owner
path takes a CODE and returns a frozen module literal — it has no string
parameter, so no exception text, provider response, classified prose,
ErrorNotification.raw_detail, or secret can travel through it. Both are
asserted below (OwnerAudienceIsNarrowTests).
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import channel_adapter, platform_event
from server_modules import personal_channel_sage_bridge_service as bridge
from server_modules import personal_channels_service as pcs


# ─── 1. The classifier: three facts, not one ────────────────────────────


class ResolveChannelReplyOutcomeTests(unittest.TestCase):
    def test_real_reply_delivers(self) -> None:
        outcome = channel_adapter.resolve_channel_reply_outcome({"text": "Booked your 3pm."})
        self.assertEqual(outcome.kind, channel_adapter.DELIVER)
        self.assertEqual(outcome.text, "Booked your 3pm.")

    def test_genuinely_silent_turn_is_silent_not_undelivered(self) -> None:
        """The agent ran to completion and chose to say nothing (a group
        message that wasn't for it). That IS a decision — it must stay
        `silent` so the no-reply marker is still written and the message is
        not retried forever. Positive control: proves this file does not
        simply reclassify every quiet turn as a failure."""
        for reply in (None, {}, {"text": ""}, {"text": "[SILENT]"}):
            with self.subTest(reply=reply):
                outcome = channel_adapter.resolve_channel_reply_outcome(reply)
                self.assertEqual(outcome.kind, channel_adapter.SILENT)
                self.assertIsNone(outcome.text)

    def test_owner_gets_no_status_line_on_a_genuinely_silent_turn(self) -> None:
        outcome = channel_adapter.resolve_channel_reply_outcome({"text": ""}, is_owner=True)
        self.assertEqual(outcome.kind, channel_adapter.SILENT)
        self.assertIsNone(outcome.text)

    def test_failed_turn_is_undelivered(self) -> None:
        reply = bridge._build_error_reply_dict(RuntimeError("HTTP 429 rate limit"), "ws-1")
        outcome = channel_adapter.resolve_channel_reply_outcome(reply)
        self.assertEqual(outcome.kind, channel_adapter.UNDELIVERED)
        self.assertEqual(outcome.status_code, platform_event.CHANNEL_EXECUTION_FAILED.code)

    def test_stranger_still_gets_absolutely_nothing_on_a_failed_turn(self) -> None:
        reply = bridge._build_error_reply_dict(RuntimeError("HTTP 429 rate limit"), "ws-1")
        outcome = channel_adapter.resolve_channel_reply_outcome(reply, is_owner=False)
        self.assertIsNone(outcome.text)

    def test_owner_gets_exactly_the_frozen_literal_and_nothing_derived(self) -> None:
        secret = "sk-live-DEADBEEF-provider-said-this"
        reply = bridge._build_error_reply_dict(RuntimeError(secret), "ws-1")
        outcome = channel_adapter.resolve_channel_reply_outcome(reply, is_owner=True)
        self.assertEqual(outcome.text, platform_event.CHANNEL_EXECUTION_FAILED.channel_text)
        self.assertNotIn("DEADBEEF", outcome.text)
        self.assertNotIn(reply["error_text"], outcome.text)
        self.assertNotIn(secret, outcome.text)

    def test_smuggled_status_string_is_undelivered_not_silence(self) -> None:
        """A producer regression that returns a hardcoded status string under
        "text". Suppression still eats it (text is None for a stranger), but
        it must no longer be recorded as the agent choosing silence — the
        turn plainly did not answer."""
        outcome = channel_adapter.resolve_channel_reply_outcome(
            {"text": platform_event.GENERIC_ERROR.channel_text}
        )
        self.assertEqual(outcome.kind, channel_adapter.UNDELIVERED)
        self.assertIsNone(outcome.text)

    def test_media_only_turn_still_delivers(self) -> None:
        outcome = channel_adapter.resolve_channel_reply_outcome(
            {"text": "", "media": [{"kind": "image"}]}
        )
        self.assertEqual(outcome.kind, channel_adapter.DELIVER)
        self.assertEqual(len(outcome.media), 1)


# ─── 2. The owner allowlist is narrow, and proves it ────────────────────


class OwnerAudienceIsNarrowTests(unittest.TestCase):
    def test_owner_safe_codes_are_still_suppressed_for_everyone_else(self) -> None:
        """The stranger boundary is byte-identical: adding the owner
        allowlist did NOT remove anything from CHANNEL_SUPPRESSED_TEXTS."""
        for code in platform_event.CHANNEL_OWNER_SAFE_CODES:
            event = next(
                e for e in platform_event._all_platform_events() if e.code == code
            )
            with self.subTest(code=code):
                self.assertIn(event.channel_text.strip(), platform_event.CHANNEL_SUPPRESSED_TEXTS)
                self.assertIsNone(channel_adapter.filter_channel_outbound_reply(event.channel_text))

    def test_every_other_platform_event_is_denied_to_the_owner_too(self) -> None:
        """Default posture is DENY on the owner path as well — a new
        PlatformEvent added later is owner-suppressed until someone
        deliberately allowlists it."""
        for event in platform_event._all_platform_events():
            if event.code in platform_event.CHANNEL_OWNER_SAFE_CODES:
                continue
            with self.subTest(code=event.code):
                self.assertIsNone(platform_event.owner_channel_text_for_code(event.code))

    def test_owner_lookup_takes_a_code_and_can_never_pass_text_through(self) -> None:
        """The one structural guarantee: there is no string-in path. Anything
        that is not an exact allowlisted code returns None, so an error
        message, a provider response, or a secret cannot become a reply."""
        for hostile in (
            "sk-live-DEADBEEF",
            platform_event.AI_LIMIT_REACHED.channel_text,
            "Something went wrong. Try again.",
            "",
            "   ",
            "unknown_code",
        ):
            with self.subTest(value=hostile):
                self.assertIsNone(platform_event.owner_channel_text_for_code(hostile))

    def test_owner_allowlist_holds_only_codes_that_are_actually_produced(self) -> None:
        """No dead entries: every allowlisted code must be reachable. Today
        exactly one producer exists (_build_error_reply_dict)."""
        produced = {
            bridge._build_error_reply_dict(RuntimeError("boom"), "ws-1")[
                channel_adapter.DELIVERY_FAILED_CODE_KEY
            ]
        }
        self.assertEqual(set(platform_event.CHANNEL_OWNER_SAFE_CODES), produced)


# ─── 3. The delivery seam: a lost message, end to end ───────────────────


def _allow_decision(operation: str) -> dict:
    return {
        "ok": True,
        "decision": "allow",
        "reason": "gateway_service_operation_allowed",
        "operation": operation,
        "next_action": "dispatch_gateway_operation",
    }


_REGISTRATION = {
    "gateway_id": "gw-1",
    "workspace_id": "default",
    "tenant_id": "tenant-1",
    "device_trust_state": "trusted",
    "active_session_id": "sess-1",
}


class _FakeInboundStore:
    """Stands in for personal_channels_repository's inbound/outbound tables,
    keeping the ONE fact that matters: reply_idempotency_key. That column is
    the redelivery guard — a row with it set will never be re-run."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.outbound: dict[str, dict] = {}
        self.dispatched: list[str] = []

    def record(self, external_message_id: str) -> dict:
        row = self.rows.setdefault(
            external_message_id,
            {"external_message_id": external_message_id, "reply_idempotency_key": None},
        )
        return dict(row)

    def mark_inbound_processed(self, *, external_message_id: str, reply_idempotency_key: str, **_kw) -> dict:
        row = self.rows.setdefault(
            external_message_id,
            {"external_message_id": external_message_id, "reply_idempotency_key": None},
        )
        row["reply_idempotency_key"] = reply_idempotency_key
        return dict(row)

    def get_outbound_message(self, *, idempotency_key: str, **_kw):
        row = self.outbound.get(idempotency_key)
        return dict(row) if row else None

    def create_or_get_outbound_message(self, *, idempotency_key: str, text: str, remote_jid: str, **_kw):
        row = self.outbound.setdefault(
            idempotency_key,
            {
                "idempotency_key": idempotency_key,
                "text": text,
                "remote_jid": remote_jid,
                "status": "queued",
                "metadata": {},
            },
        )
        return dict(row), True

    def mark_outbound_delivered(self, *, idempotency_key: str, **_kw):
        row = self.outbound.get(idempotency_key)
        if row:
            row["status"] = "delivered"
        return dict(row) if row else None


class _LocalBridgeSeamHarness:
    """Drives the REAL _deliver_local_bridge_personal_reply (the function
    _OpenClawPersonalChannelHandler inherits) against a fake store."""

    def __init__(self) -> None:
        self.store = _FakeInboundStore()
        self.audits: list[dict] = []

    def _audit(self, **kwargs) -> None:
        self.audits.append(kwargs)

    async def deliver(self, external_message_id: str, *, reply_builder, is_owner: bool = False) -> dict:
        inbound = self.store.record(external_message_id)
        with (
            patch.object(
                pcs.rust_runtime_kernel_client,
                "run_runtime_kernel_enforced",
                return_value=_allow_decision("protocol_route"),
            ),
            patch.object(
                pcs.personal_channels_repository, "mark_inbound_processed",
                side_effect=self.store.mark_inbound_processed,
            ),
            patch.object(
                pcs.personal_channels_repository, "get_outbound_message",
                side_effect=self.store.get_outbound_message,
            ),
            patch.object(
                pcs.personal_channels_repository, "create_or_get_outbound_message",
                side_effect=self.store.create_or_get_outbound_message,
            ),
            patch.object(
                pcs.personal_channels_repository, "mark_outbound_delivered",
                side_effect=self.store.mark_outbound_delivered,
            ),
            patch.object(
                pcs.personal_channel_sage_bridge_service,
                "build_personal_channel_reply_async",
                new=AsyncMock(side_effect=reply_builder),
            ),
            patch.object(pcs, "_emit_automatic_reply_audit", side_effect=self._audit),
            patch.object(
                pcs.gateway_protocol_service, "dispatch_channel_outbound",
                new=AsyncMock(side_effect=self._dispatch),
            ),
        ):
            return await pcs._deliver_local_bridge_personal_reply(
                gateway_id="gw-1",
                registration=_REGISTRATION,
                inbound=inbound,
                remote_jid="+15551234567",
                external_message_id=external_message_id,
                text="and my email",
                push_name=None,
                duplicate=False,
                channel_key="openclaw_telegram",
                provider="openclaw",
                label="Telegram",
                agent_id="agent-1",
                is_owner=is_owner,
            )

    async def _dispatch(self, *, text: str, **_kw) -> dict:
        self.store.dispatched.append(text)
        return {"external_message_id": f"out-{len(self.store.dispatched)}"}

    def marker_for(self, external_message_id: str):
        return self.store.rows[external_message_id]["reply_idempotency_key"]

    def audit_statuses(self) -> list[str]:
        return [a.get("status") for a in self.audits]


async def _ok_reply(**_kw) -> dict:
    return {"text": "Here's your calendar.", "source": "agent_turn_adapter"}


async def _failed_reply(**_kw) -> dict:
    # Exactly what the live path produces when the turn raises — credits
    # exhausted, provider unreachable, context overflow, anything.
    return bridge._build_error_reply_dict(RuntimeError("HTTP 429 rate limit"), "default")


class SecondMessageMidTurnIsNotLostTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_second_message_is_not_recorded_as_deliberate_silence(self) -> None:
        """THE regression. Without the fix the no-reply marker is written and
        the message is permanently recorded as answered."""
        harness = _LocalBridgeSeamHarness()
        await harness.deliver("msg-1", reply_builder=_ok_reply)
        result = await harness.deliver("msg-2", reply_builder=_failed_reply)

        self.assertIsNone(result["outbound"], "nothing may leak into a stranger's chat")
        self.assertIsNone(
            harness.marker_for("msg-2"),
            "a turn that never completed must NOT be marked answered — that marker "
            "is the redelivery guard, and writing it is what loses the message",
        )
        self.assertIn("failed", harness.audit_statuses())

    async def test_the_lost_message_actually_gets_answered_on_redelivery(self) -> None:
        """End to end: because no marker was written, the at-least-once
        redelivery re-runs the turn and the person finally gets their
        answer. Without the fix the redelivery short-circuits on the marker
        and returns immediately, sending nothing, forever."""
        harness = _LocalBridgeSeamHarness()
        await harness.deliver("msg-2", reply_builder=_failed_reply)
        self.assertEqual(harness.store.dispatched, [])

        await harness.deliver("msg-2", reply_builder=_ok_reply)
        self.assertEqual(harness.store.dispatched, ["Here's your calendar."])
        self.assertIsNotNone(harness.marker_for("msg-2"))

    async def test_genuine_silence_still_writes_the_marker_and_is_never_retried(self) -> None:
        """Positive control for the SAME mechanism: a deliberate silence must
        still be recorded, or a group message the agent correctly ignored
        would be re-run on every redelivery."""
        harness = _LocalBridgeSeamHarness()

        async def _silent(**_kw):
            return None

        await harness.deliver("msg-3", reply_builder=_silent)
        marker = harness.marker_for("msg-3")
        self.assertIsNotNone(marker)
        self.assertTrue(str(marker).startswith("openclaw_telegram:noreply:"))
        self.assertEqual(harness.audit_statuses(), ["skipped"])

    async def test_owner_is_told_their_own_agent_failed(self) -> None:
        """An owner texting their own agent must be able to tell "it broke"
        from "it ignored me". They get the frozen literal — never the
        classified error prose, never the exception."""
        harness = _LocalBridgeSeamHarness()
        await harness.deliver("msg-4", reply_builder=_failed_reply, is_owner=True)
        self.assertEqual(
            harness.store.dispatched,
            [platform_event.CHANNEL_EXECUTION_FAILED.channel_text],
        )

    async def test_stranger_is_told_nothing_at_all(self) -> None:
        harness = _LocalBridgeSeamHarness()
        await harness.deliver("msg-5", reply_builder=_failed_reply, is_owner=False)
        self.assertEqual(harness.store.dispatched, [])

    async def test_two_messages_in_flight_at_once_neither_is_lost(self) -> None:
        """The literal shape from the bug report: a second message arrives
        while the first turn is still running. The first succeeds, the second
        hits the provider failure the burst caused. Both must end up either
        answered or retriable — never marked answered-and-silent."""
        harness = _LocalBridgeSeamHarness()
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def _slow_ok(**_kw):
            first_started.set()
            await release_first.wait()
            return {"text": "Here's your calendar.", "source": "agent_turn_adapter"}

        task_one = asyncio.create_task(harness.deliver("msg-a", reply_builder=_slow_ok))
        await first_started.wait()
        # msg-b arrives MID-TURN.
        await harness.deliver("msg-b", reply_builder=_failed_reply)
        release_first.set()
        await task_one

        self.assertEqual(harness.store.dispatched, ["Here's your calendar."])
        self.assertIsNotNone(harness.marker_for("msg-a"), "the answered message is settled")
        self.assertIsNone(
            harness.marker_for("msg-b"),
            "the mid-turn message must stay retriable, not be recorded as answered",
        )


# ─── 4. Structural drift — the NEXT seam cannot reintroduce this ────────


class ChannelReplyOutcomeDriftTests(unittest.TestCase):
    """A behavioural test can only cover the seams that exist today. This one
    catches a fourth delivery seam written next month, which would type-check
    and behave perfectly while quietly conflating failure with silence
    again."""

    def _module_source(self) -> str:
        path = pathlib.Path(pcs.__file__)
        return path.read_text(encoding="utf-8")

    def test_personal_channels_service_never_calls_the_raw_filter_directly(self) -> None:
        tree = ast.parse(self._module_source())
        direct = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "filter_channel_outbound_reply")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "filter_channel_outbound_reply"
                )
            )
        ]
        self.assertEqual(
            direct,
            [],
            "every delivery seam in personal_channels_service.py must go through "
            "channel_adapter.resolve_channel_reply_outcome(), which decides "
            "deliver/silent/undelivered — calling the filter directly loses that "
            f"third case again (lines {direct})",
        )

    def test_every_delivery_seam_resolves_an_outcome(self) -> None:
        tree = ast.parse(self._module_source())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "resolve_channel_reply_outcome"
        ]
        self.assertEqual(len(calls), 4, "whatsapp / telegram / local-bridge+openclaw / cloud")
        for node in calls:
            kwargs = {kw.arg for kw in node.keywords}
            self.assertIn(
                "is_owner",
                kwargs,
                "is_owner must be threaded explicitly at every seam — defaulting it "
                "silently downgrades the owner to a stranger",
            )


if __name__ == "__main__":
    unittest.main()
