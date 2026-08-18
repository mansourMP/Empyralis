"""Tool execution lands on the agent's OWN machine, or on none (MAN-356).

THE BUG THIS LOCKS DOWN
-----------------------
`skills_service._resolve_direct_tool_gateway_id` ended in a workspace-wide scan
that returned ANY live gateway registered to the workspace:

    _resolve_live_gateway_from_workspace(workspace_id)   <- no caller identity
    registration_is_usable(reg, workspace_id=...)        <- no machine owner

Neither signature had a parameter that could express whose machine it was. So a
workspace member who could reach an agent could cause `shell.execute` and
`filesystem.read_write` to run on the FOUNDER'S OWN PAIRED MAC. CLAUDE.md's law
is the opposite: "Hardware attaches to its owner, never to the project… a
project invite is not physical access."

The sharpest instance is Sage: a Sage turn resolves no specialist context, so it
stamps no `preferred_gateway_id` and ALWAYS fell through to that scan — and Sage
is the one agent every member can reach by design (MAN-201).

AND `hardware_access` GATED NOTHING
-----------------------------------
`fleet_tools.resolve_hardware_access` had exactly ONE non-test caller and it was
building a UI list payload. The per-agent "Cloud only / Device / VPS" setting was
rendered in the product and enforced nowhere. `HardwareAccessIsEnforcedTests`
pins that it now reaches the live execution path.

PLACEMENT IS THE CONSENT MOMENT
-------------------------------
A box reaches an agent because the HARDWARE'S OWNER put it there — the Hardware
tab / the wizard's Placement step write `install_metadata.preferred_gateway_id`,
and the project-default path re-checks the machine owner's own live opt-in on
every resolution. That is why no per-person hardware permission exists here.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Imported so the PACKAGE ATTRIBUTES exist for `patch` below: the resolver
# does `from server_modules import gateway_state_repository` inside the
# function, and unittest.mock.patch refuses to patch an attribute that has
# not been bound yet.
from server_modules import (  # noqa: F401
    gateway_protocol_service,
    gateway_state_repository,
    skills_service,
)

WS = "ws-1"
OWNER_ID = "u-owner"
MEMBER_ID = "u-member"
OWNER_BOX = "gw_owner_mac"
MEMBER_BOX = "gw_member_laptop"


def _session_ctx(*, user_id, gateway_id=None, hardware_access=None):
    """Mirrors the shape `sage_agent_runtime_service` actually builds.

    The human identity is NESTED under `metadata.user_id` with a top-level
    `sender_id` mirror — production does not put `user_id` at the top level, and
    CLAUDE.md records a feature that shipped dead because its fixtures invented
    the flat shape. `_resolve_session_user_id` is the one reader, and it is what
    this code path uses.
    """
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


def _registration(gateway_id, user_id, status="active"):
    return {
        "gateway_id": gateway_id,
        "user_id": user_id,
        "workspace_id": WS,
        "status": status,
        "device_trust_state": "verified",
    }


class _ResolverHarness(unittest.TestCase):
    """Drives the REAL resolver against a stand-in gateway registry."""

    def _resolve(self, session_ctx, *, registrations, live=None, requested=None):
        live_set = set(live if live is not None else [r["gateway_id"] for r in registrations])
        by_id = {r["gateway_id"]: r for r in registrations}

        repo = SimpleNamespace(
            list_workspace_gateway_registrations=lambda ws, include_revoked=False: [
                r for r in registrations if r["workspace_id"] == ws
            ],
            get_gateway_registration=lambda gid: by_id.get(gid),
        )
        proto = SimpleNamespace(gateway_connection_is_live=lambda gid: gid in live_set)

        # The resolver does `from server_modules import gateway_state_repository`
        # INSIDE the function, which reads the PACKAGE ATTRIBUTE — patching
        # sys.modules would not be seen. Patch the attributes themselves.
        with patch("server_modules.gateway_protocol_service", proto), patch(
            "server_modules.gateway_state_repository", repo
        ):
            return skills_service._resolve_direct_tool_gateway_id(
                WS, session_ctx=session_ctx, requested_gateway_id=requested,
            )


class HardwareAccessIsEnforcedTests(_ResolverHarness):
    def test_hardware_access_none_gets_no_machine_at_all(self):
        """The wizard writes exactly this for a cloud-only agent
        (`placement === "cloud" ? "none" : placement`). Before MAN-356 it was
        decoration; now it ends resolution."""
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID, hardware_access="none"),
            registrations=[_registration(OWNER_BOX, OWNER_ID)],
        )
        self.assertIsNone(got)

    def test_hardware_access_none_beats_even_an_explicit_placement(self):
        """A stale preference must not survive the owner turning hardware off."""
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID, gateway_id=OWNER_BOX, hardware_access="none"),
            registrations=[_registration(OWNER_BOX, OWNER_ID)],
        )
        self.assertIsNone(got)

    def test_an_absent_bucket_is_unknown_not_none(self):
        """A Sage turn resolves no specialist context and stamps nothing.
        Treating that as "none" would take the workspace operator's hardware
        away on an inference rather than on a setting anyone chose — so it falls
        through to the owner-scoped path, which still cannot borrow."""
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID),
            registrations=[_registration(OWNER_BOX, OWNER_ID)],
        )
        self.assertEqual(got, OWNER_BOX)

    def test_the_hardware_bucket_reaches_the_execution_path_at_all(self):
        """Finding 1: resolve_hardware_access had ONE non-test caller and it was
        a UI list payload. Two different sources — the resolver's own source for
        the read, specialist_runtime_context's source for the write."""
        resolver_src = Path(skills_service.__file__).read_text()
        self.assertIn("agent_hardware_access", resolver_src)

        from server_modules import specialist_runtime_context

        producer_src = Path(specialist_runtime_context.__file__).read_text()
        self.assertIn("resolve_hardware_access", producer_src)


class HardwareBucketUnknownVsNoneTests(unittest.TestCase):
    """UNKNOWN and "none" are different facts on BOTH sides of the seam.

    `fleet_tools.resolve_hardware_access` normalizes an ABSENT value to "none"
    — correct for its own job (rendering a picker), and wrong as the whole
    decision here: the SQLite local-bundle path carries no such column, so a
    missing key means "nobody told me", not "the owner chose cloud-only".
    Collapsing them silently un-places every agent whose bundle lacks it, which
    is exactly what the pre-existing specialist-context tests caught.
    """

    def _resolve_context(self, bundle_extra):
        import asyncio
        from unittest.mock import AsyncMock

        from server_modules import specialist_runtime_context as ctx

        bundle = {
            "id": "install-1",
            "label": "Rex",
            "project_id": None,
            "agent_definition": {"agent_kind": "specialist"},
            "metadata": {"preferred_gateway_id": "gw-placed", "model_config": {}},
        }
        bundle.update(bundle_extra)
        with patch(
            "server_modules.agent_registry_repository.get_workspace_master_agent_install",
            AsyncMock(return_value={"id": "master-1"}),
        ), patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            AsyncMock(return_value=bundle),
        ):
            return asyncio.run(
                ctx.resolve_specialist_runtime_context(
                    workspace_id="ws-1", tenant_id="t1", active_agent_install_id="install-1",
                )
            )

    def test_an_absent_column_keeps_the_placement_and_reports_unknown(self):
        result = self._resolve_context({})
        self.assertEqual(result.preferred_gateway_id, "gw-placed")
        self.assertEqual(result.hardware_access, "")

    def test_an_explicit_none_suppresses_the_placement(self):
        result = self._resolve_context({"hardware_access": "none"})
        self.assertEqual(result.preferred_gateway_id, "")
        self.assertEqual(result.hardware_access, "none")
        self.assertEqual(result.source.get("preferred_gateway_id_denied"), "hardware_access_none")

    def test_an_explicit_gateway_keeps_the_placement(self):
        result = self._resolve_context({"hardware_access": "gateway"})
        self.assertEqual(result.preferred_gateway_id, "gw-placed")
        self.assertEqual(result.hardware_access, "gateway")

    def test_the_dataclass_default_is_unknown_not_none(self):
        """A context built without the field must not read as a deliberate
        cloud-only choice."""
        from server_modules.specialist_runtime_context import SpecialistRuntimeContext

        bare = SpecialistRuntimeContext(
            agent_install_id="i", agent_label="l", agent_kind="specialist", persona="p",
        )
        self.assertEqual(bare.hardware_access, "")


class ExecutionFollowsPlacementTests(_ResolverHarness):
    def test_a_placed_agent_executes_on_its_own_box(self):
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID, gateway_id=OWNER_BOX, hardware_access="gateway"),
            registrations=[_registration(OWNER_BOX, OWNER_ID), _registration(MEMBER_BOX, MEMBER_ID)],
        )
        self.assertEqual(got, OWNER_BOX)

    def test_an_offline_placement_degrades_to_no_machine_never_to_another_box(self):
        """Honest unavailability beats a silent borrow. The callers already
        expect None — `_hardware_action_offline_result` is the honest answer and
        every gateway branch is guarded by `if ... and gateway_id`."""
        got = self._resolve(
            _session_ctx(user_id=MEMBER_ID, gateway_id=MEMBER_BOX, hardware_access="gateway"),
            registrations=[_registration(OWNER_BOX, OWNER_ID), _registration(MEMBER_BOX, MEMBER_ID)],
            live=[OWNER_BOX],
        )
        self.assertIsNone(got)


class NoCrossOwnerBorrowTests(_ResolverHarness):
    """THE exploit. A member reaching an unplaced agent must never land on
    somebody else's machine."""

    def test_a_member_never_reaches_the_owners_box_through_an_unplaced_agent(self):
        got = self._resolve(
            _session_ctx(user_id=MEMBER_ID, hardware_access="gateway"),
            registrations=[_registration(OWNER_BOX, OWNER_ID)],
        )
        self.assertIsNone(got)

    def test_sage_reaches_the_asking_persons_own_box_and_only_theirs(self):
        """Sage stamps no placement, so this is the exact path that used to walk
        into the founder's Mac for whoever asked. The owner still gets their own
        box; the member gets nothing."""
        both = [_registration(OWNER_BOX, OWNER_ID), _registration(MEMBER_BOX, MEMBER_ID)]
        self.assertEqual(self._resolve(_session_ctx(user_id=OWNER_ID), registrations=both), OWNER_BOX)
        self.assertEqual(self._resolve(_session_ctx(user_id=MEMBER_ID), registrations=both), MEMBER_BOX)

    def test_an_identity_less_turn_borrows_nothing(self):
        """Fail CLOSED on empty: `""` must never behave like a wildcard — the
        same 'a scope column with a default is a loaded gun' rule this codebase
        applies to agent_id on inbound writes."""
        got = self._resolve(
            _session_ctx(user_id=""),
            registrations=[_registration(OWNER_BOX, OWNER_ID)],
        )
        self.assertIsNone(got)

    def test_a_revoked_or_inactive_box_is_not_reachable_even_by_its_owner(self):
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID),
            registrations=[_registration(OWNER_BOX, OWNER_ID, status="revoked")],
        )
        self.assertIsNone(got)


class ModelSuppliedGatewayIdIsNotAuthorizationTests(_ResolverHarness):
    """`payload.get("gateway_id")` is the MODEL's own tool-call argument. It used
    to short-circuit the resolver entirely (`payload.get(...) or _resolve(...)`),
    so a model that simply named a box skipped placement."""

    def test_naming_another_persons_box_does_not_reach_it(self):
        got = self._resolve(
            _session_ctx(user_id=MEMBER_ID, hardware_access="gateway"),
            registrations=[_registration(OWNER_BOX, OWNER_ID)],
            requested=OWNER_BOX,
        )
        self.assertIsNone(got)

    def test_naming_a_box_this_agent_is_not_placed_on_falls_back_to_its_own(self):
        got = self._resolve(
            _session_ctx(user_id=MEMBER_ID, gateway_id=MEMBER_BOX, hardware_access="gateway"),
            registrations=[_registration(OWNER_BOX, OWNER_ID), _registration(MEMBER_BOX, MEMBER_ID)],
            requested=OWNER_BOX,
        )
        self.assertEqual(got, MEMBER_BOX)

    def test_naming_this_agents_own_placement_is_honoured(self):
        got = self._resolve(
            _session_ctx(user_id=OWNER_ID, gateway_id=OWNER_BOX, hardware_access="gateway"),
            registrations=[_registration(OWNER_BOX, OWNER_ID)],
            requested=OWNER_BOX,
        )
        self.assertEqual(got, OWNER_BOX)


class NoUnscopedFallbackShapeTests(unittest.TestCase):
    """A behavioural test cannot catch the REINTRODUCTION of the unscoped scan —
    it would type-check and behave perfectly for every single-gateway workspace,
    which is most of them."""

    def test_the_unscoped_workspace_scan_is_gone(self):
        source = Path(skills_service.__file__).read_text()
        tree = ast.parse(source)
        defined = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn(
            "_resolve_live_gateway_from_workspace", defined,
            "the unscoped 'any live gateway in the workspace' resolver is back — "
            "that is the silent borrow MAN-356 closed.",
        )

    def test_the_owner_scoped_resolver_actually_filters_on_the_owner(self):
        source = Path(skills_service.__file__).read_text()
        tree = ast.parse(source)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_resolve_live_gateway_owned_by"
        )
        body = ast.get_source_segment(source, func) or ""
        self.assertIn('registration.get("user_id")', body)
        self.assertIn("clean_owner_id", body)

    def test_the_caller_identity_comes_from_verified_session_context(self):
        """Never from the tool call's own arguments — the property that makes
        this unspoofable, same as memory_write_private's partition."""
        source = Path(skills_service.__file__).read_text()
        tree = ast.parse(source)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_resolve_direct_tool_gateway_id"
        )
        body = ast.get_source_segment(source, func) or ""
        self.assertIn("_resolve_session_user_id(session_ctx)", body)

    def test_no_call_site_short_circuits_the_resolver_with_a_raw_payload_id(self):
        """`payload.get("gateway_id") or _resolve_direct_tool_gateway_id(...)`
        is the exact shape that let a model skip placement."""
        source = Path(skills_service.__file__).read_text()
        self.assertNotIn(
            'str(payload.get("gateway_id") or "").strip() or _resolve_direct_tool_gateway_id',
            source,
            "a call site bypasses placement resolution with the model's own "
            "gateway_id again (MAN-356).",
        )


if __name__ == "__main__":
    unittest.main()
