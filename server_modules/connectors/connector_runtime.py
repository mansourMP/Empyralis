"""
Shared connector runtime: state management, runtime metrics, polling loop,
and supervision — consolidated from the per-channel autopilot service files.

Replaces: telegram_autopilot_state_service, telegram_autopilot_runtime_service,
telegram_autopilot_loop_service, telegram_autopilot_supervisor_service,
telegram_poll_state_service, telegram_poll_cycle_service.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class ConnectorRuntime:
    """Consolidated state + runtime + poll + supervisor for one channel."""

    def __init__(
        self,
        *,
        provider: str,
        state: Dict[str, Any],
        lock: Any,
        read_json: Callable[[Any, Any], Dict[str, Any]],
        write_json: Callable[[Any, Dict[str, Any]], Any],
        state_file: Any,
        utc_now_iso: Callable[[], str],
        classify_error: Callable[[Any], str],
        iso_from_epoch: Callable[[float], str],
        normalize_workspace_id: Callable[[Any], str],
        load_vault: Callable[[], Dict[str, Any]],
        workspace_visible: Callable[[Any, Optional[str]], bool],
        connector_paused: Callable[[Dict[str, Any]], bool],
        resolve_secret: Callable[[Dict[str, Any]], Dict[str, Any]],
        enabled: bool,
        default_profile: str,
        require_prefix: bool,
        prefix: str,
        delivery_mode: str,
        poll_seconds: float,
        max_updates: int,
        run_timeout_seconds: int,
        max_reply_chars: int,
        thread_alive: Callable[[], bool],
        list_connector_entries: Callable[[], List[Dict[str, Any]]],
        get_updates_process_lock: Callable[[str], Any],
        notify_pending_approvals: Callable[..., Dict[str, Any]],
        telegram_api_request: Callable[..., Dict[str, Any]],
        record_channel_event_throttled: Callable[..., Any],
        autopilot_log: Callable[[str], Any],
        mark_started: Callable[[str], Any],
        sleep: Callable[[float], Any],
        poll_connector: Callable[[Dict[str, Any]], Any],
    ) -> None:
        self.provider = str(provider or "").strip().lower() or "telegram"
        self.state = state
        self.lock = lock
        self.read_json = read_json
        self.write_json = write_json
        self.state_file = state_file
        self.utc_now_iso = utc_now_iso
        self.classify_error = classify_error
        self.iso_from_epoch = iso_from_epoch
        self.normalize_workspace_id = normalize_workspace_id
        self.load_vault = load_vault
        self.workspace_visible = workspace_visible
        self.connector_paused = connector_paused
        self.resolve_secret = resolve_secret
        self.enabled = bool(enabled)
        self.default_profile = str(default_profile or "")
        self.require_prefix = bool(require_prefix)
        self.prefix = str(prefix or "")
        self.delivery_mode = str(delivery_mode or "").strip().lower() or "polling"
        self.poll_seconds = max(1.0, float(poll_seconds or 1.0))
        self.max_updates = max(1, min(int(max_updates or 1), 100))
        self.run_timeout_seconds = int(run_timeout_seconds or 0)
        self.max_reply_chars = int(max_reply_chars or 0)
        self.thread_alive = thread_alive
        self._list_connector_entries = list_connector_entries
        self.get_updates_process_lock = get_updates_process_lock
        self.notify_pending_approvals = notify_pending_approvals
        self.telegram_api_request = telegram_api_request
        self.record_channel_event_throttled = record_channel_event_throttled
        self.autopilot_log = autopilot_log
        self.mark_started = mark_started
        self.sleep = sleep
        self.poll_connector_fn = poll_connector

    # ------------------------------------------------------------------
    # State management (was TelegramAutopilotStateService)
    # ------------------------------------------------------------------

    def load_state(self) -> None:
        payload = self.read_json(
            self.state_file,
            {
                "version": 1,
                "state": {
                    "connectors": {},
                    "processed_updates": 0,
                    "runs_started": 0,
                    "last_poll_at": None,
                    "last_error": None,
                    "last_error_at": None,
                    "last_error_category": None,
                    "last_error_source": None,
                    "error_count": 0,
                    "consecutive_errors": 0,
                    "retry_count": 0,
                    "last_retry_at": None,
                    "backoff_seconds": 0.0,
                    "next_retry_at": None,
                    "last_success_at": None,
                },
            },
        )
        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
        connectors = state.get("connectors") if isinstance(state.get("connectors"), dict) else {}
        reconciled_connectors = dict(connectors)
        changed = False
        for item in self.load_vault().get("credentials", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("provider") or "").strip().lower() != f"{self.provider}_bot":
                continue
            connector_id = str(item.get("id") or "").strip()
            if not connector_id:
                continue
            explicit_workspace_id = str(self.normalize_workspace_id(item.get("workspace_id")) or "").strip()
            if not explicit_workspace_id or explicit_workspace_id == "default":
                continue
            current_state = reconciled_connectors.get(connector_id)
            state_patch = dict(current_state) if isinstance(current_state, dict) else {}
            current_workspace_id = str(self.normalize_workspace_id(state_patch.get("workspace_id")) or "").strip()
            if current_workspace_id != explicit_workspace_id:
                state_patch["workspace_id"] = explicit_workspace_id
                if "not scoped to an explicit workspace" in str(state_patch.get("last_error") or ""):
                    state_patch["last_error"] = None
                    state_patch["last_error_category"] = None
                    state_patch["last_error_at"] = None
                changed = True
            if not str(state_patch.get("label") or "").strip():
                state_patch["label"] = str(item.get("label") or connector_id).strip() or connector_id
                changed = True
            reconciled_connectors[connector_id] = state_patch
        if changed:
            state["connectors"] = reconciled_connectors
            payload["state"] = state
            self.write_json(self.state_file, payload)
        connectors = reconciled_connectors
        with self.lock:
            self.state["connectors"] = connectors
            self.state["processed_updates"] = int(state.get("processed_updates") or 0)
            self.state["runs_started"] = int(state.get("runs_started") or 0)
            self.state["last_poll_at"] = state.get("last_poll_at")
            self.state["last_error"] = state.get("last_error")
            self.state["last_error_at"] = state.get("last_error_at")
            self.state["last_error_category"] = state.get("last_error_category")
            self.state["last_error_source"] = state.get("last_error_source")
            self.state["error_count"] = int(state.get("error_count") or 0)
            self.state["consecutive_errors"] = int(state.get("consecutive_errors") or 0)
            self.state["retry_count"] = int(state.get("retry_count") or 0)
            self.state["last_retry_at"] = state.get("last_retry_at")
            self.state["backoff_seconds"] = float(state.get("backoff_seconds") or 0.0)
            self.state["next_retry_at"] = state.get("next_retry_at")
            self.state["last_success_at"] = state.get("last_success_at")

    def persist_state(self) -> None:
        with self.lock:
            payload = {
                "version": 1,
                "state": {
                    "connectors": self.state.get("connectors", {}),
                    "processed_updates": int(self.state.get("processed_updates") or 0),
                    "runs_started": int(self.state.get("runs_started") or 0),
                    "last_poll_at": self.state.get("last_poll_at"),
                    "last_error": self.state.get("last_error"),
                    "last_error_at": self.state.get("last_error_at"),
                    "last_error_category": self.state.get("last_error_category"),
                    "last_error_source": self.state.get("last_error_source"),
                    "error_count": int(self.state.get("error_count") or 0),
                    "consecutive_errors": int(self.state.get("consecutive_errors") or 0),
                    "retry_count": int(self.state.get("retry_count") or 0),
                    "last_retry_at": self.state.get("last_retry_at"),
                    "backoff_seconds": float(self.state.get("backoff_seconds") or 0.0),
                    "next_retry_at": self.state.get("next_retry_at"),
                    "last_success_at": self.state.get("last_success_at"),
                },
            }
        self.write_json(self.state_file, payload)

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

    def list_connector_entries(self, requested_workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        """List connector entries from vault filtered by provider and workspace."""
        return self._list_connector_entries_scoped(requested_workspace_id)

    def _list_connector_entries_scoped(self, requested_workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        requested_ws = self.normalize_workspace_id(requested_workspace_id)
        entries: List[Dict[str, Any]] = []
        seen_identities: set[str] = set()
        for item in self.load_vault().get("credentials", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("provider") or "").strip().lower() != f"{self.provider}_bot":
                continue
            entry_ws = str(self.normalize_workspace_id(item.get("workspace_id")) or "").strip()
            if not entry_ws or entry_ws == "default":
                continue
            if not self.workspace_visible(item.get("workspace_id"), requested_ws):
                continue
            if self.connector_paused(item):
                continue
            try:
                secret = self.resolve_secret(item)
            except Exception:
                continue
            bot_token = str(secret.get("bot_token") or "").strip()
            chat_id = str(secret.get("chat_id") or "").strip()
            identity = f"{bot_token}:{chat_id}" if bot_token and chat_id else ""
            if identity:
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
            entries.append(item)
        entries.sort(key=lambda item: str(item.get("label") or "").lower())
        return entries

    def get_connector_entry(self, connector_id: str) -> Dict[str, Any]:
        connector_token = str(connector_id or "").strip()
        if not connector_token:
            raise LookupError(f"{self.provider.title()} connector id is required.")
        for item in self.load_vault().get("credentials", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("provider") or "").strip().lower() != f"{self.provider}_bot":
                continue
            if str(item.get("id") or "").strip() != connector_token:
                continue
            if self.connector_paused(item):
                raise LookupError(f"{self.provider.title()} connector '{connector_token}' is paused.")
            try:
                self.resolve_secret(item)
            except Exception as exc:
                raise RuntimeError(f"{self.provider.title()} connector '{connector_token}' is not usable: {exc}") from exc
            return item
        raise LookupError(f"{self.provider.title()} connector '{connector_token}' is not configured.")

    def snapshot(self, *, include_connectors: bool = False) -> Dict[str, Any]:
        with self.lock:
            connectors_raw = self.state.get("connectors", {})
            connectors = dict(connectors_raw) if isinstance(connectors_raw, dict) else {}
            snapshot: Dict[str, Any] = {
                "enabled": self.enabled,
                "active": bool(self.state.get("active")),
                "started_at": self.state.get("started_at"),
                "last_poll_at": self.state.get("last_poll_at"),
                "last_error": self.state.get("last_error"),
                "last_error_at": self.state.get("last_error_at"),
                "last_error_category": self.state.get("last_error_category"),
                "last_error_source": self.state.get("last_error_source"),
                "error_count": int(self.state.get("error_count") or 0),
                "consecutive_errors": int(self.state.get("consecutive_errors") or 0),
                "retry_count": int(self.state.get("retry_count") or 0),
                "last_retry_at": self.state.get("last_retry_at"),
                "backoff_seconds": float(self.state.get("backoff_seconds") or 0.0),
                "next_retry_at": self.state.get("next_retry_at"),
                "last_success_at": self.state.get("last_success_at"),
                "connectors_seen": int(self.state.get("connectors_seen") or 0),
                "processed_updates": int(self.state.get("processed_updates") or 0),
                "runs_started": int(self.state.get("runs_started") or 0),
                "poll_seconds": self.poll_seconds,
                "max_updates": self.max_updates,
                "run_timeout_seconds": self.run_timeout_seconds,
                "max_reply_chars": self.max_reply_chars,
                "require_prefix": self.require_prefix,
                "prefix": self.prefix,
                "default_profile": self.default_profile,
                "delivery_mode": self.delivery_mode,
                "state_file": str(self.state_file),
                "thread_alive": bool(self.thread_alive()),
            }
        connector_error_count = 0
        dropped_sender_count = 0
        for connector_state in connectors.values():
            if isinstance(connector_state, dict):
                if connector_state.get("last_error"):
                    connector_error_count += 1
                dropped_sender_count += int(connector_state.get("dropped_sender_count") or 0)
        snapshot["connector_state_count"] = len(connectors)
        snapshot["connector_error_count"] = connector_error_count
        snapshot["dropped_sender_count"] = dropped_sender_count
        if include_connectors:
            snapshot["connectors"] = connectors
        return snapshot

    # ------------------------------------------------------------------
    # Runtime metrics (was TelegramAutopilotRuntimeService)
    # ------------------------------------------------------------------

    def increment_processed_updates(self) -> None:
        with self.lock:
            self.state["processed_updates"] = int(self.state.get("processed_updates") or 0) + 1

    def set_connectors_seen(self, count: int) -> None:
        with self.lock:
            self.state["connectors_seen"] = max(0, int(count or 0))

    def mark_error(self, detail: str, source: str = "loop") -> float:
        now_ts = time.time()
        now_iso = self.utc_now_iso()
        category = self.classify_error(detail)
        source_name = str(source or "loop")
        backoff_seconds = 0.0
        with self.lock:
            self.state["last_error"] = detail
            self.state["last_poll_at"] = now_iso
            self.state["last_error_at"] = now_iso
            self.state["last_error_category"] = category
            self.state["last_error_source"] = source_name
            self.state["error_count"] = int(self.state.get("error_count") or 0) + 1
            self.state["consecutive_errors"] = int(self.state.get("consecutive_errors") or 0) + 1
            if source_name == "loop":
                self.state["retry_count"] = int(self.state.get("retry_count") or 0) + 1
                self.state["last_retry_at"] = now_iso
                consecutive = int(self.state.get("consecutive_errors") or 0)
                backoff_seconds = min(60.0, self.poll_seconds * (1.6 ** min(consecutive, 8)))
                self.state["backoff_seconds"] = round(backoff_seconds, 3)
                self.state["next_retry_at"] = self.iso_from_epoch(now_ts + backoff_seconds)
        self.persist_state()
        return backoff_seconds

    def mark_poll(self, clear_error: bool = True) -> None:
        now = self.utc_now_iso()
        with self.lock:
            self.state["last_poll_at"] = now
            if clear_error:
                self.state["last_error"] = None
                self.state["last_error_at"] = None
                self.state["last_error_category"] = None
                self.state["last_error_source"] = None
                self.state["consecutive_errors"] = 0
                self.state["backoff_seconds"] = 0.0
                self.state["next_retry_at"] = None
                self.state["last_success_at"] = now

    # ------------------------------------------------------------------
    # Poll state recording (was TelegramPollStateService)
    # ------------------------------------------------------------------

    def record_processed_update(
        self,
        *,
        connector_id: str,
        label: str,
        workspace_id: str,
        update_id: int,
        chat_id: str,
        action: str,
        profile_id: str,
        allow_from: List[str],
        run_id: str = "",
        approval_state_patch: Dict[str, Any] | None = None,
    ) -> None:
        state_patch: Dict[str, Any] = {
            "label": label,
            "workspace_id": workspace_id,
            "last_update_id": update_id,
            "last_processed_at": self.utc_now_iso(),
            "last_error": None,
            "last_error_category": None,
            "last_error_at": None,
            "last_chat_id": chat_id,
            "last_action": action,
            "profile_id": profile_id,
            "allow_from": list(allow_from),
        }
        if run_id:
            state_patch["last_run_id"] = run_id
        if isinstance(approval_state_patch, dict) and approval_state_patch:
            state_patch.update(approval_state_patch)
        self.set_connector_state(connector_id, state_patch)
        self.increment_processed_updates()

    def record_poll_completion(
        self,
        *,
        connector_id: str,
        label: str,
        workspace_id: str,
        max_seen: int,
        profile_id: str,
        allow_from: List[str],
        approval_state_patch: Dict[str, Any] | None = None,
    ) -> None:
        patch: Dict[str, Any] = {
            "label": label,
            "workspace_id": workspace_id,
            "last_update_id": max_seen,
            "last_poll_at": self.utc_now_iso(),
            "last_error": None,
            "last_error_category": None,
            "last_error_at": None,
            "profile_id": profile_id,
            "allow_from": list(allow_from),
        }
        if isinstance(approval_state_patch, dict) and approval_state_patch:
            patch.update(approval_state_patch)
        self.set_connector_state(connector_id, patch)

    def record_poll_approval_only(
        self,
        *,
        connector_id: str,
        label: str,
        workspace_id: str,
        profile_id: str,
        allow_from: List[str],
        approval_state_patch: Dict[str, Any],
    ) -> None:
        self.set_connector_state(
            connector_id,
            {
                "label": label,
                "workspace_id": workspace_id,
                "last_poll_at": self.utc_now_iso(),
                "last_error": None,
                "last_error_category": None,
                "last_error_at": None,
                "profile_id": profile_id,
                "allow_from": list(allow_from),
                **approval_state_patch,
            },
        )

    def record_connector_error(
        self,
        *,
        connector_id: str,
        label: str,
        workspace_id: str,
        detail: str,
        category: str,
    ) -> None:
        self.set_connector_state(
            connector_id,
            {
                "label": label,
                "workspace_id": workspace_id,
                "last_error": detail,
                "last_error_category": category,
                "last_error_at": self.utc_now_iso(),
                "last_poll_at": self.utc_now_iso(),
            },
        )

    # ------------------------------------------------------------------
    # Poll cycle (was TelegramPollCycleService)
    # ------------------------------------------------------------------

    def begin_poll(
        self,
        *,
        connector_state: Dict[str, Any],
        bot_token: str,
        configured_chat_id: str,
        workspace_id: str,
        profile: Dict[str, Any],
        connector_id: str,
        label: str,
        last_update_id: int,
    ) -> Dict[str, Any]:
        approval_state_patch = self.notify_pending_approvals(
            connector_state=connector_state,
            bot_token=bot_token,
            chat_id=configured_chat_id,
            workspace_id=workspace_id,
            profile=profile,
            connector_id=connector_id,
        )
        with self.get_updates_process_lock(bot_token) as acquired_poll_lock:
            if not acquired_poll_lock:
                self.autopilot_log(
                    f"skipping getUpdates for {label}: another poller currently holds the bot lock"
                )
                return {
                    "skipped": True,
                    "approval_state_patch": approval_state_patch,
                    "updates": [],
                }
            updates_result = self.telegram_api_request(
                bot_token,
                "getUpdates",
                params={
                    "offset": int(last_update_id or 0) + 1,
                    "limit": self.max_updates,
                    "timeout": 0,
                },
            )
        updates = updates_result.get("result")
        if not isinstance(updates, list):
            updates = []
        return {
            "skipped": False,
            "approval_state_patch": approval_state_patch,
            "updates": updates,
        }

    def complete_poll(
        self,
        *,
        connector_id: str,
        label: str,
        workspace_id: str,
        max_seen: int,
        last_update_id: int,
        profile_id: str,
        allow_from: List[str],
        approval_state_patch: Dict[str, Any],
    ) -> None:
        if max_seen > last_update_id:
            self.record_poll_completion(
                connector_id=connector_id,
                label=label,
                workspace_id=workspace_id,
                max_seen=max_seen,
                profile_id=profile_id,
                allow_from=list(allow_from),
                approval_state_patch=approval_state_patch,
            )
        elif approval_state_patch:
            self.record_poll_approval_only(
                connector_id=connector_id,
                label=label,
                workspace_id=workspace_id,
                profile_id=profile_id,
                allow_from=list(allow_from),
                approval_state_patch=approval_state_patch,
            )

    def handle_connector_error(
        self,
        *,
        connector_id: str,
        label: str,
        workspace_id: str,
        detail: str,
    ) -> None:
        category = self.classify_error(detail)
        self.record_channel_event_throttled(
            channel=self.provider,
            direction="system",
            event_type="error",
            text=detail,
            workspace_id=workspace_id,
            action="connector",
            metadata={"connector_id": connector_id, "category": category},
            dedupe_seconds=max(30.0, float(self.poll_seconds) * 6.0),
        )
        self.record_connector_error(
            connector_id=connector_id,
            label=label,
            workspace_id=workspace_id,
            detail=detail,
            category=category,
        )
        self.mark_error(detail, "connector")

    # ------------------------------------------------------------------
    # Loop iteration (was TelegramAutopilotLoopService)
    # ------------------------------------------------------------------

    def run_iteration(self) -> float:
        sleep_seconds = self.poll_seconds
        try:
            entries = self._list_connector_entries()
            had_connector_error = False
            self.set_connectors_seen(len(entries))
            self.mark_poll(False)
            for entry in entries:
                try:
                    self.poll_connector_fn(entry)
                except Exception as connector_exc:
                    had_connector_error = True
                    self.autopilot_log(f"connector error: {connector_exc}")
                    self.record_channel_event_throttled(
                        channel=self.provider,
                        direction="system",
                        event_type="error",
                        text=str(connector_exc),
                        workspace_id=self.normalize_workspace_id(entry.get("workspace_id")),
                        action="connector",
                        metadata={
                            "connector_id": str(entry.get("id") or "").strip(),
                            "source": "connector_loop",
                        },
                        dedupe_seconds=max(30.0, float(self.poll_seconds) * 6.0),
                    )
            if not had_connector_error:
                self.mark_poll(True)
            self.persist_state()
            return sleep_seconds
        except Exception as exc:
            detail = str(exc)
            sleep_seconds = max(self.poll_seconds, self.mark_error(detail, "loop"))
            self.autopilot_log(f"loop error: {detail}")
            self.record_channel_event_throttled(
                channel=self.provider,
                direction="system",
                event_type="error",
                text=detail,
                action="autopilot_loop",
                metadata={"source": "loop"},
                dedupe_seconds=max(45.0, float(self.poll_seconds) * 8.0),
            )
            return sleep_seconds

    # ------------------------------------------------------------------
    # Supervisor (was TelegramAutopilotSupervisorService)
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        self.mark_started(self.utc_now_iso())
        self.persist_state()
        if self.delivery_mode != "polling":
            self.autopilot_log(
                f"enabled (delivery={self.delivery_mode}, polling fallback disabled)"
            )
            return
        self.autopilot_log(
            f"enabled (poll={self.poll_seconds}s, prefix={'on' if self.require_prefix else 'off'}:{self.prefix})"
        )
        while True:
            sleep_seconds = self.run_iteration()
            self.sleep(max(0.25, float(sleep_seconds)))
