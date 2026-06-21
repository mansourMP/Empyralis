from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from server_modules.channel_adapter import ChannelOrigin

from server_modules import sage_skills_api
from server_modules import workspace_context
from server_modules import workspace_context_memory_adapter

OFFICIAL_ROOT_MEMORY_FILES: tuple[str, ...] = (
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "AGENTS.md",
    "TOOLS.md",
    "MEMORY.md",
    "GOALS.md",
)
LEGACY_ROOT_MEMORY_FILES: tuple[str, ...] = (
    "HEARTBEAT.md",
)
ROOT_MEMORY_BRIEF_PRIORITY: tuple[str, ...] = (
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "GOALS.md",
    "AGENTS.md",
    "TOOLS.md",
    "MEMORY.md",
)
ROOT_MEMORY_SECTION_CHAR_LIMIT = 12_000
ROOT_MEMORY_TOTAL_CHAR_LIMIT = 48_000
ROOT_MEMORY_BRIEF_SECTION_CHAR_LIMIT = 900
ROOT_MEMORY_BRIEF_TOTAL_CHAR_LIMIT = 4_800
SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT = 12_000
SAGE_RETRIEVED_MEMORY_CHAR_LIMIT = 3_000
SAGE_PROFILE_CONTEXT_CHAR_LIMIT = 1_500
SAGE_HEARTBEAT_CONTEXT_CHAR_LIMIT = 900
CAPABILITY_MANIFEST_MAX_ITEMS = 16
CAPABILITY_DESCRIPTION_CHAR_LIMIT = 140
MEMORY_MANIFEST_LIMIT = 60
MODEL_HIDDEN_LEGACY_TOOLS = {"memory_update"}


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


def build_root_memory_sections(context_files: Mapping[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    payload = dict(context_files or {})
    sections: list[str] = []
    consumed_paths: set[str] = set()
    total_chars = 0
    truncated = False
    included_official: list[str] = []
    included_legacy: list[str] = []
    included_extra: list[str] = []

    def append_named(filename: str, *, legacy: bool = False, extra: bool = False) -> None:
        nonlocal total_chars, truncated
        content = _meaningful_context_file_content(filename, payload.get(filename))
        if not content or total_chars >= ROOT_MEMORY_TOTAL_CHAR_LIMIT:
            return
        consumed, was_truncated = _append_file_section(
            sections=sections,
            filename=filename,
            content=content,
            title_prefix="Legacy/Extra Context File: " if legacy or extra else "",
            remaining_budget=ROOT_MEMORY_TOTAL_CHAR_LIMIT - total_chars,
        )
        if consumed <= 0:
            return
        total_chars += consumed
        truncated = truncated or was_truncated
        consumed_paths.add(filename)
        if legacy:
            included_legacy.append(filename)
        elif extra:
            included_extra.append(filename)
        else:
            included_official.append(filename)

    for filename in OFFICIAL_ROOT_MEMORY_FILES:
        append_named(filename)

    for filename in LEGACY_ROOT_MEMORY_FILES:
        append_named(filename, legacy=True)

    for filename in sorted(_coerce_text(key) for key in payload.keys()):
        if not filename or filename in consumed_paths or "/" in filename:
            continue
        append_named(filename, extra=True)

    memory_paths = [
        filename
        for filename in sorted(_coerce_text(key) for key in payload.keys())
        if filename
        and filename not in consumed_paths
        and filename.startswith("memory/")
        and not filename.startswith("memory/.dreams/")
        and _meaningful_context_file_content(filename, payload.get(filename))
    ]
    if memory_paths:
        shown_paths = memory_paths[:MEMORY_MANIFEST_LIMIT]
        manifest_lines = [
            "Additional workspace memory files exist. Use memory_search and memory_get when the user's request needs them."
        ]
        manifest_lines.extend(f"- {path}" for path in shown_paths)
        if len(memory_paths) > len(shown_paths):
            manifest_lines.append(f"- ... {len(memory_paths) - len(shown_paths)} more memory file(s)")
        sections.append("### Available Memory Files\n" + "\n".join(manifest_lines))

    return sections, {
        "included_root_files": [*included_official, *included_legacy, *included_extra],
        "included_official_root_files": included_official,
        "legacy_context_files": included_legacy,
        "extra_context_files": included_extra,
        "available_memory_file_count": len(memory_paths),
        "context_truncated": truncated,
        "root_memory_chars": total_chars,
    }



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

    for filename in ROOT_MEMORY_BRIEF_PRIORITY:
        content = _meaningful_context_file_content(filename, payload.get(filename))
        if not content:
            continue
        consumed_paths.add(filename)
        total_source_chars += len(content)
        if total_brief_chars >= ROOT_MEMORY_BRIEF_TOTAL_CHAR_LIMIT:
            truncated = True
            continue
        remaining = ROOT_MEMORY_BRIEF_TOTAL_CHAR_LIMIT - total_brief_chars
        clipped, was_truncated = _clip_text(
            content,
            min(ROOT_MEMORY_BRIEF_SECTION_CHAR_LIMIT, remaining),
            "content truncated due to length limit",
        )
        sanitized = workspace_context_memory_adapter.strip_red_facts_from_external_context(clipped)
        if not sanitized:
            truncated = True
            continue
        sections.append(f"### {filename}\n{sanitized}")
        included_official.append(filename)
        total_brief_chars += len(sanitized)
        truncated = truncated or was_truncated

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


def _capability_manifest_text(capability_manifest: Sequence[Mapping[str, Any]]) -> str:
    if not capability_manifest:
        return ""
    lines = [
        "## Callable Tools",
        "Only these tools are callable in this turn. Do not mention or invent unavailable tools.",
    ]
    shown_items = list(capability_manifest)[:CAPABILITY_MANIFEST_MAX_ITEMS]
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
    omitted = len(capability_manifest) - len(shown_items)
    if omitted > 0:
        lines.append(f"- ... {omitted} more callable tool(s); use the capability panel or memory tools for details.")
    return "\n".join(lines)


def _platform_paid_ai_source(*, billing_source: str | None = None, ai_tier: str | None = None) -> bool:
    billing_token = _coerce_text(billing_source).lower()
    tier_token = _coerce_text(ai_tier).lower().replace("-", "_")
    return billing_token == "empyralis_credits" or tier_token in {"light", "pro", "max"}


def _kernel_prompt(
    *,
    provider: str,
    model: str | None,
    billing_source: str | None = None,
    ai_tier: str | None = None,
) -> str:
    if _platform_paid_ai_source(billing_source=billing_source, ai_tier=ai_tier):
        return (
            "You are operating inside Empyralis, an environment connecting the user with AI, tools, files, memory, apps, and computer capabilities. "
            "The active AI source is Empyralis AI. "
            "Workspace identity and role files may be available through tools or workspace context when relevant."
        )
    return (
        "You are operating inside Empyralis, an environment connecting the user with this AI model, tools, files, memory, apps, and computer capabilities. "
        "Workspace identity and role files may be available through tools or workspace context when relevant."
    )


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


def _normalize_recent_messages(value: Sequence[Mapping[str, Any]] | None) -> list[dict[str, str]]:
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
        normalized.append({"role": role, "content": content[:4000]})
    return normalized



# Per-channel format guidance injected into the system prompt.
# Keys match channel_origin values set by each ingress path.
CHANNEL_FORMAT_HINTS: dict[ChannelOrigin, str] = {
    ChannelOrigin.WEB: "Full markdown and tables are supported. Longer responses are acceptable.",
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
    prior_messages = _normalize_recent_messages(recent_messages)

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
            "## Customer Root Memory\n"
            "These customer-editable files are Sage's durable personal behavior layer. Kernel rules override them. Use memory_search and memory_get for full file detail.\n\n"
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
        system_prompt += "\n\n## Ongoing Conversation\nThe messages above are your recent conversation with this user. Maintain continuity — remember what was just discussed, what tools ran, and what the user said. Do not reintroduce yourself or act like this is a new chat."
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
