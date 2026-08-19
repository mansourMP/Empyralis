"""Who may reach an agent — the ONE fail-CLOSED seam, for every turn (MAN-356).

An agent is a PRINCIPAL, not a resource humans reach through. Humans touch work
artifacts (tasks, documents, chat); the agent touches machines. So the only
boundary that matters for a human is REACHABILITY: may this person talk to this
agent at all. There are deliberately no per-person tool-authority tiers — an
agent's skills are the agent's, not a function of who is asking (the founder
rejected tiering outright; MAN-356 supersedes MAN-326).

THE BUG
-------
The MAN-115 per-project ACL lived only in the LIST endpoints. The turn path made
zero project-ACL calls, so a workspace member outside a project could name that
project's agent by id and get a real turn against it:

    GET  /fleet/agents        as the non-member  ->  200, agent absent  (filtered)
    POST /api/turn  naming that same agent id    ->  200, turn RAN      (not gated)

Hiding an id is obscurity, not a gate. Reproduced live before this existed and
re-run after it to prove the refusal.

WHY THIS IS NOT `routes_fleet._enforce_agent_project_access`
------------------------------------------------------------
That helper USED TO fail open by its own docstring — `if not project_id: return`,
unconditionally, for ANY project-less agent. And `project_id` is nullable *by
schema*:

    workspace_agent_installs.project_id TEXT
        REFERENCES projects(id) ON DELETE SET NULL     <- control_plane_repository

So deleting a project silently NULLs its agents' `project_id`, and pre-migration
installs were never backfilled at all (`fleet-data.ts` documents project-less
agents in the wild and fabricates a default project id so the URL does not 404).
That predicate was not merely theoretical: a real, enabled production specialist
(not the workspace master) was project-less, reachable by every member of its
workspace through every fleet DETAIL route with no project membership at all —
fixed 2026-08-19, in the same change that added the docstring you're reading now.

Both helpers now share ONE decision, `enforce_resolved_agent_access` below, for
an already-resolved bundle — so there is exactly one opinion of "who may reach
this agent" rather than two that can drift. They differ only in the OTHER case:
what happens when the bundle cannot be resolved at all (the agent genuinely does
not exist, or the lookup itself failed). `_enforce_agent_project_access` still
returns there on purpose — it guards fleet DETAIL reads, where a missing agent
should fall through to the service call's own not-found result rather than a 404
from this seam, which would make "does this id exist" answerable two different
ways depending on which helper ran first. This function refuses instead, because
it is the ONLY gate on the turn-execution path: "I could not establish a grant"
must mean no here, with nothing downstream to fall back on.

WHERE THE GRANT COMES FROM, AND THE ONE PLACE TO CHANGE IT
-----------------------------------------------------------
    agent_kind == "master"   ALWAYS reachable  (workspace-scoped; MAN-201)
    project_id present       the project's own ACL decides
    project_id absent        workspace OWNER only — never a member
    install unresolvable     REFUSE

Today's grant is derived from the agent's project. When that stops being true,
only this function's body changes — in one place — instead of the gate quietly
evaporating everywhere at once.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

MASTER_AGENT_KIND = "master"

# The context_hints.metadata keys that actually ROUTE a turn to a specific
# agent. This tuple is not decorative and is not a superset chosen for safety:
# the guard must cover exactly the keys the runtime dispatches on. Miss one and
# a caller names the agent through the key the guard forgot; add one the runtime
# ignores and the guard refuses turns over a field that decided nothing.
#
# Derived from the two readers, not invented here:
#   direct_chat_service._active_install_id  (the WEB turn path — the one the
#       browser reaches, and the one MAN-356's exploit used) reads
#       active_agent_install_id then workspace_agent_install_id.
#   run_service._runtime_binding_install_id (the durable-run path) reads those
#       two plus master_agent_install_id.
# `master_agent_install_id` can only ever name the workspace master, which is
# exempt anyway — it is listed so this tuple mirrors the readers faithfully
# rather than being a hand-trimmed subset. test_agent_reachability_guard.py
# asserts it stays in step with direct_chat_service's own source.
TURN_AGENT_ID_METADATA_KEYS = (
    "active_agent_install_id",
    "workspace_agent_install_id",
    "master_agent_install_id",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def resolve_turn_agent_id(turn_request: Any) -> str:
    """The agent install id this turn names, or "" for a plain workspace turn.

    Reads `context_hints.metadata` (where the browser puts it) and falls back to
    a flat `context_hints` — the same two-level shape
    `turn_ingress_service._turn_request_run_id` already tolerates, and the same
    nested-vs-flat trap CLAUDE.md records for the private-memory tools, where a
    fixture invented a flat dict production never produces.
    """
    context_hints = getattr(turn_request, "context_hints", None)
    if not isinstance(context_hints, Mapping):
        return ""
    metadata = context_hints.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    for key in TURN_AGENT_ID_METADATA_KEYS:
        found = _text(metadata.get(key)) or _text(context_hints.get(key))
        if found:
            return found
    return ""


def caller_is_system_principal(current_user: Any) -> bool:
    """True when this turn has no human behind it.

    `turn_ingress_service._default_system_user()` is literally
    `{"auth_type": "api_key", "user_id": "", "email": ""}` — the placeholder for
    "no real caller", used by every channel-originated turn (Telegram, WhatsApp,
    Signal, iMessage, the whole OpenClaw family), by scheduler wakeups, and by an
    agent's own delegation calls. None of those has a browser session, a project
    membership, or a person to check, and a project ACL applied to them would
    silently kill inbound channel traffic with no error anywhere — the exact
    failure shape CLAUDE.md records for the group-ban incident.

    Identity, not `auth_type`, is the discriminator — the same test
    `agent_turn._current_user_is_owner` already makes for the same reason: a
    genuinely key-authenticated caller (an MCP key) carries a resolved user
    identity and IS gated here; only the identity-less placeholder is exempt.
    """
    if not isinstance(current_user, dict):
        # A non-dict principal carries no identity that could be checked and is
        # never a browser session.
        return True
    return not (_text(current_user.get("user_id")) or _text(current_user.get("email")))


def _agent_kind_of(bundle: Mapping[str, Any]) -> str:
    """`agent_kind` off an install bundle, tolerating both shapes it ships in.

    `agent_registry_repository.get_workspace_agent_install_bundle` SELECTs
    `ad.agent_kind` (top level), while `specialist_runtime_context._agent_kind`
    reads it from a nested `agent_definition` dict. Read both rather than betting
    on one — a miss here would silently drop the master exemption and take every
    member's Ask AI console away.
    """
    top = _text(bundle.get("agent_kind")).lower()
    if top:
        return top
    definition = bundle.get("agent_definition")
    if isinstance(definition, Mapping):
        return _text(definition.get("agent_kind")).lower()
    return ""


def _refuse() -> HTTPException:
    """404, never 403 — matching `auth.enforce_project_access`'s own convention.

    A principal who may not reach an agent must not be able to tell "no such
    agent" from "an agent you are not allowed to see"; a 403 confirms existence
    and turns the gate into an enumeration oracle.
    """
    return HTTPException(status_code=404, detail="Agent not found.")


async def lookup_agent_install_bundle(
    agent_id: str, *, tenant_id: str, workspace_id: str,
) -> Optional[Dict[str, Any]]:
    """Resolve an agent's install bundle, tenant+workspace scoped.

    Returns `None` for three distinct situations a caller must treat
    identically as "no grant could be established" — the agent does not
    exist, it exists in a DIFFERENT workspace (the lookup is tenant+workspace
    scoped, so a foreign install lands here too), or the lookup itself raised
    (logged, never re-raised here — the caller decides what "unresolvable"
    means for its own posture).

    Shared by `enforce_agent_reachable` below (which fails CLOSED on `None`)
    and `routes_fleet._enforce_agent_project_access` (which fails OPEN on
    `None`, deliberately — that helper guards fleet DETAIL reads, where a
    truly nonexistent agent should fall through to the underlying service
    call's own not-found result rather than a 404 from this seam, which would
    turn "does this id exist" into an oracle two different ways depending on
    which helper answered first). One resolution, two postures.
    """
    from server_modules import agent_registry_repository as registry

    clean_agent_id = _text(agent_id)
    if not clean_agent_id:
        return None
    try:
        bundle = await registry.get_workspace_agent_install_bundle(
            clean_agent_id,
            tenant_id=_text(tenant_id) or None,
            workspace_id=_text(workspace_id) or None,
        )
    except Exception:
        logger.exception(
            "agent reachability: install lookup failed for agent=%s workspace=%s",
            clean_agent_id, workspace_id,
        )
        return None
    return bundle if isinstance(bundle, dict) and bundle else None


async def enforce_resolved_agent_access(
    current_user: Optional[Dict[str, Any]],
    resolved_workspace_id: str,
    bundle: Mapping[str, Any],
    *,
    minimum_role: str = "viewer",
) -> None:
    """The ONE decision of who may reach an agent whose bundle is already
    resolved. Shared by both callers of `lookup_agent_install_bundle` so
    there is exactly one opinion of this rule rather than two that can drift:

        agent_kind == "master"   ALWAYS allowed  (workspace-scoped; MAN-201)
        project_id present       the project's own ACL decides
        project_id absent        workspace OWNER only — never a member

    Every exit that is not an explicit grant raises. In particular there is
    no `if not <something>: return` anywhere below — that shape is precisely
    what made the fleet-routes predicate unusable as a reachability gate.
    """
    from server_modules import auth as auth_module

    if _agent_kind_of(bundle) == MASTER_AGENT_KIND:
        # MAN-201: the workspace-scoped agent (Sage / the Operator) is reachable
        # by every member BY DESIGN — the founder's "Ask AI is per-person"
        # decision. Keyed on agent_kind, never on an empty project_id: "has no
        # project" must never be what grants reach, or a project-less SPECIALIST
        # silently becomes visible to everyone and MAN-115's boundary widens
        # without anyone choosing it.
        return

    project_id = _text(bundle.get("project_id"))
    if project_id:
        await auth_module.enforce_project_access(
            current_user, resolved_workspace_id, project_id, minimum_role=minimum_role,
        )
        return

    # A project-less SPECIALIST. There is no project grant to check, so there is
    # no member-level grant at all — only workspace-wide authority reaches it.
    # This is the branch that must not fail open: `project_id` is nullable by
    # schema (ON DELETE SET NULL) and pre-migration installs were never
    # backfilled, so "no project" is a real, reachable state today, not a
    # theoretical one.
    actual_role = auth_module.normalize_rbac_role(
        auth_module.workspace_role(current_user, resolved_workspace_id)
        or auth_module.current_user_role(current_user, default="viewer"),
        default="viewer",
    )
    if auth_module.RBAC_ROLE_ORDER[actual_role] >= auth_module.RBAC_ROLE_ORDER["owner"]:
        return
    raise _refuse()


async def enforce_agent_reachable(
    current_user: Optional[Dict[str, Any]],
    resolved_workspace_id: str,
    tenant_id: str,
    agent_id: str,
    *,
    minimum_role: str = "viewer",
) -> None:
    """Raise unless `current_user` may reach `agent_id`. FAILS CLOSED.

    Every exit that is not an explicit grant raises. In particular there is no
    `if not <something>: return` anywhere below — that shape is precisely what
    made the fleet-routes predicate unusable here.
    """
    clean_agent_id = _text(agent_id)
    if not clean_agent_id:
        # Nothing named. The caller's workspace membership (already enforced by
        # the route's own dependency) is the whole gate for a workspace turn.
        return

    bundle = await lookup_agent_install_bundle(
        clean_agent_id, tenant_id=tenant_id, workspace_id=resolved_workspace_id,
    )
    if bundle is None:
        # Either the agent does not exist, it exists in another workspace, or
        # the lookup itself failed — all three are an unestablished grant, and
        # a control-plane blip must never widen access.
        raise _refuse()

    await enforce_resolved_agent_access(
        current_user, resolved_workspace_id, bundle, minimum_role=minimum_role,
    )


async def enforce_turn_agent_reachability(turn_request: Any, current_user: Any) -> None:
    """The turn-path guard. Call once per turn, at the ingress waist.

    Placed in `turn_ingress_service` rather than on each route because
    CLAUDE.md's standing rule is that a guard called once inside a large function
    is a guard the next branch skips — the same reasoning that made
    `_guard_sage_visible_reply` a wrapper over every branch instead of a call in
    four of them. Every human turn crosses `start_turn` / `start_run_start`.
    """
    if caller_is_system_principal(current_user):
        return
    agent_id = resolve_turn_agent_id(turn_request)
    if not agent_id:
        return
    await enforce_agent_reachable(
        current_user if isinstance(current_user, dict) else None,
        _text(getattr(turn_request, "workspace_id", "")) or "default",
        _text(getattr(turn_request, "tenant_id", "")) or "default",
        agent_id,
        minimum_role="viewer",
    )
