"""Push device registration: the two routes a client app needs.

    POST   /api/push/devices           register (or re-register) a token
    DELETE /api/push/devices/{token}   retire it (signed out, push off)

Both are gated by auth_module.get_current_user + enforce_workspace_access
with minimum_role="viewer" -- registering YOUR OWN device is not an
administrative act, and the boundary that matters here is the per-person
one, which is enforced below by taking user_id from `current_user` and
NEVER from the request body. A `user_id` field on either of these routes
would let any workspace member register a device on somebody else's behalf,
or read/retire theirs, with one edit to a JSON body.

The registration response reports `apns_configured` honestly -- a device can
be registered perfectly on a deployment that has no APNs key at all (the
state today), and telling the app "you are registered" while implying pushes
will arrive is the outcome-honesty failure this codebase keeps re-learning.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server_modules import apns_service
from server_modules import auth as auth_module
from server_modules import control_plane_repository
from server_modules import push_device_repository

router = APIRouter()
get_current_user = auth_module.get_current_user
LOGGER = logging.getLogger(__name__)


class RegisterPushDeviceRequest(BaseModel):
    workspace_id: str
    device_token: str
    platform: str = "ios"
    bundle_id: str = ""
    environment: str = "production"
    device_id: str = ""


def _require_user_id(current_user: Optional[Dict[str, Any]]) -> str:
    """The person is resolved from the SESSION, never from the payload."""
    user_id = str((current_user or {}).get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Sign in to manage push devices.")
    return user_id


@router.post("/push/devices")
async def register_push_device(
    body: RegisterPushDeviceRequest,
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    workspace_id = auth_module.enforce_workspace_access(
        current_user, body.workspace_id, minimum_role="viewer"
    )
    user_id = _require_user_id(current_user)
    device_token = str(body.device_token or "").strip()
    if not device_token:
        raise HTTPException(status_code=400, detail="A device token is required.")
    tenant_id = await control_plane_repository.resolve_tenant_id_for_workspace(workspace_id)
    try:
        device = await push_device_repository.register_device_token(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            device_token=device_token,
            platform=body.platform,
            bundle_id=body.bundle_id,
            environment=body.environment,
            device_id=body.device_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOGGER.warning("push_device_register_failed error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Couldn't save this device right now."
        ) from exc
    return {
        "ok": True,
        "device": device,
        # "registered" and "pushes will arrive" are different facts. The
        # second one is false on every deployment with no APNs key, which
        # is all of them today.
        "apns_configured": apns_service.is_configured(),
    }


@router.delete("/push/devices/{device_token}")
async def deactivate_push_device(
    device_token: str,
    workspace_id: str,
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_workspace_id = auth_module.enforce_workspace_access(
        current_user, workspace_id, minimum_role="viewer"
    )
    user_id = _require_user_id(current_user)
    tenant_id = await control_plane_repository.resolve_tenant_id_for_workspace(
        resolved_workspace_id
    )
    try:
        changed = await push_device_repository.deactivate_device_token(
            tenant_id=tenant_id,
            workspace_id=resolved_workspace_id,
            user_id=user_id,
            device_token=device_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOGGER.warning("push_device_deactivate_failed error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Couldn't turn this device off right now."
        ) from exc
    # `deactivated: False` means "nothing matched" -- not yours, already
    # off, or never registered. The request succeeded either way; the two
    # outcomes are distinguishable rather than collapsed into one "ok".
    return {"ok": True, "deactivated": changed}
