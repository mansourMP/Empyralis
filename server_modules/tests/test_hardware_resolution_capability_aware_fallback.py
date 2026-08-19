"""Capability-aware owner-scoped gateway fallback, and a distinct
"placement revoked" report (MAN-356 follow-up, 2026-08-19).

THE BUG THIS LOCKS DOWN
------------------------
Confirmed live on production 2026-08-19 against agent Vale
(workspace ws_c4601e47c95a): the owner of that workspace has TWO gateways —
`gateway_a1c6b043...` (his own Mac, 42 capabilities including shell.execute)
and `gateway_00990ea4...` (a production Linux channels box, 40 capabilities,
NONE of shell/docker/computer). Vale's `preferred_gateway_id` names the Mac.

`_resolve_direct_tool_gateway_id`'s step 3 — "no placement resolved, fall
back to a box the ASKING PERSON owns" (`_resolve_live_gateway_owned_by`) —
picked WHICHEVER of the owner's live gateways it found first, with ZERO
regard for whether that box could run the capability actually being asked
for. The instant the Mac's own placement went momentarily unusable (a brief
disconnect, a workspace/status hiccup), a turn could silently land on the
Linux box instead — which structurally can NEVER run shell.execute — and
report `gateway_capability_missing`, which `gateway_reason_messages.py`
renders as "Docker isn't running on this machine. Start Docker Desktop,
then retry." for ANY gateway that doesn't declare shell.execute at all,
whether or not Docker is the real reason.

Proved directly against production's own registration data (see the fixed
capability strings below, taken from a read-only SSH pull): feeding
`gateway_00990ea4`'s real registration through the unmodified
`gateway_execution_service.gateway_registration_execution_readiness` for
`shell.execute` returns `(False, "gateway_capability_missing")`; the same
call against `gateway_a1c6b043`'s real registration returns `(True, "")`.
So "the fallback landed on the wrong box" and "the exact observed symptom"
are the same fact, not two coincidences.

Second, SEPARATE finding from the same investigation: three placed agents
(Alder, Jasper, Nova) point at gateways whose `status`/`device_trust_state`
is `revoked` — a pairing that can NEVER reconnect, ever, without the owner
re-pairing the machine. `_resolve_direct_tool_gateway_id` correctly treats a
revoked placement as unusable and returns None (or falls through to step 3)
— but nothing distinguished that from an ordinary, retriable "offline",
so a person reading "Agent Computer offline" would reasonably try
reconnecting a machine that structurally cannot reconnect.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server_modules import (  # noqa: F401  (binds package attrs for patch())
    gateway_protocol_service,
    gateway_state_repository,
    skills_service,
)

WS = "ws-1"
OWNER_ID = "u-owner"

MAC_BOX = "gateway_a1c6b043-mac"
LINUX_BOX = "gateway_00990ea4-linux"


def _session_ctx(*, user_id, gateway_id=None, hardware_access=None):
    metadata = {"source": "sage_chat", "surface": "sage", "user_id": user_id or None}
    if gateway_id:
        metadata["gateway_id"] = gateway_id
    if hardware_access:
        metadata["agent_hardware_access"] = hardware_access
    return {
        "metadata": metadata,
        "sender_id": user_id or "",
        "agent_turn_request": {
            "tenant_id": "tenant-1",
            "workspace_id": WS,
            "context_hints": {"metadata": dict(metadata)},
            "policy_context": {},
        },
    }


def _registration(gateway_id, user_id, *, status="active", device_trust_state="verified", capabilities=None):
    return {
        "gateway_id": gateway_id,
        "user_id": user_id,
        "workspace_id": WS,
        "status": status,
        "device_trust_state": device_trust_state,
        "capabilities": list(capabilities or []),
        "metadata": {},
    }


class _ResolverHarness(unittest.TestCase):
    """Drives the REAL resolver against a stand-in gateway registry — same
    harness shape as test_execution_follows_placement.py's, extended with
    `capability_id`."""

    def _resolve(self, session_ctx, *, registrations, live=None, requested=None, capability_id=""):
        live_set = set(live if live is not None else [r["gateway_id"] for r in registrations])
        by_id = {r["gateway_id"]: r for r in registrations}

        repo = SimpleNamespace(
            list_workspace_gateway_registrations=lambda ws, include_revoked=False: [
                r for r in registrations if r["workspace_id"] == ws
            ],
            get_gateway_registration=lambda gid: by_id.get(gid),
        )
        proto = SimpleNamespace(gateway_connection_is_live=lambda gid: gid in live_set)

        with patch("server_modules.gateway_protocol_service", proto), patch(
            "server_modules.gateway_state_repository", repo
        ):
            return skills_service._resolve_direct_tool_gateway_id(
                WS,
                session_ctx=session_ctx,
                requested_gateway_id=requested,
                capability_id=capability_id,
            )


class OwnerFallbackIsCapabilityAwareTests(_ResolverHarness):
    """The exact production shape: the agent's own placement (MAC_BOX) is
    momentarily unusable/unreachable, and the owner also has a second,
    incapable box (LINUX_BOX) that happens to be live."""

    def test_an_unusable_placement_falls_back_to_a_box_that_can_actually_run_the_capability(self):
        """MAC_BOX is registered but not LIVE right now (a real, momentary
        disconnect) — so step 2 fails and step 3 runs. LINUX_BOX is live but
        cannot run shell.execute; a THIRD box the same owner has can. The
        fallback must never land on the incapable one when a capable
        alternative exists."""
        capable_second_box = "gateway_second_mac"
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID, gateway_id=MAC_BOX, hardware_access="gateway"),
            registrations=[
                _registration(MAC_BOX, OWNER_ID, capabilities=["shell.execute", "filesystem.read_write"]),
                _registration(LINUX_BOX, OWNER_ID, capabilities=["channel.openclaw.telegram"]),
                _registration(capable_second_box, OWNER_ID, capabilities=["shell.execute"]),
            ],
            live=[LINUX_BOX, capable_second_box],  # MAC_BOX (the real placement) is down
            capability_id="shell.execute",
        )
        self.assertEqual(
            got, capable_second_box,
            "the fallback landed on a gateway that can never run shell.execute "
            "instead of skipping it for one that can",
        )

    def test_an_unusable_placement_with_no_capable_alternative_returns_none_not_the_wrong_box(self):
        """When NOTHING the owner has can do this, the honest answer is no
        machine at all — never a silent handoff to an incapable one."""
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID, gateway_id=MAC_BOX, hardware_access="gateway"),
            registrations=[
                _registration(MAC_BOX, OWNER_ID, capabilities=["shell.execute"]),
                _registration(LINUX_BOX, OWNER_ID, capabilities=["channel.openclaw.telegram"]),
            ],
            live=[LINUX_BOX],
            capability_id="shell.execute",
        )
        self.assertIsNone(got)

    def test_reproduces_the_exact_production_symptom_when_unfixed_reason_code(self):
        """Feeds the REAL readiness function (not a mock) production's own
        gateway_00990ea4-shaped registration for shell.execute, proving the
        reported reason ("gateway_capability_missing") is exactly what a
        wrong-gateway resolution produces — the root-cause link this whole
        test file exists to lock down."""
        from server_modules import gateway_execution_service

        linux_registration = _registration(
            LINUX_BOX, OWNER_ID,
            capabilities=[
                "browser.session.action", "external_agent_proxy", "filesystem.read_write",
                "llm.generate", "gateway.self_update",
            ],
        )
        ready, reason = gateway_execution_service.gateway_registration_execution_readiness(
            linux_registration, workspace_id=WS, capability_id="shell.execute",
        )
        self.assertFalse(ready)
        self.assertEqual(reason, "gateway_capability_missing")

        # The Mac's own real capability set (declared) does NOT hit the same
        # wall — capability discovery, not connection liveness, is the axis
        # under test here (liveness is exercised end-to-end by the resolver
        # tests above).
        mac_registration = _registration(MAC_BOX, OWNER_ID, capabilities=["shell.execute", "filesystem.read_write"])
        self.assertTrue(
            gateway_execution_service._has_gateway_capability(mac_registration, "shell.execute")
        )

    def test_the_agents_own_explicit_placement_is_never_capability_filtered(self):
        """PLACEMENT IS THE CONSENT MOMENT — if the box the owner explicitly
        chose lacks a capability, that is real config information the
        readiness check downstream should surface honestly. Silently
        swapping to a DIFFERENT owned box here would hide a real problem,
        not fix one."""
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID, gateway_id=LINUX_BOX, hardware_access="gateway"),
            registrations=[
                _registration(LINUX_BOX, OWNER_ID, capabilities=["channel.openclaw.telegram"]),
                _registration(MAC_BOX, OWNER_ID, capabilities=["shell.execute"]),
            ],
            live=[LINUX_BOX, MAC_BOX],
            capability_id="shell.execute",
        )
        self.assertEqual(
            got, LINUX_BOX,
            "the explicit placement was silently swapped for a different owned "
            "box instead of being honoured (and its capability gap reported)",
        )

    def test_no_capability_id_preserves_prior_first_live_owned_box_behavior(self):
        """Backward compatibility: every existing caller that does not pass
        capability_id (or passes "") must see today's behavior unchanged."""
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID, hardware_access="gateway"),
            registrations=[
                _registration(LINUX_BOX, OWNER_ID, capabilities=["channel.openclaw.telegram"]),
            ],
            live=[LINUX_BOX],
            capability_id="",
        )
        self.assertEqual(got, LINUX_BOX)


class RevokedPlacementIsADistinctReportableStateTests(_ResolverHarness):
    """Alder / Jasper / Nova: `preferred_gateway_id` names a gateway whose
    `status`/`device_trust_state` is `revoked` — a pairing that cannot come
    back on its own. This must not collapse into the same "offline, retry"
    bucket as a machine that is merely asleep."""

    def _placement_status(self, session_ctx, *, registrations):
        by_id = {r["gateway_id"]: r for r in registrations}
        repo = SimpleNamespace(get_gateway_registration=lambda gid: by_id.get(gid))
        with patch("server_modules.gateway_state_repository", repo):
            return skills_service._agent_placement_gateway_status(session_ctx)

    def test_a_revoked_device_trust_state_is_reported_as_revoked_not_offline(self):
        status = self._placement_status(
            _session_ctx(user_id=OWNER_ID, gateway_id=MAC_BOX),
            registrations=[_registration(MAC_BOX, OWNER_ID, device_trust_state="revoked")],
        )
        self.assertEqual(status, "revoked")

    def test_a_revoked_status_is_reported_as_revoked(self):
        status = self._placement_status(
            _session_ctx(user_id=OWNER_ID, gateway_id=MAC_BOX),
            registrations=[_registration(MAC_BOX, OWNER_ID, status="revoked")],
        )
        self.assertEqual(status, "revoked")

    def test_a_genuinely_offline_but_valid_placement_is_reported_as_offline(self):
        status = self._placement_status(
            _session_ctx(user_id=OWNER_ID, gateway_id=MAC_BOX),
            registrations=[_registration(MAC_BOX, OWNER_ID)],
        )
        self.assertEqual(status, "offline")

    def test_no_placement_at_all_is_reported_as_unplaced_not_revoked(self):
        status = self._placement_status(
            _session_ctx(user_id=OWNER_ID),
            registrations=[],
        )
        self.assertEqual(status, "unplaced")

    def test_a_deleted_registration_row_is_reported_as_revoked(self):
        """The pairing row is simply gone — permanent, same bucket as an
        explicit revocation, never a retriable "offline"."""
        status = self._placement_status(
            _session_ctx(user_id=OWNER_ID, gateway_id=MAC_BOX),
            registrations=[],  # no row for MAC_BOX at all
        )
        self.assertEqual(status, "revoked")

    def test_the_offline_result_carries_a_distinct_reason_and_message_when_revoked(self):
        session_ctx = _session_ctx(user_id=OWNER_ID, gateway_id=MAC_BOX)
        by_id = {MAC_BOX: _registration(MAC_BOX, OWNER_ID, device_trust_state="revoked")}
        repo = SimpleNamespace(get_gateway_registration=lambda gid: by_id.get(gid))
        with patch("server_modules.gateway_state_repository", repo):
            placement_status = skills_service._agent_placement_gateway_status(session_ctx)
            payload = json.loads(
                skills_service._hardware_action_offline_result(
                    "shell.execute", placement_status=placement_status,
                )
            )
        self.assertEqual(payload["reason"], "agent_placement_revoked")
        self.assertNotEqual(payload["reason"], "agent_computer_offline")
        # The message must not promise a retry that can never work.
        self.assertNotIn("reconnect", payload["summary"].lower())
        self.assertIn("revoked", payload["summary"].lower())

    def test_the_offline_result_stays_the_generic_message_when_merely_offline(self):
        """No regression for the ordinary, retriable case — same reason/
        message as before this change."""
        payload = json.loads(
            skills_service._hardware_action_offline_result("shell.execute", placement_status="offline")
        )
        self.assertEqual(payload["reason"], "agent_computer_offline")

    def test_the_offline_result_defaults_to_the_generic_message_with_no_placement_status_given(self):
        """Backward compatibility: every existing caller that does not pass
        placement_status must see byte-identical output to before this
        change."""
        payload = json.loads(skills_service._hardware_action_offline_result("shell.execute"))
        self.assertEqual(payload["reason"], "agent_computer_offline")
        self.assertEqual(payload["status"], "offline")
        self.assertEqual(payload["action_id"], "shell.execute")


if __name__ == "__main__":
    unittest.main()
