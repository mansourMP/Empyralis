from __future__ import annotations

import ipaddress
from urllib.parse import urlparse
from typing import Any, Callable, Dict, List, Optional


class AutopilotStatusService:
    def __init__(
        self,
        *,
        normalize_workspace_id: Callable[[Any], str],
        telegram_snapshot: Callable[[], Dict[str, Any]],
        telegram_list_entries: Callable[[], List[Dict[str, Any]]],
        resolve_telegram_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        telegram_webhook_path: str = "/channels/telegram/webhook/{connector_id}",
        telegram_public_base_url: Any = "",
        telegram_webhook_secret_configured: Any = False,
        telegram_delivery_mode: Any = "polling",
        whatsapp_snapshot: Callable[[], Dict[str, Any]],
        whatsapp_list_entries: Callable[[], List[Dict[str, Any]]],
        resolve_whatsapp_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        whatsapp_webhook_path: str = "/channels/whatsapp/twilio/webhook",
        whatsapp_public_base_url: Any = "",
        whatsapp_webhook_secret_configured: Any = False,
    ) -> None:
        self.normalize_workspace_id = normalize_workspace_id
        self.telegram_snapshot = telegram_snapshot
        self.telegram_list_entries = telegram_list_entries
        self.resolve_telegram_profile = resolve_telegram_profile
        self.telegram_webhook_path = str(telegram_webhook_path or "/channels/telegram/webhook/{connector_id}")
        self.telegram_public_base_url = telegram_public_base_url
        self.telegram_webhook_secret_configured = telegram_webhook_secret_configured
        self.telegram_delivery_mode = telegram_delivery_mode
        self.whatsapp_snapshot = whatsapp_snapshot
        self.whatsapp_list_entries = whatsapp_list_entries
        self.resolve_whatsapp_profile = resolve_whatsapp_profile
        self.whatsapp_webhook_path = str(whatsapp_webhook_path or "/channels/whatsapp/twilio/webhook")
        self.whatsapp_public_base_url = whatsapp_public_base_url
        self.whatsapp_webhook_secret_configured = whatsapp_webhook_secret_configured

    def _resolve_string(self, value: Any) -> str:
        try:
            resolved = value() if callable(value) else value
        except Exception:
            resolved = ""
        return str(resolved or "").strip()

    def _resolve_bool(self, value: Any) -> bool:
        try:
            resolved = value() if callable(value) else value
        except Exception:
            resolved = False
        return bool(resolved)

    def _safe_profile(
        self,
        *,
        channel: str,
        resolver: Callable[[Dict[str, Any]], Dict[str, Any]],
        entry: Dict[str, Any],
        fallback_prefix: Any,
        fallback_require_prefix: Any,
    ) -> Dict[str, Any]:
        try:
            profile = resolver(entry)
        except Exception as exc:
            profile = {
                "id": "assistant",
                "prefix": str(fallback_prefix or "").strip(),
                "require_prefix": bool(fallback_require_prefix),
                "allow_free_text": False,
                "allow_status": True,
                "allow_help": True,
                "status": "setup_needed",
                "issue_code": f"{channel}_autopilot_profile_resolution_failed",
                "issue": f"{channel.title()} autopilot profile resolution failed: {exc}",
            }
        if not isinstance(profile, dict):
            profile = {}
        return {
            "id": str(profile.get("id") or "assistant").strip().lower() or "assistant",
            "prefix": str(profile.get("prefix") or fallback_prefix or "").strip(),
            "require_prefix": bool(profile.get("require_prefix", fallback_require_prefix)),
            "status": str(profile.get("status") or "live").strip().lower() or "live",
            "issue_code": str(profile.get("issue_code") or "").strip() or None,
            "issue": str(profile.get("issue") or "").strip() or None,
        }

    def _channel_issues(
        self,
        *,
        channel: str,
        snapshot: Dict[str, Any],
        vault_error: Optional[str],
        default_profile: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        if not bool(snapshot.get("enabled")):
            issues.append(
                {
                    "code": f"{channel}_autopilot_disabled",
                    "severity": "setup_needed",
                    "message": f"{channel.title()} autopilot is disabled.",
                }
            )
        if vault_error:
            issues.append(
                {
                    "code": f"{channel}_autopilot_vault_error",
                    "severity": "degraded",
                    "message": vault_error,
                }
            )
        if default_profile.get("issue"):
            issues.append(
                {
                    "code": default_profile.get("issue_code") or f"{channel}_autopilot_profile_issue",
                    "severity": "setup_needed" if default_profile.get("status") == "setup_needed" else "degraded",
                    "message": default_profile.get("issue"),
                }
            )
        if snapshot.get("last_error"):
            issues.append(
                {
                    "code": str(snapshot.get("last_error_category") or f"{channel}_autopilot_runtime_error"),
                    "severity": "degraded",
                    "message": str(snapshot.get("last_error")),
                }
            )
        return issues

    def _channel_status(self, issues: List[Dict[str, Any]]) -> str:
        if any(str(item.get("severity") or "") == "setup_needed" for item in issues):
            return "setup_needed"
        if issues:
            return "degraded"
        return "live"

    def _telegram_connector_webhook_path(self, connector_id: str) -> str:
        connector_token = str(connector_id or "").strip()
        template = "/" + self.telegram_webhook_path.lstrip("/")
        return template.replace("{connector_id}", connector_token)

    def _build_telegram_webhook_contract(
        self,
        *,
        enabled: bool,
        connectors_seen: int,
    ) -> Dict[str, Any]:
        public_base_url = self._resolve_string(self.telegram_public_base_url).rstrip("/")
        path_template = "/" + self.telegram_webhook_path.lstrip("/")
        public_url_template = f"{public_base_url}{path_template}" if public_base_url else ""
        secret_configured = self._resolve_bool(self.telegram_webhook_secret_configured)
        delivery_mode = self._resolve_string(self.telegram_delivery_mode).lower() or "polling"

        def _setup_needed(issue_code: str, issue: str, guidance: str) -> Dict[str, Any]:
            return {
                "status": "setup_needed",
                "delivery_mode": delivery_mode,
                "path_template": path_template,
                "public_base_url": public_base_url,
                "public_url_template": public_url_template,
                "secret_configured": secret_configured,
                "telegram_reachable": False,
                "issue_code": issue_code,
                "issue": issue,
                "guidance": guidance,
            }

        if not enabled:
            return {
                "status": "disabled",
                "delivery_mode": delivery_mode,
                "path_template": path_template,
                "public_base_url": public_base_url,
                "public_url_template": public_url_template,
                "secret_configured": secret_configured,
                "telegram_reachable": False,
                "issue_code": "telegram_autopilot_disabled",
                "issue": "Telegram autopilot is disabled.",
                "guidance": "Enable ORION_TELEGRAM_AUTOPILOT_ENABLED=1 before exposing Telegram webhook ingress.",
            }
        if delivery_mode != "webhook":
            return {
                "status": "fallback",
                "delivery_mode": delivery_mode,
                "path_template": path_template,
                "public_base_url": public_base_url,
                "public_url_template": public_url_template,
                "secret_configured": secret_configured,
                "telegram_reachable": False,
                "issue_code": "telegram_webhook_fallback_polling",
                "issue": "Telegram is running in long-polling fallback mode.",
                "guidance": "Use ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE=webhook on the cloud deployment. Keep polling only for self-host or local-dev fallback.",
            }
        if connectors_seen <= 0:
            return _setup_needed(
                "telegram_connector_missing",
                "No Telegram bot connector is configured for the active workspace.",
                "Create a Telegram bot connector first, then register the public webhook URL with Telegram.",
            )
        if not secret_configured:
            return _setup_needed(
                "telegram_webhook_secret_missing",
                "Telegram webhook secret is not configured.",
                "Set ORION_TELEGRAM_AUTOPILOT_WEBHOOK_SECRET before registering the Telegram webhook URL.",
            )
        if not public_base_url:
            return _setup_needed(
                "telegram_webhook_public_base_missing",
                "Telegram public webhook base URL is not configured.",
                "Set ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL to the public Empyralis runtime base URL before registering the Telegram webhook.",
            )

        candidate = public_base_url if "://" in public_base_url else f"https://{public_base_url}"
        try:
            parsed = urlparse(candidate)
        except Exception:
            parsed = None
        scheme = (parsed.scheme or "").strip().lower() if parsed else ""
        host = (parsed.hostname or "").strip().lower() if parsed else ""
        if not host:
            return _setup_needed(
                "telegram_webhook_public_base_invalid",
                "Telegram public webhook base URL is invalid.",
                "Set ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL to a valid public HTTPS base URL such as https://runtime.empyralis.com.",
            )
        if scheme != "https":
            return _setup_needed(
                "telegram_webhook_public_base_insecure",
                "Telegram webhook delivery requires a public HTTPS base URL.",
                "Use a public HTTPS runtime URL for Telegram webhook delivery; plain HTTP and localhost are not valid for cloud production.",
            )
        if host == "localhost" or host.endswith(".local"):
            return _setup_needed(
                "telegram_webhook_public_base_unreachable",
                "Telegram cannot reach localhost or .local webhook URLs.",
                "Use a deployed public HTTPS runtime URL; localhost and .local addresses are not valid for Telegram cloud delivery.",
            )
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified):
            return _setup_needed(
                "telegram_webhook_public_base_unreachable",
                "Telegram cannot reach private, loopback, or link-local webhook URLs.",
                "Use a public HTTPS runtime URL; loopback and private LAN addresses are not valid for Telegram cloud delivery.",
            )
        return {
            "status": "live",
            "delivery_mode": delivery_mode,
            "path_template": path_template,
            "public_base_url": public_base_url,
            "public_url_template": public_url_template,
            "secret_configured": secret_configured,
            "telegram_reachable": True,
            "issue_code": None,
            "issue": None,
            "guidance": None,
        }

    def _build_whatsapp_webhook_contract(
        self,
        *,
        enabled: bool,
        connectors_seen: int,
    ) -> Dict[str, Any]:
        public_base_url = self._resolve_string(self.whatsapp_public_base_url).rstrip("/")
        path = "/" + self.whatsapp_webhook_path.lstrip("/")
        public_url = f"{public_base_url}{path}" if public_base_url else ""
        secret_configured = self._resolve_bool(self.whatsapp_webhook_secret_configured)

        def _setup_needed(issue_code: str, issue: str, guidance: str) -> Dict[str, Any]:
            return {
                "status": "setup_needed",
                "path": path,
                "public_base_url": public_base_url,
                "public_url": public_url,
                "secret_configured": secret_configured,
                "twilio_reachable": False,
                "issue_code": issue_code,
                "issue": issue,
                "guidance": guidance,
            }

        if not enabled:
            return {
                "status": "disabled",
                "path": path,
                "public_base_url": public_base_url,
                "public_url": public_url,
                "secret_configured": secret_configured,
                "twilio_reachable": False,
                "issue_code": "whatsapp_autopilot_disabled",
                "issue": "WhatsApp autopilot is disabled.",
                "guidance": "Enable ORION_WHATSAPP_AUTOPILOT_ENABLED=1 before connecting Twilio inbound traffic.",
            }
        if connectors_seen <= 0:
            return _setup_needed(
                "whatsapp_connector_missing",
                "No WhatsApp Twilio connector is configured for the active workspace.",
                "Create a WhatsApp Twilio connector first, then re-check the channel status.",
            )
        if not secret_configured:
            return _setup_needed(
                "whatsapp_webhook_secret_missing",
                "WhatsApp webhook secret is not configured.",
                "Set ORION_WHATSAPP_AUTOPILOT_WEBHOOK_SECRET before exposing the public Twilio webhook URL.",
            )
        if not public_base_url:
            return _setup_needed(
                "whatsapp_webhook_public_base_missing",
                "WhatsApp public webhook base URL is not configured.",
                "Set ORION_WHATSAPP_AUTOPILOT_PUBLIC_BASE_URL to a public runtime base URL before enabling Twilio inbound traffic.",
            )

        candidate = public_base_url if "://" in public_base_url else f"https://{public_base_url}"
        try:
            parsed = urlparse(candidate)
        except Exception:
            parsed = None
        host = (parsed.hostname or "").strip().lower() if parsed else ""
        if not host:
            return _setup_needed(
                "whatsapp_webhook_public_base_invalid",
                "WhatsApp public webhook base URL is invalid.",
                "Set ORION_WHATSAPP_AUTOPILOT_PUBLIC_BASE_URL to a valid public base URL such as https://example.ngrok.app.",
            )
        if host == "localhost" or host.endswith(".local"):
            return _setup_needed(
                "whatsapp_webhook_public_base_unreachable",
                "Twilio cannot reach localhost or .local webhook URLs.",
                "Use a public HTTPS tunnel or deployed runtime URL; localhost is not valid for Twilio.",
            )
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified):
            return _setup_needed(
                "whatsapp_webhook_public_base_unreachable",
                "Twilio cannot reach private, loopback, or link-local webhook URLs.",
                "Use a public HTTPS tunnel or deployed runtime URL; localhost, loopback, and private LAN addresses are not valid for Twilio.",
            )
        return {
            "status": "live",
            "path": path,
            "public_base_url": public_base_url,
            "public_url": public_url,
            "secret_configured": secret_configured,
            "twilio_reachable": True,
            "issue_code": None,
            "issue": None,
            "guidance": None,
        }

    def telegram_status_payload(self) -> Dict[str, Any]:
        snapshot = self.telegram_snapshot()
        items: List[Dict[str, Any]] = []
        vault_error: Optional[str] = None
        connectors_index = snapshot.get("connectors", {}) if isinstance(snapshot.get("connectors"), dict) else {}
        default_profile = self._safe_profile(
            channel="telegram",
            resolver=self.resolve_telegram_profile,
            entry={},
            fallback_prefix=snapshot.get("prefix"),
            fallback_require_prefix=snapshot.get("require_prefix"),
        )
        try:
            entries = self.telegram_list_entries()
        except Exception as exc:
            entries = []
            vault_error = str(exc)
        for entry in entries:
            credential_id = str(entry.get("id") or "").strip()
            state = connectors_index.get(credential_id) if isinstance(connectors_index.get(credential_id), dict) else {}
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            registration = metadata.get("telegram_webhook_registration") if isinstance(metadata.get("telegram_webhook_registration"), dict) else {}
            profile = self._safe_profile(
                channel="telegram",
                resolver=self.resolve_telegram_profile,
                entry=entry,
                fallback_prefix=default_profile.get("prefix"),
                fallback_require_prefix=default_profile.get("require_prefix"),
            )
            profile_status = profile.get("status")
            profile_issue_code = profile.get("issue_code")
            profile_issue = profile.get("issue")
            registration_status = self._resolve_string(registration.get("status")).lower()
            delivery_mode = self._resolve_string(self.telegram_delivery_mode).lower() or "polling"
            if delivery_mode == "webhook" and registration_status != "registered":
                profile_status = "setup_needed"
                profile_issue_code = "telegram_webhook_registration_not_ready"
                profile_issue = (
                    self._resolve_string(registration.get("last_error"))
                    or "Telegram webhook is not registered for this connector."
                )
            items.append(
                {
                    "id": credential_id,
                    "label": entry.get("label"),
                    "workspace_id": self.normalize_workspace_id(entry.get("workspace_id")),
                    "profile_id": profile.get("id"),
                    "profile_status": profile_status,
                    "profile_issue_code": profile_issue_code,
                    "profile_issue": profile_issue,
                    "webhook_registration": registration,
                    "require_prefix": profile.get("require_prefix"),
                    "prefix": profile.get("prefix"),
                    "last_update_id": int(state.get("last_update_id") or 0),
                    "last_poll_at": state.get("last_poll_at"),
                    "last_processed_at": state.get("last_processed_at"),
                    "last_run_id": state.get("last_run_id"),
                    "last_action": state.get("last_action"),
                    "last_chat_id": state.get("last_chat_id"),
                    "last_outbound_message_id": state.get("last_outbound_message_id"),
                    "last_delivery_status": state.get("last_delivery_status"),
                    "last_delivery_receipt_at": state.get("last_delivery_receipt_at"),
                    "last_delivery_idempotency_key": state.get("last_delivery_idempotency_key"),
                    "last_delivery_transport": state.get("last_delivery_transport"),
                    "allow_from": state.get("allow_from") if isinstance(state.get("allow_from"), list) else [],
                    "dropped_sender_count": int(state.get("dropped_sender_count") or 0),
                    "last_dropped_sender_id": state.get("last_dropped_sender_id"),
                    "last_dropped_sender_username": state.get("last_dropped_sender_username"),
                    "last_dropped_at": state.get("last_dropped_at"),
                    "last_error": state.get("last_error"),
                    "last_error_category": state.get("last_error_category"),
                    "last_error_at": state.get("last_error_at"),
                    "webhook_path": self._telegram_connector_webhook_path(credential_id),
                    "webhook_url": (
                        f"{self._resolve_string(self.telegram_public_base_url).rstrip('/')}{self._telegram_connector_webhook_path(credential_id)}"
                        if self._resolve_string(self.telegram_public_base_url).strip()
                        else ""
                    ),
                }
            )
        issues = self._channel_issues(
            channel="telegram",
            snapshot=snapshot,
            vault_error=vault_error,
            default_profile=default_profile,
        )
        webhook = self._build_telegram_webhook_contract(
            enabled=bool(snapshot.get("enabled")),
            connectors_seen=int(snapshot.get("connectors_seen") or len(items)),
        )
        if webhook.get("issue") and webhook.get("status") in {"setup_needed", "fallback"}:
            issues.append(
                {
                    "code": webhook.get("issue_code") or "telegram_webhook_setup_issue",
                    "severity": "setup_needed" if webhook.get("status") == "setup_needed" else "degraded",
                    "message": webhook.get("issue"),
                }
            )
        for item in items:
            if item.get("profile_issue") and item.get("profile_issue_code") == "telegram_webhook_registration_not_ready":
                issues.append(
                    {
                        "code": item.get("profile_issue_code"),
                        "severity": "setup_needed",
                        "message": item.get("profile_issue"),
                        "connector_id": item.get("id"),
                    }
                )
        channel_status = "disabled" if webhook.get("status") == "disabled" else self._channel_status(issues)
        return {
            "ok": bool(snapshot.get("enabled")),
            "status": channel_status,
            "issues": issues,
            "webhook": webhook,
            "autopilot": {
                "status": channel_status,
                "enabled": snapshot.get("enabled"),
                "active": snapshot.get("active"),
                "thread_alive": snapshot.get("thread_alive"),
                "started_at": snapshot.get("started_at"),
                "last_poll_at": snapshot.get("last_poll_at"),
                "last_error": snapshot.get("last_error"),
                "last_error_at": snapshot.get("last_error_at"),
                "last_error_category": snapshot.get("last_error_category"),
                "last_error_source": snapshot.get("last_error_source"),
                "error_count": snapshot.get("error_count"),
                "consecutive_errors": snapshot.get("consecutive_errors"),
                "retry_count": snapshot.get("retry_count"),
                "last_retry_at": snapshot.get("last_retry_at"),
                "backoff_seconds": snapshot.get("backoff_seconds"),
                "next_retry_at": snapshot.get("next_retry_at"),
                "last_success_at": snapshot.get("last_success_at"),
                "processed_updates": snapshot.get("processed_updates"),
                "runs_started": snapshot.get("runs_started"),
                "connectors_seen": snapshot.get("connectors_seen"),
                "connector_state_count": snapshot.get("connector_state_count"),
                "connector_error_count": snapshot.get("connector_error_count"),
                "dropped_sender_count": snapshot.get("dropped_sender_count"),
                "state_file": snapshot.get("state_file"),
                "delivery_mode": snapshot.get("delivery_mode") or webhook.get("delivery_mode"),
                "poll_seconds": snapshot.get("poll_seconds"),
                "prefix": snapshot.get("prefix"),
                "require_prefix": snapshot.get("require_prefix"),
                "default_profile": snapshot.get("default_profile"),
                "default_profile_resolved": default_profile.get("id"),
                "default_profile_status": default_profile.get("status"),
                "default_profile_issue_code": default_profile.get("issue_code"),
                "default_profile_issue": default_profile.get("issue"),
                "channel_state": channel_status,
            },
            "connectors": items,
            "vault_error": vault_error,
        }

    def whatsapp_status_payload(self) -> Dict[str, Any]:
        snapshot = self.whatsapp_snapshot()
        items: List[Dict[str, Any]] = []
        vault_error: Optional[str] = None
        connectors_index = snapshot.get("connectors", {}) if isinstance(snapshot.get("connectors"), dict) else {}
        default_profile = self._safe_profile(
            channel="whatsapp",
            resolver=self.resolve_whatsapp_profile,
            entry={},
            fallback_prefix=snapshot.get("prefix"),
            fallback_require_prefix=snapshot.get("require_prefix"),
        )
        try:
            entries = self.whatsapp_list_entries()
        except Exception as exc:
            entries = []
            vault_error = str(exc)
        for entry in entries:
            credential_id = str(entry.get("id") or "").strip()
            state = connectors_index.get(credential_id) if isinstance(connectors_index.get(credential_id), dict) else {}
            profile = self._safe_profile(
                channel="whatsapp",
                resolver=self.resolve_whatsapp_profile,
                entry=entry,
                fallback_prefix=default_profile.get("prefix"),
                fallback_require_prefix=default_profile.get("require_prefix"),
            )
            items.append(
                {
                    "id": credential_id,
                    "label": entry.get("label"),
                    "workspace_id": self.normalize_workspace_id(entry.get("workspace_id")),
                    "profile_id": profile.get("id"),
                    "profile_status": profile.get("status"),
                    "profile_issue_code": profile.get("issue_code"),
                    "profile_issue": profile.get("issue"),
                    "require_prefix": profile.get("require_prefix"),
                    "prefix": profile.get("prefix"),
                    "last_processed_at": state.get("last_processed_at"),
                    "last_run_id": state.get("last_run_id"),
                    "last_action": state.get("last_action"),
                    "last_message_sid": state.get("last_message_sid"),
                    "last_from_number": state.get("last_from_number"),
                    "last_to_number": state.get("last_to_number"),
                    "last_outbound_message_sid": state.get("last_outbound_message_sid"),
                    "last_delivery_status": state.get("last_delivery_status"),
                    "last_delivery_receipt_at": state.get("last_delivery_receipt_at"),
                    "last_delivery_idempotency_key": state.get("last_delivery_idempotency_key"),
                    "last_delivery_transport": state.get("last_delivery_transport"),
                    "last_error": state.get("last_error"),
                    "last_error_category": state.get("last_error_category"),
                    "last_error_at": state.get("last_error_at"),
                }
            )
        issues = self._channel_issues(
            channel="whatsapp",
            snapshot=snapshot,
            vault_error=vault_error,
            default_profile=default_profile,
        )
        webhook = self._build_whatsapp_webhook_contract(
            enabled=bool(snapshot.get("enabled")),
            connectors_seen=int(snapshot.get("connectors_seen") or len(items)),
        )
        if webhook.get("issue"):
            issues.append(
                {
                    "code": webhook.get("issue_code") or "whatsapp_webhook_setup_issue",
                    "severity": "setup_needed" if webhook.get("status") in {"disabled", "setup_needed"} else "degraded",
                    "message": webhook.get("issue"),
                }
            )
        channel_status = "disabled" if webhook.get("status") == "disabled" else self._channel_status(issues)
        if webhook.get("status") == "live" and channel_status != "disabled":
            channel_status = "live" if not any(str(item.get("severity") or "") == "degraded" for item in issues) else "degraded"
        return {
            "ok": bool(snapshot.get("enabled")),
            "status": channel_status,
            "issues": issues,
            "webhook": webhook,
            "autopilot": {
                "status": channel_status,
                "enabled": snapshot.get("enabled"),
                "active": snapshot.get("active"),
                "thread_alive": snapshot.get("thread_alive"),
                "started_at": snapshot.get("started_at"),
                "last_inbound_at": snapshot.get("last_inbound_at"),
                "last_error": snapshot.get("last_error"),
                "last_error_at": snapshot.get("last_error_at"),
                "last_error_category": snapshot.get("last_error_category"),
                "last_error_source": snapshot.get("last_error_source"),
                "error_count": snapshot.get("error_count"),
                "consecutive_errors": snapshot.get("consecutive_errors"),
                "processed_messages": snapshot.get("processed_messages"),
                "runs_started": snapshot.get("runs_started"),
                "connectors_seen": snapshot.get("connectors_seen"),
                "connector_state_count": snapshot.get("connector_state_count"),
                "connector_error_count": snapshot.get("connector_error_count"),
                "state_file": snapshot.get("state_file"),
                "prefix": snapshot.get("prefix"),
                "require_prefix": snapshot.get("require_prefix"),
                "default_profile": snapshot.get("default_profile"),
                "default_profile_resolved": default_profile.get("id"),
                "default_profile_status": default_profile.get("status"),
                "default_profile_issue_code": default_profile.get("issue_code"),
                "default_profile_issue": default_profile.get("issue"),
                "channel_state": webhook.get("status"),
            },
            "connectors": items,
            "vault_error": vault_error,
        }
