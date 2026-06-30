from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from server_modules import rust_runtime_kernel_client
from server_modules.auth import enforce_workspace_access


PROFILE_TEMPLATE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "custom": {
        "name": "General Profile",
        "icon": "✨",
        "backend_role": "orchestrator",
        "description": "Flexible general assistant for mixed work.",
        "instructions": (
            "You are Empyralis, a practical assistant. Stay clear, direct, and useful. "
            "Handle mixed work, ask for approval before sensitive actions, and keep the user in control."
        ),
        "capabilities": {
            "web_search": False,
            "calendar_access": False,
            "code_execution": False,
            "local_execution": False,
            "document_work": True,
        },
        "policy": {"security_level": "balanced"},
    },
    "support": {
        "name": "Support",
        "icon": "💬",
        "backend_role": "support",
        "description": "Customer replies, inbox triage, and follow-up.",
        "instructions": (
            "Act like a calm support operator. Triage requests, reply clearly, gather missing facts, "
            "and protect the brand voice."
        ),
        "capabilities": {
            "web_search": False,
            "calendar_access": False,
            "code_execution": False,
            "local_execution": False,
            "document_work": True,
        },
        "policy": {"security_level": "balanced"},
    },
    "research": {
        "name": "Research",
        "icon": "🔎",
        "backend_role": "research",
        "description": "Briefs, analysis, memory, and synthesis.",
        "instructions": (
            "Act like a research partner. Build concise briefs, compare options, cite evidence when available, "
            "and make tradeoffs explicit."
        ),
        "capabilities": {
            "web_search": True,
            "calendar_access": False,
            "code_execution": False,
            "local_execution": False,
            "document_work": True,
        },
        "policy": {"security_level": "balanced"},
    },
    "builder": {
        "name": "Builder",
        "icon": "🛠️",
        "backend_role": "builder",
        "description": "Product, coding, design, and automations.",
        "instructions": (
            "Act like a strong builder. Break down product and engineering work clearly, use tools carefully, "
            "and stop for approval before risky local actions."
        ),
        "capabilities": {
            "web_search": True,
            "calendar_access": False,
            "code_execution": True,
            "local_execution": True,
            "document_work": True,
        },
        "policy": {"security_level": "balanced"},
    },
    "crypto-analyst": {
        "name": "Crypto Analyst",
        "icon": "📈",
        "backend_role": "research",
        "description": "Market research, watchlists, and risk review.",
        "instructions": (
            "Act like a disciplined market analyst. Summarize conditions, risks, and decision points without pretending certainty."
        ),
        "capabilities": {
            "web_search": True,
            "calendar_access": False,
            "code_execution": False,
            "local_execution": False,
            "document_work": True,
        },
        "policy": {"security_level": "strict"},
    },
    "private-assistant": {
        "name": "Personal",
        "icon": "🧭",
        "backend_role": "private-assistant",
        "description": "Planning, study, reminders, and daily organization.",
        "instructions": (
            "Act like a trusted personal assistant. Keep things simple, organized, and proactive without becoming intrusive."
        ),
        "capabilities": {
            "web_search": False,
            "calendar_access": True,
            "code_execution": False,
            "local_execution": False,
            "document_work": True,
        },
        "policy": {"security_level": "balanced"},
    },
}

PROFILE_BOOTSTRAP_ORDER = [
    "custom",
    "support",
    "research",
    "builder",
    "crypto-analyst",
    "private-assistant",
]
VALID_SECURITY_LEVELS = {"strict", "balanced", "experimental"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify_profile_token(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return cleaned[:64] or "profile"


def _normalize_workspace(workspace_id: Optional[str]) -> str:
    token = str(workspace_id or "").strip()
    return _normalize_workspace_id(token) if token else "default"


def _profile_root_for_workspace(workspace_id: Optional[str]) -> Path:
    workspace_token = _normalize_workspace(workspace_id)
    root = Path(ORION_PROFILE_ROOT) / workspace_token
    root.mkdir(parents=True, exist_ok=True)
    return root


def _profile_dir(profile_id: str, workspace_id: Optional[str]) -> Path:
    token = _slugify_profile_token(profile_id)
    return _profile_root_for_workspace(workspace_id) / token


def _profile_metadata_path(profile_id: str, workspace_id: Optional[str]) -> Path:
    return _profile_dir(profile_id, workspace_id) / "profile.json"


def _profile_instructions_path(profile_id: str, workspace_id: Optional[str]) -> Path:
    return _profile_dir(profile_id, workspace_id) / "instructions.md"


def _profile_capabilities_path(profile_id: str, workspace_id: Optional[str]) -> Path:
    return _profile_dir(profile_id, workspace_id) / "capabilities.json"


def _profile_policy_path(profile_id: str, workspace_id: Optional[str]) -> Path:
    return _profile_dir(profile_id, workspace_id) / "policy.json"


def _profile_allowlist_path(profile_id: str, workspace_id: Optional[str]) -> Path:
    return _profile_dir(profile_id, workspace_id) / "allowlist.json"


def _profile_knowledge_dir(profile_id: str, workspace_id: Optional[str]) -> Path:
    path = _profile_dir(profile_id, workspace_id) / "knowledge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_read_json(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback
    return fallback


def _safe_read_text(path: Path, fallback: str = "") -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        return fallback
    return fallback


def _write_json(path: Path, payload: Any) -> None:
    _enforce_profile_file_state_decision(path=path, kind="json", payload=payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    _enforce_profile_file_state_decision(path=path, kind="text", payload={"content_bytes": len(str(text or "").encode("utf-8"))})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


class ProfileApiRustGateError(RuntimeError):
    pass


def _enforce_profile_file_state_decision(*, path: Path, kind: str, payload: Any) -> None:
    if isinstance(payload, dict):
        safe_payload = dict(payload)
    else:
        safe_payload = {"value_type": type(payload).__name__}
    safe_payload["path"] = str(path)
    safe_payload["kind"] = str(kind or "").strip()
    try:
        payload_bytes = len(
            json.dumps(
                safe_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_profile_api_file",
            state_class="profile_api_files",
            actor_id="system",
            status="active",
            payload=safe_payload,
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_profile_api_file":
            raise ProfileApiRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise ProfileApiRustGateError(exc.reason) from exc


def _default_profile_registry() -> Dict[str, Any]:
    return {"default_profile_ids": {}, "updated_at": _utc_now_iso()}


def _load_default_profile_registry() -> Dict[str, Any]:
    payload = _safe_read_json(Path(ORION_PROFILE_DEFAULT_FILE), _default_profile_registry())
    if not isinstance(payload, dict):
        return _default_profile_registry()
    default_ids = payload.get("default_profile_ids")
    payload["default_profile_ids"] = dict(default_ids) if isinstance(default_ids, dict) else {}
    payload.setdefault("updated_at", _utc_now_iso())
    return payload


def _save_default_profile_registry(payload: Dict[str, Any]) -> None:
    payload["updated_at"] = _utc_now_iso()
    _write_json(Path(ORION_PROFILE_DEFAULT_FILE), payload)


def _template_defaults(template_id: str) -> Dict[str, Any]:
    return dict(PROFILE_TEMPLATE_DEFAULTS.get(template_id) or PROFILE_TEMPLATE_DEFAULTS["custom"])


def _ensure_profile_files(profile_id: str, workspace_id: Optional[str], template_id: str, metadata_patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    workspace_token = _normalize_workspace(workspace_id)
    token = _slugify_profile_token(profile_id)
    template = _template_defaults(template_id)
    profile_dir = _profile_dir(token, workspace_token)
    profile_dir.mkdir(parents=True, exist_ok=True)
    _profile_knowledge_dir(token, workspace_token)

    metadata_path = _profile_metadata_path(token, workspace_token)
    existing_metadata = _safe_read_json(metadata_path, {})
    if not isinstance(existing_metadata, dict):
        existing_metadata = {}
    now = _utc_now_iso()
    metadata: Dict[str, Any] = {
        "id": token,
        "workspace_id": workspace_token,
        "template": template_id,
        "name": template["name"],
        "icon": template["icon"],
        "backend_role": template["backend_role"],
        "description": template["description"],
        "provider": None,
        "model": None,
        "created_at": existing_metadata.get("created_at") or now,
        "updated_at": now,
        "version": 1,
    }
    metadata.update({k: v for k, v in existing_metadata.items() if v is not None})
    if metadata_patch:
        metadata.update({k: v for k, v in metadata_patch.items() if v is not None})
    metadata["id"] = token
    metadata["workspace_id"] = workspace_token
    metadata["template"] = metadata.get("template") or template_id
    metadata["updated_at"] = now
    _write_json(metadata_path, metadata)

    instructions_path = _profile_instructions_path(token, workspace_token)
    if not instructions_path.exists():
        _write_text(instructions_path, str(template["instructions"]))

    capabilities_path = _profile_capabilities_path(token, workspace_token)
    if not capabilities_path.exists():
        _write_json(capabilities_path, {"items": template["capabilities"], "updated_at": now})

    policy_path = _profile_policy_path(token, workspace_token)
    if not policy_path.exists():
        _write_json(policy_path, {"security_level": template["policy"]["security_level"], "updated_at": now})

    allowlist_path = _profile_allowlist_path(token, workspace_token)
    if not allowlist_path.exists():
        _write_json(allowlist_path, {"commands": [], "updated_at": now})

    return metadata


def _bootstrap_profiles(workspace_id: Optional[str]) -> None:
    workspace_token = _normalize_workspace(workspace_id)
    root = _profile_root_for_workspace(workspace_token)
    if any(item.is_dir() for item in root.iterdir()):
        return
    for template_id in PROFILE_BOOTSTRAP_ORDER:
        _ensure_profile_files(template_id, workspace_token, template_id)
    registry = _load_default_profile_registry()
    default_ids = registry.get("default_profile_ids")
    if isinstance(default_ids, dict) and not str(default_ids.get(workspace_token) or "").strip():
        default_ids[workspace_token] = "custom"
        _save_default_profile_registry(registry)


def _list_profile_ids(workspace_id: Optional[str]) -> List[str]:
    _bootstrap_profiles(workspace_id)
    root = _profile_root_for_workspace(workspace_id)
    return sorted(item.name for item in root.iterdir() if item.is_dir())


def _serialize_profile(profile_id: str, workspace_id: Optional[str], *, include_content: bool) -> Dict[str, Any]:
    workspace_token = _normalize_workspace(workspace_id)
    metadata = _ensure_profile_files(profile_id, workspace_token, _safe_read_json(_profile_metadata_path(profile_id, workspace_token), {}).get("template") or "custom")
    capabilities = _safe_read_json(_profile_capabilities_path(profile_id, workspace_token), {"items": {}, "updated_at": None})
    policy = _safe_read_json(_profile_policy_path(profile_id, workspace_token), {"security_level": "balanced", "updated_at": None})
    allowlist = _safe_read_json(_profile_allowlist_path(profile_id, workspace_token), {"commands": [], "updated_at": None})
    knowledge_dir = _profile_knowledge_dir(profile_id, workspace_token)
    knowledge_items: List[Dict[str, Any]] = []
    for item in sorted(knowledge_dir.iterdir(), key=lambda value: value.name.lower()):
        if not item.is_file():
            continue
        stat = item.stat()
        knowledge_items.append(
            {
                "name": item.name,
                "size_bytes": int(stat.st_size),
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )

    out = {
        "id": str(metadata.get("id") or profile_id),
        "workspace_id": workspace_token,
        "name": str(metadata.get("name") or ""),
        "icon": str(metadata.get("icon") or ""),
        "template": str(metadata.get("template") or "custom"),
        "backend_role": str(metadata.get("backend_role") or "orchestrator"),
        "description": str(metadata.get("description") or ""),
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "created_at": metadata.get("created_at"),
        "updated_at": metadata.get("updated_at"),
        "knowledge_count": len(knowledge_items),
        "knowledge_items": knowledge_items,
        "capabilities": capabilities.get("items") if isinstance(capabilities, dict) else {},
        "policy": policy if isinstance(policy, dict) else {"security_level": "balanced"},
        "allowlist": allowlist if isinstance(allowlist, dict) else {"commands": []},
    }
    if include_content:
        out["instructions"] = _safe_read_text(_profile_instructions_path(profile_id, workspace_token))
    return out


def _get_default_profile_id(workspace_id: Optional[str]) -> str:
    workspace_token = _normalize_workspace(workspace_id)
    registry = _load_default_profile_registry()
    default_ids = registry.get("default_profile_ids")
    if not isinstance(default_ids, dict):
        return "custom"
    token = str(default_ids.get(workspace_token) or "").strip()
    return token or "custom"


def _set_default_profile_id(workspace_id: Optional[str], profile_id: str) -> str:
    workspace_token = _normalize_workspace(workspace_id)
    registry = _load_default_profile_registry()
    default_ids = registry.get("default_profile_ids")
    if not isinstance(default_ids, dict):
        default_ids = {}
        registry["default_profile_ids"] = default_ids
    default_ids[workspace_token] = _slugify_profile_token(profile_id)
    _save_default_profile_registry(registry)
    return str(default_ids[workspace_token])


def _profile_exists(profile_id: str, workspace_id: Optional[str]) -> bool:
    return _profile_dir(profile_id, workspace_id).exists()


class ProfileCreateRequest(BaseModel):
    id: Optional[str] = None
    workspace_id: Optional[str] = None
    name: str
    icon: Optional[str] = None
    template: Optional[str] = None
    backend_role: Optional[str] = None
    description: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    instructions: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    policy: Optional[Dict[str, Any]] = None
    allowlist: Optional[Dict[str, Any]] = None

    def validate_fields(self) -> None:
        if len(str(self.name or "").strip()) < 2:
            raise HTTPException(status_code=400, detail="Profile name is required.")
        template = str(self.template or "custom").strip()
        if template and template not in PROFILE_TEMPLATE_DEFAULTS:
            raise HTTPException(status_code=400, detail=f"Unsupported profile template '{template}'.")


class ProfilePatchRequest(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    template: Optional[str] = None
    backend_role: Optional[str] = None
    description: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    instructions: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    policy: Optional[Dict[str, Any]] = None
    allowlist: Optional[Dict[str, Any]] = None

    def validate_fields(self) -> None:
        if self.name is not None and len(str(self.name).strip()) < 2:
            raise HTTPException(status_code=400, detail="Profile name must be at least 2 characters.")
        if self.template is not None and str(self.template).strip() not in PROFILE_TEMPLATE_DEFAULTS:
            raise HTTPException(status_code=400, detail=f"Unsupported profile template '{self.template}'.")
        if self.policy is not None and not isinstance(self.policy, dict):
            raise HTTPException(status_code=400, detail="policy must be an object.")
        if self.policy is not None:
            level = str(self.policy.get("security_level") or "").strip().lower()
            if level and level not in VALID_SECURITY_LEVELS:
                raise HTTPException(status_code=400, detail="security_level must be strict, balanced, or experimental.")


def register_profile_routes(app) -> None:
    import server as _server

    module_globals = globals()
    for key, value in _server.__dict__.items():
        if key not in module_globals:
            module_globals[key] = value

    @app.get("/profiles", dependencies=[Depends(require_admin_api_key)])
    async def list_profiles(workspace_id: Optional[str] = None, current_user=Depends(require_admin_api_key)):
        workspace_token = _normalize_workspace(enforce_workspace_access(current_user, workspace_id))
        items = [_serialize_profile(profile_id, workspace_token, include_content=False) for profile_id in _list_profile_ids(workspace_token)]
        return {
            "items": items,
            "default_profile_id": _get_default_profile_id(workspace_token),
            "workspace_id": workspace_token,
        }

    @app.get("/profiles/{profile_id}", dependencies=[Depends(require_admin_api_key)])
    async def get_profile(profile_id: str, workspace_id: Optional[str] = None, current_user=Depends(require_admin_api_key)):
        workspace_token = _normalize_workspace(enforce_workspace_access(current_user, workspace_id))
        token = _slugify_profile_token(profile_id)
        if not _profile_exists(token, workspace_token):
            raise HTTPException(status_code=404, detail="Profile not found.")
        return {
            "item": _serialize_profile(token, workspace_token, include_content=True),
            "default_profile_id": _get_default_profile_id(workspace_token),
        }

    @app.post("/profiles", dependencies=[Depends(require_admin_api_key)])
    async def create_profile(body: ProfileCreateRequest, current_user=Depends(require_admin_api_key)):
        body.validate_fields()
        body.workspace_id = enforce_workspace_access(current_user, body.workspace_id)
        workspace_token = _normalize_workspace(body.workspace_id)
        template_id = str(body.template or "custom").strip() or "custom"
        explicit_id = _slugify_profile_token(body.id or body.name)
        profile_id = explicit_id
        suffix = 2
        while _profile_exists(profile_id, workspace_token):
            profile_id = f"{explicit_id}-{suffix}"
            suffix += 1

        metadata = _ensure_profile_files(
            profile_id,
            workspace_token,
            template_id,
            metadata_patch={
                "name": str(body.name).strip(),
                "icon": str(body.icon or "").strip() or _template_defaults(template_id)["icon"],
                "backend_role": str(body.backend_role or "").strip() or _template_defaults(template_id)["backend_role"],
                "description": str(body.description or "").strip() or _template_defaults(template_id)["description"],
                "provider": str(body.provider or "").strip() or None,
                "model": str(body.model or "").strip() or None,
            },
        )
        if body.instructions is not None:
            _write_text(_profile_instructions_path(profile_id, workspace_token), body.instructions)
        if body.capabilities is not None:
            _write_json(_profile_capabilities_path(profile_id, workspace_token), {"items": body.capabilities, "updated_at": _utc_now_iso()})
        if body.policy is not None:
            policy = dict(body.policy)
            policy["updated_at"] = _utc_now_iso()
            _write_json(_profile_policy_path(profile_id, workspace_token), policy)
        if body.allowlist is not None:
            allowlist = dict(body.allowlist)
            allowlist["updated_at"] = _utc_now_iso()
            _write_json(_profile_allowlist_path(profile_id, workspace_token), allowlist)
        return {"item": _serialize_profile(str(metadata["id"]), workspace_token, include_content=True)}

    @app.patch("/profiles/{profile_id}", dependencies=[Depends(require_admin_api_key)])
    async def patch_profile(
        profile_id: str,
        body: ProfilePatchRequest,
        workspace_id: Optional[str] = None,
        current_user=Depends(require_admin_api_key),
    ):
        body.validate_fields()
        workspace_token = _normalize_workspace(enforce_workspace_access(current_user, workspace_id))
        token = _slugify_profile_token(profile_id)
        if not _profile_exists(token, workspace_token):
            raise HTTPException(status_code=404, detail="Profile not found.")
        current = _safe_read_json(_profile_metadata_path(token, workspace_token), {})
        if not isinstance(current, dict):
            raise HTTPException(status_code=500, detail="Profile metadata is invalid.")
        template_id = str(body.template or current.get("template") or "custom").strip() or "custom"
        _ensure_profile_files(
            token,
            workspace_token,
            template_id,
            metadata_patch={
                "name": str(body.name).strip() if body.name is not None else None,
                "icon": str(body.icon).strip() if body.icon is not None else None,
                "template": template_id,
                "backend_role": str(body.backend_role).strip() if body.backend_role is not None else None,
                "description": str(body.description).strip() if body.description is not None else None,
                "provider": str(body.provider).strip() if body.provider is not None else None,
                "model": str(body.model).strip() if body.model is not None else None,
            },
        )
        if body.instructions is not None:
            _write_text(_profile_instructions_path(token, workspace_token), body.instructions)
        if body.capabilities is not None:
            _write_json(_profile_capabilities_path(token, workspace_token), {"items": body.capabilities, "updated_at": _utc_now_iso()})
        if body.policy is not None:
            policy = dict(body.policy)
            policy["updated_at"] = _utc_now_iso()
            _write_json(_profile_policy_path(token, workspace_token), policy)
        if body.allowlist is not None:
            allowlist = dict(body.allowlist)
            allowlist["updated_at"] = _utc_now_iso()
            _write_json(_profile_allowlist_path(token, workspace_token), allowlist)
        return {"item": _serialize_profile(token, workspace_token, include_content=True)}

    @app.post("/profiles/{profile_id}/set-default", dependencies=[Depends(require_admin_api_key)])
    async def set_default_profile(profile_id: str, workspace_id: Optional[str] = None, current_user=Depends(require_admin_api_key)):
        workspace_token = _normalize_workspace(enforce_workspace_access(current_user, workspace_id))
        token = _slugify_profile_token(profile_id)
        if not _profile_exists(token, workspace_token):
            raise HTTPException(status_code=404, detail="Profile not found.")
        default_profile_id = _set_default_profile_id(workspace_token, token)
        return {"ok": True, "default_profile_id": default_profile_id, "workspace_id": workspace_token}

    @app.post("/profiles/{profile_id}/reset", dependencies=[Depends(require_admin_api_key)])
    async def reset_profile(profile_id: str, workspace_id: Optional[str] = None, current_user=Depends(require_admin_api_key)):
        workspace_token = _normalize_workspace(enforce_workspace_access(current_user, workspace_id))
        token = _slugify_profile_token(profile_id)
        metadata = _safe_read_json(_profile_metadata_path(token, workspace_token), {})
        if not isinstance(metadata, dict):
            raise HTTPException(status_code=404, detail="Profile not found.")
        template_id = str(metadata.get("template") or "custom").strip() or "custom"
        template = _template_defaults(template_id)
        _ensure_profile_files(
            token,
            workspace_token,
            template_id,
            metadata_patch={
                "name": template["name"],
                "icon": template["icon"],
                "backend_role": template["backend_role"],
                "description": template["description"],
                "provider": None,
                "model": None,
            },
        )
        _write_text(_profile_instructions_path(token, workspace_token), template["instructions"])
        _write_json(_profile_capabilities_path(token, workspace_token), {"items": template["capabilities"], "updated_at": _utc_now_iso()})
        _write_json(_profile_policy_path(token, workspace_token), {"security_level": template["policy"]["security_level"], "updated_at": _utc_now_iso()})
        _write_json(_profile_allowlist_path(token, workspace_token), {"commands": [], "updated_at": _utc_now_iso()})
        knowledge_dir = _profile_knowledge_dir(token, workspace_token)
        for item in knowledge_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
        return {"item": _serialize_profile(token, workspace_token, include_content=True)}

    @app.post("/profiles/{profile_id}/test", dependencies=[Depends(require_admin_api_key)])
    async def test_profile(profile_id: str, workspace_id: Optional[str] = None, current_user=Depends(require_admin_api_key)):
        workspace_token = _normalize_workspace(enforce_workspace_access(current_user, workspace_id))
        token = _slugify_profile_token(profile_id)
        if not _profile_exists(token, workspace_token):
            raise HTTPException(status_code=404, detail="Profile not found.")
        item = _serialize_profile(token, workspace_token, include_content=True)
        goal = f"Act as my {item['name']} profile. {item['description'] or 'Handle this request directly and stay practical.'}"
        return {
            "ok": True,
            "profile_id": token,
            "workspace_id": workspace_token,
            "agent_role": item.get("backend_role"),
            "goal": goal,
            "chat_href": f"/?goal={quote_plus(goal)}",
        }
