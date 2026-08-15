"""The OpenClaw-transported lane's DM policy: a default the transport's own
audit accepts, and a write path with a real caller.

THE DEADLOCK THIS LOCKS DOWN
----------------------------
Established live on 2026-08-14 against a real openclaw@2026.6.10 with a real
Telegram bot credential on the box. `POST .../openclaw/gateways/{id}/provision`
returned::

    status:  refused
    code:    openclaw_security_audit_not_clean
    detail:  channels.telegram.dm.open  [critical]  Telegram DMs are open
             channels.imessage.dm.open  [critical]  iMessage DMs are open

and it did so on every box, for every customer, permanently. The chain::

    DEFAULT_DM_POLICY_MODE = open        (the FIRST-PARTY lane's deliberate
      │                                   live-agent-compat decision)
      │  applied to OpenClaw channels too, because nothing could ever write
      │  a different value: _persist_agent_dm_policy_config had exactly two
      │  callers, BOTH inside personal_channels_service.py —
      │    * approve_dm_policy_pairing_request, itself with zero callers
      │      anywhere ("Not yet wired to a route", its own docstring said)
      │    * the inbound branch guarded by `mode == DM_POLICY_PAIRING`,
      │      i.e. only reachable once the mode is ALREADY pairing
      │  and no dm-policy route existed in routes_personal_channels.py.
      ▼
    openclaw-config-plan.ts  ─▶  dmPolicy: "open", allowFrom: ["*"]
      ▼
    `openclaw security audit`  ─▶  channels.<id>.dm.open  [CRITICAL]
      ▼
    blockingAuditFindings  ─▶  provisioning REFUSED.

Not a credential problem, and not a bug in any one function: a missing write
path colliding with a mandatory audit. This is the codebase's own "built,
tested, and never wired" failure mode with a security audit downstream of it.

WHY THESE TESTS ARE STRUCTURAL
------------------------------
A behavioural test cannot catch the return of either half. A default of `open`
type-checks, round-trips through the store perfectly, and passes every
existing dm-policy test — it only fails against a real OpenClaw, which no test
in this repo can reach. And a write primitive with zero callers behaves
identically to one with a hundred when you call it directly from a test, which
is exactly how the original went unnoticed. So:

  * OpenClawLaneDefaultTests pins the DEFAULT, and derives the set of modes
    the transport cannot carry from the GATEWAY'S OWN SOURCE rather than
    transcribing it — if someone teaches openclaw-config-plan.ts to express
    `pairing` natively, this goes red here rather than leaving the service
    quietly refusing a mode that now works.
  * DmPolicyWritePathIsWiredTests AST-asserts that the write primitives are
    actually reached from a route module.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

from server_modules import channel_lane_contract_service, personal_channels_service

# The one channel in DM_POLICY_CHANNEL_KEYS that is NOT on the OpenClaw
# transport — i.e. what "the first-party lane" means after the 2026-08-14
# cutover. Taken from the lane contract's own constant rather than typed, so
# it cannot go on naming a key the gate no longer reads (which is exactly what
# whatsapp_personal, the name these tests used to carry, had become).
_FIRST_PARTY_CHANNEL_KEY = channel_lane_contract_service.CLOUD_SESSION_TELEGRAM_CHANNEL_KEY


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICE_PATH = _REPO_ROOT / "server_modules" / "personal_channels_service.py"
_ROUTES_PATH = _REPO_ROOT / "server_modules" / "routes_personal_channels.py"
_CONFIG_PLAN_PATH = (
    _REPO_ROOT / "empyralis-gateway" / "src" / "openclaw" / "provisioning" / "openclaw-config-plan.ts"
)


class _FakeAgentInstallStore:
    """Same minimal stand-in test_personal_channels_dm_policy.py uses: enough
    of get_workspace_agent_install_bundle / update_workspace_agent_install's
    "install_metadata, shallow top-level merge" contract to round-trip
    against."""

    def __init__(self) -> None:
        self.installs: Dict[str, Dict[str, Any]] = {}

    async def get_bundle(self, agent_id: str, *, tenant_id: str, workspace_id: str):
        return {"id": agent_id, "install_metadata": dict(self.installs.get(agent_id, {}))}

    async def update(self, agent_id: str, *, tenant_id: str, workspace_id: str, metadata=None, **_kwargs):
        merged = {**self.installs.get(agent_id, {}), **(metadata or {})}
        self.installs[agent_id] = merged
        return {"id": agent_id, "install_metadata": dict(merged)}


def _patch_agent_install_store(store: "_FakeAgentInstallStore"):
    return patch.multiple(
        "server_modules.agent_registry_repository",
        get_workspace_agent_install_bundle=AsyncMock(side_effect=store.get_bundle),
        update_workspace_agent_install=AsyncMock(side_effect=store.update),
    )


def _openclaw_manifest_channels() -> list:
    """The generated manifest, read straight off disk — the same
    single-source-of-truth `openclaw_channel_registry` loads, never a second
    hand-written list."""
    import json

    manifest = json.loads(
        (_REPO_ROOT / "server_modules" / "openclaw_channel_manifest.json").read_text(encoding="utf-8")
    )
    return list(manifest.get("channels") or [])


def _an_openclaw_channel_key() -> str:
    """A real key off the generated manifest, never a literal — the OpenClaw
    channel set is derived from OpenClaw (see CLAUDE.md, "channels is ONE
    system"), so a test that types one has already broken the rule."""
    return sorted(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS)[0]


class OpenClawLaneDefaultTests(unittest.IsolatedAsyncioTestCase):
    def test_widened_to_open_modes_are_derived_from_the_gateway_not_transcribed(self) -> None:
        """OPENCLAW_SETTABLE_DM_POLICY_MODES must be exactly the modes the
        gateway does NOT widen to OpenClaw's `open`.

        The gateway names them itself: every non-exact mapping in
        openclaw-config-plan.ts's dmPolicy switch pushes a widening whose code
        is `dm_<mode>_widened_to_open`, and `open` maps to open by definition.
        So the expected set and the actual set come from two different files —
        the discipline preflight._check_rls had to learn the hard way.
        """
        source = _CONFIG_PLAN_PATH.read_text(encoding="utf-8")
        widened = set(re.findall(r'code:\s*"dm_([a-z_]+?)_widened_to_open"', source))
        self.assertTrue(
            widened,
            "found no dm_<mode>_widened_to_open codes in openclaw-config-plan.ts — the derivation this "
            "test depends on has moved, so it is no longer checking anything. Re-derive it before "
            "trusting OPENCLAW_SETTABLE_DM_POLICY_MODES.",
        )
        # One of the codes the gateway emits is a CONDITIONAL widening, and it
        # is the reason this is not a bare set-difference. Written verdict, in
        # the allowlist-with-a-reason idiom this repo already uses for
        # preflight._RLS_COVERAGE_EXCEPTIONS:
        #
        #   dm_allowlist_mode_unavailable_widened_to_open
        #     fires ONLY in the `else` of `if (shape.dmPolicyModes.includes(
        #     "allowlist"))` — i.e. for a channel whose own schema has a
        #     dmPolicy field that cannot hold "allowlist". Vacuous today; the
        #     next assertion is what keeps that claim honest rather than
        #     assumed.
        conditional = {"allowlist_mode_unavailable"}
        unconditional = widened - conditional
        # `open` is not a "widening" (it maps to open exactly), so the gateway
        # emits no code for it — add it here, from our own constant.
        unavailable = unconditional | {personal_channels_service.DM_POLICY_OPEN}
        unknown = unavailable - personal_channels_service.DM_POLICY_MODES
        self.assertFalse(
            unknown,
            f"the gateway widens mode(s) {sorted(unknown)} that are not in DM_POLICY_MODES — either the "
            "two vocabularies drifted apart, or a widening became conditional/unconditional and this "
            "test's own verdict list is now wrong. Re-read openclaw-config-plan.ts's dmPolicy switch.",
        )
        self.assertEqual(
            set(personal_channels_service.OPENCLAW_SETTABLE_DM_POLICY_MODES),
            personal_channels_service.DM_POLICY_MODES - unavailable,
        )

    def test_no_transported_channel_has_a_dm_policy_field_that_cannot_hold_allowlist(self) -> None:
        """The conditional widening above, checked against the real channel
        set rather than assumed vacuous.

        A channel that declares a `dmPolicy` enum WITHOUT "allowlist" is one
        where our only expressible mode gets widened back to `open` — i.e.
        that single channel would put the whole box back into the refusing
        state, and the deadlock would return for everyone who enables it.

        Every one of the 27 channels today either offers "allowlist" or has
        no `dmPolicy` field at all (the second group takes a different branch
        entirely: the gateway writes only `allowFrom`, never `dmPolicy`, so
        the plugin's own non-open default stands — verified live, audit clean
        with all of them present). If upstream ever ships the third shape,
        this fails on the day it lands rather than on the day a customer
        enables it."""
        offenders = sorted(
            channel["id"]
            for channel in _openclaw_manifest_channels()
            if (channel.get("policy_shape") or {}).get("dm_policy_modes")
            and "allowlist" not in ((channel.get("policy_shape") or {}).get("dm_policy_modes") or [])
        )
        self.assertEqual(offenders, [])

    def test_openclaw_lane_default_is_a_mode_the_audit_accepts(self) -> None:
        """THE regression test for the deadlock. `open` is the one value
        OpenClaw's audit calls critical (verified against their own
        dist/audit-channel.collect.*.js: the check fires on
        `dmPolicy === "open"` UNCONDITIONALLY — the `allowFrom` wildcard their
        remediation text mentions only clears the separate `dm.open_invalid`
        warn, never the critical), and a critical finding refuses the whole
        provisioning run."""
        for channel_key in sorted(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS):
            with self.subTest(channel_key=channel_key):
                mode = personal_channels_service._default_dm_policy_mode_for_channel(channel_key)
                self.assertIn(mode, personal_channels_service.OPENCLAW_SETTABLE_DM_POLICY_MODES)
                self.assertNotEqual(mode, personal_channels_service.DM_POLICY_OPEN)

    def test_unresolved_identity_fallback_is_also_expressible_on_this_lane(self) -> None:
        """The OTHER fallback, and it had the same problem for the same
        reason: owner_only renders as OpenClaw's `open` too
        (dm_owner_only_widened_to_open), so an OpenClaw channel whose agent
        identity could not be resolved would put the box straight back into
        the refusing state. Must be expressible AND no less closed — an empty
        allowlist admits strictly fewer senders than owner_only does."""
        for channel_key in sorted(personal_channels_service.OPENCLAW_PERSONAL_CHANNELS):
            with self.subTest(channel_key=channel_key):
                config = personal_channels_service._unresolved_identity_dm_policy_config(channel_key=channel_key)
                self.assertIn(config["mode"], personal_channels_service.OPENCLAW_SETTABLE_DM_POLICY_MODES)
                self.assertEqual(config["allowlist"], [])

    def test_first_party_lane_is_untouched(self) -> None:
        """The lane split is the whole point: DEFAULT_DM_POLICY_MODE is a
        deliberate live-agent-compat product decision for the first-party
        channels and this build must not have moved it. CLAUDE.md records
        what happened the last time one constant served two callers."""
        self.assertEqual(
            personal_channels_service.DEFAULT_DM_POLICY_MODE,
            personal_channels_service.DM_POLICY_OPEN,
        )
        for channel_key in (
            personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
            personal_channels_service.TELEGRAM_PERSONAL_CHANNEL_KEY,
        ):
            with self.subTest(channel_key=channel_key):
                self.assertEqual(
                    personal_channels_service._default_dm_policy_mode_for_channel(channel_key),
                    personal_channels_service.DM_POLICY_OPEN,
                )
                self.assertEqual(
                    personal_channels_service._unresolved_identity_dm_policy_config(channel_key=channel_key)["mode"],
                    personal_channels_service.DM_POLICY_OWNER_ONLY,
                )

    async def test_a_never_configured_openclaw_channel_loads_as_allowlist(self) -> None:
        """End of the read path, not just the constant: an agent install that
        has never stored a dm_policy for this channel — the state EVERY agent
        was permanently in — must resolve to the expressible default."""
        store = _FakeAgentInstallStore()
        store.installs["ainstall_x"] = {}
        with _patch_agent_install_store(store):
            config = await personal_channels_service._load_agent_dm_policy_config(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="ainstall_x",
                channel_key=_an_openclaw_channel_key(),
            )
        self.assertEqual(config["mode"], personal_channels_service.DM_POLICY_ALLOWLIST)
        self.assertEqual(config["allowlist"], [])


class DmPolicyWriteWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_openclaw_channel_refuses_a_mode_the_transport_cannot_carry(self) -> None:
        """Refused at WRITE time, not at provision time. Storing it would
        return 200 and then make every later provisioning run on that box
        fail with a finding that names OpenClaw's config rather than the
        setting responsible — and CLAUDE.md's "no dead controls" law applies
        just as hard to a control whose only outcome is bricking the box."""
        store = _FakeAgentInstallStore()
        store.installs["ainstall_x"] = {}
        channel_key = _an_openclaw_channel_key()
        for mode in sorted(
            personal_channels_service.DM_POLICY_MODES
            - set(personal_channels_service.OPENCLAW_SETTABLE_DM_POLICY_MODES)
        ):
            with self.subTest(mode=mode):
                with _patch_agent_install_store(store):
                    with self.assertRaises(personal_channels_service.UnsupportedDmPolicyModeError):
                        await personal_channels_service.update_agent_dm_policy_config(
                            tenant_id="tenant-1",
                            workspace_id="ws-1",
                            agent_id="ainstall_x",
                            channel_key=channel_key,
                            mode=mode,
                        )
        self.assertEqual(store.installs["ainstall_x"], {}, "a refused mode must not have been written")

    async def test_refusal_names_no_mechanism(self) -> None:
        """The message is what an owner reads (the route passes str(exc)
        straight through as `detail`, and getErrorMessage surfaces a string
        detail verbatim). A professional tool labels; it does not lecture, and
        it never hands a customer the name of a config key on a machine."""
        store = _FakeAgentInstallStore()
        store.installs["ainstall_x"] = {}
        with _patch_agent_install_store(store):
            with self.assertRaises(personal_channels_service.UnsupportedDmPolicyModeError) as caught:
                await personal_channels_service.update_agent_dm_policy_config(
                    tenant_id="tenant-1",
                    workspace_id="ws-1",
                    agent_id="ainstall_x",
                    channel_key=_an_openclaw_channel_key(),
                    mode=personal_channels_service.DM_POLICY_OPEN,
                )
        message = str(caught.exception).lower()
        for banned in ("dmpolicy", "openclaw", "allowfrom", "owner_only", "config", "audit", "provision"):
            self.assertNotIn(banned, message, f"owner-facing refusal names mechanism: {banned!r}")

    async def test_unsupported_mode_error_is_a_valueerror(self) -> None:
        """So a route with only `except ValueError` still degrades to a 400
        rather than a 500 — the distinct type buys a better status code, it
        must never buy an unhandled exception."""
        self.assertTrue(issubclass(personal_channels_service.UnsupportedDmPolicyModeError, ValueError))

    async def test_first_party_channel_still_accepts_every_mode(self) -> None:
        """RETARGETED 2026-08-15 from WHATSAPP_PERSONAL_CHANNEL_KEY. Its
        subject is "the NON-OpenClaw lane still accepts every mode", and
        whatsapp_personal stopped being a lane that day: the OpenClaw cutover
        deleted its runtime, and DM_POLICY_CHANNEL_KEYS dropped it so the
        route stops persisting policy no gate will ever read. The
        cloud-session lane (cloud-session-manager, gramjs Telegram) is the
        one non-OpenClaw personal channel a real message still crosses
        _enforce_dm_policy on, so it is what this assertion is actually
        about."""
        store = _FakeAgentInstallStore()
        store.installs["ainstall_x"] = {}
        for mode in sorted(personal_channels_service.DM_POLICY_MODES):
            with self.subTest(mode=mode):
                with _patch_agent_install_store(store):
                    updated = await personal_channels_service.update_agent_dm_policy_config(
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        agent_id="ainstall_x",
                        channel_key=_FIRST_PARTY_CHANNEL_KEY,
                        mode=mode,
                        allowlist=["  15551234567 ", "", "15551234567"],
                    )
                self.assertIsNotNone(updated)
                self.assertEqual(updated["mode"], mode)
                self.assertEqual(updated["allowlist"], ["15551234567"])

    async def test_pending_pairing_survives_an_unrelated_edit(self) -> None:
        """A pending entry is the record that a stranger has ALREADY been
        challenged. Dropping it on a mode/allowlist edit would re-challenge
        every one of them the next time they wrote — the exact "record but do
        not re-message" contract _enforce_dm_policy's repeat branch exists to
        keep."""
        store = _FakeAgentInstallStore()
        channel_key = _FIRST_PARTY_CHANNEL_KEY
        store.installs["ainstall_x"] = {
            "dm_policy": {
                channel_key: {
                    "mode": personal_channels_service.DM_POLICY_PAIRING,
                    "allowlist": [],
                    "pending_pairing": {"stranger@s.whatsapp.net": {"code": "482913"}},
                }
            }
        }
        with _patch_agent_install_store(store):
            updated = await personal_channels_service.update_agent_dm_policy_config(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                agent_id="ainstall_x",
                channel_key=channel_key,
                mode=personal_channels_service.DM_POLICY_ALLOWLIST,
                allowlist=["15551234567"],
            )
        self.assertEqual(updated["pending_pairing"]["stranger@s.whatsapp.net"]["code"], "482913")

    async def test_unresolvable_agent_and_unknown_channel_raise(self) -> None:
        for kwargs in (
            {"agent_id": "", "channel_key": personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY},
            {"agent_id": "ainstall_x", "channel_key": "not_a_channel"},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    await personal_channels_service.update_agent_dm_policy_config(
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        mode=personal_channels_service.DM_POLICY_ALLOWLIST,
                        **kwargs,
                    )

    def test_every_channel_the_dm_gate_reads_is_a_channel_the_owner_can_write(self) -> None:
        """The deadlock in one assertion, at the level it actually lived: a
        channel _enforce_dm_policy consults but DM_POLICY_CHANNEL_KEYS does
        not cover is a gate with no lever."""
        self.assertEqual(
            set(personal_channels_service.DM_POLICY_CHANNEL_KEYS),
            set(personal_channels_service.GROUP_POLICY_CHANNEL_KEYS),
        )
        for channel_key in personal_channels_service.OPENCLAW_PERSONAL_CHANNELS:
            self.assertIn(channel_key, personal_channels_service.DM_POLICY_CHANNEL_KEYS)


class DmGateDoesNotDecideGroupTrafficTests(unittest.IsolatedAsyncioTestCase):
    """Gate 1 governs DMs; Gate 2/3 govern groups.

    Found by this build's own regression run rather than by reading: giving
    the OpenClaw lane the allowlist default its transport requires made an
    ALREADY-ALLOWED group go silent, because _enforce_dm_policy ran on group
    messages too and `sender_id` in a group is the individual participant.
    An owner who deliberately opened a group would have had to enumerate its
    entire membership before the agent answered anybody in it. The old
    `open` default hid this by admitting everything."""

    async def test_an_allowed_group_message_is_not_re_decided_by_the_dm_gate(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["ainstall_x"] = {}  # never configured -> allowlist, empty
        with _patch_agent_install_store(store):
            decision = await personal_channels_service._enforce_dm_policy(
                registration={"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                channel_key=_an_openclaw_channel_key(),
                agent_id="ainstall_x",
                message={"is_group": True, "sender_jid": "U-a-participant"},
                remote_jid="C-a-group",
                existing_state=None,
                label="Telegram",
            )
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["mode"], "group_gate")

    async def test_a_direct_message_is_still_decided_by_the_dm_gate(self) -> None:
        store = _FakeAgentInstallStore()
        store.installs["ainstall_x"] = {}
        with _patch_agent_install_store(store):
            decision = await personal_channels_service._enforce_dm_policy(
                registration={"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                channel_key=_an_openclaw_channel_key(),
                agent_id="ainstall_x",
                message={"is_group": False, "sender_jid": "U-stranger"},
                remote_jid="U-stranger",
                existing_state=None,
                label="Telegram",
            )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["mode"], personal_channels_service.DM_POLICY_ALLOWLIST)
        self.assertIsNone(decision["system_reply"], "a blocked stranger is answered with silence, never a hint")

    async def test_the_skip_is_scoped_to_the_openclaw_lane(self) -> None:
        """A first-party owner who explicitly chose owner_only has group
        traffic blocked here today. Quietly un-blocking it is a loosening
        this change is not entitled to make as a side effect."""
        store = _FakeAgentInstallStore()
        store.installs["ainstall_x"] = {
            "dm_policy": {
                personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY: {
                    "mode": personal_channels_service.DM_POLICY_OWNER_ONLY,
                    "allowlist": [],
                    "pending_pairing": {},
                }
            }
        }
        with _patch_agent_install_store(store):
            decision = await personal_channels_service._enforce_dm_policy(
                registration={"tenant_id": "tenant-1", "workspace_id": "ws-1"},
                channel_key=personal_channels_service.WHATSAPP_PERSONAL_CHANNEL_KEY,
                agent_id="ainstall_x",
                message={"is_group": True, "sender_jid": "stranger@s.whatsapp.net"},
                remote_jid="group@g.us",
                existing_state=None,
                label="WhatsApp",
            )
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["mode"], personal_channels_service.DM_POLICY_OWNER_ONLY)


def _called_names(tree: ast.AST) -> set:
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


class DmPolicyWritePathIsWiredTests(unittest.TestCase):
    """The "built, tested, and never wired" guard, and the only kind of test
    that could have caught the original. Every behavioural test in this repo's
    dm-policy suite called the write primitive DIRECTLY and passed, for
    months, while nothing in the product could reach it."""

    def setUp(self) -> None:
        self.routes_tree = ast.parse(_ROUTES_PATH.read_text(encoding="utf-8"))
        self.service_tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
        self.routes_calls = _called_names(self.routes_tree)

    def test_the_dm_policy_write_wrapper_is_called_from_a_route_module(self) -> None:
        self.assertIn("update_agent_dm_policy_config", self.routes_calls)

    def test_the_pairing_approval_primitive_is_called_from_a_route_module(self) -> None:
        """approve_dm_policy_pairing_request's own docstring said "Not yet
        wired to a route" — so `pairing` mode challenged a stranger and then
        asked the owner for an approval that was impossible to give."""
        self.assertIn("approve_dm_policy_pairing_request", self.routes_calls)

    def test_a_dm_policy_route_exists_and_pushes_the_setting_to_the_box(self) -> None:
        """A saved policy that never reaches the machine is not in force: on
        the OpenClaw lane their config decides what we ever SEE, so a setting
        stored only in Postgres changes nothing about the traffic. Same
        reason the group-policy route reconciles after saving."""
        paths = {
            decorator.args[0].value
            for node in ast.walk(self.routes_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
            and isinstance(decorator.args[0].value, str)
        }
        self.assertTrue(
            any(path.endswith("/dm-policy") for path in paths),
            f"no .../dm-policy route is registered; found {sorted(paths)}",
        )
        self.assertIn("reconcile_openclaw_policy_best_effort", self.routes_calls)

    def test_the_private_persist_primitive_is_never_called_from_a_route(self) -> None:
        """Routes go through the validating wrapper, exactly as the
        group-policy pair already requires — calling
        _persist_agent_dm_policy_config directly would let a typo'd mode be
        silently coerced to the default by _normalize_dm_policy_config, which
        is correct for a READ path facing stale stored data and wrong for a
        WRITE path facing a live caller."""
        self.assertNotIn("_persist_agent_dm_policy_config", self.routes_calls)

    def test_the_two_dm_policy_fallbacks_stay_independent(self) -> None:
        """_unresolved_identity_dm_policy_config must never read either
        default constant. CLAUDE.md records the one-line change (ee3fca4f7c)
        that reopened this fallback by accident when the two shared a
        constant; this build added a SECOND default beside the first, which
        is exactly the situation that mistake came out of."""
        for node in ast.walk(self.service_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_unresolved_identity_dm_policy_config":
                referenced = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                self.assertNotIn("DEFAULT_DM_POLICY_MODE", referenced)
                self.assertNotIn("DEFAULT_OPENCLAW_DM_POLICY_MODE", referenced)
                self.assertNotIn("_default_dm_policy_mode_for_channel", referenced)
                return
        self.fail("_unresolved_identity_dm_policy_config not found in personal_channels_service.py")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
