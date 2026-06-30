from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_GWS_CONFIG_DIR = ROOT_DIR / ".gws-config"
DEFAULT_GWS_PROJECT_ID = str(os.getenv("GOOGLE_WORKSPACE_PROJECT_ID") or "empyralis-gws-cli").strip() or "empyralis-gws-cli"
LOCAL_AUTH_MODES = {"gws_local", "local_gws", "google_workspace_cli", "gws"}
GOOGLE_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
CA_BUNDLE_CANDIDATES = (
    Path("/etc/ssl/cert.pem"),
    Path("/opt/homebrew/etc/openssl@3/cert.pem"),
    Path("/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/site-packages/certifi/cacert.pem"),
)


def google_workspace_uses_local_cli(credentials: Dict[str, Any]) -> bool:
    auth_mode = str(credentials.get("auth_mode") or credentials.get("authMode") or "").strip().lower()
    if auth_mode in LOCAL_AUTH_MODES:
        return True
    if credentials.get("use_local_cli") is True or credentials.get("use_local_gws") is True:
        return True
    raw_config = str(credentials.get("gws_config_dir") or "").strip()
    if raw_config:
        return True
    return False


def resolve_gws_config_dir(credentials: Optional[Dict[str, Any]] = None) -> Path:
    payload = credentials if isinstance(credentials, dict) else {}
    raw = (
        str(payload.get("gws_config_dir") or "").strip()
        or str(os.getenv("GOOGLE_WORKSPACE_CLI_CONFIG_DIR") or "").strip()
        or str(DEFAULT_GWS_CONFIG_DIR)
    )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def _gws_env(credentials: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    env = dict(os.environ)
    env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = str(resolve_gws_config_dir(credentials))
    env.setdefault("GOOGLE_WORKSPACE_PROJECT_ID", DEFAULT_GWS_PROJECT_ID)
    # File backend keeps the runtime independent from a GUI keychain session.
    env.setdefault("GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND", "file")
    if not env.get("SSL_CERT_FILE"):
        for candidate in CA_BUNDLE_CANDIDATES:
            if candidate.exists():
                env["SSL_CERT_FILE"] = str(candidate)
                env.setdefault("REQUESTS_CA_BUNDLE", str(candidate))
                env.setdefault("CURL_CA_BUNDLE", str(candidate))
                break
    return env


def _extract_json_blob(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass

    starts = [idx for idx in (raw.find("{"), raw.find("[")) if idx >= 0]
    if not starts:
        raise RuntimeError(raw)
    start = min(starts)
    trimmed = raw[start:]
    for end in range(len(trimmed), 0, -1):
        snippet = trimmed[:end].strip()
        if not snippet:
            continue
        try:
            return json.loads(snippet)
        except Exception:
            continue
    raise RuntimeError(raw)


def _error_text(stdout: str, stderr: str) -> str:
    pieces = [part.strip() for part in (stderr, stdout) if str(part or "").strip()]
    joined = "\n".join(pieces).strip()
    if not joined:
        return "gws command failed."
    parsed = _extract_json_blob(joined)
    if isinstance(parsed, dict):
        error = parsed.get("error") if isinstance(parsed.get("error"), dict) else parsed
        message = str(error.get("message") or "").strip() if isinstance(error, dict) else ""
        if message:
            return message
    return joined


def run_gws(args: List[str], credentials: Optional[Dict[str, Any]] = None, *, timeout: int = 30) -> Any:
    command = ["gws", *args]
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        env=_gws_env(credentials),
        cwd=str(ROOT_DIR),
    )
    stdout = str(proc.stdout or "")
    stderr = str(proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(_error_text(stdout, stderr))
    parsed = _extract_json_blob(stdout)
    return parsed if parsed is not None else {}


def validate_google_workspace_local_connector(credentials: Dict[str, Any]) -> Dict[str, Any]:
    config_dir = resolve_gws_config_dir(credentials)
    client_secret = config_dir / "client_secret.json"
    if not client_secret.exists():
        raise RuntimeError(
            "Google Workspace CLI client config is missing. Finish local Google auth first or set gws_config_dir."
        )

    status = run_gws(["auth", "status"], credentials, timeout=20)
    if not isinstance(status, dict):
        raise RuntimeError("Google Workspace CLI auth status was invalid.")
    if str(status.get("auth_method") or "").strip().lower() == "none":
        raise RuntimeError("Google Workspace CLI is installed but not authenticated.")
    if status.get("token_valid") is False:
        raise RuntimeError("Google Workspace CLI token is not valid. Re-authenticate with `gws auth login`.")

    profile = run_gws(["gmail", "users", "getProfile", "--params", '{"userId":"me"}'], credentials, timeout=20)
    if not isinstance(profile, dict):
        raise RuntimeError("Google Workspace CLI profile lookup failed.")

    calendars_preview: List[Dict[str, Any]] = []
    calendar_access = False
    calendar_warning: Optional[str] = None
    try:
        calendars = run_gws(["calendar", "calendarList", "list"], credentials, timeout=20)
        if isinstance(calendars, dict):
            calendar_access = True
            for item in calendars.get("items", []) if isinstance(calendars.get("items"), list) else []:
                if not isinstance(item, dict):
                    continue
                calendars_preview.append(
                    {
                        "id": item.get("id"),
                        "summary": item.get("summary"),
                        "primary": bool(item.get("primary")),
                    }
                )
                if len(calendars_preview) >= 10:
                    break
    except Exception as exc:
        calendar_access = False
        detail = str(exc).strip()
        if len(detail) > 220:
            detail = detail[:220] + "..."
        calendar_warning = (
            "Calendar access is unavailable in local Google Workspace CLI auth."
            + (f" ({detail})" if detail else "")
        )

    files_access = False
    drive_warning: Optional[str] = None
    try:
        run_gws(
            [
                "drive",
                "files",
                "list",
                "--params",
                json.dumps(
                    {
                        "pageSize": 1,
                        "fields": "files(id,name,mimeType,webViewLink)",
                        "q": "trashed = false",
                    },
                    separators=(",", ":"),
                ),
            ],
            credentials,
            timeout=20,
        )
        files_access = True
    except Exception as exc:
        detail = str(exc).strip()
        if len(detail) > 220:
            detail = detail[:220] + "..."
        drive_warning = (
            "Drive access is unavailable in local Google Workspace CLI auth."
            + (f" ({detail})" if detail else "")
        )

    warnings = [warning for warning in (calendar_warning, drive_warning) if warning]

    return {
        "ok": True,
        "status": 200,
        "message": (
            "Google Workspace local connector is valid."
            if calendar_access and files_access
            else "Google Workspace local connector is valid, but some Google Workspace capabilities are unavailable."
        ),
        "profile": {
            "emailAddress": profile.get("emailAddress"),
            "messagesTotal": profile.get("messagesTotal"),
            "threadsTotal": profile.get("threadsTotal"),
        },
        "auth_mode": "gws_local",
        "config_dir": str(config_dir),
        "calendar_access": calendar_access,
        "files_access": files_access,
        "warning": " ".join(warnings) if warnings else None,
        "warnings": warnings,
        "calendars_preview": calendars_preview,
        "drive": {
            "driveType": "Google Drive",
            "webUrl": "https://drive.google.com/drive/my-drive",
        },
    }


def google_workspace_local_get_profile(credentials: Dict[str, Any]) -> Dict[str, Any]:
    payload = run_gws(["gmail", "users", "getProfile", "--params", '{"userId":"me"}'], credentials, timeout=20)
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Google Workspace CLI profile response was invalid.")


def _gmail_header_map(payload: Dict[str, Any]) -> Dict[str, str]:
    raw = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    headers = raw.get("headers") if isinstance(raw.get("headers"), list) else []
    out: Dict[str, str] = {}
    for item in headers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        if name and value and name not in out:
            out[name] = value
    return out


def _gmail_decode_body_data(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode((value + padding).encode("utf-8"))
    except Exception:
        return ""
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return decoded.decode("latin-1", errors="replace")


def _gmail_extract_body_from_payload_node(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    mime_type = str(node.get("mimeType") or "").strip().lower()
    body = node.get("body") if isinstance(node.get("body"), dict) else {}
    data = _gmail_decode_body_data(body.get("data"))
    if mime_type.startswith("text/plain") and data.strip():
        return data
    parts = node.get("parts") if isinstance(node.get("parts"), list) else []
    plain_parts: List[str] = []
    html_parts: List[str] = []
    other_parts: List[str] = []
    for item in parts:
        if not isinstance(item, dict):
            continue
        part_mime = str(item.get("mimeType") or "").strip().lower()
        extracted = _gmail_extract_body_from_payload_node(item)
        if not extracted.strip():
            continue
        if part_mime.startswith("text/plain"):
            plain_parts.append(extracted)
        elif part_mime.startswith("text/html"):
            html_parts.append(extracted)
        else:
            other_parts.append(extracted)
    if plain_parts:
        return "\n".join(part for part in plain_parts if part.strip())
    if html_parts:
        return "\n".join(_html_to_text(part) for part in html_parts if part.strip())
    if data.strip():
        if mime_type.startswith("text/html"):
            return _html_to_text(data)
        return data
    if other_parts:
        return "\n".join(part for part in other_parts if part.strip())
    return ""


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", value)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\\s+", " ", text)
    return text.strip()


def _gmail_message_summary(detail: Dict[str, Any], message_id: str) -> Dict[str, Any]:
    headers = _gmail_header_map(detail)
    raw_payload = detail.get("payload") if isinstance(detail.get("payload"), dict) else {}
    body_text = _gmail_extract_body_from_payload_node(raw_payload).strip()
    snippet = str(detail.get("snippet") or "").strip()
    preview = body_text or snippet
    preview = re.sub(r"\s+", " ", preview).strip()
    if len(preview) > 1200:
        preview = preview[:1197] + "..."
    return {
        "id": message_id,
        "threadId": detail.get("threadId"),
        "snippet": preview,
        "body_text": body_text,
        "subject": headers.get("subject", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
    }


def google_workspace_local_list_recent_messages(
    credentials: Dict[str, Any],
    *,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 3), 10))
    listing = run_gws(
        [
            "gmail",
            "users",
            "messages",
            "list",
            "--params",
            json.dumps(
                {
                    "userId": "me",
                    "maxResults": safe_limit,
                    "q": "in:inbox",
                },
                separators=(",", ":"),
            ),
        ],
        credentials,
        timeout=20,
    )
    messages = listing.get("messages") if isinstance(listing.get("messages"), list) else []
    results: List[Dict[str, Any]] = []
    for item in messages[:safe_limit]:
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("id") or "").strip()
        if not message_id:
            continue
        detail = run_gws(
            [
                "gmail",
                "users",
                "messages",
                "get",
                "--params",
                json.dumps(
                    {
                        "userId": "me",
                        "id": message_id,
                        "format": "full",
                    },
                    separators=(",", ":"),
                ),
            ],
            credentials,
            timeout=20,
        )
        if not isinstance(detail, dict):
            continue
        results.append(_gmail_message_summary(detail, message_id))
    return results


def google_workspace_local_create_draft(credentials: Dict[str, Any], to_email: str, subject: str, body_text: str) -> Dict[str, Any]:
    message = (
        f"To: {to_email}\r\n"
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        f"{body_text}\r\n"
    )
    raw_encoded = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8").rstrip("=")
    payload = run_gws(
        [
            "gmail",
            "users",
            "drafts",
            "create",
            "--params",
            '{"userId":"me"}',
            "--json",
            json.dumps({"message": {"raw": raw_encoded}}, separators=(",", ":")),
        ],
        credentials,
        timeout=30,
    )
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Google Workspace CLI Gmail draft response was invalid.")

def google_workspace_local_send_message(credentials: Dict[str, Any], to_email: str, subject: str, body_text: str) -> Dict[str, Any]:
    message = (
        f"To: {to_email}\r\n"
        f"Subject: {subject}\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        f"{body_text}\r\n"
    )
    raw_encoded = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8").rstrip("=")
    payload = run_gws(
        [
            "gmail",
            "users",
            "messages",
            "send",
            "--params",
            '{"userId":"me"}',
            "--json",
            json.dumps({"raw": raw_encoded}, separators=(",", ":")),
        ],
        credentials,
        timeout=30,
    )
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Google Workspace CLI Gmail send response was invalid.")


def google_workspace_local_create_calendar_event(
    credentials: Dict[str, Any],
    *,
    calendar_id: str,
    send_updates: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    result = run_gws(
        [
            "calendar",
            "events",
            "insert",
            "--params",
            json.dumps({"calendarId": calendar_id, "sendUpdates": send_updates}, separators=(",", ":")),
            "--json",
            json.dumps(payload, separators=(",", ":")),
        ],
        credentials,
        timeout=30,
    )
    if isinstance(result, dict):
        return result
    raise RuntimeError("Google Workspace CLI calendar insert response was invalid.")


def google_workspace_local_list_calendar_events(
    credentials: Dict[str, Any],
    *,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    top: int = 10,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = str(time_min or now.isoformat().replace("+00:00", "Z")).strip()
    end = str(time_max or (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")).strip()
    safe_top = max(1, min(int(top or 10), 20))
    payload = run_gws(
        [
            "calendar",
            "events",
            "list",
            "--params",
            json.dumps(
                {
                    "calendarId": calendar_id or "primary",
                    "timeMin": start,
                    "timeMax": end,
                    "maxResults": safe_top,
                    "singleEvents": True,
                    "orderBy": "startTime",
                },
                separators=(",", ":"),
            ),
        ],
        credentials,
        timeout=30,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("Google Workspace CLI calendar listing response was invalid.")
    events: List[Dict[str, Any]] = []
    for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        start_payload = item.get("start") if isinstance(item.get("start"), dict) else {}
        end_payload = item.get("end") if isinstance(item.get("end"), dict) else {}
        events.append(
            {
                "id": item.get("id"),
                "summary": item.get("summary"),
                "start": start_payload.get("dateTime") or start_payload.get("date"),
                "end": end_payload.get("dateTime") or end_payload.get("date"),
                "location": item.get("location"),
                "description": item.get("description"),
                "htmlLink": item.get("htmlLink"),
                "attendeeCount": len(item.get("attendees") or []) if isinstance(item.get("attendees"), list) else 0,
            }
        )
    return {
        "calendar_id": calendar_id or "primary",
        "time_min": start,
        "time_max": end,
        "events": events,
    }


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
    return [part for part in relative.split("/") if part]


def _google_drive_children_query(parent_id: str) -> str:
    return f"trashed = false and '{parent_id}' in parents"


def _google_drive_folder_query(parent_id: str, name: str) -> str:
    escaped_name = json.dumps(str(name or ""))
    return (
        f"trashed = false and mimeType = '{GOOGLE_DRIVE_FOLDER_MIME}' "
        f"and '{parent_id}' in parents and name = {escaped_name}"
    )


def _google_drive_list(credentials: Dict[str, Any], params: Dict[str, Any], *, timeout: int = 30) -> List[Dict[str, Any]]:
    payload = run_gws(
        [
            "drive",
            "files",
            "list",
            "--params",
            json.dumps(params, separators=(",", ":")),
        ],
        credentials,
        timeout=timeout,
    )
    if isinstance(payload, dict):
        items = payload.get("files")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    raise RuntimeError("Google Drive listing response was invalid.")


def google_workspace_local_list_drive_children(
    credentials: Dict[str, Any],
    *,
    path: str = "",
    top: int = 50,
) -> Dict[str, Any]:
    parts = _google_drive_path_parts(path)
    normalized_path = _normalize_google_drive_path(path)
    current_folder_id = "root"

    for segment in parts:
        folders = _google_drive_list(
            credentials,
            {
                "pageSize": 1,
                "fields": "files(id,name,mimeType)",
                "q": _google_drive_folder_query(current_folder_id, segment),
            },
            timeout=20,
        )
        if not folders:
            raise RuntimeError(f"Google Drive folder not found: {segment}")
        current_folder_id = str(folders[0].get("id") or "").strip() or current_folder_id

    items = _google_drive_list(
        credentials,
        {
            "pageSize": max(1, min(int(top or 50), 100)),
            "fields": "files(id,name,mimeType,webViewLink,size,modifiedTime)",
            "q": _google_drive_children_query(current_folder_id),
            "orderBy": "folder,name",
        },
        timeout=30,
    )
    prefix = normalized_path.rstrip("/")
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

    return {
        "path": normalized_path,
        "items": children,
    }


def google_workspace_local_create_document(credentials: Dict[str, Any], title: str) -> Dict[str, Any]:
    payload = run_gws(
        [
            "docs",
            "documents",
            "create",
            "--json",
            json.dumps({"title": title}, separators=(",", ":")),
        ],
        credentials,
        timeout=30,
    )
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Google Workspace CLI Docs create response was invalid.")


def google_workspace_local_create_spreadsheet(credentials: Dict[str, Any], title: str) -> Dict[str, Any]:
    payload = run_gws(
        [
            "sheets",
            "spreadsheets",
            "create",
            "--json",
            json.dumps({"properties": {"title": title}}, separators=(",", ":")),
        ],
        credentials,
        timeout=30,
    )
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Google Workspace CLI Sheets create response was invalid.")
