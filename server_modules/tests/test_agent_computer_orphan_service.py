"""Tests for MAN-353's detection half: agent_computer_orphan_service.py.

The bug being guarded against, stated once: revoking a Gateway registration
never told the cloud provider anything, so a droplet kept running (and kept
billing) with nothing anywhere saying so. This module does NOT destroy the
box -- destroying it is the founder's call and DELETE /hardware/vps/{id} is
where it lives -- so what these tests prove is that the fact is (a) detected
from the only durable link there is (`metadata.vps_id` on the registration),
and (b) reported without collapsing distinct facts into one.

Four things this file proves:

  (A) The pure classifiers return the RIGHT verdict for each of the shapes
      that actually occur, including the two that a two-state "orphan / not
      orphan" boolean would silently get wrong: a registration with no
      vps_id at all (a physical Mac -- not evidence of anything) and a
      vps_id whose record cannot be read (cloud-backed, outcome UNKNOWN).
      CLAUDE.md: "empty" and "I could not load this" are different facts.

  (B) The sweep finds the MAN-353 shape end to end -- an ACTIVE VPS record
      whose only bound registration is revoked -- while leaving alone a box
      whose registration is still active and a box that has not paired yet.

  (C) THE NO-DESTROY PROOF. A structural (AST) assertion that this module
      contains no call to any provider-destroy primitive, plus a behavioural
      assertion that a full sweep over an orphan makes zero calls into
      vps_provisioning_service. A behavioural test alone cannot catch a
      destroy call added on a branch the fixture never takes, and the
      structural one alone cannot catch a destroy reached through an alias
      -- which is why both are here. This is the assertion that keeps the
      module honest about the line CLAUDE.md draws between reporting a
      problem and acting on it unasked.

  (D) The revoke ROUTE is actually wired to it. An AST wiring check, because
      "built, tested, and never wired" is this codebase's most common defect
      and a detection service nobody calls detects nothing.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import unittest
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

from server_modules import agent_computer_orphan_service as orphan


def _run(coro):
    return asyncio.run(coro)


def _vps(
    vps_id: str = "vps_abc",
    *,
    status: str = "ready",
    workspace_id: str = "ws_1",
    tenant_id: str = "tenant_1",
) -> Dict[str, Any]:
    return {
        "vps_id": vps_id,
        "workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "provider": "digitalocean",
        "provider_resource_id": "592011393",
        "region": "sfo2",
        "size": "s-1vcpu-2gb",
        "status": status,
        "created_at": "2026-08-14T00:00:00Z",
    }


def _registration(
    gateway_id: str = "gateway_1",
    *,
    status: str = "active",
    vps_id: str | None = "vps_abc",
    revoked_at: str = "",
    revoked_reason: str = "",
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"hostname": "empyralis-install-diag"}
    if vps_id is not None:
        metadata["vps_id"] = vps_id
    return {
        "gateway_id": gateway_id,
        "status": status,
        "revoked_at": revoked_at,
        "revoked_reason": revoked_reason,
        "metadata": metadata,
    }


class BackingVpsIdTests(unittest.TestCase):
    def test_reads_metadata_vps_id(self):
        self.assertEqual(orphan.backing_vps_id(_registration(vps_id="vps_xyz")), "vps_xyz")

    def test_missing_vps_id_is_empty_not_an_error(self):
        self.assertEqual(orphan.backing_vps_id(_registration(vps_id=None)), "")

    def test_non_mapping_metadata_is_treated_as_absent(self):
        """This runs on the revoke route's best-effort path, where raising
        would turn a revoke that already committed into a 500."""
        self.assertEqual(orphan.backing_vps_id({"gateway_id": "g", "metadata": "not-a-dict"}), "")
        self.assertEqual(orphan.backing_vps_id({"gateway_id": "g"}), "")
        self.assertEqual(orphan.backing_vps_id(None), "")


class ClassifyAgentComputerOrphanTests(unittest.TestCase):
    """The sweep's pure decision: one active VPS record + its workspace's
    registrations -> is anything still using this box."""

    def test_active_record_whose_only_registration_is_revoked_is_an_orphan(self):
        """THE MAN-353 SHAPE. This is the exact state a revoke leaves behind."""
        verdict = orphan.classify_agent_computer_orphan(
            vps_record=_vps(),
            registrations=[
                _registration(
                    status="revoked",
                    revoked_at="2026-08-14T07:49:21Z",
                    revoked_reason="removed_from_hardware_page",
                )
            ],
        )
        self.assertEqual(verdict["verdict"], orphan.VERDICT_ORPHANED)
        self.assertEqual(verdict["provider_resource_id"], "592011393")
        self.assertEqual(verdict["revoked_at"], "2026-08-14T07:49:21Z")
        self.assertEqual(verdict["revoked_reason"], "removed_from_hardware_page")
        self.assertEqual(verdict["gateway_ids"], ["gateway_1"])

    def test_a_still_active_registration_keeps_the_box_live(self):
        verdict = orphan.classify_agent_computer_orphan(
            vps_record=_vps(),
            registrations=[_registration(status="active")],
        )
        self.assertEqual(verdict["verdict"], orphan.VERDICT_LIVE)

    def test_one_live_registration_among_several_revoked_is_not_an_orphan(self):
        """Re-registration is normal (the prod store has several boxes whose
        earlier rows are revoked as duplicate_registration_same_hostname).
        Flagging those would make the report cry wolf on healthy boxes."""
        verdict = orphan.classify_agent_computer_orphan(
            vps_record=_vps(),
            registrations=[
                _registration("gateway_old", status="revoked", revoked_at="2026-08-14T07:49:21Z"),
                _registration("gateway_new", status="active"),
            ],
        )
        self.assertEqual(verdict["verdict"], orphan.VERDICT_LIVE)

    def test_unpaired_record_is_never_paired_not_orphaned(self):
        """A box mid-provision has no registration yet. Calling that an
        orphan would flag every healthy provision in flight."""
        verdict = orphan.classify_agent_computer_orphan(vps_record=_vps(), registrations=[])
        self.assertEqual(verdict["verdict"], orphan.VERDICT_NEVER_PAIRED)
        self.assertEqual(verdict["created_at"], "2026-08-14T00:00:00Z")

    def test_registrations_for_other_boxes_are_not_counted_as_bindings(self):
        verdict = orphan.classify_agent_computer_orphan(
            vps_record=_vps("vps_abc"),
            registrations=[
                _registration("gateway_other", status="active", vps_id="vps_someone_else"),
                _registration("gateway_none", status="active", vps_id=None),
            ],
        )
        self.assertEqual(verdict["verdict"], orphan.VERDICT_NEVER_PAIRED)
        self.assertEqual(verdict["gateway_ids"], [])


class ClassifyRegistrationBackingTests(unittest.TestCase):
    """The revoke-time decision. Four outcomes, and the two extra ones are
    the whole point -- see this file's header."""

    def test_registration_with_no_vps_id_is_not_cloud_backed(self):
        """A physical Mac. Reporting this as an orphan would tell the owner
        to go destroy a machine that is sitting on their desk."""
        verdict = orphan.classify_registration_backing(
            registration=_registration(vps_id=None), vps_record=None
        )
        self.assertEqual(verdict["verdict"], orphan.VERDICT_NOT_CLOUD_BACKED)

    def test_unreadable_backing_record_is_its_own_verdict(self):
        """Cloud-backed, and we cannot tell what happened to it. Folding
        this into 'released' is the exact lie CLAUDE.md's outcome-honesty
        rule exists to stop."""
        verdict = orphan.classify_registration_backing(
            registration=_registration(), vps_record=None
        )
        self.assertEqual(verdict["verdict"], orphan.VERDICT_BACKING_RECORD_MISSING)
        self.assertEqual(verdict["vps_id"], "vps_abc")
        self.assertNotIn("still_billing", verdict)

    def test_terminal_backing_record_is_released_and_not_billing(self):
        for status in ("deleted", "failed"):
            with self.subTest(status=status):
                verdict = orphan.classify_registration_backing(
                    registration=_registration(), vps_record=_vps(status=status)
                )
                self.assertEqual(verdict["verdict"], orphan.VERDICT_LIVE)
                self.assertIs(verdict["still_billing"], False)

    def test_active_backing_record_is_orphaned_and_still_billing(self):
        verdict = orphan.classify_registration_backing(
            registration=_registration(), vps_record=_vps(status="ready")
        )
        self.assertEqual(verdict["verdict"], orphan.VERDICT_ORPHANED)
        self.assertIs(verdict["still_billing"], True)
        self.assertEqual(verdict["provider_resource_id"], "592011393")


class ResolveRevokedRegistrationBackingTests(unittest.TestCase):
    def test_returns_none_for_a_registration_that_was_never_cloud_backed(self):
        """None, not a verdict dict, so the caller can omit the key rather
        than render an empty box for a laptop."""
        self.assertIsNone(_run(orphan.resolve_revoked_registration_backing(_registration(vps_id=None))))

    def test_reports_the_orphan_without_touching_the_provider(self):
        with patch.object(
            orphan.agent_computers_repository, "get_vps_record", AsyncMock(return_value=_vps())
        ) as get_record:
            verdict = _run(orphan.resolve_revoked_registration_backing(_registration()))
        self.assertEqual(verdict["verdict"], orphan.VERDICT_ORPHANED)
        self.assertEqual(get_record.await_count, 1)

    def test_a_failed_record_read_degrades_to_unknown_and_never_raises(self):
        with patch.object(
            orphan.agent_computers_repository,
            "get_vps_record",
            AsyncMock(side_effect=RuntimeError("control plane unreachable")),
        ):
            verdict = _run(orphan.resolve_revoked_registration_backing(_registration()))
        self.assertEqual(verdict["verdict"], orphan.VERDICT_BACKING_RECORD_MISSING)


class OrphanSweepTests(unittest.TestCase):
    def _sweep(self, records: List[Dict[str, Any]], registrations: List[Dict[str, Any]]):
        with patch.object(
            orphan.agent_computers_repository,
            "list_active_vps_for_metering",
            AsyncMock(return_value=records),
        ), patch.object(
            orphan, "_list_workspace_registrations", return_value=registrations
        ):
            return _run(orphan.run_agent_computer_orphan_sweep())

    def test_sweep_reports_the_orphan_and_leaves_the_live_box_alone(self):
        result = self._sweep(
            [_vps("vps_orphan"), _vps("vps_live"), _vps("vps_fresh")],
            [
                _registration("gateway_dead", status="revoked", vps_id="vps_orphan",
                              revoked_at="2026-08-14T07:49:21Z"),
                _registration("gateway_alive", status="active", vps_id="vps_live"),
            ],
        )
        self.assertEqual(result.vps_considered, 3)
        self.assertEqual([item["vps_id"] for item in result.orphans], ["vps_orphan"])
        self.assertEqual([item["vps_id"] for item in result.never_paired], ["vps_fresh"])
        self.assertEqual(result.errors, [])

    def test_unreadable_registrations_are_an_error_never_a_silent_orphan(self):
        """'I could not read this workspace' must not render as 'nothing
        points at this box' -- that would report a healthy box as an orphan
        and invite the owner to destroy it."""
        with patch.object(
            orphan.agent_computers_repository,
            "list_active_vps_for_metering",
            AsyncMock(return_value=[_vps("vps_abc")]),
        ), patch.object(
            orphan, "_list_workspace_registrations", side_effect=RuntimeError("sqlite is gone")
        ):
            result = _run(orphan.run_agent_computer_orphan_sweep())
        self.assertEqual(result.orphans, [])
        self.assertEqual(result.never_paired, [])
        self.assertEqual(result.errors, ["registrations_unreadable:ws_1"])
        self.assertEqual(result.vps_considered, 1)

    def test_an_unreadable_workspace_is_listed_once_not_once_per_record(self):
        calls: List[Any] = []

        def _boom(workspace_id, tenant_id):
            calls.append(workspace_id)
            raise RuntimeError("sqlite is gone")

        with patch.object(
            orphan.agent_computers_repository,
            "list_active_vps_for_metering",
            AsyncMock(return_value=[_vps("vps_a"), _vps("vps_b"), _vps("vps_c")]),
        ), patch.object(orphan, "_list_workspace_registrations", side_effect=_boom):
            result = _run(orphan.run_agent_computer_orphan_sweep())
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.errors, ["registrations_unreadable:ws_1"])

    def test_sweep_is_a_no_op_when_disabled(self):
        with patch.dict(
            "os.environ", {"EMPYRALIS_AGENT_COMPUTER_ORPHAN_SWEEP_ENABLED": "0"}, clear=False
        ), patch.object(
            orphan.agent_computers_repository, "list_active_vps_for_metering", AsyncMock()
        ) as lister:
            result = _run(orphan.run_agent_computer_orphan_sweep())
        self.assertEqual(lister.await_count, 0)
        self.assertEqual(result.vps_considered, 0)


class OrphanServiceNeverDestroysTests(unittest.TestCase):
    """(C) -- the load-bearing assertion of this whole file.

    Detection was chosen over destruction deliberately: a revoke can be
    accidental or temporary, and tearing the box down takes the agent's
    local state with it. Nothing enforces that choice except this."""

    _DESTRUCTIVE_NAMES = {
        "delete_recorded_vps",
        "_destroy_vps_provider_resource",
        "_destroy_and_persist_refreshed_credentials",
        "_delete_provider_resource",
        "_delete_aws_resource",
        "mark_vps_provision_failed",
        "delete_vps_record",
    }

    def test_module_source_contains_no_provider_destroy_call(self):
        tree = ast.parse(inspect.getsource(orphan))
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called.add(func.attr)
        offenders = sorted(called & self._DESTRUCTIVE_NAMES)
        self.assertEqual(
            offenders,
            [],
            "agent_computer_orphan_service must never destroy a cloud resource -- reaping is a "
            f"separate, founder-approved change. Found call(s) to: {offenders}",
        )

    def test_module_does_not_import_the_provisioning_service_at_all(self):
        """A destroy reached through an alias would slip past the name check
        above; not importing the module that owns every destroy primitive is
        what closes that."""
        tree = ast.parse(inspect.getsource(orphan))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
                if node.module:
                    imported.add(node.module)
        self.assertNotIn("vps_provisioning_service", imported)

    def test_a_full_sweep_over_an_orphan_calls_nothing_destructive(self):
        from server_modules import vps_provisioning_service

        with patch.object(
            orphan.agent_computers_repository,
            "list_active_vps_for_metering",
            AsyncMock(return_value=[_vps("vps_orphan")]),
        ), patch.object(
            orphan,
            "_list_workspace_registrations",
            return_value=[
                _registration("gateway_dead", status="revoked", vps_id="vps_orphan")
            ],
        ), patch.object(
            vps_provisioning_service, "delete_recorded_vps", AsyncMock()
        ) as delete_vps, patch.object(
            vps_provisioning_service, "_destroy_vps_provider_resource"
        ) as destroy_resource:
            result = _run(orphan.run_agent_computer_orphan_sweep())
        self.assertEqual(len(result.orphans), 1)
        self.assertEqual(delete_vps.await_count, 0)
        self.assertEqual(destroy_resource.call_count, 0)


class RevokeRouteIsWiredToOrphanDetectionTests(unittest.TestCase):
    """(D) -- "built, tested, and never wired" is this codebase's most common
    defect, and it is silent by construction: the service passes every test
    above whether or not a single caller exists."""

    def _revoke_route_source(self) -> ast.FunctionDef:
        from server_modules import routes_gateway

        tree = ast.parse(inspect.getsource(routes_gateway))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "revoke_gateway_registration":
                return node
        self.fail("routes_gateway.revoke_gateway_registration was not found")

    def test_the_revoke_route_resolves_the_backing_agent_computer(self):
        node = self._revoke_route_source()
        attrs = {
            sub.func.attr
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
        }
        self.assertIn("resolve_revoked_registration_backing", attrs)

    def test_the_revoke_route_never_destroys_a_cloud_resource(self):
        """The route is where a well-meaning follow-up would most naturally
        add the destroy. It is the founder's call, not a side effect of
        revoking a registration."""
        node = self._revoke_route_source()
        attrs = {
            sub.func.attr
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
        }
        self.assertEqual(
            sorted(attrs & OrphanServiceNeverDestroysTests._DESTRUCTIVE_NAMES),
            [],
        )


class OrphanLoopIsWiredIntoAppLifespanTests(unittest.TestCase):
    def test_shared_app_lifespan_starts_the_orphan_loop(self):
        from server_modules import shared

        source = inspect.getsource(shared)
        self.assertIn("agent_computer_orphan_service.agent_computer_orphan_loop()", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
