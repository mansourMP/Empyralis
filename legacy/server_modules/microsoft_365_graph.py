from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote
import ssl

import certifi


MICROSOFT_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
HttpJsonRequest = Callable[..., Dict[str, Any]]


def microsoft_365_access_token(credentials: Dict[str, Any]) -> str:
    return str(credentials.get("access_token") or "").strip()


def microsoft_365_headers(credentials: Dict[str, Any]) -> Dict[str, str]:
    access_token = microsoft_365_access_token(credentials)
    if not access_token:
        raise RuntimeError("Microsoft 365 access_token is required.")
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def microsoft_graph_request(
    http_json_request: HttpJsonRequest,
    credentials: Dict[str, Any],
    path: str,
    *,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    url = path if path.startswith("http://") or path.startswith("https://") else f"{MICROSOFT_GRAPH_ROOT}{path}"
    response = http_json_request(url, method=method, headers=microsoft_365_headers(credentials), payload=payload, timeout=timeout)
    body = response.get("json") if isinstance(response.get("json"), dict) else {}
    if int(response.get("status") or 0) >= 400:
        raise RuntimeError(str(body.get("error", {}).get("message") or f"Microsoft Graph request failed for {path}").strip())
    if isinstance(body, dict):
        return body
    raise RuntimeError(f"Microsoft Graph response for {path} was invalid.")


def microsoft_graph_binary_request(
    credentials: Dict[str, Any],
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    content_type: str | None = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    headers = microsoft_365_headers(credentials)
    if data is not None and content_type:
        headers["Content-Type"] = content_type
    elif data is not None:
        headers.pop("Content-Type", None)
    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    context = ssl.create_default_context(cafile=certifi.where())
    opener = urlrequest.build_opener(
        urlrequest.ProxyHandler({}),
        urlrequest.HTTPSHandler(context=context),
        urlrequest.HTTPHandler(),
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
            return {
                "status": resp.status,
                "body": body,
                "text": body.decode("utf-8", errors="ignore"),
            }
    except urlerror.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        detail = raw.decode("utf-8", errors="ignore").strip() or str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def microsoft_365_normalize_drive_path(path: str) -> str:
    raw = str(path or "").strip()
    for prefix in ("onedrive:/", "m365:/", "onedrive://", "m365://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    return raw.lstrip("/")


def microsoft_365_drive_item_url(path: str, *, suffix: str = "") -> str:
    normalized = microsoft_365_normalize_drive_path(path)
    if not normalized:
        raise RuntimeError("OneDrive path is required.")
    encoded = quote(normalized, safe="/")
    return f"{MICROSOFT_GRAPH_ROOT}/me/drive/root:/{encoded}:{suffix}"


def microsoft_365_download_drive_file(credentials: Dict[str, Any], path: str) -> bytes:
    response = microsoft_graph_binary_request(
        credentials,
        microsoft_365_drive_item_url(path, suffix="/content"),
        method="GET",
        timeout=30,
    )
    return response.get("body") if isinstance(response.get("body"), bytes) else b""


def microsoft_365_upload_drive_file(
    credentials: Dict[str, Any],
    path: str,
    content: bytes,
    *,
    content_type: str = "application/octet-stream",
) -> Dict[str, Any]:
    response = microsoft_graph_binary_request(
        credentials,
        microsoft_365_drive_item_url(path, suffix="/content"),
        method="PUT",
        data=content,
        content_type=content_type,
        timeout=60,
    )
    text = response.get("text") if isinstance(response.get("text"), str) else ""
    try:
        import json
        payload = json.loads(text) if text else {}
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Microsoft Graph upload response was invalid.")


def microsoft_365_get_profile(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    return microsoft_graph_request(
        http_json_request,
        credentials,
        "/me?$select=id,displayName,mail,userPrincipalName",
    )


def microsoft_365_probe_capabilities(credentials: Dict[str, Any], http_json_request: HttpJsonRequest) -> Dict[str, Any]:
    def _probe(path: str) -> Tuple[bool, Dict[str, Any], str | None]:
        try:
            payload = microsoft_graph_request(http_json_request, credentials, path)
            return True, payload, None
        except Exception as exc:
            return False, {}, str(exc)

    mail_access, _mail_payload, mail_error = _probe("/me/mailFolders/inbox/messages?$top=1&$select=id,subject")
    calendar_access, calendars_payload, calendar_error = _probe("/me/calendars?$top=10&$select=id,name,isDefaultCalendar")
    files_access, drive_payload, files_error = _probe("/me/drive?$select=id,driveType,webUrl")

    calendars_preview: List[Dict[str, Any]] = []
    if calendar_access:
        for item in calendars_payload.get("value", []) if isinstance(calendars_payload.get("value"), list) else []:
            if not isinstance(item, dict):
                continue
            calendars_preview.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "default": bool(item.get("isDefaultCalendar")),
                }
            )
            if len(calendars_preview) >= 10:
                break

    drive: Dict[str, Any] = {}
    if files_access:
        drive = {
            "id": drive_payload.get("id"),
            "driveType": drive_payload.get("driveType"),
            "webUrl": drive_payload.get("webUrl"),
        }

    warnings: List[str] = []
    if not mail_access:
        warnings.append(
            "Outlook mail scopes are missing; email actions will stay disabled until Graph mail access is granted."
            + (f" ({mail_error})" if mail_error else "")
        )
    if not calendar_access:
        warnings.append(
            "Calendar scopes are missing; scheduling actions will stay disabled until Graph calendar access is granted."
            + (f" ({calendar_error})" if calendar_error else "")
        )
    if not files_access:
        warnings.append(
            "OneDrive file scopes are missing; Word, Excel, PowerPoint, and file actions will stay disabled until Graph file access is granted."
            + (f" ({files_error})" if files_error else "")
        )

    return {
        "mail_access": mail_access,
        "calendar_access": calendar_access,
        "files_access": files_access,
        "calendars_preview": calendars_preview,
        "drive": drive or None,
        "warnings": warnings,
    }


def microsoft_365_send_message(
    credentials: Dict[str, Any],
    http_json_request: HttpJsonRequest,
    to_email: str,
    subject: str,
    body_text: str,
) -> Dict[str, Any]:
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body_text,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": to_email,
                    }
                }
            ],
        },
        "saveToSentItems": True,
    }
    return microsoft_graph_request(
        http_json_request,
        credentials,
        "/me/sendMail",
        method="POST",
        payload=payload,
        timeout=30,
    )


def microsoft_365_create_draft(
    credentials: Dict[str, Any],
    http_json_request: HttpJsonRequest,
    to_email: str,
    subject: str,
    body_text: str,
) -> Dict[str, Any]:
    payload = {
        "subject": subject,
        "body": {
            "contentType": "Text",
            "content": body_text,
        },
        "toRecipients": [
            {
                "emailAddress": {
                    "address": to_email,
                }
            }
        ],
    }
    return microsoft_graph_request(
        http_json_request,
        credentials,
        "/me/messages",
        method="POST",
        payload=payload,
        timeout=30,
    )


def microsoft_365_create_calendar_event(
    credentials: Dict[str, Any],
    http_json_request: HttpJsonRequest,
    *,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return microsoft_graph_request(
        http_json_request,
        credentials,
        "/me/events",
        method="POST",
        payload=payload,
        timeout=30,
    )


def microsoft_365_list_drive_children(
    credentials: Dict[str, Any],
    http_json_request: HttpJsonRequest,
    *,
    path: str = "",
    top: int = 50,
) -> Dict[str, Any]:
    normalized = microsoft_365_normalize_drive_path(path)
    query = f"?$top={max(1, min(int(top), 100))}&$select=id,name,file,folder,webUrl,lastModifiedDateTime,size"
    resource = "/me/drive/root/children" + query if not normalized else f"/me/drive/root:/{quote(normalized, safe='/')}:/children" + query
    payload = microsoft_graph_request(
        http_json_request,
        credentials,
        resource,
        timeout=20,
    )
    items: List[Dict[str, Any]] = []
    for item in payload.get("value", []) if isinstance(payload.get("value"), list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        child_path = f"{normalized}/{name}".strip("/") if normalized else name
        items.append(
            {
                "id": item.get("id"),
                "name": name,
                "kind": "folder" if isinstance(item.get("folder"), dict) else "file",
                "size": item.get("size"),
                "lastModifiedDateTime": item.get("lastModifiedDateTime"),
                "webUrl": item.get("webUrl"),
                "path": f"onedrive:/{child_path}" if child_path else "onedrive:/",
            }
        )
    return {
        "path": f"onedrive:/{normalized}" if normalized else "onedrive:/",
        "normalized_path": normalized,
        "items": items,
    }


def microsoft_365_list_drive_items(
    credentials: Dict[str, Any],
    http_json_request: HttpJsonRequest,
    *,
    top: int = 10,
) -> List[Dict[str, Any]]:
    payload = microsoft_graph_request(
        http_json_request,
        credentials,
        f"/me/drive/root/children?$top={max(1, min(int(top), 25))}&$select=id,name,file,folder,webUrl",
        timeout=20,
    )
    items: List[Dict[str, Any]] = []
    for item in payload.get("value", []) if isinstance(payload.get("value"), list) else []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "kind": "folder" if isinstance(item.get("folder"), dict) else "file",
                "webUrl": item.get("webUrl"),
            }
        )
    return items
