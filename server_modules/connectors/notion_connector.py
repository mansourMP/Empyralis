from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


NotionHttpRequest = Callable[..., Dict[str, Any]]

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


from server_modules.channel_sdk import _http_json_request


def _ensure_ok(response: Dict[str, Any], *, fallback_error: str) -> Any:
    status = int(response.get("status") or 0)
    if 200 <= status < 300:
        return response.get("json")
    body = response.get("json") if isinstance(response.get("json"), dict) else {}
    detail = str(body.get("message") or body.get("code") or response.get("text") or "").strip()
    raise RuntimeError(detail or fallback_error)


def _token(credentials: Dict[str, Any]) -> str:
    token = str(
        credentials.get("integration_token")
        or credentials.get("access_token")
        or credentials.get("oauth_access_token")
        or credentials.get("token")
        or ""
    ).strip()
    if not token:
        raise RuntimeError("Notion token is required.")
    return token


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Notion-Version": NOTION_VERSION,
        "User-Agent": "Empyralist-Notion-Connector",
    }


def _request_json(
    path: str,
    credentials: Dict[str, Any],
    *,
    method: str = "GET",
    payload: Optional[Any] = None,
    http_json_request: Optional[NotionHttpRequest] = None,
) -> Any:
    request_fn = http_json_request or _http_json_request
    response = request_fn(
        f"{NOTION_API_BASE}{path}",
        method=method,
        headers=_headers(_token(credentials)),
        payload=payload,
    )
    return _ensure_ok(response, fallback_error=f"Notion request failed: {path}")


def _normalize_block_text(value: Any) -> List[Dict[str, Any]]:
    text = str(value or "").strip()
    if not text:
        return []
    return [{"type": "text", "text": {"content": text}}]


def _title_property(title: str, *, property_name: str = "title") -> Dict[str, Any]:
    return {
        str(property_name or "title"): {
            "title": _normalize_block_text(title),
        }
    }


def _normalize_parent(parent_id: str, parent_type: str = "page_id") -> Dict[str, Any]:
    normalized_id = str(parent_id or "").strip()
    if not normalized_id:
        raise RuntimeError("Notion parent_id is required.")
    normalized_type = str(parent_type or "").strip().lower()
    if normalized_type in {"database", "database_id", "db"}:
        return {"database_id": normalized_id}
    return {"page_id": normalized_id}


def validate_notion_credentials(
    credentials: Dict[str, Any],
    *,
    http_json_request: Optional[NotionHttpRequest] = None,
) -> Dict[str, Any]:
    body = _request_json("/users/me", credentials, http_json_request=http_json_request)
    if not isinstance(body, dict):
        raise RuntimeError("Notion validation response was invalid.")
    bot = body.get("bot") if isinstance(body.get("bot"), dict) else {}
    owner = bot.get("owner") if isinstance(bot.get("owner"), dict) else {}
    workspace_name = str(owner.get("workspace_name") or "").strip()
    workspace_id = str(owner.get("workspace_id") or "").strip()
    auth_mode = "oauth" if str(credentials.get("access_token") or credentials.get("oauth_access_token") or "").strip() else "integration_token"
    normalized = dict(credentials)
    normalized["auth_mode"] = auth_mode
    if workspace_name:
        normalized["workspace_name"] = workspace_name
    if workspace_id:
        normalized["workspace_id"] = workspace_id
    return {
        "ok": True,
        "status": 200,
        "message": "Notion connector is valid.",
        "auth_mode": auth_mode,
        "workspace_name": workspace_name or None,
        "workspace_id": workspace_id or None,
        "profile": {
            "name": workspace_name or None,
            "workspace_id": workspace_id or None,
            "bot_id": str(bot.get("id") or "").strip() or None,
        },
        "credentials": normalized,
    }


def search(
    credentials: Dict[str, Any],
    query: str,
    *,
    filter_type: Optional[str] = None,
    http_json_request: Optional[NotionHttpRequest] = None,
) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"query": str(query or "").strip()}
    if str(filter_type or "").strip():
        payload["filter"] = {"property": "object", "value": str(filter_type).strip()}
    body = _request_json("/search", credentials, method="POST", payload=payload, http_json_request=http_json_request)
    results = body.get("results") if isinstance(body, dict) and isinstance(body.get("results"), list) else []
    return [item for item in results if isinstance(item, dict)]


def get_page(
    credentials: Dict[str, Any],
    page_id: str,
    *,
    http_json_request: Optional[NotionHttpRequest] = None,
) -> Dict[str, Any]:
    body = _request_json(
        f"/pages/{urlparse.quote(str(page_id or '').strip())}",
        credentials,
        http_json_request=http_json_request,
    )
    return body if isinstance(body, dict) else {}


def create_page(
    credentials: Dict[str, Any],
    parent_id: str,
    title: str,
    content_blocks: List[Dict[str, Any]],
    *,
    parent_type: str = "page_id",
    title_property_name: str = "title",
    http_json_request: Optional[NotionHttpRequest] = None,
) -> Dict[str, Any]:
    payload = {
        "parent": _normalize_parent(parent_id, parent_type),
        "properties": _title_property(title, property_name=title_property_name),
        "children": [item for item in (content_blocks or []) if isinstance(item, dict)],
    }
    body = _request_json("/pages", credentials, method="POST", payload=payload, http_json_request=http_json_request)
    return body if isinstance(body, dict) else {}


def update_page(
    credentials: Dict[str, Any],
    page_id: str,
    properties: Dict[str, Any],
    *,
    archived: Optional[bool] = None,
    http_json_request: Optional[NotionHttpRequest] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"properties": properties if isinstance(properties, dict) else {}}
    if isinstance(archived, bool):
        payload["archived"] = archived
    body = _request_json(
        f"/pages/{urlparse.quote(str(page_id or '').strip())}",
        credentials,
        method="PATCH",
        payload=payload,
        http_json_request=http_json_request,
    )
    return body if isinstance(body, dict) else {}


def append_blocks(
    credentials: Dict[str, Any],
    page_id: str,
    blocks: List[Dict[str, Any]],
    *,
    http_json_request: Optional[NotionHttpRequest] = None,
) -> Dict[str, Any]:
    body = _request_json(
        f"/blocks/{urlparse.quote(str(page_id or '').strip())}/children",
        credentials,
        method="PATCH",
        payload={"children": [item for item in (blocks or []) if isinstance(item, dict)]},
        http_json_request=http_json_request,
    )
    return body if isinstance(body, dict) else {}


def query_database(
    credentials: Dict[str, Any],
    database_id: str,
    *,
    filter: Optional[Dict[str, Any]] = None,
    sorts: Optional[List[Dict[str, Any]]] = None,
    http_json_request: Optional[NotionHttpRequest] = None,
) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {}
    if isinstance(filter, dict) and filter:
        payload["filter"] = filter
    if isinstance(sorts, list) and sorts:
        payload["sorts"] = [item for item in sorts if isinstance(item, dict)]
    body = _request_json(
        f"/databases/{urlparse.quote(str(database_id or '').strip())}/query",
        credentials,
        method="POST",
        payload=payload,
        http_json_request=http_json_request,
    )
    results = body.get("results") if isinstance(body, dict) and isinstance(body.get("results"), list) else []
    return [item for item in results if isinstance(item, dict)]


def create_database_item(
    credentials: Dict[str, Any],
    database_id: str,
    properties: Dict[str, Any],
    *,
    content_blocks: Optional[List[Dict[str, Any]]] = None,
    http_json_request: Optional[NotionHttpRequest] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "parent": {"database_id": str(database_id or "").strip()},
        "properties": properties if isinstance(properties, dict) else {},
    }
    if isinstance(content_blocks, list) and content_blocks:
        payload["children"] = [item for item in content_blocks if isinstance(item, dict)]
    body = _request_json("/pages", credentials, method="POST", payload=payload, http_json_request=http_json_request)
    return body if isinstance(body, dict) else {}
