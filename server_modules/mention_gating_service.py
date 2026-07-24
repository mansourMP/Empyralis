"""ONE shared, centralized mention-gating resolver — the single source of
truth for "should this inbound GROUP message be skipped for lack of an
explicit address."

Mirrors OpenClaw's mechanism (dist/mention-gating-3P8aSD7o.js /
src/channels/mention-gating.ts — see docs/OpenClaw.md's GROUP/MENTION
GATING section, researched 2026-07-19):

    shouldSkip = requireMention && canDetectMention && !wasMentioned
    effectiveWasMentioned = wasMentioned || implicitMention

This module owns ONLY that formula plus the fact-extraction that feeds it.
It does not know about dm_policy, group_policy storage, or any specific
channel's wire format beyond the small, already-normalized set of fields
personal_channels_service's handlers pass through today (is_mentioned,
is_reply_to_sage). group_policy's identity axis (open|allowlist|disabled —
"is this GROUP allowed to have the agent active at all") lives in
personal_channels_service._enforce_group_policy, which calls into this
module for the SEPARATE mention/addressing axis.

HARD CONSTRAINT: this module must NEVER read message text/topic/content —
only pre-computed, platform-level addressing facts (an explicit @mention
entity, a reply-to-agent linkage) that a channel plugin (Telegram/WhatsApp
runtime.ts, a local bridge) computed from platform semantics, never from
what the message says. If a future caller is tempted to pass message text
in here, that is the wrong layer for it — see personal_channels_service's
own "NEVER filter on message CONTENT" ruling.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional, Set

# Implicit-mention kinds this resolver understands today. "reply_to_agent"
# is the one Empyralis's channels already compute (Telegram/WhatsApp/local-
# bridge runtimes' is_reply_to_sage: the sender replied directly to a
# message the agent itself sent in this chat — addressed exactly like an
# explicit @mention, per the pre-existing group-gate comments this module
# replaces). A future channel could add more kinds (e.g. OpenClaw's own
# provider-specific implicit kinds) without changing this formula — just
# extend KNOWN_IMPLICIT_MENTION_KINDS and mention_facts_from_message.
IMPLICIT_MENTION_KIND_REPLY_TO_AGENT = "reply_to_agent"
KNOWN_IMPLICIT_MENTION_KINDS: FrozenSet[str] = frozenset({IMPLICIT_MENTION_KIND_REPLY_TO_AGENT})


def resolve_inbound_mention_decision(
    *,
    facts: Dict[str, Any],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """THE decision. Mirrors OpenClaw's resolveInboundMentionDecision({facts,
    policy}) exactly in shape (see module docstring for the formula).

    facts (booleans/sets a caller computes from PLATFORM signals only —
    never from message text):
      can_detect_mention: this channel/provider is able to compute mention
        facts at all. Defaults True — a channel that genuinely can't detect
        mentions must pass False explicitly to avoid ever skipping (fail
        OPEN on missing capability, never fail closed by accident).
      was_mentioned: an explicit @mention/text-mention entity targeted the
        agent's own identity (never the raw "message.mentioned" platform
        bit — see docs/OpenClaw.md's hasExplicitTelegramMention note on why
        that distinction is the actual fix for a prior production bug).
      implicit_mention_kinds: iterable of implicit-address kinds this
        message satisfies (e.g. {"reply_to_agent"}).

    policy (owner-configured, resolved BEFORE this call from group_policy
    storage — never derived from message content):
      is_group: False short-circuits to never-skip; mention gating only
        ever applies inside a group — a DM is always "addressed".
      require_mention: the owner's requireMention lever. False (the
        product default — Ruling A, "see-and-decide") means shouldSkip is
        always False: the agent sees every group message and exercises its
        own judgment (the [SILENT] sentinel) about whether to reply, the
        same way a human group member would. True (an explicit owner
        opt-in) restores a hard mention-only gate.
      allowed_implicit_mention_kinds: optional iterable restricting which
        implicit_mention_kinds count as "addressed" for this policy.
        Defaults to KNOWN_IMPLICIT_MENTION_KINDS (all of them).

    Returns {"should_skip": bool, "effective_was_mentioned": bool}.
    """
    if not bool(policy.get("is_group")):
        return {"should_skip": False, "effective_was_mentioned": True}

    require_mention = bool(policy.get("require_mention"))
    can_detect_mention = bool(facts.get("can_detect_mention", True))
    was_mentioned = bool(facts.get("was_mentioned"))

    allowed_implicit_raw = policy.get("allowed_implicit_mention_kinds")
    allowed_implicit_kinds: Set[str] = (
        {str(kind) for kind in allowed_implicit_raw}
        if allowed_implicit_raw is not None
        else set(KNOWN_IMPLICIT_MENTION_KINDS)
    )
    implicit_kinds = {str(kind) for kind in (facts.get("implicit_mention_kinds") or ())}
    implicit_mention = bool(implicit_kinds & allowed_implicit_kinds)

    effective_was_mentioned = was_mentioned or implicit_mention
    should_skip = bool(require_mention and can_detect_mention and not effective_was_mentioned)
    return {"should_skip": should_skip, "effective_was_mentioned": effective_was_mentioned}


def mention_facts_from_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical wire-field -> resolver-facts mapping. THE one place every
    personal-channel handler (WhatsApp/Telegram/local-bridge/cloud) reads
    is_mentioned/is_reply_to_sage from, instead of each re-deriving its own
    shape inline. Reads ONLY those two pre-computed booleans — never
    message text — matching the channel-plugin-computes-facts contract
    mention-gating.ts's own callers follow (hasExplicitTelegramMention,
    WhatsApp's mentionedJid scan: see docs/OpenClaw.md).

    can_detect_mention is always True here: every personal channel that
    reaches this function already computes is_mentioned gateway-side (a
    missing/absent field just reads as False, i.e. "not mentioned", not
    "can't tell" — see the existing is_group-absent-defaults-to-ungated
    backward-compat contract these handlers already honor for is_group
    itself).
    """
    implicit_kinds = set()
    if bool(message.get("is_reply_to_sage")):
        implicit_kinds.add(IMPLICIT_MENTION_KIND_REPLY_TO_AGENT)
    return {
        "can_detect_mention": True,
        "was_mentioned": bool(message.get("is_mentioned")),
        "implicit_mention_kinds": implicit_kinds,
    }
