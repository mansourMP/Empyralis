"""Pilot invite service — technical cohort enforcement for customer pilot programs."""

from __future__ import annotations

import os
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException

from server_modules import control_plane_repository


ORION_PILOT_SIGNUP_MODE = str(os.environ.get("ORION_PILOT_SIGNUP_MODE") or "").strip().lower()
ORION_PILOT_SIGNUP_ENABLED = ORION_PILOT_SIGNUP_MODE in {"open", "invite_only"}


def pilot_signup_requires_invite() -> bool:
    return ORION_PILOT_SIGNUP_MODE == "invite_only"


def _generate_code() -> str:
    """Generate a 12-character uppercase alphanumeric pilot invite code."""
    raw = secrets.token_urlsafe(9).replace("-", "").replace("_", "")
    while len(raw) < 12:
        raw += secrets.token_urlsafe(3).replace("-", "").replace("_", "")
    return raw.upper()[:12]


async def create_pilot_invite(
    *,
    email: Optional[str] = None,
    role: str = "owner",
    plan_id: str = "pilot",
    max_uses: int = 1,
    expires_hours: Optional[int] = None,
    created_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new pilot invite code."""
    code = _generate_code()
    expires_at: Optional[int] = None
    if expires_hours is not None:
        expires_at = int(time.time()) + (expires_hours * 3600)
    invite = await control_plane_repository.create_pilot_invite(
        code=code,
        email=email,
        role=role,
        plan_id=plan_id,
        max_uses=max_uses,
        expires_at=expires_at,
        created_by_user_id=created_by_user_id,
        metadata=metadata,
    )
    if not isinstance(invite, dict):
        raise HTTPException(status_code=500, detail="Pilot invite could not be created.")
    return invite


async def validate_pilot_invite_code(code: str) -> Dict[str, Any]:
    """Validate a pilot invite code without consuming it."""
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        raise HTTPException(status_code=400, detail="Invite code is required.")
    invite = await control_plane_repository.get_pilot_invite_by_code(clean_code)
    if not isinstance(invite, dict):
        raise HTTPException(status_code=404, detail="Invite code not found.")
    if invite.get("status") != "active":
        raise HTTPException(status_code=410, detail="Invite code is no longer active.")
    expires_at = invite.get("expires_at")
    if expires_at is not None and int(expires_at) < int(time.time()):
        raise HTTPException(status_code=410, detail="Invite code has expired.")
    uses = int(invite.get("uses_count") or 0)
    max_uses = int(invite.get("max_uses") or 1)
    if uses >= max_uses:
        raise HTTPException(status_code=410, detail="Invite code has reached its use limit.")
    return {
        "valid": True,
        "code": invite.get("code"),
        "email": invite.get("email"),
        "role": invite.get("role"),
        "plan_id": invite.get("plan_id"),
        "uses_remaining": max(0, max_uses - uses),
    }


async def claim_pilot_invite_code(code: str) -> Dict[str, Any]:
    """Consume a pilot invite code and return the associated plan/role."""
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        raise HTTPException(status_code=400, detail="Invite code is required.")
    invite = await control_plane_repository.claim_pilot_invite(clean_code)
    if not isinstance(invite, dict):
        raise HTTPException(status_code=404, detail="Invite code is invalid, expired, or fully consumed.")
    return {
        "claimed": True,
        "code": invite.get("code"),
        "email": invite.get("email"),
        "role": invite.get("role"),
        "plan_id": invite.get("plan_id"),
    }


async def list_pilot_invites() -> list[Dict[str, Any]]:
    """List all pilot invites."""
    return await control_plane_repository.list_pilot_invites()


async def revoke_pilot_invite(invite_id: str) -> Dict[str, Any]:
    """Revoke a pilot invite by ID."""
    clean_invite_id = str(invite_id or "").strip()
    if not clean_invite_id:
        raise HTTPException(status_code=400, detail="Invite ID is required.")
    invite = await control_plane_repository.revoke_pilot_invite(clean_invite_id)
    if not isinstance(invite, dict):
        raise HTTPException(status_code=404, detail="Pilot invite not found.")
    return invite
