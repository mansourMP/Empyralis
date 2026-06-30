from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules import rust_runtime_kernel_client, workspace_context


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

SAGE_PROFILE_PROJECTED_FILES: tuple[str, ...] = (
    "USER.md",
    "IDENTITY.md",
    "SOUL.md",
    "HEARTBEAT.md",
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


def _project_user_md(profile: Dict[str, Any]) -> str:
    name = _coerce_text(profile.get("user_name")) or "Not set yet."
    return (
        "# User Profile\n\n"
        f"- Preferred name: {name}\n"
    )


def _project_identity_md(profile: Dict[str, Any]) -> str:
    summary = _coerce_text(profile.get("identity_summary")) or "Not set yet."
    return (
        "# Identity\n\n"
        f"- Role and focus: {summary}\n"
    )


def _project_soul_md(profile: Dict[str, Any]) -> str:
    style = _coerce_text(profile.get("communication_style")) or "Keep replies clear, useful, and calm."
    rules = _normalize_rules(profile.get("standing_rules"))
    lines = [
        "# Empyralis",
        "",
        "Empyralis is a calm mobile-first AI product.",
        "Its job is to help the user through one personal assistant named Sage.",
        "",
        "## Personal communication style",
        "",
        f"- {style}",
    ]
    lines.extend(["", "## Standing rules", ""])
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
        "USER.md": _project_user_md(normalized),
        "IDENTITY.md": _project_identity_md(normalized),
        "SOUL.md": _project_soul_md(normalized),
        "HEARTBEAT.md": _project_heartbeat_md(normalized),
    }


def sync_profile_context_files(*, workspace_id: str, profile: Dict[str, Any]) -> Dict[str, str]:
    projections = projected_context_files(profile)
    empty_profile_projections = projected_context_files(_default_state()["profile"])
    existing_files = workspace_context.read_workspace_context_files(workspace_id=workspace_id)
    for filename, content in projections.items():
        existing_content = str(existing_files.get(filename) or "")
        default_content = str(workspace_context.DEFAULT_CONTEXT_FILE_CONTENTS.get(filename) or "")
        empty_projection_content = str(empty_profile_projections.get(filename) or "")
        is_replaceable_projection = existing_content.strip() in {
            "",
            default_content.strip(),
            empty_projection_content.strip(),
        }
        if not is_replaceable_projection:
            continue
        _enforce_sage_profile_state_decision(
            operation="update_workspace_context_file",
            state_class="workspace_context_files",
            workspace_id=workspace_id,
            actor_user_id=None,
            payload={
                "filename": filename,
                "content": content,
                "source": "sage_profile_projection",
            },
        )
        workspace_context.write_workspace_context_file(
            filename,
            content,
            workspace_id=workspace_id,
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
