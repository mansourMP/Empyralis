"""@-mention parsing and resolution for task comments (MAN-66).

FIRST QUESTION ANSWERED, BEFORE WRITING ANY OF THIS: does a shared mention
resolver already exist that this should reuse? MAN-79 shipped "one shared
mention resolver" (`server_modules/mention_gating_service.py`) — but it is
NOT this. That module answers a completely different question for GROUP
CHANNELS ("was this inbound platform message addressed to the agent at
all?", a boolean) from PRE-COMPUTED platform booleans
(`is_mentioned`/`is_reply_to_sage`) and its own docstring makes it a HARD
CONSTRAINT that it must never read message text. It has no concept of
resolving a name string to a specific agent/user identity, and structurally
cannot grow one without breaking that constraint. MAN-117's title ("...not
on the shared mention resolver") confirms the same thing — that resolver is
about Slack/Discord group gating, unrelated to this feature.

What DOES already exist, and IS reused here, is the set of breadcrumbs
several engineers already left for exactly this module:
  - `routes_fleet.py`'s `fleet_assign_task` docstring: "assign_task is the
    ONE code path a future @-mention resolver must also call for agents."
    Read literally this reads as "call assign_task," but
    docs/design/tasks-to-agents-research.md §2 pitfall #2 (the doc that
    line cites) is explicit that assignment and mention must differ ONLY
    in whether `assignee_agent_id` changes — mentioning must NOT reassign
    the task. What's actually shared is the WAKE mechanism
    (`bounded_scheduler_service.schedule_task_commented_wakeup`), not the
    ownership-changing `assign_task` call itself. This module calls the
    wake function, not `assign_task`.
  - `mcp_external_agent_roster_service.list_unified_roster`'s docstring:
    "the ONE interface a future @-mention resolver reads." For AGENT
    identity this is the right north star, but it is built for the
    external-MCP-roster UI: it routes through `agent_registry_repository`
    (RLS-scoped connections + registry-seeding side effects) rather than
    the lightweight `ensure_control_plane_schema()` pool this file's
    sibling functions (`_agent_install_exists`, `_workspace_user_exists` in
    project_tasks_service.py) already use, and it also surfaces EXTERNAL
    agents. `assign_task` only ever validates against
    `workspace_agent_installs` (see `_agent_install_exists`) — an external
    agent cannot be a task assignee today (MAN-66's own tracking notes this
    explicitly). Since mention-driven WAKING reuses `assign_task`'s own
    validated agent scope on purpose (an external agent has no runtime this
    scheduler can wake), this module queries `workspace_agent_installs`
    directly, matching `_agent_install_exists`'s exact scope, rather than
    going through `list_unified_roster` and then having to filter its
    output back down to `kind == "platform"` anyway. Extending
    `list_unified_roster`-based mention support to external agents (chip
    rendering without waking) is real, reasonable v2 scope — deliberately
    not built here.

SCOPING (hard security constraint): every roster query below filters by
BOTH tenant_id AND workspace_id, mirroring `_agent_install_exists` /
`_workspace_user_exists` exactly. A mention can never resolve to an agent
or member outside the caller's own workspace — there is no code path here
that queries without both filters.

PARSING RULES (documented here since there is no UI-driven composer token
yet — see TaskComposer's own plain <textarea>; a structured autocomplete
that inserts an unambiguous token is real future work, noted in MAN-66):

  - An `@` only starts a mention candidate when it is NOT preceded by a
    "word" character (letter/digit/underscore) and IS followed by one.
    This is what excludes email addresses ("reach me at jane@example.com"
    — the `@` is preceded by "e") while still allowing "(@Atlas can you
    help?)" (preceded by "(") or "@Atlas" at the very start of a comment.
  - Content inside inline code spans (`` `...` ``) is never scanned for
    mentions — "printf uses `@decorator` syntax" produces no mention.
  - After a candidate `@`, the "run" of possible name characters
    (letters/digits/`_`/`-`/`'`/`.`, with single spaces allowed BETWEEN
    words) is captured, capped at a small word/char count so a pathological
    input can't make this scan slow. The run is then matched against the
    workspace's roster using GREEDY LONGEST-PREFIX MATCH: try the whole run
    first, then drop one trailing word at a time, until a prefix exactly
    (case-insensitively) equals some roster entry's display name. This is
    what correctly resolves "@Jane Doe, can you look?" to the person named
    "Jane Doe" (2 words) rather than stopping at a same-named "Jane" if one
    also existed, while still falling back to shorter names when the full
    run matches nothing.
  - UNKNOWN names (no prefix matches anything in the roster): left as
    plain text. Never fabricated, never partially resolved.
  - AMBIGUOUS names (a given prefix's *display name* matches more than one
    roster entry — e.g., a human and an agent that happen to share a
    display name, since only the external-agent roster append path
    enforces name uniqueness and only among agents) are a TERMINAL match
    failure for that occurrence: this module does NOT fall through to a
    shorter prefix once it finds an ambiguous one. Falling through would
    silently resolve "@Jane Doe" (ambiguous) to an unrelated third person
    also named "Jane" — a worse outcome than just leaving it as text. The
    whole `@`-occurrence renders as plain text instead.
  - Matching is exact (case-insensitive) on the full display name only —
    no fuzzy or prefix-only matching in v1. A real composer autocomplete
    (picking a name from a list rather than free-typing it) would remove
    this ambiguity/unknown-name class of problem entirely; that is
    explicitly out of scope for this pass (see MAN-66's own "secondary"
    note on the composer).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from server_modules import control_plane_repository
from server_modules.config_loader import config_int

LOGGER = logging.getLogger(__name__)

# STEP 6 doctrine (bounded_scheduler_service.py's own words: "every
# framework studied ... backstops agent reasoning with a hard numeric
# ceiling, never reasoning alone") applied here: a single comment must
# never be able to wake an unbounded number of agents. 3 is deliberately
# small — enough for a real "@researcher @writer @reviewer, thoughts?"
# three-way nudge, far short of "five agents from one comment" (the
# failure mode this constant exists to rule out). Additional protection
# beyond this per-comment cap: schedule_task_commented_wakeup's own
# per-(task, agent) debounce (see bounded_scheduler_service.py) and the
# shared, task-wide DEFAULT_MAX_WAKES_PER_TASK_PER_DAY ceiling both still
# apply on top of this — this cap bounds ONE comment, those bound a
# whole day. Tunable via EMPYRALIS_MAX_MENTIONED_AGENT_WAKES_PER_COMMENT.
DEFAULT_MAX_MENTIONED_AGENT_WAKES_PER_COMMENT = 3

# Notifications are cheap and never consume the scheduler's wake budget or
# spawn an agent turn — the actually expensive/risky resource that
# DEFAULT_MAX_MENTIONED_AGENT_WAKES_PER_COMMENT exists to protect. A looser
# but still finite bound is appropriate here; this only exists to stop one
# comment from fanning out to the entire membership of a huge workspace.
# Tunable via EMPYRALIS_MAX_MENTIONED_HUMAN_NOTIFICATIONS_PER_COMMENT.
DEFAULT_MAX_MENTIONED_HUMAN_NOTIFICATIONS_PER_COMMENT = 10


def max_mentioned_agent_wakes_per_comment() -> int:
    return max(
        1,
        config_int(
            "EMPYRALIS_MAX_MENTIONED_AGENT_WAKES_PER_COMMENT",
            DEFAULT_MAX_MENTIONED_AGENT_WAKES_PER_COMMENT,
        ),
    )


def max_mentioned_human_notifications_per_comment() -> int:
    return max(
        1,
        config_int(
            "EMPYRALIS_MAX_MENTIONED_HUMAN_NOTIFICATIONS_PER_COMMENT",
            DEFAULT_MAX_MENTIONED_HUMAN_NOTIFICATIONS_PER_COMMENT,
        ),
    )


# ── Parsing (pure, no I/O) ───────────────────────────────────────────────

_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
# `@` not preceded by a word char (excludes "jane@example.com"), followed
# by a name-run character.
_MENTION_START_RE = re.compile(r"(?<!\w)@(?=[A-Za-z0-9_])")
_NAME_CHAR_RE = re.compile(r"[A-Za-z0-9_'\-.]")

_MAX_MENTION_RUN_WORDS = 8
_MAX_MENTION_RUN_CHARS = 80


def _code_span_ranges(text: str) -> List[Tuple[int, int]]:
    return [(m.start(), m.end()) for m in _CODE_SPAN_RE.finditer(text)]


def _inside_any(position: int, ranges: Sequence[Tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in ranges)


def _maximal_run_end(text: str, start: int) -> int:
    """Given `start` is the index right after an `@`, return the exclusive
    end index of the maximal "name-shaped" run: words of _NAME_CHAR_RE
    characters, separated by single spaces, capped so a pathological input
    (a very long comment with no real mention in it) can't make this scan
    expensive."""
    n = len(text)
    i = start
    end = start
    words = 0
    while i < n and words < _MAX_MENTION_RUN_WORDS and (i - start) < _MAX_MENTION_RUN_CHARS:
        j = i
        while j < n and _NAME_CHAR_RE.match(text[j]):
            j += 1
        if j == i:
            break
        end = j
        words += 1
        i = j
        if i < n and text[i] == " " and i + 1 < n and _NAME_CHAR_RE.match(text[i + 1]):
            i += 1
            continue
        break
    return end


def find_mention_candidates(text: str) -> List[Tuple[int, int, str]]:
    """Pure text scan: returns `(start, end, run_text)` for every `@`-started
    name-shaped run in `text`, skipping inline code spans and email-shaped
    occurrences. `start` is the index of `@` itself; `end` is the exclusive
    end of the run (NOT including the leading `@`). Does not resolve
    anything against a roster — that is resolve_mention_candidates's job,
    kept separate so parsing is independently testable without any DB."""
    if not text:
        return []
    code_ranges = _code_span_ranges(text)
    candidates: List[Tuple[int, int, str]] = []
    for match in _MENTION_START_RE.finditer(text):
        at_index = match.start()
        if _inside_any(at_index, code_ranges):
            continue
        run_end = _maximal_run_end(text, at_index + 1)
        if run_end <= at_index + 1:
            continue
        candidates.append((at_index, run_end, text[at_index + 1 : run_end]))
    return candidates


def resolve_mention_candidates(
    candidates: Sequence[Tuple[int, int, str]],
    *,
    roster: Sequence[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Greedy-longest-prefix-match each candidate run against `roster`
    (a flat list of `{"kind": "agent"|"user", "id": ..., "display_name":
    ...}`, both kinds mixed together on purpose — ambiguity is checked
    across the WHOLE roster, not per-kind, exactly like
    `_existing_roster_names` in mcp_external_agent_roster_service.py
    already does for agent-vs-agent name collisions). Returns only
    RESOLVED mentions — unknown/ambiguous candidates are silently dropped
    (the caller renders/stores nothing for them; the raw `@text` stays in
    the comment body untouched)."""
    by_name: Dict[str, List[Dict[str, str]]] = {}
    for entry in roster:
        name = str(entry.get("display_name") or "").strip().lower()
        if not name:
            continue
        by_name.setdefault(name, []).append(entry)

    resolved: List[Dict[str, Any]] = []
    for at_index, run_end, run_text in candidates:
        words = run_text.split(" ")
        match_entry: Optional[Dict[str, str]] = None
        consumed_words = 0
        for length in range(len(words), 0, -1):
            candidate_name = " ".join(words[:length]).strip().lower()
            if not candidate_name:
                continue
            matches = by_name.get(candidate_name) or []
            if len(matches) == 1:
                match_entry = matches[0]
                consumed_words = length
                break
            if len(matches) > 1:
                # Ambiguous at this length -- terminal for this occurrence.
                # Deliberately do NOT fall through to a shorter prefix (see
                # module docstring): a wrong-but-confident resolution is
                # worse than none.
                match_entry = None
                consumed_words = 0
                break
            # zero matches at this length -- try one word shorter.
        if match_entry is None:
            continue
        consumed_text = " ".join(words[:consumed_words])
        mention_end = at_index + 1 + len(consumed_text)
        resolved.append(
            {
                "raw": "@" + consumed_text,
                "start": at_index,
                "end": mention_end,
                "kind": match_entry["kind"],
                "id": match_entry["id"],
                "display_name": match_entry["display_name"],
            }
        )
    return resolved


# ── Roster loading (I/O; workspace-scoped) ───────────────────────────────


async def _load_agent_roster(pool: Any, *, tenant_id: str, workspace_id: str) -> List[Dict[str, str]]:
    """Mirrors `_agent_install_exists`'s exact scope (tenant_id +
    workspace_id against `workspace_agent_installs`) -- deliberately the
    SAME table/scope `assign_task` itself validates against, since
    mention-driven waking must only ever target an agent `assign_task`
    itself could also address. See module docstring for why this does not
    route through `list_unified_roster` (which also includes EXTERNAL
    agents -- not valid wake targets, see `assign_task`'s own validation)."""
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT id, label
        FROM workspace_agent_installs
        WHERE tenant_id = $1 AND workspace_id = $2
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    out: List[Dict[str, str]] = []
    for row in rows or []:
        r = dict(row)
        agent_id = str(r.get("id") or "").strip()
        label = str(r.get("label") or "").strip()
        if not agent_id or not label:
            continue
        out.append({"kind": "agent", "id": agent_id, "display_name": label})
    return out


async def _load_member_roster(pool: Any, *, tenant_id: str, workspace_id: str) -> List[Dict[str, str]]:
    """Mirrors `_workspace_user_exists`'s exact scope (tenant_id +
    workspace_id + status='active' against `workspace_memberships` joined
    to `users`)."""
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    rows = await control_plane_repository.rls_fetch(
        pool,
        """
        SELECT wm.user_id, u.email, u.display_name
        FROM workspace_memberships wm
        JOIN users u ON u.id = wm.user_id
        WHERE wm.tenant_id = $1 AND wm.workspace_id = $2 AND wm.status = 'active'
        """,
        resolved_tenant_id,
        resolved_workspace_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )
    out: List[Dict[str, str]] = []
    for row in rows or []:
        r = dict(row)
        user_id = str(r.get("user_id") or "").strip()
        display_name = str(r.get("display_name") or "").strip() or str(r.get("email") or "").strip()
        if not user_id or not display_name:
            continue
        out.append({"kind": "user", "id": user_id, "display_name": display_name})
    return out


async def load_mention_roster(*, tenant_id: str, workspace_id: str, pool: Any = None) -> List[Dict[str, str]]:
    """The workspace's full addressable-by-@mention roster: every platform
    agent install + every active workspace member, tenant+workspace scoped.
    `pool` may be passed in by a caller that already holds one (e.g.
    `add_task_comment`, which needs a pool for its own write anyway) to
    avoid a redundant `ensure_control_plane_schema()` round-trip; when
    omitted this acquires its own."""
    resolved_pool = pool
    if resolved_pool is None:
        resolved_pool = await control_plane_repository.ensure_control_plane_schema()
    if resolved_pool is None:
        return []
    agents = await _load_agent_roster(resolved_pool, tenant_id=tenant_id, workspace_id=workspace_id)
    members = await _load_member_roster(resolved_pool, tenant_id=tenant_id, workspace_id=workspace_id)
    return agents + members


async def resolve_task_mentions(
    *, tenant_id: str, workspace_id: str, body: str, pool: Any = None,
) -> List[Dict[str, Any]]:
    """The one orchestration entry point: load this workspace's roster,
    parse `body` for `@`-candidates, resolve against the roster. Never
    raises -- a roster-load failure (e.g. Postgres unavailable) degrades to
    "no mentions resolved" (the comment still posts as plain text) rather
    than blocking the comment itself, matching this feature's own "never
    block the agent loop" constraint applied to the human/agent posting the
    comment too."""
    text = str(body or "")
    if not text or "@" not in text:
        return []
    try:
        roster = await load_mention_roster(tenant_id=tenant_id, workspace_id=workspace_id, pool=pool)
    except Exception:
        LOGGER.warning("Failed to load mention roster for %s/%s", tenant_id, workspace_id, exc_info=True)
        return []
    if not roster:
        return []
    candidates = find_mention_candidates(text)
    if not candidates:
        return []
    return resolve_mention_candidates(candidates, roster=roster)


# ── Dispatch (wake agents / notify humans; bounded, self-mention-safe) ──


def _is_self_mention(mention: Dict[str, Any], *, author_type: str, author_id: str) -> bool:
    author_type_norm = str(author_type or "").strip().lower()
    author_id_norm = str(author_id or "").strip()
    if not author_id_norm:
        return False
    if mention["kind"] == "agent":
        return author_type_norm == "agent" and mention["id"] == author_id_norm
    # kind == "user": add_human_task_comment always hardcodes "human"; some
    # callers historically use "user" for the same identity space (see
    # commentAuthorLabel's own author_type == "user" or "human" handling in
    # TaskDetailView.tsx) -- treat both as the same person.
    return author_type_norm in {"human", "user"} and mention["id"] == author_id_norm


async def dispatch_resolved_mentions(
    *,
    tenant_id: str,
    workspace_id: str,
    task_id: str,
    task_title: str,
    resolved_mentions: Sequence[Dict[str, Any]],
    author_type: str,
    author_id: str,
    comment_body: str = "",
    triggered_by: Optional[str] = None,
) -> Dict[str, Any]:
    """For each resolved mention: an AGENT gets woken (via the existing,
    shared `schedule_task_commented_wakeup` -- same debounce + same
    task-wide daily ceiling as the assignee-comment wake path, so quiet
    hours/battery/network policy gates already enforced there apply here
    automatically, unchanged); a USER gets a real notification, never a
    wakeup ("people are not woken by schedulers" -- this feature's own hard
    constraint, mirrored from assign_task_to_user's identical rule).

    SELF-MENTION: dropped before either dispatch, universally (not just for
    agents) -- an author mentioning their own identity is a no-op, not a
    wake and not a notification. This is what makes it safe for this
    function to run for EVERY comment author (human, agent, or system) via
    the single shared call site in `add_task_comment`, rather than needing
    a second, agent-specific code path with its own self-check.

    BOUNDING: de-duplicated first, preserving first-occurrence order, then
    each kind is capped independently (see the module-level constants and
    their docstrings) -- a comment mentioning 6 agents wakes at most
    `max_mentioned_agent_wakes_per_comment()` of them, never all 6.

    Never raises: every dispatch is individually try/except'd, mirroring
    add_human_task_comment's own "the comment already succeeded, a
    notify/wake failure must not undo it" contract."""
    resolved_author_type = str(author_type or "").strip().lower()
    resolved_author_id = str(author_id or "").strip()
    resolved_triggered_by = str(triggered_by or author_id or "owner").strip() or "owner"
    resolved_task_id = str(task_id or "").strip()
    resolved_title = str(task_title or "").strip()

    seen: set[Tuple[str, str]] = set()
    agent_targets: List[Dict[str, Any]] = []
    human_targets: List[Dict[str, Any]] = []
    for mention in resolved_mentions:
        key = (str(mention.get("kind") or ""), str(mention.get("id") or ""))
        if key in seen or not key[1]:
            continue
        seen.add(key)
        if _is_self_mention(mention, author_type=resolved_author_type, author_id=resolved_author_id):
            continue
        if mention["kind"] == "agent":
            agent_targets.append(mention)
        elif mention["kind"] == "user":
            human_targets.append(mention)

    agent_cap = max_mentioned_agent_wakes_per_comment()
    human_cap = max_mentioned_human_notifications_per_comment()

    woken_agent_ids: List[str] = []
    wake_errors: List[Dict[str, str]] = []
    notified_user_ids: List[str] = []
    notify_errors: List[Dict[str, str]] = []

    if agent_targets and resolved_task_id and resolved_title:
        from server_modules import bounded_scheduler_service

        for mention in agent_targets[:agent_cap]:
            target_agent_id = str(mention["id"])
            try:
                record = await bounded_scheduler_service.schedule_task_commented_wakeup(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    agent_id=target_agent_id,
                    task_id=resolved_task_id,
                    title=resolved_title,
                    comment_body=comment_body,
                    triggered_by=resolved_triggered_by,
                )
                if record is not None:
                    woken_agent_ids.append(target_agent_id)
            except Exception as exc:
                wake_errors.append({"agent_id": target_agent_id, "error": str(exc)})
                LOGGER.warning(
                    "Mention wake failed for agent %s on task %s: %s", target_agent_id, resolved_task_id, exc,
                )

    if human_targets:
        from server_modules import outbox_service

        for mention in human_targets[:human_cap]:
            target_user_id = str(mention["id"])
            try:
                outbox_service.emit_notification_event(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    action="task_mention",
                    text=f'You were mentioned on "{resolved_title or resolved_task_id}"',
                    metadata={
                        "task_id": resolved_task_id,
                        "mentioned_user_id": target_user_id,
                        "author_type": resolved_author_type,
                        "author_id": resolved_author_id,
                        "path": f"/tasks/{resolved_task_id}" if resolved_task_id else None,
                    },
                )
                notified_user_ids.append(target_user_id)
            except Exception as exc:
                notify_errors.append({"user_id": target_user_id, "error": str(exc)})
                LOGGER.warning(
                    "Mention notification failed for user %s on task %s: %s", target_user_id, resolved_task_id, exc,
                )

    return {
        "agent_wakes": woken_agent_ids,
        "wake_errors": wake_errors,
        "notified_users": notified_user_ids,
        "notify_errors": notify_errors,
    }
