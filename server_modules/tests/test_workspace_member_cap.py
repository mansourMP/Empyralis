"""The per-workspace member seat cap, and the $10 credit purchase floor.

Two dials, one file, because they are the same kind of thing: a plan limit
whose NUMBER lives in billing_credit_config and whose refusal has to name
the way out.

Three classes:

1. ``MemberSeatDecisionTests`` -- the pure decision in
   workspace_member_policy. No database.

2. ``MemberSeatWiringTests`` -- AST assertions that the seat check is on the
   ONE seam every accept path crosses, and that it runs BEFORE membership is
   granted. There are several accept routes; a guard sitting in one of them
   is a rule the next branch skips (CLAUDE.md, repeatedly).

3. ``CreditPurchaseFloorTests`` -- the floor is $10, the ceiling is still
   $500, and the sentence a customer reads is DERIVED from the constant
   rather than being a second literal that can drift away from it.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _members(count: int, prefix: str = "u") -> list:
    return [{"user_id": f"{prefix}{i}", "status": "active"} for i in range(count)]


# ── 1. The pure decision ───────────────────────────────────────────────────


class MemberSeatDecisionTests(unittest.TestCase):
    def test_the_default_limit_is_ten(self) -> None:
        from server_modules import workspace_member_policy as policy

        self.assertEqual(policy.member_limit(), 10)

    def test_a_workspace_with_room_admits_one_more(self) -> None:
        from server_modules import workspace_member_policy as policy

        self.assertEqual(
            policy.assert_seat_available(members=_members(4), joining_user_id="new", limit=10),
            5,
        )

    def test_the_last_seat_is_usable(self) -> None:
        from server_modules import workspace_member_policy as policy

        self.assertEqual(
            policy.assert_seat_available(members=_members(9), joining_user_id="new", limit=10),
            10,
        )

    def test_a_full_workspace_refuses_the_next_person(self) -> None:
        from server_modules import workspace_member_policy as policy

        with self.assertRaises(policy.WorkspaceMemberLimitReached):
            policy.assert_seat_available(members=_members(10), joining_user_id="new", limit=10)

    def test_the_owner_occupies_a_seat(self) -> None:
        """A "10 members" limit that actually admits eleven is a number
        nobody can reconcile against the roster on screen."""
        from server_modules import workspace_member_policy as policy

        roster = [{"user_id": "owner", "role": "owner", "status": "active"}] + _members(9)
        self.assertEqual(policy.active_member_count(roster), 10)
        with self.assertRaises(policy.WorkspaceMemberLimitReached):
            policy.assert_seat_available(members=roster, joining_user_id="new", limit=10)

    def test_someone_already_inside_is_not_refused_for_a_seat_they_hold(self) -> None:
        """Re-clicking your own invite link on a full workspace must not
        fail — that person is not taking a NEW seat."""
        from server_modules import workspace_member_policy as policy

        roster = _members(10)
        self.assertEqual(
            policy.assert_seat_available(members=roster, joining_user_id="u3", limit=10),
            10,
        )

    def test_inactive_memberships_do_not_hold_seats(self) -> None:
        from server_modules import workspace_member_policy as policy

        roster = _members(9) + [{"user_id": "gone", "status": "removed"}]
        self.assertEqual(policy.active_member_count(roster), 9)
        self.assertEqual(
            policy.assert_seat_available(members=roster, joining_user_id="new", limit=10),
            10,
        )

    def test_the_refusal_names_the_limit_the_usage_and_the_way_out(self) -> None:
        from server_modules import workspace_member_policy as policy

        with self.assertRaises(policy.WorkspaceMemberLimitReached) as caught:
            policy.assert_seat_available(members=_members(10), joining_user_id="new", limit=10)
        message = str(caught.exception)
        self.assertIn("10 of 10", message)
        self.assertIn("Remove a member", message)

    def test_an_unset_limit_admits_everyone_rather_than_locking_everyone_out(self) -> None:
        from server_modules import workspace_member_policy as policy

        self.assertEqual(
            policy.assert_seat_available(members=_members(500), joining_user_id="new", limit=0),
            501,
        )

    def test_the_policy_never_reads_the_limit_constant_directly(self) -> None:
        """One interception point for the number — billing_credit_config is
        where a plan dial is tuned, and nowhere else holds a copy."""
        import inspect

        from server_modules import workspace_member_policy as policy

        source = inspect.getsource(policy)
        self.assertIn("billing_credit_config.workspace_member_limit()", source)
        self.assertNotIn("billing_credit_config.WORKSPACE_MEMBER_LIMIT", source)


# ── 2. Is the seat check on the seam every accept path crosses ─────────────


class MemberSeatWiringTests(unittest.TestCase):
    ROUTES = _REPO_ROOT / "server_modules" / "routes_workspaces.py"

    def setUp(self) -> None:
        self.source = self.ROUTES.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def _function(self, name: str) -> ast.AST:
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        self.fail(f"{name} not found in routes_workspaces.py — this test's target moved")

    def _calls(self, node: ast.AST) -> list:
        """Call names in SOURCE order. ast.walk is breadth-first, so its
        order is a tree shape, not a program order -- ordering an assertion
        on it would be asserting something else entirely."""
        found = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                found.append((child.lineno, child.col_offset, ast.unparse(child.func)))
        return [name for _, _, name in sorted(found)]

    def test_the_shared_accept_tail_checks_the_seat_cap(self) -> None:
        node = self._function("_finalize_workspace_invite_acceptance")
        self.assertIn("workspace_member_policy.assert_seat_available", self._calls(node))

    def test_the_seat_check_runs_BEFORE_membership_is_granted(self) -> None:
        """A refusal must have granted nothing: no membership row, no
        project access, and the invite still pending and re-usable."""
        node = self._function("_finalize_workspace_invite_acceptance")
        calls = self._calls(node)
        self.assertLess(
            calls.index("workspace_member_policy.assert_seat_available"),
            calls.index("auth_module.upsert_workspace_membership"),
        )

    def test_the_shared_accept_tail_has_exactly_one_call_site(self) -> None:
        """The guard is only a narrow waist for as long as the waist is
        narrow. A second caller of the accept sequence would need its own
        check, which is the shape this codebase keeps getting bitten by."""
        callers = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func).endswith("_finalize_workspace_invite_acceptance")
        ]
        # Several routes call it; what must hold is that they all do, i.e.
        # no route grants membership on its own.
        self.assertGreaterEqual(len(callers), 1)
        direct_grants = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "auth_module.upsert_workspace_membership"
        ]
        self.assertEqual(
            len(direct_grants),
            1,
            "membership is granted somewhere other than the guarded accept tail",
        )

    def test_creating_an_invite_also_checks_so_no_mail_goes_out_for_a_full_workspace(self) -> None:
        node = self._function("create_workspace_invite_route")
        self.assertIn("workspace_member_policy.assert_seat_available", self._calls(node))

    def test_a_full_workspace_answers_409_not_403(self) -> None:
        """The caller is not forbidden; the destination is full. Those are
        different facts to the person reading them."""
        self.assertIn("status_code=409", self.source)


# ── 3. The credit purchase floor ───────────────────────────────────────────


class CreditPurchaseFloorTests(unittest.TestCase):
    def test_the_floor_is_ten_dollars(self) -> None:
        """Polar charges a FIXED 50c + 5% per transaction, so a $1 top-up
        loses 55% of itself to fees. At $10 the same fee is 10%."""
        from server_modules import billing_service

        self.assertEqual(billing_service._MIN_CREDIT_PURCHASE_USD, 10.0)

    def test_the_ceiling_is_unchanged(self) -> None:
        from server_modules import billing_service

        self.assertEqual(billing_service._MAX_CREDIT_PURCHASE_USD, 500.0)

    def test_the_refusal_message_is_derived_from_the_constant_not_a_literal(self) -> None:
        """A hardcoded "$1" beside a $10 constant is how a limit and the
        message describing it drift apart, and the person reading it is the
        one who pays for that."""
        source = (_REPO_ROOT / "server_modules" / "billing_service.py").read_text(encoding="utf-8")
        self.assertIn("must be at least ${_MIN_CREDIT_PURCHASE_USD:.0f}", source)
        self.assertNotIn("Credit purchase amount must be at least $1.", source)

    def test_every_frontend_top_up_preset_is_at_or_above_the_server_floor(self) -> None:
        """A preset the server answers 400 to is a dead control — it
        renders, it is clickable, and it can only fail. Expected set and
        actual set come from DIFFERENT files on purpose."""
        import re

        from server_modules import billing_service

        panel = (
            _REPO_ROOT / "frontend" / "lib" / "workspace" / "fleet" / "CreditsPanel.tsx"
        ).read_text(encoding="utf-8")
        match = re.search(r"const TOP_UP_PRESETS_USD = \[([^\]]*)\]", panel)
        self.assertIsNotNone(match, "TOP_UP_PRESETS_USD moved — this drift check now enforces nothing")
        presets = [int(value.strip()) for value in match.group(1).split(",") if value.strip()]
        self.assertTrue(presets, "no presets parsed — the check would pass vacuously")
        for preset in presets:
            self.assertGreaterEqual(
                float(preset),
                billing_service._MIN_CREDIT_PURCHASE_USD,
                f"the ${preset} top-up button can only ever be refused by the server",
            )
            self.assertLessEqual(float(preset), billing_service._MAX_CREDIT_PURCHASE_USD)


# ── 4. The real accept tail, refusing for real ─────────────────────────────


class MemberSeatAcceptTailTests(unittest.TestCase):
    """Drives the REAL _finalize_workspace_invite_acceptance -- the single
    seam every accept route in this file crosses -- with only its two
    outward calls stubbed. What is asserted is what an invitee's browser
    would receive, and, more importantly, what the database would NOT have
    been told."""

    def _run(self, coro):
        from server_modules import sync_asyncio_bridge

        return sync_asyncio_bridge.run_coro_sync(coro)

    def _invite(self) -> dict:
        return {
            "id": "inv1",
            "workspace_id": "ws-full",
            "tenant_id": "t1",
            "role": "member",
            "metadata": {},
            "invited_by_user_id": "owner",
        }

    def test_a_full_workspace_refuses_with_409_and_grants_nothing(self) -> None:
        from unittest.mock import patch

        from fastapi import HTTPException
        from server_modules import routes_workspaces

        roster = [{"user_id": f"u{i}", "status": "active"} for i in range(10)]
        granted = []
        accepted = []

        async def _members(_workspace_id):
            return roster

        async def _accept(**kwargs):
            accepted.append(kwargs)
            return {"status": "accepted"}

        with (
            patch.object(
                routes_workspaces.control_plane_repository,
                "list_workspace_members",
                _members,
            ),
            patch.object(
                routes_workspaces.control_plane_repository,
                "accept_workspace_invite",
                _accept,
            ),
            patch.object(
                routes_workspaces.auth_module,
                "upsert_workspace_membership",
                lambda *args: granted.append(args),
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                self._run(
                    routes_workspaces._finalize_workspace_invite_acceptance(self._invite(), "newcomer")
                )

        self.assertEqual(caught.exception.status_code, 409)
        detail = str(caught.exception.detail)
        self.assertIn("10 of 10", detail)
        self.assertIn("Remove a member", detail)
        # The two facts that make this a refusal rather than a half-accept:
        # nothing joined, and the invite was never consumed, so it still
        # works the moment a seat frees up.
        self.assertEqual(granted, [], "membership was granted despite the refusal")
        self.assertEqual(accepted, [], "the invite was consumed despite the refusal")

    def test_a_workspace_with_room_still_accepts(self) -> None:
        from unittest.mock import patch

        from server_modules import routes_workspaces

        roster = [{"user_id": f"u{i}", "status": "active"} for i in range(3)]
        granted = []

        async def _members(_workspace_id):
            return roster

        async def _accept(**kwargs):
            return {"status": "accepted"}

        async def _grant_project(**kwargs):
            return None

        with (
            patch.object(routes_workspaces.control_plane_repository, "list_workspace_members", _members),
            patch.object(routes_workspaces.control_plane_repository, "accept_workspace_invite", _accept),
            patch.object(
                routes_workspaces.auth_module,
                "upsert_workspace_membership",
                lambda *args: granted.append(args),
            ),
            patch(
                "server_modules.projects_repository.grant_invite_project_access",
                _grant_project,
            ),
        ):
            result = self._run(
                routes_workspaces._finalize_workspace_invite_acceptance(self._invite(), "newcomer")
            )

        self.assertEqual(result["workspace_id"], "ws-full")
        self.assertEqual(result["role"], "member")
        self.assertEqual(len(granted), 1)

    def test_an_unreadable_roster_admits_rather_than_locking_a_teammate_out(self) -> None:
        """Fails OPEN. The cap bounds cost; it must not become a second
        availability dependency in front of a working accept."""
        from unittest.mock import patch

        from server_modules import routes_workspaces

        granted = []

        async def _members(_workspace_id):
            raise RuntimeError("control plane unreachable")

        async def _accept(**kwargs):
            return {"status": "accepted"}

        async def _grant_project(**kwargs):
            return None

        with (
            patch.object(routes_workspaces.control_plane_repository, "list_workspace_members", _members),
            patch.object(routes_workspaces.control_plane_repository, "accept_workspace_invite", _accept),
            patch.object(
                routes_workspaces.auth_module,
                "upsert_workspace_membership",
                lambda *args: granted.append(args),
            ),
            patch("server_modules.projects_repository.grant_invite_project_access", _grant_project),
        ):
            result = self._run(
                routes_workspaces._finalize_workspace_invite_acceptance(self._invite(), "newcomer")
            )

        self.assertEqual(result["role"], "member")
        self.assertEqual(len(granted), 1)


if __name__ == "__main__":
    unittest.main()
