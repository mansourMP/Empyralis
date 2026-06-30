from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def extract_run_owner_user_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = str(payload.get("owner_user_id") or "").strip()
    if direct:
        return direct
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return str(metadata.get("owner_user_id") or "").strip()


def current_user_is_privileged(
    current_user: Any,
    *,
    admin_user_ids: set[str],
    admin_emails: set[str],
) -> bool:
    if not isinstance(current_user, dict):
        return False
    if bool(current_user.get("is_admin")):
        return True
    if str(current_user.get("auth_type") or "").strip() == "api_key":
        return True
    user_id = str(current_user.get("user_id") or "").strip()
    email = str(current_user.get("email") or "").strip().lower()
    return bool((user_id and user_id in admin_user_ids) or (email and email in admin_emails))


def enforce_run_owner_access(
    current_user: Any,
    payload: Any,
    *,
    current_user_is_privileged_fn,
    extract_run_owner_user_id_fn,
) -> None:
    if current_user_is_privileged_fn(current_user):
        return
    if not isinstance(current_user, dict):
        raise HTTPException(status_code=401, detail="Authentication required.")
    request_user_id = str(current_user.get("user_id") or "").strip()
    if not request_user_id:
        raise HTTPException(status_code=401, detail="Authenticated user id is required.")
    owner_user_id = extract_run_owner_user_id_fn(payload)
    if not owner_user_id:
        raise HTTPException(status_code=403, detail="Run is not bound to an owner.")
    if owner_user_id != request_user_id:
        raise HTTPException(status_code=403, detail="Run is owned by another user.")


def stamp_request_owner(req: Any, current_user: Any) -> Any:
    if not isinstance(current_user, dict):
        return req
    auth_type = str(current_user.get("auth_type") or "").strip().lower()
    if auth_type == "api_key":
        metadata = dict(req.metadata or {})
        metadata["auth_type"] = "api_key"
        metadata.setdefault("owner_role", "owner")
        metadata["owner_is_admin"] = True
        req.metadata = metadata
        return req
    owner_user_id = str(current_user.get("user_id") or "").strip()
    if not owner_user_id:
        return req
    metadata = dict(req.metadata or {})
    metadata["owner_user_id"] = owner_user_id
    email = str(current_user.get("email") or "").strip().lower()
    if email:
        metadata["owner_email"] = email
    role = str(current_user.get("role") or "").strip().lower()
    if role:
        metadata["owner_role"] = role
    metadata["owner_is_admin"] = bool(current_user.get("is_admin"))
    if auth_type:
        metadata["auth_type"] = auth_type
    req.metadata = metadata
    return req
