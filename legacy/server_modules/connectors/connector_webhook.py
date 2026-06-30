"""
Shared connector webhook handler — consolidated from the per-channel webhook
bridge and compatibility bridge services.

Replaces: telegram_webhook_bridge_service, telegram_compatibility_bridge_service.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class ConnectorWebhook:
    """Consolidated webhook handler + compatibility callable storage."""

    def __init__(
        self,
        *,
        init_runtime: Callable[[], None],
        parse_update: Callable[[bytes], Dict[str, Any]],
        webhook_auth_result: Callable[..., Dict[str, Any]],
        handle_inbound: Callable[[str, Dict[str, Any]], Any],
        error_response: Callable[[int, str], Any],
        enabled: bool,
        delivery_mode: str,
        configured_secret: str,
        safe_path_token: Callable[[Any], str],
        build_goal_with_profile: Callable[[str, Dict[str, str]], str],
        workspace_connector_context: Callable[[str, str, str], Dict[str, Any]],
        extract_message: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        build_goal_with_attachments: Callable[[str, List[Dict[str, Any]]], str],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        # Webhook bridge fields
        self.init_runtime = init_runtime
        self.parse_update = parse_update
        self.webhook_auth_result = webhook_auth_result
        self.handle_inbound = handle_inbound
        self.error_response = error_response
        self.enabled = bool(enabled)
        self.delivery_mode = str(delivery_mode or "").strip().lower() or "polling"
        self.configured_secret = str(configured_secret or "").strip()

        # Compatibility bridge fields
        self.safe_path_token = safe_path_token
        self.build_goal_with_profile = build_goal_with_profile
        self.workspace_connector_context = workspace_connector_context
        self.extract_message = extract_message
        self.build_goal_with_attachments = build_goal_with_attachments
        self.route_message = route_message

    # ------------------------------------------------------------------
    # Webhook handling (was TelegramWebhookBridgeService)
    # ------------------------------------------------------------------

    def handle_webhook(
        self,
        *,
        raw_body: bytes,
        connector_id: str,
        header_secret: str = "",
    ) -> Any:
        self.init_runtime()
        auth_result = self.webhook_auth_result(
            enabled=self.enabled,
            delivery_mode=self.delivery_mode,
            configured_secret=self.configured_secret,
            header_secret=header_secret,
        )
        if int(auth_result.get("status_code") or 200) != 200:
            return self.error_response(
                int(auth_result.get("status_code") or 500),
                str(auth_result.get("content") or "forbidden"),
            )
        try:
            update = self.parse_update(raw_body)
            result = self.handle_inbound(str(connector_id or "").strip(), update)
        except LookupError as exc:
            return self.error_response(404, str(exc))
        except ValueError as exc:
            return self.error_response(400, str(exc))
        except RuntimeError as exc:
            return self.error_response(503, str(exc))
        return {"ok": True, **(result if isinstance(result, dict) else {})}
