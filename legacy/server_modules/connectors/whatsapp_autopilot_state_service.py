from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class WhatsAppAutopilotStateService:
    def __init__(
        self,
        *,
        state: Dict[str, Any],
        lock: Any,
        read_json: Callable[[Any, Any], Dict[str, Any]],
        write_json: Callable[[Any, Dict[str, Any]], Any],
        state_file: Any,
        utc_now_iso: Callable[[], str],
        classify_error: Callable[[Any], str],
        normalize_workspace_id: Callable[[Any], str],
        resolve_workspace_scope: Callable[[Dict[str, Any], str | None, str], str],
        load_vault: Callable[[], Dict[str, Any]],
        workspace_visible: Callable[[Any, Optional[str]], bool],
        connector_paused: Callable[[Dict[str, Any]], bool],
        resolve_vault_credential: Callable[[str, Optional[str]], Dict[str, Any]],
        normalize_whatsapp_number: Callable[[Any], str],
        enabled: bool,
        default_profile: str,
        require_prefix: bool,
        prefix: str,
        run_timeout_seconds: int,
        max_reply_chars: int,
    ) -> None:
        self.state = state
        self.lock = lock
        self.read_json = read_json
        self.write_json = write_json
        self.state_file = state_file
        self.utc_now_iso = utc_now_iso
        self.classify_error = classify_error
        self.normalize_workspace_id = normalize_workspace_id
        self.resolve_workspace_scope = resolve_workspace_scope
        self.load_vault = load_vault
        self.workspace_visible = workspace_visible
        self.connector_paused = connector_paused
        self.resolve_vault_credential = resolve_vault_credential
        self.normalize_whatsapp_number = normalize_whatsapp_number
        self.enabled = bool(enabled)
        self.default_profile = str(default_profile or "")
        self.require_prefix = bool(require_prefix)
        self.prefix = str(prefix or "")
        self.run_timeout_seconds = int(run_timeout_seconds or 0)
        self.max_reply_chars = int(max_reply_chars or 0)

    def load_state(self) -> None:
        payload = self.read_json(
            self.state_file,
            {
                "version": 1,
                "state": {
                    "connectors": {},
                    "processed_message_ids": {},
                    "processed_messages": 0,
                    "runs_started": 0,
                    "last_inbound_at": None,
                    "last_error": None,
                    "last_error_at": None,
                    "last_error_category": None,
                    "last_error_source": None,
                    "error_count": 0,
                    "consecutive_errors": 0,
                },
            },
        )
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        connectors = state.get("connectors") if isinstance(state.get("connectors"), dict) else {}
        with self.lock:
            self.state["connectors"] = connectors
            processed_message_ids = state.get("processed_message_ids") if isinstance(state.get("processed_message_ids"), dict) else {}
            self.state["processed_message_ids"] = processed_message_ids
            self.state["processed_messages"] = int(state.get("processed_messages") or 0)
            self.state["runs_started"] = int(state.get("runs_started") or 0)
            self.state["last_inbound_at"] = state.get("last_inbound_at")
            self.state["last_error"] = state.get("last_error")
            self.state["last_error_at"] = state.get("last_error_at")
            self.state["last_error_category"] = state.get("last_error_category")
            self.state["last_error_source"] = state.get("last_error_source")
            self.state["error_count"] = int(state.get("error_count") or 0)
            self.state["consecutive_errors"] = int(state.get("consecutive_errors") or 0)

    def persist_state(self) -> None:
        with self.lock:
            payload = {
                "version": 1,
                "state": {
                    "connectors": self.state.get("connectors", {}),
                    "processed_message_ids": self.state.get("processed_message_ids", {}),
                    "processed_messages": int(self.state.get("processed_messages") or 0),
                    "runs_started": int(self.state.get("runs_started") or 0),
                    "last_inbound_at": self.state.get("last_inbound_at"),
                    "last_error": self.state.get("last_error"),
                    "last_error_at": self.state.get("last_error_at"),
                    "last_error_category": self.state.get("last_error_category"),
                    "last_error_source": self.state.get("last_error_source"),
                    "error_count": int(self.state.get("error_count") or 0),
                    "consecutive_errors": int(self.state.get("consecutive_errors") or 0),
                },
            }
        self.write_json(self.state_file, payload)

    def mark_error(self, detail: str, source: str = "webhook") -> None:
        now = self.utc_now_iso()
        category = self.classify_error(detail)
        with self.lock:
            self.state["last_error"] = detail
            self.state["last_error_at"] = now
            self.state["last_error_category"] = category
            self.state["last_error_source"] = str(source or "webhook")
            self.state["error_count"] = int(self.state.get("error_count") or 0) + 1
            self.state["consecutive_errors"] = int(self.state.get("consecutive_errors") or 0) + 1
            self.state["last_inbound_at"] = now
        self.persist_state()

    def activate(self) -> None:
        if not self.enabled:
            return
        with self.lock:
            self.state["enabled"] = True
            self.state["active"] = True
            if not self.state.get("started_at"):
                self.state["started_at"] = self.utc_now_iso()
        self.persist_state()

    def mark_inbound(self, clear_error: bool = True) -> None:
        with self.lock:
            self.state["last_inbound_at"] = self.utc_now_iso()
            if clear_error:
                self.state["last_error"] = None
                self.state["last_error_at"] = None
                self.state["last_error_category"] = None
                self.state["last_error_source"] = None
                self.state["consecutive_errors"] = 0
        self.persist_state()

    def increment_processed(self) -> None:
        with self.lock:
            self.state["processed_messages"] = int(self.state.get("processed_messages") or 0) + 1

    def mark_processed_message(self, connector_id: str, message_sid: str) -> bool:
        clean_connector_id = str(connector_id or "").strip()
        clean_message_sid = str(message_sid or "").strip()
        if not clean_connector_id or not clean_message_sid:
            return True
        with self.lock:
            processed = self.state.setdefault("processed_message_ids", {})
            bucket = processed.get(clean_connector_id)
            if not isinstance(bucket, dict):
                bucket = {}
            if clean_message_sid in bucket:
                return False
            bucket[clean_message_sid] = self.utc_now_iso()
            while len(bucket) > 512:
                oldest_key = next(iter(bucket.keys()))
                bucket.pop(oldest_key, None)
            processed[clean_connector_id] = bucket
        self.persist_state()
        return True

    def connector_state(self, credential_id: str) -> Dict[str, Any]:
        with self.lock:
            connectors = self.state.setdefault("connectors", {})
            raw = connectors.get(credential_id)
            if not isinstance(raw, dict):
                raw = {}
            connectors[credential_id] = raw
            return raw

    def set_connector_state(self, credential_id: str, patch: Dict[str, Any]) -> None:
        with self.lock:
            connectors = self.state.setdefault("connectors", {})
            current = connectors.get(credential_id)
            if not isinstance(current, dict):
                current = {}
            for key, value in patch.items():
                current[key] = value
            connectors[credential_id] = current
        self.persist_state()

    def connector_match(
        self,
        account_sid: str,
        from_number: str,
        to_number: str,
        requested_workspace_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        inbound_account = str(account_sid or "").strip()
        inbound_from = self.normalize_whatsapp_number(from_number)
        inbound_to = self.normalize_whatsapp_number(to_number)
        entries = self.list_connector_entries(requested_workspace_id)
        with self.lock:
            self.state["connectors_seen"] = len(entries)
        for entry in entries:
            credential_id = str(entry.get("id") or "").strip()
            try:
                workspace_id = self.resolve_workspace_scope(entry, None, "WhatsApp connector")
            except Exception:
                continue
            if not credential_id:
                continue
            try:
                secret = self.resolve_vault_credential(credential_id, workspace_id)
            except Exception:
                continue
            connector_sid = str(secret.get("account_sid") or "").strip()
            if inbound_account and connector_sid and inbound_account != connector_sid:
                continue
            connector_to = self.normalize_whatsapp_number(secret.get("from_number"))
            if connector_to and inbound_to and connector_to != inbound_to:
                continue
            connector_from = self.normalize_whatsapp_number(secret.get("to_number"))
            if (
                connector_from
                and connector_from not in {"*", "whatsapp:*"}
                and inbound_from
                and connector_from != inbound_from
            ):
                continue
            return {
                "entry": entry,
                "secret": secret,
                "connector_id": credential_id,
                "workspace_id": workspace_id,
            }
        return None

    def get_connector_entry(self, connector_id: str) -> Dict[str, Any]:
        connector_token = str(connector_id or "").strip()
        if not connector_token:
            raise LookupError("WhatsApp connector id is required.")
        for item in self.load_vault().get("credentials", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("provider") or "").strip().lower() != "whatsapp_twilio":
                continue
            if str(item.get("id") or "").strip() != connector_token:
                continue
            if self.connector_paused(item):
                raise LookupError(f"WhatsApp connector '{connector_token}' is paused.")
            workspace_id = self.resolve_workspace_scope(item, None, "WhatsApp connector")
            try:
                self.resolve_vault_credential(connector_token, workspace_id)
            except Exception as exc:
                raise RuntimeError(f"WhatsApp connector '{connector_token}' is not usable: {exc}") from exc
            return item
        raise LookupError(f"WhatsApp connector '{connector_token}' is not configured.")

    def list_connector_entries(self, requested_workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        requested_ws = self.normalize_workspace_id(requested_workspace_id)
        entries: List[Dict[str, Any]] = []
        seen_identities: set[str] = set()
        for item in self.load_vault().get("credentials", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("provider") or "").strip().lower() != "whatsapp_twilio":
                continue
            if not self.workspace_visible(item.get("workspace_id"), requested_ws):
                continue
            if self.connector_paused(item):
                continue
            credential_id = str(item.get("id") or "").strip()
            try:
                workspace_id = self.resolve_workspace_scope(item, None, "WhatsApp connector")
            except Exception:
                continue
            if not credential_id:
                continue
            try:
                secret = self.resolve_vault_credential(credential_id, workspace_id)
            except Exception:
                continue
            account_sid = str(secret.get("account_sid") or "").strip()
            from_number = self.normalize_whatsapp_number(secret.get("from_number"))
            to_number = self.normalize_whatsapp_number(secret.get("to_number"))
            identity = f"{account_sid}:{from_number}:{to_number}" if account_sid and from_number and to_number else ""
            if identity:
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
            entries.append(item)
        entries.sort(key=lambda item: str(item.get("label") or "").lower())
        return entries

    def snapshot(
        self,
        *,
        include_connectors: bool = False,
        requested_workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            connectors_raw = self.state.get("connectors", {})
            connectors = dict(connectors_raw) if isinstance(connectors_raw, dict) else {}
            snapshot: Dict[str, Any] = {
                "enabled": self.enabled,
                "active": bool(self.state.get("active")),
                "started_at": self.state.get("started_at"),
                "last_inbound_at": self.state.get("last_inbound_at"),
                "last_error": self.state.get("last_error"),
                "last_error_at": self.state.get("last_error_at"),
                "last_error_category": self.state.get("last_error_category"),
                "last_error_source": self.state.get("last_error_source"),
                "error_count": int(self.state.get("error_count") or 0),
                "consecutive_errors": int(self.state.get("consecutive_errors") or 0),
                "processed_messages": int(self.state.get("processed_messages") or 0),
                "runs_started": int(self.state.get("runs_started") or 0),
                "run_timeout_seconds": self.run_timeout_seconds,
                "max_reply_chars": self.max_reply_chars,
                "require_prefix": self.require_prefix,
                "prefix": self.prefix,
                "default_profile": self.default_profile,
                "state_file": str(self.state_file),
                "thread_alive": bool(self.enabled and self.state.get("active")),
            }
        connector_error_count = 0
        for connector_state in connectors.values():
            if isinstance(connector_state, dict) and connector_state.get("last_error"):
                connector_error_count += 1
        snapshot["connector_state_count"] = len(connectors)
        snapshot["connector_error_count"] = connector_error_count
        try:
            snapshot["connectors_seen"] = len(self.list_connector_entries(requested_workspace_id))
        except Exception as exc:
            snapshot["connectors_seen"] = 0
            if not snapshot.get("last_error"):
                snapshot["last_error"] = str(exc)
                snapshot["last_error_category"] = self.classify_error(exc)
                snapshot["last_error_source"] = "list_connectors"
        with self.lock:
            self.state["connectors_seen"] = int(snapshot.get("connectors_seen") or 0)
        if include_connectors:
            snapshot["connectors"] = connectors
        return snapshot
