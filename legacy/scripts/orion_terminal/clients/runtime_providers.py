from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from urllib import parse as urlparse

from ..core import Choice


RuntimeCall = Callable[[str, str, Optional[Dict[str, Any]], Optional[str]], Dict[str, Any]]


def list_providers_raw(call: RuntimeCall) -> List[Dict[str, Any]]:
    payload = call("/providers", "GET", None, None)
    items = payload.get("providers")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def list_provider_catalog(call: RuntimeCall) -> List[Choice]:
    out: List[Choice] = []
    for item in list_providers_raw(call):
        provider_id = str(item.get("id") or "").strip().lower()
        label = str(item.get("label") or provider_id or "provider")
        auth = item.get("auth") if isinstance(item.get("auth"), list) else []
        default_model = str(item.get("default_model") or "").strip()
        note_parts: List[str] = []
        if auth:
            note_parts.append("auth:" + ",".join([str(x) for x in auth]))
        if default_model:
            note_parts.append("default:" + default_model)
        if provider_id:
            out.append(Choice(provider_id, label, " | ".join(note_parts)))
    out.sort(key=lambda choice: choice.label.lower())
    return out


def list_workspace_credentials(call: RuntimeCall, workspace_id: str, provider: Optional[str] = None) -> List[Dict[str, Any]]:
    qs = f"?workspace_id={urlparse.quote(workspace_id)}"
    payload = call(f"/credentials/vault{qs}", "GET", None, None)
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if provider:
            p = str(item.get("provider") or "").strip().lower()
            if p != provider:
                continue
        out.append(item)
    return out


def list_workspace_profiles(call: RuntimeCall, workspace_id: str, provider: Optional[str] = None) -> List[Dict[str, Any]]:
    qs = f"?workspace_id={urlparse.quote(workspace_id)}"
    if provider:
        qs += f"&provider={urlparse.quote(provider)}"
    payload = call(f"/providers/profiles{qs}", "GET", None, None)
    items = payload.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def list_provider_models(
    call: RuntimeCall,
    provider_id: str,
    workspace_id: str,
    credential_id: Optional[str] = None,
) -> List[str]:
    path = f"/providers/{urlparse.quote(provider_id)}/models?workspace_id={urlparse.quote(workspace_id)}"
    if credential_id:
        path += f"&credential_id={urlparse.quote(credential_id)}"
    payload = call(path, "GET", None, None)
    items = payload.get("models")
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for item in items:
        model = str(item or "").strip()
        if model:
            out.append(model)
    return out
