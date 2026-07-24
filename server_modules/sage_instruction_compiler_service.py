from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from server_modules.channel_adapter import ChannelOrigin

from server_modules import sage_skills_api
from server_modules import tool_registry_service
from server_modules import workspace_context
from server_modules import workspace_context_memory_adapter

# Founder ruling (2026-07-23, final): SOUL.md/IDENTITY.md/USER.md/GOALS.md/
# AGENTS.md/TOOLS.md are removed from the root-file taxonomy entirely (see
# workspace_context.py's ALLOWED_CONTEXT_FILENAMES and
# docs/design/root-taxonomy-removal-scope.md). MEMORY.md is now the only
# "official" root memory file: static persona/operating-rule copy that used
# to live in SOUL.md/AGENTS.md/TOOLS.md belongs in the always-in-window
# kernel/system prompt instead (see _kernel_prompt below); durable per-user
# facts that used to live in USER.md/IDENTITY.md/GOALS.md now live in
# MEMORY.md-indexed topic files (memory/files/profile.md,
# memory/files/goals.md) pulled on demand via memory_search/memory_get, the
# same as any other topic file.
OFFICIAL_ROOT_MEMORY_FILES: tuple[str, ...] = (
    "MEMORY.md",
)
LEGACY_ROOT_MEMORY_FILES: tuple[str, ...] = (
    "HEARTBEAT.md",
)
ROOT_MEMORY_BRIEF_PRIORITY: tuple[str, ...] = (
    "MEMORY.md",
)
# The agent's always-loaded operating instructions tier -- now empty: with
# the six taxonomy files removed, nothing is left in ROOT_MEMORY_BRIEF_PRIORITY
# besides MEMORY.md itself (excluded by construction below, since MEMORY.md
# gets its own dedicated index-only treatment, not full-file injection). Kept
# as a named tuple (rather than deleted outright) so build_root_memory_brief_
# sections' loop over it needs no special-casing if a future always-loaded
# instruction file is ever reintroduced.
ALWAYS_LOAD_INSTRUCTION_FILES: tuple[str, ...] = tuple(
    filename for filename in ROOT_MEMORY_BRIEF_PRIORITY if filename != "MEMORY.md"
)
ROOT_MEMORY_SECTION_CHAR_LIMIT = 12_000
ROOT_MEMORY_TOTAL_CHAR_LIMIT = 48_000
# docs/design/context-engineering-plan.md item 7 (read side): MEMORY.md used
# to compete with SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS for a shared
# 4,800-char "brief" pool (the now-removed ROOT_MEMORY_BRIEF_TOTAL_CHAR_LIMIT)
# — on any workspace where those six files were even modestly populated,
# MEMORY.md's remaining share went to zero and the whole index silently
# vanished from context (verified empirically while building this fix: six
# ~1KB root files alone already exhausted the pool). The write side
# (memory_service.MEMORY_MD_INDEX_MAX_LINES/_BYTES, same plan item) already
# guarantees MEMORY.md itself never exceeds 200 lines / 25KB — Claude Code's
# own published discipline, adopted verbatim by founder decision. This gives
# MEMORY.md a matching, DEDICATED read-time allowance, decoupled from the
# other six files' consumption, so an index the agent curated to fit that
# write-time cap loads WHOLE, never silently truncated by an unrelated
# budget. Imported from memory_service so the two sides can't drift apart;
# falls back to the same 25,000-char number if that import ever fails.
try:
    from server_modules.memory_service import MEMORY_MD_SELF_CURATION_CAP_CHARS as _MEMORY_MD_WRITE_CAP_CHARS
except Exception:
    _MEMORY_MD_WRITE_CAP_CHARS = 25_000
MEMORY_MD_LOAD_CHAR_LIMIT = int(_MEMORY_MD_WRITE_CAP_CHARS) or 25_000
SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT = 12_000
SAGE_RETRIEVED_MEMORY_CHAR_LIMIT = 3_000
SAGE_PROFILE_CONTEXT_CHAR_LIMIT = 1_500
SAGE_HEARTBEAT_CONTEXT_CHAR_LIMIT = 900
# docs/design/context-engineering-plan.md item 10: the specialist branch
# (sage_agent_runtime_service.py) has no compiler budget of its own at all —
# unlike the master path, nothing there ever clipped the assembled prompt
# against SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT. Give the specialist's
# newly-added capability manifest (previously omitted entirely) an explicit
# ceiling of its own rather than leaving it unbounded like every other piece
# of that branch's prompt.
SPECIALIST_CAPABILITY_MANIFEST_CHAR_LIMIT = 3_000
# docs/design/audit-context-anatomy.md fix #2: _normalize_recent_messages
# previously only capped each of the last 16 messages at 4,000 chars with no
# ceiling on the block as a whole — 16 genuinely long turns is ~64,000 chars
# (~16,000 tokens), several times SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT
# (12,000 chars/~3,000 tokens, the entire system prompt's own hard cap just
# below). Set to the same order of magnitude as that total so history can
# never dwarf everything else in the window; truncated oldest-first (see
# _normalize_recent_messages) so the most recent turns stay intact.
SAGE_RECENT_HISTORY_TOTAL_CHAR_LIMIT = 12_000
CAPABILITY_MANIFEST_MAX_ITEMS = 16
# Skills share this budget with the (larger) builtin-tool list, which lists
# first in build_sage_capabilities_payload. Verified empirically while
# wiring the unified skill catalog (docs/design/audit-skills.md §3 item 3):
# ~36 builtin tool records alone already exhaust CAPABILITY_MANIFEST_MAX_ITEMS,
# so skills would be silently crowded out of the rendered prompt text 100%
# of the time regardless of catalog correctness. This reserves a small slice
# so the skill catalog is never fully starved — a narrower, non-dynamic
# version of the cap-sizing follow-up audit item 6 calls for; that broader
# "scale with context window like Claude Code" redesign is still open.
CAPABILITY_MANIFEST_SKILL_RESERVED_ITEMS = 6
# docs/design/context-engineering-plan.md item 4: this limit now applies ONLY
# to manifest-only capabilities (named skills, and connector/MCP/other tools
# not yet natively schema'd this turn) — the slots where the manifest prose
# is the ONLY channel the model has. It used to also truncate tools that
# already have a full, native function schema in the same request (see
# _has_native_schema_this_turn / MODEL_HIDDEN_LEGACY_TOOLS below), which was
# pure duplication (audit-context-anatomy.md §3, §7.3) that starved the
# slots with no other channel. Raised from 140 -> 220 chars: empirically
# covers the large majority of real skill/tool descriptions in full
# (measured against the live builtin+skill catalog while building this fix
# — most cluster 90-260 chars) while still bounding the handful of outliers
# (e.g. send_image, skill_write) and leaving headroom, inside the same
# shared SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT total, for
# MEMORY_MD_LOAD_CHAR_LIMIT below (item 7, read side — see that constant's
# docstring). 320 was measured and tried first; 220 was chosen instead once
# item 7's read-side fix was added to this same wave, to keep a realistic
# rich-workspace turn from consistently maxing out the shared budget.
CAPABILITY_DESCRIPTION_CHAR_LIMIT = 220
MEMORY_MANIFEST_LIMIT = 60
MODEL_HIDDEN_LEGACY_TOOLS = {"memory_update"}
# Tools with a real, native function schema on this turn's `tools=` payload
# (see tool_registry_service.ALWAYS_ON_TOOL_NAMES and
# sage_agent_runtime_service._direct_tool_bundle's unconditional fleet__*
# addition on the master path) need no prose re-description in the manifest
# text below — the model already has their full name/description/parameters
# from the real schema. A specialist's capability_manifest never contains
# fleet__* tools by the time it reaches this module (filtered upstream via
# _specialist_tool_allowed, sage_agent_runtime_service.py's specialist
# branch), so treating any "fleet__"-prefixed tool_id as native is accurate
# for whichever caller (master or specialist) passed the manifest in.
_NATIVE_SCHEMA_TOOL_NAMES = frozenset(tool_registry_service.ALWAYS_ON_TOOL_NAMES)


@dataclass(frozen=True)
class SageInstructionBundle:
    messages: list[dict[str, str]]
    diagnostics: dict[str, Any]
    capability_manifest: list[dict[str, Any]]
    system_prompt: str
    user_message: str
    prior_messages: list[dict[str, str]]


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _external_safe_context_text(value: Any) -> str:
    return workspace_context_memory_adapter.strip_red_facts_from_external_context(_coerce_text(value))


def _estimate_token_count(value: Any) -> int:
    text = _coerce_text(value)
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _system_context_char_budget() -> int:
    return _safe_positive_int(
        os.getenv("EMPYRALIS_SAGE_SYSTEM_CONTEXT_CHAR_BUDGET"),
        SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT,
    )


def _clip_text(text: Any, limit: int, marker: str) -> tuple[str, bool]:
    value = _coerce_text(text)
    safe_limit = max(0, int(limit or 0))
    if not value or safe_limit <= 0:
        return "", bool(value)
    if len(value) <= safe_limit:
        return value, False
    suffix = f"\n[{marker}]"
    if safe_limit <= len(suffix):
        return value[:safe_limit].rstrip(), True
    body_limit = max(0, safe_limit - len(suffix))
    return value[:body_limit].rstrip() + suffix, True


def _default_context_file_content(filename: str) -> str:
    return _coerce_text(workspace_context.DEFAULT_CONTEXT_FILE_CONTENTS.get(filename))


def _meaningful_context_file_content(filename: str, value: Any) -> str:
    text = _coerce_text(value)
    if not text:
        return ""
    default = _default_context_file_content(filename)
    if default and text == default:
        return ""
    return text


def _clip_context_file_content(content: str, *, remaining_budget: int) -> tuple[str, bool]:
    safe_limit = max(0, min(ROOT_MEMORY_SECTION_CHAR_LIMIT, int(remaining_budget or 0)))
    if safe_limit <= 0:
        return "", bool(content)
    text = _coerce_text(content)
    if len(text) <= safe_limit:
        return text, False
    return text[: max(0, safe_limit - 32)].rstrip() + "\n[context file truncated]", True


def _append_file_section(
    *,
    sections: list[str],
    filename: str,
    content: str,
    title_prefix: str = "",
    remaining_budget: int,
) -> tuple[int, bool]:
    clipped, truncated = _clip_context_file_content(content, remaining_budget=remaining_budget)
    if not clipped:
        return 0, truncated
    sanitized = workspace_context_memory_adapter.strip_red_facts_from_external_context(clipped)
    if not sanitized:
        return 0, True
    title = f"{title_prefix}{filename}".strip()
    sections.append(f"### {title}\n{sanitized}")
    return len(clipped), truncated


# build_root_memory_sections (the pre-Phase-N full-file-injection predecessor
# of build_root_memory_brief_sections below) was deleted 2026-07-23 as part
# of the root-taxonomy removal: it had zero callers anywhere in this
# codebase (confirmed by repo-wide grep, and independently already flagged as
# dead in docs/design/memory-context-design.md's C4 item before this
# change), and it existed only to full-inject OFFICIAL_ROOT_MEMORY_FILES /
# LEGACY_ROOT_MEMORY_FILES -- constants this change repoints away from the
# six removed taxonomy filenames. Rather than keep a confirmed-dead function
# referencing a taxonomy that no longer exists, it's removed outright;
# build_root_memory_brief_sections is the one live path.


# Phase 0.2: Per-session context cache — avoid rebuilding context every turn
_SESSION_CONTEXT_CACHE: dict[tuple[str, str], tuple[list[str], dict[str, Any]]] = {}

def _cached_build_root_memory_brief_sections(
    context_files: Mapping[str, Any] | None,
    *,
    workspace_id: str = "",
    session_id: str = "",
) -> tuple[list[str], dict[str, Any]]:
    """Cache-aware wrapper. On session start (empty session_id or cache miss),
    builds context fresh. On subsequent turns, reuses cached result."""
    cache_key = (workspace_id, session_id)
    if cache_key in _SESSION_CONTEXT_CACHE:
        return _SESSION_CONTEXT_CACHE[cache_key]
    sections, diagnostics = build_root_memory_brief_sections(context_files)
    if workspace_id and session_id:
        _SESSION_CONTEXT_CACHE[cache_key] = (sections, diagnostics)
    return sections, diagnostics

def invalidate_session_context_cache(workspace_id: str, session_id: str = "") -> None:
    """Invalidate cache on context file writes, daily reset, or /new."""
    if session_id:
        _SESSION_CONTEXT_CACHE.pop((workspace_id, session_id), None)
    else:
        keys_to_drop = [k for k in _SESSION_CONTEXT_CACHE if k[0] == workspace_id]
        for k in keys_to_drop:
            _SESSION_CONTEXT_CACHE.pop(k, None)

def build_root_memory_brief_sections(context_files: Mapping[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    payload = dict(context_files or {})
    sections: list[str] = []
    consumed_paths: set[str] = set()
    included_official: list[str] = []
    legacy_present: list[str] = []
    extra_present: list[str] = []
    total_source_chars = 0
    total_brief_chars = 0
    truncated = False

    # ── Always-loaded instruction tier ──────────────────────────────────
    # Historical note: this loop used to full-inject SOUL/IDENTITY/USER/
    # GOALS/AGENTS/TOOLS every turn (the fix for the Pipeline B content
    # blackout, design doc finding #1 / C5). All six were removed from the
    # root-file taxonomy 2026-07-23 (see ALWAYS_LOAD_INSTRUCTION_FILES's own
    # docstring above) -- static persona/operating-rule copy moved to the
    # always-in-window kernel prompt, durable per-user facts moved to
    # MEMORY.md-indexed topic files. ALWAYS_LOAD_INSTRUCTION_FILES is empty
    # now, so this loop is a no-op; kept (rather than deleted) so a future
    # always-loaded instruction file needs no new plumbing here.
    for filename in ALWAYS_LOAD_INSTRUCTION_FILES:
        content = _meaningful_context_file_content(filename, payload.get(filename))
        if not content or total_source_chars >= ROOT_MEMORY_TOTAL_CHAR_LIMIT:
            continue
        consumed, was_truncated = _append_file_section(
            sections=sections,
            filename=filename,
            content=content,
            remaining_budget=ROOT_MEMORY_TOTAL_CHAR_LIMIT - total_source_chars,
        )
        total_source_chars += len(content)
        if consumed <= 0:
            continue
        consumed_paths.add(filename)
        included_official.append(filename)
        total_brief_chars += consumed
        truncated = truncated or was_truncated

    # ── MEMORY.md: index only, capped + backed by memory_search/memory_get ──
    # This is the one file that keeps the Phase N (Stage 5) index-only
    # treatment — correct and intentional, not part of the regression. Its
    # own dedicated MEMORY_MD_LOAD_CHAR_LIMIT (matching the write-side 200-
    # line/25KB cap) is used here instead of competing with the six
    # always-load files above for a shared pool — see that constant's
    # docstring for why (item 7, read side).
    mem_content = _meaningful_context_file_content("MEMORY.md", payload.get("MEMORY.md"))
    if mem_content:
        consumed_paths.add("MEMORY.md")
        total_source_chars += len(mem_content)
        clipped, was_truncated = _clip_text(
            mem_content,
            MEMORY_MD_LOAD_CHAR_LIMIT,
            "content truncated due to length limit",
        )
        sanitized = workspace_context_memory_adapter.strip_red_facts_from_external_context(clipped)
        if sanitized:
            sections.append(f"### MEMORY.md (Agent Memory Index)\n{sanitized}")
            included_official.append("MEMORY.md")
            total_brief_chars += len(sanitized)
            truncated = truncated or was_truncated

    # Track any remaining official files that exist on disk but weren't
    # injected above (e.g. entirely redacted by red-fact stripping) so they
    # still get accounted for and don't leak into the "extra files" bucket.
    for filename in OFFICIAL_ROOT_MEMORY_FILES:
        if filename in consumed_paths:
            continue
        if _meaningful_context_file_content(filename, payload.get(filename)):
            consumed_paths.add(filename)

    for filename in LEGACY_ROOT_MEMORY_FILES:
        if _meaningful_context_file_content(filename, payload.get(filename)):
            consumed_paths.add(filename)
            legacy_present.append(filename)

    official_set = set(OFFICIAL_ROOT_MEMORY_FILES)
    legacy_set = set(LEGACY_ROOT_MEMORY_FILES)
    for filename in sorted(_coerce_text(key) for key in payload.keys()):
        if not filename or filename in official_set or filename in legacy_set or "/" in filename:
            continue
        if _meaningful_context_file_content(filename, payload.get(filename)):
            consumed_paths.add(filename)
            extra_present.append(filename)

    memory_paths = [
        filename
        for filename in sorted(_coerce_text(key) for key in payload.keys())
        if filename
        and filename not in consumed_paths
        and filename.startswith("memory/")
        and not filename.startswith("memory/.dreams/")
        and _meaningful_context_file_content(filename, payload.get(filename))
    ]
    if memory_paths or legacy_present or extra_present:
        manifest_lines = [
            "Full root and workspace memory files stay available through memory_search and memory_get when the request needs detail."
        ]
        for label, filenames in (
            ("Legacy/extra root files", [*legacy_present, *extra_present]),
            ("Workspace memory files", memory_paths[:MEMORY_MANIFEST_LIMIT]),
        ):
            if filenames:
                manifest_lines.append(f"{label}:")
                manifest_lines.extend(f"- {path}" for path in filenames)
        if len(memory_paths) > MEMORY_MANIFEST_LIMIT:
            manifest_lines.append(f"- ... {len(memory_paths) - MEMORY_MANIFEST_LIMIT} more memory file(s)")
        sections.append("### Root Memory Index\n" + "\n".join(manifest_lines))

    return sections, {
        "included_root_files": included_official,
        "included_official_root_files": included_official,
        "legacy_context_files": legacy_present,
        "extra_context_files": extra_present,
        "available_memory_file_count": len(memory_paths),
        "context_truncated": truncated,
        "root_memory_source_chars": total_source_chars,
        "root_memory_brief_chars": total_brief_chars,
        "root_memory_chars": total_brief_chars,
        "full_root_memory_included": False,
    }


def _normalize_capability_status(value: Any) -> str:
    return _coerce_text(value).lower()


def build_model_capability_manifest(capability_payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    items = capability_payload.get("items") if isinstance(capability_payload, Mapping) else []
    manifest: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return manifest
    for item in items:
        if not isinstance(item, Mapping):
            continue
        status = _normalize_capability_status(item.get("status"))
        requires_approval = bool(item.get("requires_approval")) or status == "approval_required"
        if status not in {"ready", "approval_required"}:
            continue
        tool_id = _coerce_text(item.get("tool_id"))
        if not tool_id:
            continue
        if tool_id in MODEL_HIDDEN_LEGACY_TOOLS:
            continue
        manifest.append(
            {
                "tool": tool_id,
                "label": _coerce_text(item.get("label")) or tool_id,
                "when_to_use": _coerce_text(item.get("description")),
                "type": _coerce_text(item.get("type")) or "tool",
                "source": _coerce_text(item.get("source")),
                "approval_required": requires_approval,
                "runtime_requirement": _coerce_text(item.get("runtime_requirement")) or "cloud",
                "risk_level": _coerce_text(item.get("risk_level")),
            }
        )
    return manifest


def _has_native_schema_this_turn(tool_id: str) -> bool:
    """True when ``tool_id`` already has a real, callable function schema in
    this turn's native ``tools=`` payload — meaning re-describing it in the
    manifest's prose is pure duplication (docs/design/context-engineering-plan.md
    item 4; audit-context-anatomy.md §3, §7.3). A native tool still gets a
    full manifest line, not the name-only collapse, when it carries
    safety-relevant metadata the native JSON schema can't express — see the
    ``carries_unexpressed_flags`` check at the call site below."""
    if not tool_id:
        return False
    if tool_id in _NATIVE_SCHEMA_TOOL_NAMES:
        return True
    return tool_id.startswith("fleet__")


def _capability_manifest_text(capability_manifest: Sequence[Mapping[str, Any]]) -> str:
    if not capability_manifest:
        return ""
    lines = [
        "## Callable Tools",
        "Only these tools are callable in this turn. Do not mention or invent unavailable tools.",
    ]
    all_items = list(capability_manifest)
    skill_items = [item for item in all_items if _coerce_text(item.get("type")) == "skill"]
    other_items = [item for item in all_items if _coerce_text(item.get("type")) != "skill"]

    # Dedup pass (item 4): a tool already native this turn needs no prose
    # description here unless it carries approval/runtime metadata the
    # native schema itself can't express. Everything else in `other_items`
    # is manifest-only — skills and not-yet-pulled connector/MCP actions
    # have NO other channel to the model until skill_invoke/query_tool_registry
    # actually pulls them, so they keep the full (now un-truncated) treatment.
    native_only_tool_ids: list[str] = []
    manifest_only_other: list[Mapping[str, Any]] = []
    for item in other_items:
        tool_id = _coerce_text(item.get("tool"))
        carries_unexpressed_flags = bool(item.get("approval_required")) or (
            _coerce_text(item.get("runtime_requirement")) not in ("", "cloud")
        )
        if _has_native_schema_this_turn(tool_id) and not carries_unexpressed_flags:
            native_only_tool_ids.append(tool_id)
        else:
            manifest_only_other.append(item)

    # Within the reserved skill slice, workspace/global/bundled-filesystem
    # skills (source != "built_in") outrank the ~20 hardcoded
    # skill_registry._BUILT_IN_SKILLS entries (stable sort keeps each
    # group's incoming — alphabetical — order otherwise). Those built-ins
    # mostly duplicate capabilities already visible elsewhere in this same
    # manifest as concrete native tools (browser, memory, code execution,
    # ...); an operator-installed or agent-authored custom skill is the one
    # actually worth spending the small reserved Level-1 budget on, and a
    # blind alphabetical cut was starving every custom skill whose id
    # happened to sort after the ~20 built-ins' labels.
    skill_items = sorted(skill_items, key=lambda item: _coerce_text(item.get("source")) == "built_in")
    skill_budget = min(len(skill_items), CAPABILITY_MANIFEST_SKILL_RESERVED_ITEMS, CAPABILITY_MANIFEST_MAX_ITEMS)
    other_budget = CAPABILITY_MANIFEST_MAX_ITEMS - skill_budget
    shown_other = manifest_only_other[:other_budget]
    shown_skills = skill_items[:skill_budget]
    shown_items = shown_other + shown_skills
    for item in shown_items:
        label = _coerce_text(item.get("label")) or _coerce_text(item.get("tool"))
        tool = _coerce_text(item.get("tool"))
        description, _description_truncated = _clip_text(
            _coerce_text(item.get("when_to_use")) or "Available workspace capability.",
            CAPABILITY_DESCRIPTION_CHAR_LIMIT,
            "tool description truncated",
        )
        approval = "approval required" if item.get("approval_required") else "no approval required"
        runtime = _coerce_text(item.get("runtime_requirement")) or "cloud"
        lines.append(f"- {tool}: {label}. {description} ({runtime}; {approval}).")
    if native_only_tool_ids:
        lines.append(
            "- Also callable now, already fully described in your own tool schemas "
            "(no separate entry needed here): " + ", ".join(native_only_tool_ids) + "."
        )
    omitted = len(manifest_only_other) + len(skill_items) - len(shown_items)
    if omitted > 0:
        lines.append(f"- ... {omitted} more callable tool(s); use the capability panel or memory tools for details.")
    return "\n".join(lines)


def render_capability_manifest_text(
    capability_manifest: Sequence[Mapping[str, Any]],
    *,
    char_limit: int | None = None,
) -> str:
    """Public entry point for the "## Callable Tools" manifest text, for
    callers outside this module. Added for docs/design/context-engineering-
    plan.md item 10: sage_agent_runtime_service's specialist branch used to
    omit the capability manifest entirely (audit-system-prompt-doctrine.md
    §2b, §4.1&5) — it now calls this with a capability_manifest already
    filtered down to what that specific specialist install can call (see
    _specialist_tool_allowed there), getting the same dedup/un-truncation
    treatment (item 4) the master path gets, scoped correctly instead of
    unscoped. ``char_limit`` (see SPECIALIST_CAPABILITY_MANIFEST_CHAR_LIMIT)
    gives that branch a budget cap of its own — the master path is capped by
    the shared system-context budget already; the specialist branch has no
    such budget at all, so this function must enforce its own."""
    text = _capability_manifest_text(capability_manifest)
    if char_limit is None or not text:
        return text
    clipped, _truncated = _clip_text(text, char_limit, "capability manifest truncated")
    return clipped


def _platform_paid_ai_source(*, billing_source: str | None = None, ai_tier: str | None = None) -> bool:
    billing_token = _coerce_text(billing_source).lower()
    tier_token = _coerce_text(ai_tier).lower().replace("-", "_")
    return billing_token == "empyralis_credits" or tier_token in {"light", "pro", "max"}


# docs/design/context-engineering-plan.md item 9: the doctrine audit's #1
# finding was a zero-hit grep for any first-priority phrasing anywhere in the
# compiled prompt (audit-system-prompt-doctrine.md §3 row 1, §4.4). This is
# that statement — it must render FIRST, before the scene-setting sentence
# and before the memory rule, on every path (see _kernel_prompt below).
# Adapted from agent-service-doctrine-research.md §B4's identity/priority
# template to what Empyralis actually is: a service that works the owner's
# standing goals and in-the-moment requests autonomously, choosing its own
# tools — never an approval workflow (owner law: no approval language here).
_FIRST_PRIORITY_STATEMENT = (
    "Your job: work the user's standing goals and whatever they ask, end to end. Decide "
    "for yourself which of your tools, skills, memory, and connected apps a task needs — "
    "the user will not name one for you. Verify results before reporting them, and keep "
    "going until the work is actually done, not merely attempted."
)


def _kernel_prompt(
    *,
    provider: str,
    model: str | None,
    billing_source: str | None = None,
    ai_tier: str | None = None,
) -> str:
    # Why-first (item 9): the durability reason now leads the memory rule
    # instead of being buried after the mechanical recipe
    # (audit-system-prompt-doctrine.md §3 "Memory (master)" row, §5.5) — the
    # model should know WHY it's silently calling a tool before it's told
    # HOW. (Stale-comment fix, 2026-07-23: this used to describe a
    # SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS/MEMORY.md taxonomy header that
    # commit 57ee98d82 already deleted from the actual rendered prompt text
    # -- the header is just "## Memory" now, and that whole six-file
    # taxonomy is removed entirely as of this change. Nothing below this
    # comment changed; only the stale description of what used to be here.)
    # This rule stays focused on one thing: why memory exists and when to
    # reach for it.
    memory_rule = (
        "\n\nMemory — why first: everything the user tells you is gone once this session "
        "ends unless a memory tool actually runs — that's the only reason the steps below "
        "exist.\n"
        "\n"
        "## When to WRITE (autonomous fact capture)\n"
        "After every user message, silently check: did they share something durable and "
        "reusable — identity, preferences, goals, projects, deadlines, decisions, accounts, "
        "tools — not a one-off detail? If yes, call memory_write with path='MEMORY.md' and "
        "mode='append'. Do this SILENTLY: never say \"I'll remember that\" in place of the "
        "tool call, and never announce the call either — just make it, then continue "
        "naturally. A text reply alone persists nothing.\n"
        "\n"
        "## When to READ (mandatory memory lookup)\n"
        "When the user asks a vague recall question (\"what do you know about me\", \"what were we\n"
        "talking about\", \"what's my favorite X\", \"do you remember Y\", \"tell me about myself\"),\n"
        "you MUST call memory_search or memory_read FIRST — before composing your answer.\n"
        "Never say \"I don't have any information\" without actually checking memory first.\n"
        "If memory is empty after checking, say \"I don't have anything saved yet\" — not \"I don't\n"
        "remember.\"\n"
        "\n"
        "## Format for MEMORY.md\n"
        "Write one fact per line: \"- key: value\" or \"- category: fact\". Examples:\n"
        "- name: Mansur\n"
        "- project: Q3 marketing plan for Acme account"
    )
    # Deliberately no blanket "computer capabilities" claim here — whether a
    # personal computer is actually paired and online varies per workspace
    # per turn, and is stated accurately (and only when true) by the
    # unconditional hardware-status block _run_sage_action_loop_v3 appends
    # to every turn's system_prompt, right below the callable-tools list.
    if _platform_paid_ai_source(billing_source=billing_source, ai_tier=ai_tier):
        return (
            _FIRST_PRIORITY_STATEMENT + "\n\n"
            "You are operating inside Empyralis, an environment connecting the user with AI, tools, files, memory, and apps. "
            "The active AI source is Empyralis AI. "
            "Workspace identity and role files may be available through tools or workspace context when relevant."
        ) + memory_rule
    return (
        _FIRST_PRIORITY_STATEMENT + "\n\n"
        "You are operating inside Empyralis, an environment connecting the user with this AI model, tools, files, memory, and apps. "
        "Workspace identity and role files may be available through tools or workspace context when relevant."
    ) + memory_rule


def _message_needs_memory_context(message: str) -> bool:
    compact = " ".join(_coerce_text(message).lower().split())
    if not compact:
        return False
    markers = (
        "remember",
        "memory",
        "saved",
        "preference",
        "prefer",
        "goal",
        "procedure",
        "identity",
        "profile",
        "prior",
        "previous",
        "earlier",
        "before",
        "decision",
        "decide",
        "decided",
        "what were we",
        "what did we",
        "how i work",
        "working style",
        "reflection",
        "self-improve",
        "self improve",
        "change how you",
        "behave",
        "soul",
    )
    return any(marker in compact for marker in markers)


def _message_needs_workspace_state(message: str) -> bool:
    compact = " ".join(_coerce_text(message).lower().split())
    if not compact:
        return False
    markers = (
        "heartbeat",
        "reminder",
        "queue",
        "running",
        "pending",
        "approval",
        "blocked",
        "workspace state",
        "what is open",
        "what's open",
        "what is pending",
        "what's pending",
    )
    return any(marker in compact for marker in markers)


def _normalize_recent_messages(
    value: Sequence[Mapping[str, Any]] | None,
    current_channel: str = "",
) -> list[dict[str, str]]:
    # BUG 4 fix (compaction summary was write-only): a `role ==
    # "compaction_summary"` entry used to fall straight through the
    # `role not in {"user", "assistant"}` filter below and vanish —
    # confirmed by running this exact function with one in the input. A
    # summary an earlier turn paid an LLM call to produce would then never
    # reach a single subsequent ordinary turn. Scanned across the WHOLE
    # input (not just the last-16 slice below) because a compaction_summary
    # row can be older than the 16 most recent raw turns and still be the
    # only durable memory of everything before it.
    #
    # Kept as role="user" (never "system") once extracted: every
    # downstream cloud-provider transport this list eventually reaches
    # (scripts/orion_local_worker_llm.py's _normalize_prior_messages,
    # allowed_roles={"user", assistant_role}) silently drops a "system"-
    # role prior_messages entry — "user" is the only role guaranteed to
    # survive every transport. Tagged unambiguously as an automated note,
    # not the user's own words.
    summary_entry: dict[str, str] | None = None
    for item in list(value or []):
        if not isinstance(item, Mapping):
            continue
        if _coerce_text(item.get("role")).lower() != "compaction_summary":
            continue
        _summary_content = _coerce_text(item.get("content"))
        if _summary_content:
            summary_entry = {
                "role": "user",
                "content": (
                    "[Automated note — compacted summary of earlier "
                    "conversation, not something the user actually said]:\n"
                    + _summary_content
                )[:4000],
            }
        # Keep scanning — a later compaction_summary row (if more than one
        # somehow made it into `value`) should win as the most current one.

    normalized: list[dict[str, str]] = []
    for item in list(value or [])[-16:]:
        if not isinstance(item, Mapping):
            continue
        role = _coerce_text(item.get("role")).lower()
        if role == "agent":
            role = "assistant"
        if role not in {"user", "assistant"}:
            continue
        content = _coerce_text(item.get("content"))
        if not content:
            continue
        # Tag messages from other channels so Sage knows which channel
        # each turn originated from — essential for cross-channel continuity.
        msg_channel = _coerce_text(
            (item.get("metadata") or {}).get("channel", "")
        )
        if msg_channel and current_channel and msg_channel != current_channel:
            content = f"[via {msg_channel}] {content}"
        normalized.append({"role": role, "content": content[:4000]})
    # Aggregate budget on top of the per-message cap above (SAGE_RECENT_
    # HISTORY_TOTAL_CHAR_LIMIT, see its definition for why) — drop the
    # oldest messages first until the whole block fits, always keeping at
    # least the single most recent message. Applied BEFORE the summary is
    # prepended (below) so the summary itself is never the thing this loop
    # discards to make room — it is the one thing here that summarizes
    # everything the budget squeeze is dropping.
    total_chars = sum(len(m["content"]) for m in normalized)
    while total_chars > SAGE_RECENT_HISTORY_TOTAL_CHAR_LIMIT and len(normalized) > 1:
        dropped = normalized.pop(0)
        total_chars -= len(dropped["content"])

    if summary_entry is not None:
        normalized.insert(0, summary_entry)
    return normalized



# Per-channel format guidance injected into the system prompt.
# Keys match channel_origin values set by each ingress path.
CHANNEL_FORMAT_HINTS: dict[ChannelOrigin, str] = {
    ChannelOrigin.WEB: "Full markdown and tables are supported. Longer responses are acceptable.",
    ChannelOrigin.ACP: "Full markdown and tables are supported. Longer responses are acceptable.",
    ChannelOrigin.TELEGRAM_HOSTED: "Markdown is supported (no HTML). Keep responses concise — under ~800 chars unless detail is needed.",
    ChannelOrigin.TELEGRAM_PERSONAL: "Markdown is supported (no HTML). Keep responses concise — under ~800 chars unless detail is needed.",
    ChannelOrigin.WHATSAPP_PERSONAL: "Plain text preferred with minimal markdown. Keep responses short and conversational.",
    ChannelOrigin.DISCORD_PERSONAL: "Markdown is supported. Embeds are not available via DM.",
}

def build_sage_instruction_bundle(
    *,
    workspace_id: str,
    message: str,
    tenant_id: str = "",
    channel_origin: str = "",
    provider: str = "",
    model: str | None = None,
    billing_source: str | None = None,
    ai_tier: str | None = None,
    user_id: str = "",
    root_context_files: Mapping[str, Any] | None = None,
    profile_context: str = "",
    memory_context: str = "",
    heartbeat_context: str = "",
    capability_payload: Mapping[str, Any] | None = None,
    recent_messages: Sequence[Mapping[str, Any]] | None = None,
    sender_name: str | None = None,
    sender_id: str | None = None,
    canonical_name: str | None = None,
    linked_channels: list[str] | None = None,
    policy_context: str = "",
) -> SageInstructionBundle:
    normalized_workspace_id = _coerce_text(workspace_id)
    normalized_message = _coerce_text(message)
    if capability_payload is None:
        capability_payload = sage_skills_api.build_sage_capabilities_payload(
            workspace_id=normalized_workspace_id,
            tenant_id=_coerce_text(tenant_id),
        )
    capability_manifest = build_model_capability_manifest(capability_payload)
    root_sections, root_diagnostics = build_root_memory_brief_sections(root_context_files)
    prior_messages = _normalize_recent_messages(recent_messages, current_channel=channel_origin)

    section_char_counts: dict[str, int] = {}
    truncated_sections: list[str] = []
    skipped_sections: list[str] = []
    budget = _system_context_char_budget()
    remaining_budget = budget
    sections: list[str] = []

    def append_section(name: str, value: str, *, limit: int | None = None) -> None:
        nonlocal remaining_budget
        text = _coerce_text(value)
        if not text:
            return
        section_limit = remaining_budget if limit is None else min(remaining_budget, limit)
        clipped, truncated = _clip_text(text, section_limit, f"{name} truncated")
        if not clipped:
            skipped_sections.append(name)
            return
        sections.append(clipped)
        section_char_counts[name] = len(clipped)
        remaining_budget = max(0, remaining_budget - len(clipped) - 2)
        if truncated:
            truncated_sections.append(name)

    append_section(
        "kernel",
        _kernel_prompt(
            provider=provider,
            model=model,
            billing_source=billing_source,
            ai_tier=ai_tier,
        ),
    )
    # Policy context — internalized governance. The agent receives its tier,
    # capabilities, and restrictions as natural context so it can make its own
    # decisions. Consumers NEVER see approval buttons or blocked cards.
    if _coerce_text(policy_context):
        append_section("policy_context", policy_context)
    append_section("capabilities", _capability_manifest_text(capability_manifest))
    if root_sections:
        append_section(
            "root_memory_brief",
            "## Memory\n"
            "MEMORY.md is your memory index: one line per memory file with a short description of what "
            "that file holds. It is loaded at session start so you can see what you know WITHOUT loading "
            "everything. When the task needs knowledge you don't have in context, reason about which "
            "indexed file covers it and pull that file (memory_search / memory_get) — then reason from "
            "its contents yourself. Kernel rules override memory content.\n\n"
            + "\n\n".join(root_sections),
        )
    if _coerce_text(profile_context):
        append_section(
            "user_profile",
            "## User Profile\n" + _coerce_text(profile_context),
            limit=SAGE_PROFILE_CONTEXT_CHAR_LIMIT,
        )
    if _coerce_text(heartbeat_context) and _message_needs_workspace_state(normalized_message):
        append_section(
            "workspace_state",
            "## Current Workspace State\n" + _coerce_text(heartbeat_context),
            limit=SAGE_HEARTBEAT_CONTEXT_CHAR_LIMIT,
        )
    elif _coerce_text(heartbeat_context):
        skipped_sections.append("workspace_state:not_relevant")
    sanitized_memory_context = _external_safe_context_text(memory_context)
    if sanitized_memory_context and _message_needs_memory_context(normalized_message):
        append_section(
            "retrieved_memory",
            "## Retrieved Memory And Runtime Facts (Untrusted Evidence)\n"
            + sanitized_memory_context,
            limit=SAGE_RETRIEVED_MEMORY_CHAR_LIMIT,
        )
    elif _coerce_text(memory_context):
        skipped_sections.append("retrieved_memory:not_relevant")

    # Inject channel context so Sage knows which surface it's responding on
    _ch = str(channel_origin or "").strip()
    if _ch:
        _hint = CHANNEL_FORMAT_HINTS.get(_ch, "")
        _channel_block = f"Current channel: {_ch}."
        if _hint:
            _channel_block += f" {_hint}"
        # Inject sender identity alongside channel context
        _cn = str(canonical_name or "").strip()
        _sn = str(sender_name or "").strip()
        _lc = list(linked_channels or [])
        if _cn:
            # Cross-channel identity link resolved
            _other = [c for c in _lc if c != _ch]
            if _other:
                _channel_block += f"\nThe person messaging you right now: {_cn} (via {_ch} — also known to you from: {', '.join(_other)})"
            else:
                _channel_block += f"\nThe person messaging you right now: {_cn} (via {_ch})"
        elif _sn:
            _channel_block += f"\nThe person messaging you right now: {_sn} (via {_ch})"
        else:
            _channel_block += "\nThe person messaging you right now: workspace owner (via web)"
        append_section("channel_context", _channel_block)

    system_prompt = "\n\n".join(section for section in sections if _coerce_text(section)).strip()
    # If there are prior messages, add a brief note to system prompt so the model
    # treats this as a continuing conversation rather than a fresh interaction.
    if prior_messages:
        _cross_note = "\n\n## Ongoing Conversation\nThe messages above are your recent conversation with this user. Maintain continuity — remember what was just discussed, what tools ran, and what the user said. Do not reintroduce yourself or act like this is a new chat."
        # If any turns came from a different channel, explain the [via ...] prefix
        # convention so Sage can reference where past info originated.
        _all_chs: set[str] = set()
        for _t in (recent_messages or []):
            if isinstance(_t, dict):
                _tc = _coerce_text((_t.get("metadata") or {}).get("channel", ""))
                if _tc:
                    _all_chs.add(_tc)
        _other = sorted(c for c in _all_chs if c and c != _ch)
        if _other:
            _cross_note += (
                f" Messages prefixed with [via ...] are from other channels"
                f" ({', '.join(_other)}). Messages from your current channel"
                f" ({_ch}) are unmarked. You can naturally reference where past"
                f" information came from (e.g., \"you mentioned Tokyo on Telegram earlier\")."
            )
        system_prompt += _cross_note
    messages = [
        {"role": "system", "content": system_prompt},
        *prior_messages,
        {"role": "user", "content": normalized_message},
    ]
    approval_required_tools = [
        _coerce_text(item.get("tool"))
        for item in capability_manifest
        if item.get("approval_required") and _coerce_text(item.get("tool"))
    ]
    diagnostics = {
        "workspace_id": normalized_workspace_id,
        "tenant_id": _coerce_text(tenant_id),
        "user_id": _coerce_text(user_id),
        "provider": _coerce_text(provider) or None,
        "model": _coerce_text(model) or None,
        "capability_count": len(capability_manifest),
        "approval_required_tools": approval_required_tools,
        "profile_context_included": "user_profile" in section_char_counts,
        "heartbeat_context_included": "workspace_state" in section_char_counts,
        "retrieved_memory_included": "retrieved_memory" in section_char_counts,
        "recent_messages_included": len(prior_messages),
        "estimated_input_tokens": sum(_estimate_token_count(item.get("content")) for item in messages),
        "system_prompt_chars": len(system_prompt),
        "system_context_char_budget": budget,
        "section_char_counts": section_char_counts,
        "truncated_sections": truncated_sections,
        "skipped_sections": skipped_sections,
        "capability_manifest_chars": section_char_counts.get("capabilities", 0),
        "root_memory_brief_included": "root_memory_brief" in section_char_counts,
        "setup_warnings_in_prompt": False,
        **root_diagnostics,
    }
    return SageInstructionBundle(
        messages=messages,
        diagnostics=diagnostics,
        capability_manifest=capability_manifest,
        system_prompt=system_prompt,
        user_message=normalized_message,
        prior_messages=prior_messages,
    )
