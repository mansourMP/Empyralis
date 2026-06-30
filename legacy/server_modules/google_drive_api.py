"""
Google Workspace Drive + Docs + Sheets API helpers.

Extracted from server.py to reduce hotspot size.
All function signatures and behaviour are unchanged.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List
from urllib.parse import urlencode

from .google_workspace_cli import (
    GOOGLE_DRIVE_FOLDER_MIME,
    google_workspace_uses_local_cli,
    google_workspace_local_list_drive_children,
    google_workspace_local_create_document,
    google_workspace_local_create_spreadsheet,
)

_server = None  # populated by _init()


def _init():
    """Late-bind references to server.py globals. Called on first use."""
    global _server
    if _server is not None:
        return
    import server as _s
    _server = _s


def http_json_request(url: str, **kwargs):
    _init()
    return _server.http_json_request(url, **kwargs)


def _normalize_google_drive_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "gdrive:/"
    normalized = raw.replace("gdrive://", "gdrive:/").replace("drive://", "gdrive:/")
    if normalized.startswith("drive:/"):
        normalized = "gdrive:/" + normalized[len("drive:/"):].lstrip("/")
    if not normalized.startswith("gdrive:/"):
        normalized = "gdrive:/" + normalized.lstrip("/")
    normalized = normalized.rstrip("/")
    return normalized or "gdrive:/"


def _google_drive_path_parts(path: str) -> List[str]:
    normalized = _normalize_google_drive_path(path)
    relative = normalized.replace("gdrive:/", "", 1).strip("/")
    if not relative:
        return []
    return [segment for segment in relative.split("/") if segment]


def _google_drive_headers(credentials: Dict[str, Any]) -> Dict[str, str]:
    access_token = str(credentials.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Google Workspace access token is missing.")
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _google_drive_list_files(credentials: Dict[str, Any], *, q: str, page_size: int = 50) -> List[Dict[str, Any]]:
    params = urlencode(
        {
            "pageSize": max(1, min(int(page_size or 50), 100)),
            "fields": "files(id,name,mimeType,webViewLink,size,modifiedTime)",
            "orderBy": "folder,name",
            "q": q,
        }
    )
    response = http_json_request(
        f"https://www.googleapis.com/drive/v3/files?{params}",
        headers=_google_drive_headers(credentials),
    )
    if int(response.get("status") or 0) != 200:
        raise RuntimeError("Failed to browse Google Drive.")
    body = response.get("json")
    if not isinstance(body, dict):
        raise RuntimeError("Google Drive listing response was invalid.")
    items = body.get("files")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _resolve_google_drive_folder_id(credentials: Dict[str, Any], path: str) -> str:
    current_folder_id = "root"
    for segment in _google_drive_path_parts(path):
        escaped_name = json.dumps(segment)
        items = _google_drive_list_files(
            credentials,
            q=(
                f"trashed = false and mimeType = '{GOOGLE_DRIVE_FOLDER_MIME}' "
                f"and '{current_folder_id}' in parents and name = {escaped_name}"
            ),
            page_size=1,
        )
        if not items:
            raise RuntimeError(f"Google Drive folder not found: {segment}")
        current_folder_id = str(items[0].get("id") or "").strip() or current_folder_id
    return current_folder_id


def google_workspace_list_drive_children(credentials: Dict[str, Any], *, path: str = "", top: int = 50) -> Dict[str, Any]:
    if google_workspace_uses_local_cli(credentials):
        return google_workspace_local_list_drive_children(credentials, path=path, top=top)

    normalized_path = _normalize_google_drive_path(path)
    parent_id = _resolve_google_drive_folder_id(credentials, normalized_path)
    prefix = normalized_path.rstrip("/")
    items = _google_drive_list_files(
        credentials,
        q=f"trashed = false and '{parent_id}' in parents",
        page_size=top,
    )
    children: List[Dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        mime_type = str(item.get("mimeType") or "").strip()
        child_path = f"{prefix}/{name}" if prefix and prefix != "gdrive:" else f"gdrive:/{name}"
        child_path = child_path.replace("gdrive://", "gdrive:/")
        children.append(
            {
                "id": item.get("id"),
                "name": name,
                "kind": "folder" if mime_type == GOOGLE_DRIVE_FOLDER_MIME else "file",
                "path": child_path,
                "webUrl": item.get("webViewLink"),
                "size": item.get("size"),
                "modifiedTime": item.get("modifiedTime"),
                "mimeType": mime_type,
            }
        )
    return {"path": normalized_path, "items": children}


def google_workspace_create_document(credentials: Dict[str, Any], title: str) -> Dict[str, Any]:
    if google_workspace_uses_local_cli(credentials):
        return google_workspace_local_create_document(credentials, title)

    response = http_json_request(
        "https://docs.googleapis.com/v1/documents",
        method="POST",
        headers=_google_drive_headers(credentials),
        payload={"title": title},
    )
    if int(response.get("status") or 0) not in {200, 201}:
        raise RuntimeError("Google Docs create request failed.")
    body = response.get("json")
    if isinstance(body, dict):
        return body
    raise RuntimeError("Google Docs create response was invalid.")


def google_workspace_create_spreadsheet(credentials: Dict[str, Any], title: str) -> Dict[str, Any]:
    if google_workspace_uses_local_cli(credentials):
        return google_workspace_local_create_spreadsheet(credentials, title)

    response = http_json_request(
        "https://sheets.googleapis.com/v4/spreadsheets",
        method="POST",
        headers=_google_drive_headers(credentials),
        payload={"properties": {"title": title}},
    )
    if int(response.get("status") or 0) not in {200, 201}:
        raise RuntimeError("Google Sheets create request failed.")
    body = response.get("json")
    if isinstance(body, dict):
        return body
    raise RuntimeError("Google Sheets create response was invalid.")


def google_workspace_read_spreadsheet(credentials: Dict[str, Any], spreadsheet_id: str, range: str = "Sheet1") -> List[Dict[str, Any]]:
    """Read values from a Google Sheet and return them as a list of row dicts.

    The first row is treated as headers. Subsequent rows are mapped to dicts
    using those headers. Empty cells become empty strings.
    """
    if google_workspace_uses_local_cli(credentials):
        # Local CLI mode not yet implemented for reads; fall through to API.
        pass

    encoded_range = range.replace("/", "%2F")
    response = http_json_request(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
        headers=_google_drive_headers(credentials),
    )
    if int(response.get("status") or 0) != 200:
        raise RuntimeError(f"Google Sheets read request failed for {spreadsheet_id}.")
    body = response.get("json")
    if not isinstance(body, dict):
        raise RuntimeError("Google Sheets read response was invalid.")
    values = body.get("values", [])
    if not values:
        return []
    headers = [str(h).strip() for h in values[0]]
    rows: List[Dict[str, Any]] = []
    for row in values[1:]:
        row_dict: Dict[str, Any] = {}
        for i, header in enumerate(headers):
            row_dict[header] = row[i] if i < len(row) else ""
        rows.append(row_dict)
    return rows

