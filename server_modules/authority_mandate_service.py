"""Execution-authority mandate — a narrow owner-only floor, not a tier system.

Authority attaches to the INITIATING PRINCIPAL of a turn and is inherited by
everything that turn spawns (runs, scheduled wake-ups, delegated specialist
calls). There is no approval UX here — a turn stays fully autonomous inside
its mandate; a call outside its mandate is blocked outright with a
platform-voice reply, never queued for a human to approve.

2026-08-21 — THE TOOL-CAPABILITY TIER IS GONE. Founder's decision, stated
four times: *"tools removal is something that is going to happen anyways
there is no question about it because nobody is going to do that shit not
even me… I don't want to enable and disable and sit on the platform to press
buttons every other week."* This is the same standing rule CLAUDE.md already
records ("No tool-authority tiers — access to an agent is binary; never
weaken an agent's tools per viewer, gate who can reach it") and the same
posture as the 2026-08-19 destructive-action ruling (awareness in the prompt,
never a mechanism).

WHAT THAT REPLACED, so nobody rebuilds it: every tool used to carry an
`audience_safe` flag on its ToolDescriptor, a non-owner sender had every
unflagged tool stripped from the model's tool list mid-turn
(audience_tool_filter.py, deleted), and an owner could hand individual tools
back one at a time through a per-agent Tools tab that wrote
`mandate.audience_tools` (deleted). Deny-by-default with an eight-tool
allowlist, in other words — the inverse of what the transport we adopted
does.

WHAT IT IS NOW — OpenClaw's own shape, adopted rather than invented. Their
gateway ALLOWS every tool by default and denies exactly three
machine-administration ones (`GATEWAY_OWNER_ONLY_CORE_TOOLS = ["cron",
"gateway", "nodes"]`), plus a handful of explicit owner checks on genuinely
administrative actions. Ours is the same idea pointed at our own equivalents:
the fleet/agent-configuration family and the scheduling family.

    allowed = (tier is owner) OR (this tool is not machine administration)

STATE THE CONSEQUENCE PLAINLY, because it is real: anyone allowed to MESSAGE
an agent can now make it do anything that agent can do. The boundaries are
the channel gates (DM policy, group allowlist, mention gating — they decide
who may message it at all, before any model runs), agent reachability
(agent_reachability_service), and the agent's own configuration. If that
agent has hardware attached, "anything it can do" includes a shell on the
owner's machine.

Tiers (unchanged — they are still how a turn's principal is recorded, and
they still decide the one thing below):
  owner    — the workspace owner/admin, acting directly (web/API session, or
             a channel message that matches the owner's linked identity).
  audience — end-customers over any channel. Also the fail-safe default for
             anyone who cannot be positively identified as the owner.
  system   — scheduled/automated turns with no live sender. A system turn
             that was spawned by an earlier owner/audience turn carries that
             turn's tier permanently (see inherit_tier); a system turn with no
             traceable parent (bare platform automation) defaults to audience.
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


# ── The owner-only floor: MACHINE ADMINISTRATION, and nothing else ──────────
#
# This list is named ONCE, here, and must not grow into a second sprawling
# allowlist — that is the thing being deleted, and re-accumulating it one
# "obviously sensitive" tool at a time is how it comes back. The test for
# membership is not "could this be misused" (a shell can, and is deliberately
# NOT here) — it is "does this administer the platform itself rather than do
# the work the agent exists to do."
#
#   fleet__*   creating, reconfiguring, listing and inspecting AGENTS, plus
#              scheduling and cancelling their recurring work. OpenClaw's
#              "gateway" + "nodes" + "cron", in our vocabulary.
#   goal__*    a goal is a durable retry loop that schedules future turns
#              (see agent_goals / bounded_scheduler_service). Scheduling, so:
#              OpenClaw's "cron". Denied whole, reads included, exactly as
#              they deny the whole `cron` tool rather than half of it.
#
# Both are expressed as PREFIXES so a tool added to either family tomorrow is
# covered the day it ships — a hand-listed set of the sixteen names that
# exist today is the shape that goes stale silently.
OWNER_ONLY_TOOL_PREFIXES = ("fleet__", "goal__")

# The same two families reached through the connector/action id space
# ("fleet"."schedule_task") rather than through a flat tool name, which is how
# runs_execution's connector/MCP choke point sees them.
OWNER_ONLY_CONNECTOR_IDS = frozenset({"fleet", "goal"})

# Exact names outside those two prefixes. `empyralis_configure_agent` is the
# MCP-server spelling of fleet__configure_agent (mcp_server.py) — the same
# administrative action reached by an external agent over the MCP surface,
# so it answers to the same rule. CLAUDE.md's agent-context-grant section is
# explicit that a model must never be able to widen its own configuration.
OWNER_ONLY_TOOL_NAMES = frozenset({"empyralis_configure_agent"})


def is_owner_only_tool(
    *,
    tool_name: object = "",
    connector_id: object = "",
    action_id: object = "",
) -> bool:
    """Is this call machine administration (owner-only), by any of its names?

    A caller may know the flat tool name, the connector/action pair, or both;
    any one of them matching is sufficient. `action_id` is accepted so the
    signature matches how the two choke points already address a call, and so
    a future rule that needs it does not change every call site.
    """
    name = str(tool_name or "").strip().lower()
    connector = str(connector_id or "").strip().lower()
    if name:
        if name in OWNER_ONLY_TOOL_NAMES:
            return True
        if any(name.startswith(prefix) for prefix in OWNER_ONLY_TOOL_PREFIXES):
            return True
    if connector and connector in OWNER_ONLY_CONNECTOR_IDS:
        return True
    return False


def derive_tier_from_sender_class(sender_class: Optional[str]) -> str:
    """Derive the authority tier from a resolved sender classification.

    Mirrors triage_service.resolve_sender_identity()'s output
    ("owner" | "audience" | "unknown"). Anything that isn't positively
    "owner" — audience, unknown, empty, or garbage input — is "audience".
    This is the one and only place "unknown" gets collapsed into a tier; it
    must fail toward the least-privileged tier, never toward "owner".
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


def is_tool_call_allowed(
    tier: object,
    *,
    tool_name: object = "",
    connector_id: object = "",
    action_id: object = "",
) -> bool:
    """The single mandate enforcement rule — ALLOW by default.

    Owner tier is allowed everything. Every other tier — audience, system, or
    anything invalid/unrecognized — is allowed everything EXCEPT machine
    administration (is_owner_only_tool). Unrecognized tier values are
    normalized (fail safe to audience) before the check, so a caller can
    never accidentally reach the owner branch by passing a malformed tier.

    Note the direction of the default: a tool this function has never heard
    of is ALLOWED, which is deliberate and is the inversion. The safety story
    is no longer "the model cannot reach this tool" — it is "this sender
    could not reach this agent at all" (the channel gates and
    agent_reachability_service), decided before a model ever runs.
    """
    if normalize_tier(tier) == TIER_OWNER:
        return True
    return not is_owner_only_tool(
        tool_name=tool_name, connector_id=connector_id, action_id=action_id
    )
