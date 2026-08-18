"""A workspace member cannot reach an agent in a project they are not in.

THE BUG THIS LOCKS DOWN (MAN-356)
---------------------------------
The MAN-115 per-project ACL lived only in the LIST endpoints. `POST /api/turn`
gated on workspace membership alone, and no turn-path module made a single
project-ACL call. Proven live on a disposable stack, two users, two projects:

    GET  /fleet/agents        as the non-member  ->  200, agent absent  (filtered)
    POST /api/turn  naming that same agent id    ->  200, turn RAN      (not gated)

Hiding an id from a list is obscurity. The turn path honoured the id anyway, so
the agent was reachable by anyone who could guess or ever see it — and the
agent is what reaches the machine.

WHY THE GUARD IS AT THE INGRESS WAIST
-------------------------------------
CLAUDE.md: "a guard called once inside a 2,300-line function is a guard the next
branch will skip." `start_turn` and `start_run_start` are the only two functions
that resolve a turn_request for a human caller; every route funnels through one
of them. `StructuralGuardSeamTests` fails if either stops calling the guard,
because a behavioural test can only ever cover the routes that exist today.

IT FAILS CLOSED, AND THAT IS THE POINT
--------------------------------------
The obvious implementation reuses `routes_fleet._enforce_agent_project_access`.
That helper returns (allows) when the agent resolves to no project — and
`workspace_agent_installs.project_id` is nullable BY SCHEMA
(`REFERENCES projects(id) ON DELETE SET NULL`), with pre-migration installs never
backfilled. So a gate derived from it is already partly vacuous and would become
entirely vacuous the day agents stop carrying a project: still present, still
shaped like a gate, enforcing nothing. `FailClosedTests` pins every refusal.

THE TWO EXEMPTIONS, AND WHY NEITHER IS A HOLE
---------------------------------------------
  system principal   channel/scheduler turns carry no human identity at all, so
                     there is no person a project ACL could check. Gating them
                     would silently kill inbound Telegram/WhatsApp/Signal.
  agent_kind master  the workspace-scoped agent (Sage), reachable by every
                     member BY DESIGN — MAN-201, the founder's "Ask AI is
                     per-person" decision. Keyed on agent_kind, NEVER on an
                     empty project_id, so a project-less SPECIALIST does not
                     inherit the exemption (`test_a_project_less_specialist_
                     is_not_exempted`).
"""

from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import agent_reachability_service as reach
from server_modules import direct_chat_service, turn_ingress_service
from server_modules.api_contract import request_body_to_turn_request

PRIVATE_AGENT = "ainstall_private_bot"
PRIVATE_PROJECT = "project_founder_only"


def _browser_turn_payload(agent_install_id: str | None) -> dict:
    """The payload AgentChat.tsx actually sends.

    Built through the REAL `request_body_to_turn_request` below rather than by
    hand-constructing an AgentTurnRequest, because the nesting is the whole
    point: the agent id lives at `context_hints.metadata.active_agent_install_id`,
    one level down. CLAUDE.md records a feature that shipped dead because its
    tests hand-built a FLAT `{"user_id": ...}` production never produces — a
    fixture that invents its own input cannot notice the real input is shaped
    differently.
    """
    hints: dict = {"source": "fleet-chat", "thread_id": "t1", "force_direct_chat": True}
    if agent_install_id:
        hints["metadata"] = {"active_agent_install_id": agent_install_id}
    return {
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "thread_id": "t1",
        "session_id": "s1",
        "client_request_id": "r1",
        "channel": "web",
        "actor": {"type": "user", "id": "u-member", "display_name": "Member"},
        "message": "hello",
        "attachments": [],
        "context_hints": hints,
        "execution_mode": "sync",
        "response_mode": "stream",
        "policy_context": {},
    }


def _turn(agent_install_id: str | None):
    return request_body_to_turn_request(_browser_turn_payload(agent_install_id))


MEMBER = {"user_id": "u-member", "email": "member@example.com", "role": "member"}
SYSTEM = {"auth_type": "api_key", "user_id": "", "email": ""}


class TurnAgentIdResolutionTests(unittest.TestCase):
    def test_the_browser_payload_survives_the_real_converter(self):
        """If this breaks, the guard is reading a key production stopped using
        and would silently permit everything."""
        self.assertEqual(reach.resolve_turn_agent_id(_turn(PRIVATE_AGENT)), PRIVATE_AGENT)

    def test_a_turn_naming_no_agent_resolves_to_empty(self):
        self.assertEqual(reach.resolve_turn_agent_id(_turn(None)), "")

    def test_the_key_tuple_matches_the_web_turn_path_s_own_reader(self):
        """direct_chat_service is what actually routes a web turn to a
        specialist. The guard must cover exactly the keys it dispatches on —
        a key it reads and the guard does not is a bypass.

        Two different sources: this reads direct_chat_service's SOURCE, the
        expected set is the guard's own constant.
        """
        source = Path(direct_chat_service.__file__).read_text()
        for key in ("active_agent_install_id", "workspace_agent_install_id"):
            self.assertIn(
                f'_ctx_metadata.get("{key}")', source,
                f"{key} is no longer read by the web turn path — re-derive "
                "TURN_AGENT_ID_METADATA_KEYS from the readers.",
            )
            self.assertIn(key, reach.TURN_AGENT_ID_METADATA_KEYS)


class SystemPrincipalTests(unittest.TestCase):
    def test_the_identity_less_placeholder_is_a_system_principal(self):
        """turn_ingress_service._default_system_user() built from the real
        producer, not a hand-written dict."""
        self.assertTrue(
            reach.caller_is_system_principal(turn_ingress_service._default_system_user())
        )

    def test_a_real_human_is_not_a_system_principal(self):
        self.assertFalse(reach.caller_is_system_principal(MEMBER))

    def test_a_key_authenticated_caller_with_an_identity_is_still_gated(self):
        """An MCP key resolves a real user; only the identity-less placeholder
        is exempt. Same discriminator agent_turn._current_user_is_owner uses."""
        self.assertFalse(
            reach.caller_is_system_principal(
                {"auth_type": "api_key", "user_id": "u-mcp", "email": "k@example.com"}
            )
        )


class _GuardHarness(unittest.TestCase):
    """Drives the REAL guard with only the install lookup and the project ACL
    stood in for.

    The end-to-end path is proven by the live HTTP reproduction recorded on
    MAN-356 (member outside a project: 200 before, 404 after) — a mock protects
    a seam, not a path, which is why this file also carries the structural
    wiring assertions at the bottom.
    """

    def _run(self, *, agent_id, current_user, bundle, acl_raises=False, lookup_raises=False):
        lookup = AsyncMock(return_value=bundle)
        if lookup_raises:
            lookup.side_effect = RuntimeError("control plane unavailable")
        enforce = AsyncMock()
        if acl_raises:
            enforce.side_effect = HTTPException(status_code=404, detail="Project not found.")
        with patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            lookup,
        ), patch(
            "server_modules.auth.enforce_project_access", enforce
        ):
            asyncio.run(
                reach.enforce_turn_agent_reachability(_turn(agent_id), current_user)
            )
        return lookup, enforce


def _specialist(project_id):
    return {"id": PRIVATE_AGENT, "agent_kind": "specialist", "project_id": project_id}


def _master():
    return {"id": "ainstall_ws-1_sage", "agent_kind": "master", "project_id": None}


class TurnReachabilityTests(_GuardHarness):
    def test_a_member_outside_the_project_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run(
                agent_id=PRIVATE_AGENT, current_user=MEMBER,
                bundle=_specialist(PRIVATE_PROJECT), acl_raises=True,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_a_member_inside_the_project_passes(self):
        _, enforce = self._run(
            agent_id=PRIVATE_AGENT, current_user=MEMBER,
            bundle=_specialist(PRIVATE_PROJECT), acl_raises=False,
        )
        self.assertEqual(enforce.await_count, 1)

    def test_the_workspace_scoped_agent_stays_reachable_by_every_member(self):
        """MAN-201. The master is exempt by agent_kind, and the project ACL is
        never consulted — it must not be, or every member loses Ask AI."""
        _, enforce = self._run(
            agent_id="ainstall_ws-1_sage", current_user=MEMBER,
            bundle=_master(), acl_raises=True,
        )
        self.assertEqual(enforce.await_count, 0)

    def test_a_channel_turn_is_never_gated_and_costs_no_lookup(self):
        """Asserting the CALL COUNT, not just the absence of a raise: 'nothing
        happened' and 'something else happened' are different facts, and a
        control-plane read on every inbound channel message is a real cost."""
        lookup, enforce = self._run(
            agent_id=PRIVATE_AGENT, current_user=SYSTEM,
            bundle=_specialist(PRIVATE_PROJECT), acl_raises=True,
        )
        self.assertEqual(lookup.await_count, 0)
        self.assertEqual(enforce.await_count, 0)

    def test_a_turn_naming_no_agent_costs_no_lookup(self):
        lookup, enforce = self._run(
            agent_id=None, current_user=MEMBER,
            bundle=_specialist(PRIVATE_PROJECT), acl_raises=True,
        )
        self.assertEqual(lookup.await_count, 0)
        self.assertEqual(enforce.await_count, 0)


class FailClosedTests(_GuardHarness):
    """Every branch that cannot ESTABLISH a grant must refuse.

    This is the class that separates a real gate from one that merely looks
    like a gate. Each case below returns/raises something the fleet-routes
    predicate treats as "allow".
    """

    def test_a_project_less_specialist_is_refused_for_a_member(self):
        """THE regression this whole file exists for. project_id is nullable by
        schema (ON DELETE SET NULL) and pre-migration installs were never
        backfilled, so this state is real today — and under a fail-open
        predicate it is a completely ungated agent."""
        with self.assertRaises(HTTPException) as ctx:
            self._run(
                agent_id=PRIVATE_AGENT, current_user=MEMBER, bundle=_specialist(None),
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_a_project_less_specialist_is_not_exempted_as_if_it_were_master(self):
        """'Has no project' must never be what grants reach — otherwise a
        project-less specialist silently inherits Sage's exemption and MAN-115's
        boundary widens with nobody choosing it. Same negative the MAN-201 list
        test already pins on the other side."""
        owner = {"user_id": "u-owner", "email": "o@example.com", "role": "owner"}
        _, enforce = self._run(
            agent_id=PRIVATE_AGENT, current_user=owner, bundle=_specialist(None),
        )
        # Reached the workspace-owner branch, NOT the master exemption: the
        # master branch would also have skipped the ACL, so the distinguishing
        # assertion is the MEMBER refusal in the test above, which a master
        # bundle would have allowed.
        self.assertEqual(enforce.await_count, 0)

    def test_a_project_less_specialist_stays_reachable_by_the_workspace_owner(self):
        """Fail-closed must not mean 'nobody' — project-less agents exist in the
        wild and their OWNER must keep reaching them, or this fix breaks the
        founder's own fleet."""
        owner = {"user_id": "u-owner", "email": "o@example.com", "role": "owner"}
        self._run(agent_id=PRIVATE_AGENT, current_user=owner, bundle=_specialist(None))

    def test_an_unknown_agent_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run(agent_id=PRIVATE_AGENT, current_user=MEMBER, bundle=None)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_an_agent_in_another_workspace_is_refused(self):
        """The lookup is tenant+workspace scoped, so a foreign install returns
        None and lands on the same refusal."""
        with self.assertRaises(HTTPException):
            self._run(agent_id=PRIVATE_AGENT, current_user=MEMBER, bundle={})

    def test_a_control_plane_failure_refuses_rather_than_widening_access(self):
        """A blip must never be an allow. This is the branch a try/except that
        swallowed into `return` would silently invert."""
        with self.assertRaises(HTTPException) as ctx:
            self._run(
                agent_id=PRIVATE_AGENT, current_user=MEMBER,
                bundle=_specialist(PRIVATE_PROJECT), lookup_raises=True,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_the_refusal_is_404_not_403(self):
        """403 confirms the agent exists and turns the gate into an enumeration
        oracle — matching auth.enforce_project_access's own convention."""
        with self.assertRaises(HTTPException) as ctx:
            self._run(agent_id=PRIVATE_AGENT, current_user=MEMBER, bundle=None)
        self.assertEqual(ctx.exception.status_code, 404)


class NoFailOpenShapeTests(unittest.TestCase):
    """The predicate must contain no `if not <x>: return` after the agent id is
    known — that single shape is what made the fleet-routes helper unusable as
    a security gate, and it type-checks and reads perfectly."""

    def test_enforce_agent_reachable_has_no_bare_early_return_on_a_missing_value(self):
        source = Path(reach.__file__).read_text()
        tree = ast.parse(source)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
            and n.name == "enforce_agent_reachable"
        )
        offenders = []
        for node in ast.walk(func):
            if not isinstance(node, ast.If):
                continue
            # `if not X:` whose body is a bare `return`
            if not (isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not)):
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Return) and node.body[0].value is None:
                offenders.append(ast.get_source_segment(source, node.test))
        # The ONLY permitted one is the "no agent named" pre-check, which is
        # not a grant decision at all — it is the absence of a subject.
        self.assertEqual(
            offenders, ["not clean_agent_id"],
            f"fail-open shape reintroduced in enforce_agent_reachable: {offenders}",
        )

    def test_the_master_exemption_is_keyed_on_agent_kind_not_on_project_id(self):
        source = Path(reach.__file__).read_text()
        self.assertIn("_agent_kind_of(bundle) == MASTER_AGENT_KIND", source)


class StructuralGuardSeamTests(unittest.TestCase):
    """A future route must not be able to reach a turn around the guard.

    Behavioural tests only cover the ingress functions that exist today; these
    assert the SHAPE that makes a new one safe by construction.
    """

    @staticmethod
    def _tree() -> ast.Module:
        return ast.parse(Path(turn_ingress_service.__file__).read_text())

    def _func(self, name: str):
        for node in ast.walk(self._tree()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        raise AssertionError(f"{name} not found in turn_ingress_service")

    def _calls_guard(self, func) -> bool:
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "enforce_turn_agent_reachability":
                    return True
        return False

    def test_start_turn_enforces_reachability(self):
        self.assertTrue(
            self._calls_guard(self._func("start_turn")),
            "start_turn no longer calls enforce_turn_agent_reachability — every "
            "web turn is ungated again (MAN-356).",
        )

    def test_start_run_start_enforces_reachability(self):
        self.assertTrue(
            self._calls_guard(self._func("start_run_start")),
            "start_run_start resolves its own turn_request and never crosses "
            "start_turn, so it needs the guard in its own right (MAN-356).",
        )

    def test_every_ingress_entry_point_is_covered(self):
        """The waist is 'the public functions that resolve a turn_request for a
        caller'. If a THIRD appears it must be added here deliberately rather
        than silently becoming an ungated door."""
        tree = self._tree()
        resolvers = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
            and any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id in {"_resolve_turn_ingress", "resolve_run_start_turn_request"}
                for c in ast.walk(node)
            )
        }
        self.assertEqual(
            resolvers, {"start_turn", "start_run_start"},
            "a new public turn-ingress entry point appeared; give it the "
            "reachability guard and add it to this assertion.",
        )


if __name__ == "__main__":
    unittest.main()
