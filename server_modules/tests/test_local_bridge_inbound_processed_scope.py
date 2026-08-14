"""Regression tests for the agent-id scope of the local-bridge inbound
"processed / no-reply" marker.

THE BUG (fixed 2026-08-08). `_handle_local_bridge_gateway_channel_inbound`
records its inbound row AGENT-SCOPED:

    record_inbound_message(gateway_id, channel_key, agent_id=<real agent>, ...)

but `_deliver_local_bridge_personal_reply` had no agent_id parameter at all,
so every one of its four `mark_inbound_processed` calls defaulted to
`LEGACY_UNSCOPED_AGENT_ID` (""). That UPDATE is scoped by
(gateway_id, channel_key, agent_id, external_message_id), so it matched ZERO
rows and `reply_idempotency_key` / `processed_at` stayed NULL forever.

WHY IT IS A SAFETY BUG, NOT BOOKKEEPING. The no-reply marker is the durable
record that the agent was asked and DELIBERATELY SAID NOTHING, and the guard
at the top of `_deliver_local_bridge_personal_reply` is its only reader.
`channel.inbound` is at-least-once by design on every leg of this transport
(GatewayWsClient.publishEvent re-enqueues into a replayable outbox when the
socket is down; local-bridge-runtime's seen-event set is in-memory and dies
with the gateway process; the OpenClaw bridge plugin retries through its own
durable BoundedRetryQueue). With a blank marker, a redelivered message
re-ran the turn — and a second ask can answer where the first chose silence.
That is the same "SILENCE IS A DECISION, NOT A FAILURE" invariant
personal_channel_sage_bridge_service enforces one layer down, defeated from
the persistence layer instead. It is the shape of the incident where an
agent replied unprompted in a public group.

THESE TESTS DELIBERATELY DO NOT READ THE UNION OF BOTH AGENT SCOPES.
test_openclaw_channel_outbound.py's `_all_rows` merges the real agent scope
and the legacy blank scope on purpose, so it stays green either way — that
is why the bug survived. Everything below asserts the EXACT scope, and
`test_no_row_is_ever_written_under_the_legacy_blank_scope` asserts the blank
scope is empty rather than merely not-preferred.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from server_modules import personal_channels_service, personal_channels_repository


GATEWAY_ID = "gw-scope-1"
AGENT_ID = "agent-scope-1"
# signal_personal/signal_local_bridge (the first-party local-bridge
# channel/provider) DELETED 2026-08-14 (full OpenClaw channel cutover);
# openclaw_signal/openclaw is its live replacement and exercises the
# identical scoped-write contract this file tests.
CHANNEL_KEY = "openclaw_signal"
PROVIDER = "openclaw"
EXTERNAL_MESSAGE_ID = "sig-scope-1"

_ALLOW_DISPATCH_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "gateway_service_operation_allowed",
    "operation": "protocol_route",
    "next_action": "dispatch_gateway_operation",
}

_ALLOWED_DM_DECISION = {
    "allowed": True,
    "mode": "owner_only",
    "sender_id": "+15551234567",
    "is_owner": True,
    "system_reply": None,
    "config_changed": False,
}


class _FakeAgentInstallStore:
    """Same minimal stand-in used by test_local_bridge_agent_identity.py —
    enough of agent_registry_repository's install-bundle contract for the
    group/dm policy config readers to round-trip against."""

    def __init__(self) -> None:
        self.installs: Dict[str, Dict[str, Any]] = {}

    async def get_bundle(self, agent_id: str, *, tenant_id: str, workspace_id: str):
        return {"id": agent_id, "install_metadata": dict(self.installs.get(agent_id, {}))}

    async def update(self, agent_id: str, *, tenant_id: str, workspace_id: str, metadata=None, **_kwargs):
        merged = {**self.installs.get(agent_id, {}), **dict(metadata or {})}
        self.installs[agent_id] = merged
        return {"id": agent_id, "install_metadata": dict(merged)}


def _patch_agent_install_store(store: "_FakeAgentInstallStore"):
    return patch.multiple(
        "server_modules.agent_registry_repository",
        get_workspace_agent_install_bundle=AsyncMock(side_effect=store.get_bundle),
        update_workspace_agent_install=AsyncMock(side_effect=store.update),
    )


class LocalBridgeInboundProcessedScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global personal_channels_service, personal_channels_repository
        personal_channels_service = importlib.import_module("server_modules.personal_channels_service")
        personal_channels_repository = importlib.import_module("server_modules.personal_channels_repository")

        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        personal_channels_repository.init_personal_channels_db(db_path)
        self.db_patcher = patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", db_path)
        self.db_patcher.start()

        self.registration = {
            "gateway_id": GATEWAY_ID,
            "workspace_id": "ws-scope",
            "tenant_id": "tenant-scope",
            "device_trust_state": "trusted",
            "active_session_id": "sess-1",
        }
        # Claim (gateway, channel) for a REAL agent id, so
        # _resolve_local_bridge_agent_id's fast path resolves it and
        # record_inbound_message writes an agent-scoped row.
        personal_channels_repository.upsert_local_bridge_state(
            gateway_id=GATEWAY_ID,
            tenant_id="tenant-scope",
            workspace_id="ws-scope",
            user_id="",
            channel_key=CHANNEL_KEY,
            agent_id=AGENT_ID,
            provider=PROVIDER,
            status="linked",
        )
        self.store = _FakeAgentInstallStore()

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    # ── helpers ──────────────────────────────────────────────────────

    def _payload(self) -> Dict[str, Any]:
        return {
            "provider": PROVIDER,
            "message": {
                "external_message_id": EXTERNAL_MESSAGE_ID,
                "remote_jid": "+15551234567",
                "sender_jid": "+15551234567",
                "push_name": "Mansur",
                "text": "you around?",
                "from_me": False,
                "is_group": False,
            },
        }

    def _inbound_rows(self, agent_id: str) -> List[Dict[str, Any]]:
        """Rows for exactly ONE agent scope. Never a union — see module
        docstring."""
        return personal_channels_repository.list_recent_gateway_messages(
            GATEWAY_ID, channel_key=CHANNEL_KEY, agent_id=agent_id, limit=25
        )["inbound"]

    async def _deliver(self, *, reply_side_effect: List[Optional[Dict[str, Any]]]):
        """Run the LIVE handler once per entry in reply_side_effect, each
        time with the SAME external_message_id — i.e. the same platform
        message redelivered, which every leg of this transport can do.

        Returns (reply_builder_mock, dispatch_mock)."""
        reply_mock = AsyncMock(side_effect=list(reply_side_effect))
        dispatch_mock = AsyncMock(return_value={"external_message_id": "sig-out-1"})
        with (
            _patch_agent_install_store(self.store),
            patch(
                "server_modules.personal_channels_service.rust_runtime_kernel_client.run_runtime_kernel_enforced",
                return_value=_ALLOW_DISPATCH_DECISION,
            ),
            patch(
                "server_modules.personal_channels_service._enforce_dm_policy",
                new=AsyncMock(return_value=dict(_ALLOWED_DM_DECISION)),
            ),
            patch(
                "server_modules.personal_channels_service.personal_channel_sage_bridge_service"
                ".build_personal_channel_reply_async",
                new=reply_mock,
                create=True,
            ),
            patch(
                "server_modules.personal_channels_service.gateway_protocol_service.dispatch_channel_outbound",
                new=dispatch_mock,
                create=True,
            ),
            patch("server_modules.personal_channels_service.security_audit_service.emit_security_audit_event"),
        ):
            for _ in reply_side_effect:
                await personal_channels_service._handle_local_bridge_gateway_channel_inbound(
                    gateway_id=GATEWAY_ID,
                    registration=self.registration,
                    payload=self._payload(),
                    channel_key=CHANNEL_KEY,
                    provider=PROVIDER,
                    label="Signal",
                )
        return reply_mock, dispatch_mock

    # ── the scope assertion ──────────────────────────────────────────

    async def test_silent_turn_marks_the_row_under_the_real_agent_id(self) -> None:
        """The no-reply marker must land on the SAME row
        record_inbound_message wrote — the agent-scoped one."""
        await self._deliver(reply_side_effect=[None])

        rows = self._inbound_rows(AGENT_ID)
        self.assertEqual(len(rows), 1, "inbound row must exist under the real agent id")
        row = rows[0]
        self.assertEqual(
            str(row.get("reply_idempotency_key") or ""),
            f"{CHANNEL_KEY}:noreply:{EXTERNAL_MESSAGE_ID}",
            "the agent-scoped row must carry the no-reply marker; before the fix "
            "mark_inbound_processed updated the legacy blank scope and matched zero rows",
        )
        self.assertIsNotNone(row.get("processed_at"), "processed_at must be set on the agent-scoped row")

    async def test_no_row_is_ever_written_under_the_legacy_blank_scope(self) -> None:
        """The blank scope must be EMPTY, not merely secondary. Reading the
        union of both scopes is what let this bug survive."""
        await self._deliver(reply_side_effect=[None])

        self.assertEqual(
            self._inbound_rows(personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID),
            [],
            "nothing on this path may address the legacy unscoped agent id",
        )

    # ── the product-safety assertion ─────────────────────────────────

    async def test_redelivery_after_a_silent_turn_never_re_asks_and_never_sends(self) -> None:
        """The one that matters.

        First delivery: the agent returns nothing — silence is the decision.
        Second delivery of the SAME external_message_id (a gateway restart,
        an outbox replay, an OpenClaw retry): the persisted marker must
        short-circuit it.

        Both assertions are load-bearing, and per this codebase's own rule a
        test asserting an ABSENCE must assert the call count too — otherwise
        it cannot tell "nothing happened" from "something else happened".

        Without the fix the marker never persists, so the model is asked a
        SECOND time, answers this time, and a message the agent had already
        declined to send goes out over a real channel.
        """
        reply_mock, dispatch_mock = await self._deliver(
            reply_side_effect=[
                None,  # first ask: deliberate silence
                {"text": "Hello! How can I help you today?", "source": "sage"},  # a second ask would answer
            ]
        )

        self.assertEqual(
            reply_mock.await_count,
            1,
            "a redelivered message must not re-ask the model after a recorded silent turn",
        )
        self.assertEqual(
            dispatch_mock.await_count,
            0,
            "no outbound message may ever be sent for an inbound the agent already answered with silence",
        )

    async def test_delivered_reply_is_marked_under_the_real_agent_id_too(self) -> None:
        """Same scope contract on the path that DOES reply — the marker
        there is what stops a replay re-running a turn that already sent."""
        _, dispatch_mock = await self._deliver(
            reply_side_effect=[{"text": "I am here.", "source": "sage"}]
        )
        self.assertEqual(dispatch_mock.await_count, 1)

        rows = self._inbound_rows(AGENT_ID)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            str(rows[0].get("reply_idempotency_key") or ""),
            f"{CHANNEL_KEY}:{EXTERNAL_MESSAGE_ID}",
        )
        self.assertIsNotNone(rows[0].get("processed_at"))
        self.assertEqual(self._inbound_rows(personal_channels_repository.LEGACY_UNSCOPED_AGENT_ID), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
