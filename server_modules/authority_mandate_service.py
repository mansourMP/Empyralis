"""Execution-authority mandate — scoping, not approval.

Authority attaches to the INITIATING PRINCIPAL of a turn and is inherited by
everything that turn spawns (runs, scheduled wake-ups, delegated specialist
calls). There is no approval UX here — a turn stays fully autonomous inside
its mandate; a call outside its mandate is blocked outright with a
platform-voice reply, never queued for a human to approve.

Tiers:
  owner    — the workspace owner/admin, acting directly (web/API session, or
             a channel message that matches the owner's linked identity).
  audience — end-customers over any channel. Also the fail-safe default for
             anyone who cannot be positively identified as the owner.
  system   — scheduled/automated turns with no live sender. A system turn
             that was spawned by an earlier owner/audience turn carries that
             turn's tier permanently (see inherit_tier); a system turn with no
             traceable parent (bare platform automation) defaults to audience.

Enforcement is a single rule: a non-owner tier may only call tools marked
audience_safe in their ToolDescriptor manifest (skills_service.ToolDescriptor).
"owner" is the only tier that ever bypasses that check — "audience" and
"system" are both restricted, which is why callers don't need to special-case
"system" anywhere except when they persist it for audit provenance.
"""

from __future__ import annotations

from typing import Optional

TIER_OWNER = "owner"
TIER_AUDIENCE = "audience"
TIER_SYSTEM = "system"

VALID_TIERS = frozenset({TIER_OWNER, TIER_AUDIENCE, TIER_SYSTEM})

# Distinct from the generic "blocked_action" event_class so mandate blocks
# can be queried/alerted on independently of kill-switch/budget/broker blocks.
MANDATE_BLOCKED_EVENT_CLASS = "mandate_blocked"

MANDATE_BLOCKED_MESSAGE = (
    "Heads up: that action is only available to the workspace owner."
)

# A choke point (skills_service's tool-execution gate, runs_execution's
# connector/MCP gate) or a wake-request consumer (bounded_scheduler_service)
# reached a point where it needed a tier and found none stamped — logged so
# the gap is observable rather than silently passing through. Distinct from
# MANDATE_BLOCKED_EVENT_CLASS: this is "we couldn't tell," not "we could tell
# and said no."
MANDATE_UNATTRIBUTED_EVENT_CLASS = "mandate_unattributed"


def derive_tier_from_sender_class(sender_class: Optional[str]) -> str:
    """Derive the authority tier from a resolved sender classification.

    Mirrors triage_service.resolve_sender_identity()'s output
    ("owner" | "audience" | "unknown") and audience_tool_filter's
    resolve_sender_class(). Anything that isn't positively "owner" — audience,
    unknown, empty, or garbage input — is "audience". This is the one and
    only place "unknown" gets collapsed into a tier; it must fail toward the
    least-privileged tier, never toward "owner".
    """
    return TIER_OWNER if str(sender_class or "").strip().lower() == TIER_OWNER else TIER_AUDIENCE


def derive_tier_from_owner_flag(is_owner: bool) -> str:
    """Derive the authority tier from a boolean owner check.

    For call sites keyed off an authenticated ``current_user`` dict (web/API
    sessions) rather than a channel sender identity — e.g. agent_turn.py's
    own ``_current_user_is_owner()`` / auth.py's ``workspace_role()``.
    """
    return TIER_OWNER if bool(is_owner) else TIER_AUDIENCE


def normalize_tier(value: object) -> str:
    """Coerce an arbitrary stored/passed value into a valid tier.

    Used whenever a tier is read back (from a session_ctx dict, a persisted
    wake-request payload, a deserialized turn contract, ...) rather than
    freshly derived. Invalid or missing input fails safe to "audience" —
    never to "owner" — so a dropped field degrades to "more restricted",
    not "more privileged".
    """
    token = str(value or "").strip().lower()
    return token if token in VALID_TIERS else TIER_AUDIENCE


def inherit_tier(parent_tier: object) -> str:
    """Resolve the tier a spawned run/schedule/delegation must carry.

    Any run, scheduled task, delegation, or follow-up turn created FROM a
    turn carries that turn's tier permanently — an audience-tier turn must
    never spawn work that later executes as owner. This is a thin alias over
    normalize_tier: same fail-safe behavior, named for the inheritance call
    site so the intent ("this value is being carried forward, not derived
    fresh from a sender") is visible in the caller.
    """
    return normalize_tier(parent_tier)


def is_tool_call_allowed(tier: object, *, audience_safe: bool) -> bool:
    """The single mandate enforcement rule.

    Owner tier bypasses the check entirely. Every other tier — audience,
    system, or anything invalid/unrecognized — may only call tools the
    manifest marks audience_safe=True. Unrecognized tier values are
    normalized (fail safe to audience) before the check, so a caller can
    never accidentally grant access by passing a malformed tier string.
    """
    return normalize_tier(tier) == TIER_OWNER or bool(audience_safe)


def connector_tool_key(connector_id: object, action_id: object) -> str:
    """Canonical id for a connector/MCP action inside a mandate.audience_tools
    allowlist — "{connector_id}.{action_id}", matching the id format already
    used for metering/ledger entries in runs_execution.py."""
    return f"{str(connector_id or '').strip().lower()}.{str(action_id or '').strip().lower()}"


def is_audience_tool_allowed(audience_tools: object, tool_key: str) -> bool:
    """Owner-declared mandate check for connector/MCP actions, which have no
    catalog-level audience_safe manifest flag (skills_service.ToolDescriptor
    only covers local/builtin tools). Connector/MCP actions default to NOT
    audience_safe — this is the only way one becomes audience-callable: the
    owner explicitly lists it in this agent's mandate.audience_tools."""
    if not isinstance(audience_tools, (list, tuple, set, frozenset)):
        return False
    key = str(tool_key or "").strip().lower()
    return bool(key) and key in {str(t or "").strip().lower() for t in audience_tools}
