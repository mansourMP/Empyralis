from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from server_modules import rust_runtime_kernel_client

try:
    import dropbox as _dropbox_sdk
except Exception:  # pragma: no cover - optional dependency
    _dropbox_sdk = None


DropboxClientFactory = Callable[[str], Any]


def _access_token(credentials: Dict[str, Any]) -> str:
    token = str(
        credentials.get("access_token")
        or credentials.get("oauth_access_token")
        or credentials.get("token")
        or ""
    ).strip()
    if not token:
        raise RuntimeError("Dropbox access token is required.")
    return token


def _sdk(dropbox_module: Any = None) -> Any:
    module = dropbox_module or _dropbox_sdk
    if module is None:
        raise RuntimeError("Dropbox SDK is not installed. Add the 'dropbox' package to use this connector.")
    return module


def _client(
    credentials: Dict[str, Any],
    *,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> Any:
    if client is not None:
        return client
    token = _access_token(credentials)
    if client_factory is not None:
        return client_factory(token)
    return _sdk(dropbox_module).Dropbox(token)


def _read_attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _entry_kind(entry: Any) -> str:
    tag = _read_attr(entry, ".tag")
    if isinstance(tag, str) and tag.strip():
        return tag.strip()
    cls_name = entry.__class__.__name__.lower()
    if "folder" in cls_name:
        return "folder"
    if "deleted" in cls_name:
        return "deleted"
    return "file"


def _entry_payload(entry: Any) -> Dict[str, Any]:
    return {
        "id": _read_attr(entry, "id"),
        "name": _read_attr(entry, "name"),
        "path_display": _read_attr(entry, "path_display"),
        "path_lower": _read_attr(entry, "path_lower"),
        "kind": _entry_kind(entry),
        "client_modified": _read_attr(entry, "client_modified"),
        "server_modified": _read_attr(entry, "server_modified"),
        "size": _read_attr(entry, "size"),
    }


def _metadata_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {
        "id": _read_attr(value, "id"),
        "name": _read_attr(value, "name"),
        "path_display": _read_attr(value, "path_display"),
        "path_lower": _read_attr(value, "path_lower"),
    }


class DropboxConnectorRustGateError(RuntimeError):
    pass


def _enforce_dropbox_download_write(*, dropbox_path: str, local_path: Path, content_bytes: int) -> None:
    payload = {
        "dropbox_path": str(dropbox_path or "").strip(),
        "local_path": str(local_path),
        "content_bytes": max(0, int(content_bytes or 0)),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_dropbox_download_file",
            state_class="dropbox_download_files",
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=int(payload["content_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_dropbox_download_file":
            raise DropboxConnectorRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise DropboxConnectorRustGateError(exc.reason) from exc


def validate_dropbox_credentials(
    credentials: Dict[str, Any],
    *,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(
        credentials,
        client=client,
        client_factory=client_factory,
        dropbox_module=dropbox_module,
    )
    account = sdk_client.users_get_current_account()
    name = _read_attr(account, "name")
    display_name = str(_read_attr(name, "display_name") or _read_attr(account, "display_name") or "").strip()
    email = str(_read_attr(account, "email") or "").strip()
    account_id = str(_read_attr(account, "account_id") or "").strip()
    normalized = dict(credentials)
    normalized["auth_mode"] = "oauth"
    if display_name:
        normalized["display_name"] = display_name
    if email:
        normalized["email"] = email
    if account_id:
        normalized["account_id"] = account_id
    return {
        "ok": True,
        "status": 200,
        "message": "Dropbox connector is valid.",
        "auth_mode": "oauth",
        "display_name": display_name or None,
        "email": email or None,
        "account_id": account_id or None,
        "profile": {
            "displayName": display_name or None,
            "email": email or None,
            "accountId": account_id or None,
        },
        "credentials": normalized,
    }


def list_folder(
    credentials: Dict[str, Any],
    path: str = "/",
    *,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> List[Dict[str, Any]]:
    sdk_client = _client(
        credentials,
        client=client,
        client_factory=client_factory,
        dropbox_module=dropbox_module,
    )
    normalized_path = str(path or "").strip()
    listing = sdk_client.files_list_folder(normalized_path or "")
    entries = list(_read_attr(listing, "entries") or [])
    has_more = bool(_read_attr(listing, "has_more"))
    cursor = _read_attr(listing, "cursor")
    while has_more and cursor:
        listing = sdk_client.files_list_folder_continue(cursor)
        entries.extend(list(_read_attr(listing, "entries") or []))
        has_more = bool(_read_attr(listing, "has_more"))
        cursor = _read_attr(listing, "cursor")
    return [_entry_payload(item) for item in entries]


def upload_file(
    credentials: Dict[str, Any],
    local_path: str,
    dropbox_path: str,
    *,
    overwrite: bool = True,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> Dict[str, Any]:
    path = Path(str(local_path or "")).expanduser()
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"Local file does not exist: {path}")
    sdk_client = _client(
        credentials,
        client=client,
        client_factory=client_factory,
        dropbox_module=dropbox_module,
    )
    mode = None
    if client is None and client_factory is None:
        module = _sdk(dropbox_module)
        mode = module.files.WriteMode.overwrite if overwrite else module.files.WriteMode.add
    result = sdk_client.files_upload(
        path.read_bytes(),
        str(dropbox_path or "").strip(),
        mode=mode,
        mute=True,
    )
    payload = _metadata_payload(result)
    payload.update(
        {
            "ok": True,
            "local_path": str(path),
            "dropbox_path": str(dropbox_path or "").strip(),
            "overwrite": bool(overwrite),
        }
    )
    return payload


def download_file(
    credentials: Dict[str, Any],
    dropbox_path: str,
    local_path: str,
    *,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(
        credentials,
        client=client,
        client_factory=client_factory,
        dropbox_module=dropbox_module,
    )
    metadata, response = sdk_client.files_download(str(dropbox_path or "").strip())
    target = Path(str(local_path or "")).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = getattr(response, "content", None)
    if content is None and hasattr(response, "read"):
        content = response.read()
    if content is None:
        raise RuntimeError("Dropbox download response did not contain file content.")
    _enforce_dropbox_download_write(
        dropbox_path=str(dropbox_path or "").strip(),
        local_path=target,
        content_bytes=len(bytes(content)),
    )
    target.write_bytes(bytes(content))
    payload = _metadata_payload(metadata)
    payload.update(
        {
            "ok": True,
            "dropbox_path": str(dropbox_path or "").strip(),
            "local_path": str(target),
        }
    )
    return payload


def delete(
    credentials: Dict[str, Any],
    path: str,
    *,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(
        credentials,
        client=client,
        client_factory=client_factory,
        dropbox_module=dropbox_module,
    )
    result = sdk_client.files_delete_v2(str(path or "").strip())
    metadata = _read_attr(result, "metadata", result)
    payload = _metadata_payload(metadata)
    payload.update({"ok": True, "path": str(path or "").strip()})
    return payload


def move(
    credentials: Dict[str, Any],
    from_path: str,
    to_path: str,
    *,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(
        credentials,
        client=client,
        client_factory=client_factory,
        dropbox_module=dropbox_module,
    )
    result = sdk_client.files_move_v2(str(from_path or "").strip(), str(to_path or "").strip(), autorename=False)
    metadata = _read_attr(result, "metadata", result)
    payload = _metadata_payload(metadata)
    payload.update(
        {
            "ok": True,
            "from_path": str(from_path or "").strip(),
            "to_path": str(to_path or "").strip(),
        }
    )
    return payload


def get_shared_link(
    credentials: Dict[str, Any],
    path: str,
    *,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(
        credentials,
        client=client,
        client_factory=client_factory,
        dropbox_module=dropbox_module,
    )
    normalized_path = str(path or "").strip()
    try:
        existing = sdk_client.sharing_list_shared_links(path=normalized_path, direct_only=True)
        links = list(_read_attr(existing, "links") or [])
        if links:
            first = links[0]
            return {
                "ok": True,
                "path": normalized_path,
                "url": _read_attr(first, "url"),
                "id": _read_attr(first, "id"),
            }
    except Exception:
        pass
    created = sdk_client.sharing_create_shared_link_with_settings(normalized_path)
    return {
        "ok": True,
        "path": normalized_path,
        "url": _read_attr(created, "url"),
        "id": _read_attr(created, "id"),
    }


def search(
    credentials: Dict[str, Any],
    query: str,
    *,
    client: Any = None,
    client_factory: Optional[DropboxClientFactory] = None,
    dropbox_module: Any = None,
) -> List[Dict[str, Any]]:
    sdk_client = _client(
        credentials,
        client=client,
        client_factory=client_factory,
        dropbox_module=dropbox_module,
    )
    result = sdk_client.files_search_v2(str(query or "").strip())
    matches = list(_read_attr(result, "matches") or [])
    out: List[Dict[str, Any]] = []
    for item in matches:
        metadata_container = _read_attr(item, "metadata", item)
        metadata = _read_attr(metadata_container, "metadata", metadata_container)
        payload = _entry_payload(metadata)
        payload["match_type"] = _read_attr(item, "match_type")
        out.append(payload)
    return out
