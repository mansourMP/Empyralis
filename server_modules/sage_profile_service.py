from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import memory_service, rust_runtime_kernel_client, workspace_context


SAGE_PROFILE_BOOTSTRAP_QUESTIONS: tuple[Dict[str, str], ...] = (
    {
        "id": "user_name",
        "field": "user_name",
        "prompt": "What should I call you?",
        "placeholder": "Example: Mansur",
    },
    {
        "id": "identity_summary",
        "field": "identity_summary",
        "prompt": "What do you do? Share your role, work, or the projects that matter most.",
        "placeholder": "Example: I run product and engineering for a mobile-first agent platform.",
    },
    {
        "id": "communication_style",
        "field": "communication_style",
        "prompt": "How should I communicate with you? Tone, style, format, or decision preferences.",
        "placeholder": "Example: Be direct, concise, and lead with the answer.",
    },
    {
        "id": "recurring_responsibility",
        "field": "recurring_responsibility",
        "prompt": "What's one thing you want me to keep handling automatically?",
        "placeholder": "Example: Keep my inbox triaged and surface urgent replies.",
    },
    {
        "id": "standing_rules",
        "field": "standing_rules",
        "prompt": "Any rules I should always follow?",
        "placeholder": "Example: Never send external messages without approval.",
    },
)

# Founder ruling (2026-07-23, final): USER.md/IDENTITY.md/SOUL.md are removed
# from the root-file taxonomy. Onboarding's projection of the durable bits of
# the profile (preferred name, role/focus, communication style, standing
# rules) now targets a MEMORY.md-indexed topic file instead -- routed through
# memory_service.update_memory_context_file so it gets the exact same caps
# (200-line/25KB per topic file, 200-line/25KB MEMORY.md index cap) and
# auto-index-upsert every other topic file gets, rather than a bespoke write
# path for onboarding data. HEARTBEAT.md is untouched: it's a runtime log,
# never part of the SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS taxonomy in spirit,
# and out of scope for this removal.
SAGE_PROFILE_PROJECTED_FILES: tuple[str, ...] = (
    "HEARTBEAT.md",
)
SAGE_PROFILE_MEMORY_TOPIC_FILE = "memory/files/profile.md"
SAGE_PROFILE_MEMORY_TOPIC_DESCRIPTION = (
    "Owner profile: preferred name, role/focus, communication style, and "
    "standing rules captured during Sage setup."
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_file(workspace_id: str) -> Path:
    return workspace_context.workspace_scope_dir(workspace_id) / "sage_profile.json"


def _default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _utc_now_iso(),
        "profile": {
            "user_name": "",
            "identity_summary": "",
            "communication_style": "",
            "recurring_responsibility": "",
            "standing_rules": [],
        },
    }


def _safe_read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("profile"), dict):
        raw["profile"] = {}
    return raw


def _save_state(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _state_file(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _utc_now_iso()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


class SageProfileRustGateError(RuntimeError):
    pass


def _enforce_sage_profile_state_decision(
    *,
    operation: str,
    workspace_id: str,
    payload: Dict[str, Any],
    actor_user_id: Optional[str] = None,
    state_class: str = "sage_profile",
) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    try:
        payload_bytes = len(
            json.dumps(
                normalized_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation=operation,
            state_class=state_class,
            workspace_id=str(workspace_id or "").strip(),
            actor_id=_coerce_text(actor_user_id) or "system",
            status="active",
            payload=normalized_payload,
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = _coerce_text(decision.get("next_action"))
        if next_action != operation:
            raise SageProfileRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise SageProfileRustGateError(exc.reason) from exc


def _coerce_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_rules(value: Any) -> List[str]:
    if isinstance(value, list):
        parts = [str(item or "").strip() for item in value]
    else:
        text = _coerce_text(value)
        if not text:
            return []
        normalized = text.replace("\r\n", "\n")
        parts = []
        for chunk in normalized.split("\n"):
            stripped = chunk.strip().lstrip("-").lstrip("*").strip()
            if not stripped:
                continue
            if ";" in stripped:
                parts.extend(piece.strip() for piece in stripped.split(";"))
            else:
                parts.append(stripped)
    seen: set[str] = set()
    normalized_rules: List[str] = []
    for item in parts:
        rule = _coerce_text(item)
        if not rule:
            continue
        key = rule.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized_rules.append(rule)
    return normalized_rules[:12]


def _normalize_profile(raw_profile: Dict[str, Any] | None) -> Dict[str, Any]:
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    return {
        "user_name": _coerce_text(profile.get("user_name")),
        "identity_summary": _coerce_text(profile.get("identity_summary")),
        "communication_style": _coerce_text(profile.get("communication_style")),
        "recurring_responsibility": _coerce_text(profile.get("recurring_responsibility")),
        "standing_rules": _normalize_rules(profile.get("standing_rules")),
    }


def _read_state(workspace_id: str) -> Dict[str, Any]:
    raw = _safe_read_json(_state_file(workspace_id))
    raw["profile"] = _normalize_profile(raw.get("profile") if isinstance(raw.get("profile"), dict) else {})
    return raw


def _answered_count(profile: Dict[str, Any]) -> int:
    count = 0
    for question in SAGE_PROFILE_BOOTSTRAP_QUESTIONS:
        field = question["field"]
        value = profile.get(field)
        if field == "standing_rules":
            if isinstance(value, list) and value:
                count += 1
            continue
        if _coerce_text(value):
            count += 1
    return count


def _current_question(profile: Dict[str, Any]) -> Optional[Dict[str, str]]:
    for question in SAGE_PROFILE_BOOTSTRAP_QUESTIONS:
        field = question["field"]
        value = profile.get(field)
        if field == "standing_rules":
            if isinstance(value, list) and value:
                continue
            return dict(question)
        if not _coerce_text(value):
            return dict(question)
    return None


def _bootstrap_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    current_question = _current_question(profile)
    answered_count = _answered_count(profile)
    total = len(SAGE_PROFILE_BOOTSTRAP_QUESTIONS)
    return {
        "complete": current_question is None,
        "current_question": current_question,
        "answered_count": answered_count,
        "total_count": total,
        "progress_label": f"{answered_count}/{total}",
    }


def _has_profile_content(profile: Dict[str, Any]) -> bool:
    return bool(
        _coerce_text(profile.get("user_name"))
        or _coerce_text(profile.get("identity_summary"))
        or _coerce_text(profile.get("communication_style"))
        or _coerce_text(profile.get("recurring_responsibility"))
        or _normalize_rules(profile.get("standing_rules"))
    )


def _project_profile_topic_file(profile: Dict[str, Any]) -> str:
    """Replaces the old separate USER.md/IDENTITY.md/SOUL.md projections
    with one MEMORY.md-indexed topic file (see SAGE_PROFILE_MEMORY_TOPIC_FILE
    above) -- same durable facts (preferred name, role/focus, communication
    style, standing rules), one file instead of three root files."""
    name = _coerce_text(profile.get("user_name")) or "Not set yet."
    summary = _coerce_text(profile.get("identity_summary")) or "Not set yet."
    style = _coerce_text(profile.get("communication_style")) or "Keep replies clear, useful, and calm."
    rules = _normalize_rules(profile.get("standing_rules"))
    lines = [
        "# Owner Profile",
        "",
        f"- Preferred name: {name}",
        f"- Role and focus: {summary}",
        f"- Communication style: {style}",
        "",
        "## Standing rules",
        "",
    ]
    if rules:
        lines.extend(f"- {rule}" for rule in rules)
    else:
        lines.append("- No standing rules saved yet.")
    return "\n".join(lines).strip() + "\n"


def _project_heartbeat_md(profile: Dict[str, Any]) -> str:
    responsibility = _coerce_text(profile.get("recurring_responsibility"))
    if not responsibility:
        return (
            "# Heartbeat\n\n"
            "- Add recurring responsibilities Sage should keep track of here.\n"
        )
    return (
        "# Heartbeat\n\n"
        f"- [ ] {responsibility}\n"
    )


def projected_context_files(profile: Dict[str, Any]) -> Dict[str, str]:
    normalized = _normalize_profile(profile)
    return {
        "HEARTBEAT.md": _project_heartbeat_md(normalized),
        SAGE_PROFILE_MEMORY_TOPIC_FILE: _project_profile_topic_file(normalized),
    }


def _progressive_profile_states(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every profile snapshot `profile` could have passed through on its way
    here, answered in SAGE_PROFILE_BOOTSTRAP_QUESTIONS order (empty, then one
    field added at a time). The profile topic file aggregates four fields
    (user_name, identity_summary, communication_style, standing_rules) that
    the bootstrap wizard fills in one at a time across separate calls --
    unlike the old one-field-per-file design (USER.md/IDENTITY.md/SOUL.md),
    a naive "does this match the fully-empty projection" check would treat
    the file as "manually edited" (and stop syncing it) the moment the
    SECOND question gets answered, since its content no longer matches the
    all-empty state. Comparing against every point along the natural
    progression instead lets the wizard keep updating the file through every
    question while still refusing to clobber a genuinely foreign (manually
    edited) file."""
    normalized = _normalize_profile(profile)
    states: List[Dict[str, Any]] = [dict(_default_state()["profile"])]
    running = dict(states[0])
    for question in SAGE_PROFILE_BOOTSTRAP_QUESTIONS:
        field = question["field"]
        running = dict(running)
        running[field] = normalized.get(field)
        states.append(running)
    return states


def sync_profile_context_files(*, workspace_id: str, profile: Dict[str, Any]) -> Dict[str, str]:
    projections = projected_context_files(profile)
    empty_profile_projections = projected_context_files(_default_state()["profile"])
    progressive_topic_projections = {
        str(_project_profile_topic_file(state) or "").strip()
        for state in _progressive_profile_states(profile)
    }

    # HEARTBEAT.md: unchanged direct workspace-context write. Out of scope
    # for the root-taxonomy removal (see SAGE_PROFILE_PROJECTED_FILES above).
    heartbeat_content = projections["HEARTBEAT.md"]
    existing_files = workspace_context.read_workspace_context_files(workspace_id=workspace_id)
    existing_heartbeat = str(existing_files.get("HEARTBEAT.md") or "")
    default_heartbeat = str(workspace_context.DEFAULT_CONTEXT_FILE_CONTENTS.get("HEARTBEAT.md") or "")
    empty_heartbeat_projection = str(empty_profile_projections.get("HEARTBEAT.md") or "")
    if existing_heartbeat.strip() in {"", default_heartbeat.strip(), empty_heartbeat_projection.strip()}:
        _enforce_sage_profile_state_decision(
            operation="update_workspace_context_file",
            state_class="workspace_context_files",
            workspace_id=workspace_id,
            actor_user_id=None,
            payload={
                "filename": "HEARTBEAT.md",
                "content": heartbeat_content,
                "source": "sage_profile_projection",
            },
        )
        workspace_context.write_workspace_context_file(
            "HEARTBEAT.md",
            heartbeat_content,
            workspace_id=workspace_id,
        )

    # Profile topic file (migrated off USER.md/IDENTITY.md/SOUL.md, 2026-07-23
    # root-taxonomy removal): routed through memory_service's existing
    # topic-file write path (caps + auto-index-upsert), never a bespoke
    # write path for onboarding data.
    profile_topic_content = projections[SAGE_PROFILE_MEMORY_TOPIC_FILE]
    existing_topic_content = workspace_context.read_workspace_context_file(
        SAGE_PROFILE_MEMORY_TOPIC_FILE,
        workspace_id=workspace_id,
    )
    if existing_topic_content.strip() == "" or existing_topic_content.strip() in progressive_topic_projections:
        _enforce_sage_profile_state_decision(
            operation="update_workspace_context_file",
            state_class="workspace_context_files",
            workspace_id=workspace_id,
            actor_user_id=None,
            payload={
                "filename": SAGE_PROFILE_MEMORY_TOPIC_FILE,
                "content": profile_topic_content,
                "source": "sage_profile_projection",
            },
        )
        memory_service.update_memory_context_file(
            workspace_id,
            SAGE_PROFILE_MEMORY_TOPIC_FILE,
            profile_topic_content,
            reason="sage_profile_projection",
            description=SAGE_PROFILE_MEMORY_TOPIC_DESCRIPTION,
        )
    return projections


def _storage_policy() -> Dict[str, Any]:
    return {
        "authority": "structured_profile_cloud_canonical",
        "runtime_format": "structured_profile",
        "markdown_format": "projection_only",
        "projected_files": list(SAGE_PROFILE_PROJECTED_FILES),
    }


def list_sage_profile(
    *,
    workspace_id: str,
    account_seed: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    state = _read_state(workspace_id)
    profile = _normalize_profile(state.get("profile") if isinstance(state.get("profile"), dict) else {})
    projections = projected_context_files(profile)
    seed = account_seed if isinstance(account_seed, dict) else {}
    return {
        "workspace_id": workspace_id,
        "profile": {
            **profile,
            "standing_rules_text": "\n".join(profile["standing_rules"]),
        },
        "bootstrap": _bootstrap_payload(profile),
        "storage_policy": _storage_policy(),
        "projections": projections,
        "account_seed": {
            "display_name": _coerce_text(seed.get("display_name")),
            "email": _coerce_text(seed.get("email")),
        },
        "updated_at": state.get("updated_at"),
    }


def upsert_sage_profile(
    *,
    workspace_id: str,
    actor_user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    identity_summary: Optional[str] = None,
    communication_style: Optional[str] = None,
    recurring_responsibility: Optional[str] = None,
    standing_rules: Optional[List[str]] = None,
    standing_rules_text: Optional[str] = None,
) -> Dict[str, Any]:
    state = _read_state(workspace_id)
    profile = _normalize_profile(state.get("profile") if isinstance(state.get("profile"), dict) else {})
    if user_name is not None:
        profile["user_name"] = _coerce_text(user_name)
    if identity_summary is not None:
        profile["identity_summary"] = _coerce_text(identity_summary)
    if communication_style is not None:
        profile["communication_style"] = _coerce_text(communication_style)
    if recurring_responsibility is not None:
        profile["recurring_responsibility"] = _coerce_text(recurring_responsibility)
    if standing_rules is not None:
        profile["standing_rules"] = _normalize_rules(standing_rules)
    elif standing_rules_text is not None:
        profile["standing_rules"] = _normalize_rules(standing_rules_text)
    state["profile"] = profile
    state["last_updated_by"] = _coerce_text(actor_user_id) or None
    _enforce_sage_profile_state_decision(
        operation="upsert_sage_profile",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        payload=state,
    )
    _save_state(workspace_id, state)
    sync_profile_context_files(workspace_id=workspace_id, profile=profile)
    return list_sage_profile(workspace_id=workspace_id)


def answer_sage_profile_bootstrap(
    *,
    workspace_id: str,
    answer: str,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    state = _read_state(workspace_id)
    profile = _normalize_profile(state.get("profile") if isinstance(state.get("profile"), dict) else {})
    question = _current_question(profile)
    if question is None:
        return list_sage_profile(workspace_id=workspace_id)
    normalized_answer = _coerce_text(answer)
    if not normalized_answer:
        raise HTTPException(status_code=400, detail="Bootstrap answer is required.")
    field = question["field"]
    if field == "standing_rules":
        rules = _normalize_rules(normalized_answer)
        if not rules:
            raise HTTPException(status_code=400, detail="Add at least one standing rule.")
        profile["standing_rules"] = rules
    else:
        profile[field] = normalized_answer
    state["profile"] = profile
    state["last_updated_by"] = _coerce_text(actor_user_id) or None
    _enforce_sage_profile_state_decision(
        operation="upsert_sage_profile",
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        payload=state,
    )
    _save_state(workspace_id, state)
    sync_profile_context_files(workspace_id=workspace_id, profile=profile)
    return list_sage_profile(workspace_id=workspace_id)
