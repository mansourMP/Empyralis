from __future__ import annotations

import hmac
from typing import Any, Callable, Dict, Optional


class AutopilotEndpointService:
    def _profile_payload(self, profile_id: str, info: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": profile_id,
            "label": info.get("label", profile_id),
            "description": info.get("description", ""),
            "allow_free_text": bool(info.get("allow_free_text")),
            "allow_status": bool(info.get("allow_status")),
            "allow_help": bool(info.get("allow_help")),
        }

    def autopilot_profiles_payload(
        self,
        *,
        telegram_enabled: bool,
        telegram_default_profile: str,
        telegram_catalog: Dict[str, Dict[str, Any]],
        telegram_webhook_path: str,
        whatsapp_enabled: bool,
        whatsapp_default_profile: str,
        whatsapp_catalog: Dict[str, Dict[str, Any]],
        whatsapp_webhook_path: str,
    ) -> Dict[str, Any]:
        return {
            "channels": {
                "telegram": {
                    "enabled": bool(telegram_enabled),
                    "default_profile": telegram_default_profile,
                    "webhook_path": telegram_webhook_path,
                    "profiles": [
                        self._profile_payload(profile_id, info if isinstance(info, dict) else {})
                        for profile_id, info in telegram_catalog.items()
                    ],
                },
                "whatsapp": {
                    "enabled": bool(whatsapp_enabled),
                    "default_profile": whatsapp_default_profile,
                    "profiles": [
                        self._profile_payload(profile_id, info if isinstance(info, dict) else {})
                        for profile_id, info in whatsapp_catalog.items()
                    ],
                    "webhook_path": whatsapp_webhook_path,
                },
            }
        }

    def telegram_webhook_auth_result(
        self,
        *,
        enabled: bool,
        delivery_mode: str,
        configured_secret: str,
        header_secret: str = "",
    ) -> Dict[str, Any]:
        if not enabled:
            return {"status_code": 503, "content": "Empyralis Telegram autopilot is disabled."}
        resolved_delivery_mode = str(delivery_mode or "").strip().lower() or "polling"
        if resolved_delivery_mode != "webhook":
            return {
                "status_code": 503,
                "content": "Empyralis Telegram webhook mode is disabled. Set ORION_TELEGRAM_AUTOPILOT_DELIVERY_MODE=webhook for cloud production.",
            }
        expected_secret = str(configured_secret or "").strip()
        if not expected_secret:
            return {"status_code": 503, "content": "Empyralis Telegram webhook is not configured: no webhook secret is available."}
        provided_header_secret = str(header_secret or "").strip()
        if not provided_header_secret:
            return {"status_code": 401, "content": "Telegram webhook secret header is required."}
        if not hmac.compare_digest(expected_secret, provided_header_secret):
            return {"status_code": 403, "content": "Telegram webhook secret is invalid."}
        return {"status_code": 200}

    def whatsapp_webhook_auth_result(
        self,
        *,
        enabled: bool,
        configured_secret: str,
        query_secret: str = "",
        header_secret: str = "",
    ) -> Dict[str, Any]:
        if not enabled:
            return {"status_code": 503, "content": "Empyralis WhatsApp autopilot is disabled."}
        expected_secret = str(configured_secret or "").strip()
        if not expected_secret:
            return {"status_code": 503, "content": "Empyralis WhatsApp webhook is not configured: no webhook secret is available."}
        provided_query_secret = str(query_secret or "").strip()
        if not provided_query_secret:
            return {"status_code": 401, "content": "WhatsApp webhook query secret is required."}
        if not hmac.compare_digest(expected_secret, provided_query_secret):
            return {"status_code": 403, "content": "WhatsApp webhook secret is invalid."}
        return {"status_code": 200}

    def whatsapp_webhook_result(
        self,
        *,
        enabled: bool,
        configured_secret: str,
        query_secret: str = "",
        header_secret: str = "",
        form: Optional[Dict[str, str]] = None,
        handle_inbound: Optional[Callable[[Dict[str, str]], str]] = None,
    ) -> Dict[str, Any]:
        auth_result = self.whatsapp_webhook_auth_result(
            enabled=enabled,
            configured_secret=configured_secret,
            query_secret=query_secret,
            header_secret=header_secret,
        )
        if int(auth_result.get("status_code") or 200) != 200 or handle_inbound is None:
            return auth_result
        response_text = handle_inbound(form if isinstance(form, dict) else {})
        return {"status_code": 200, "text": response_text}
