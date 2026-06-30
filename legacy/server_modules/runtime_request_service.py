from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def require_authenticated_user(current_user: Any) -> dict[str, Any]:
    if not isinstance(current_user, dict):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current_user


async def read_json_payload(request: Any, *, invalid_detail: str) -> Any:
    try:
        return await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{invalid_detail}: {exc}") from exc


async def read_json_object_payload(request: Any, *, invalid_detail: str) -> dict[str, Any]:
    payload = await read_json_payload(request, invalid_detail=invalid_detail)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail=invalid_detail)
    return payload
