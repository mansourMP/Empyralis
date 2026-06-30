from __future__ import annotations

from typing import Any, Callable, Dict


class WhatsAppWebhookBridgeService:
    def __init__(
        self,
        *,
        init_runtime: Callable[[], None],
        parse_form_urlencoded: Callable[[bytes], Dict[str, str]],
        webhook_auth_result: Callable[..., Dict[str, Any]],
        resolve_inbound_connector: Callable[[Dict[str, str]], Any],
        validate_signature: Callable[[str, Dict[str, str], str, str], bool],
        ingest_webhook: Callable[[Dict[str, str]], Any],
        twiml_response: Callable[[str], Any],
        error_response: Callable[[int, str], Any],
        enabled: bool,
        configured_secret: str,
    ) -> None:
        self.init_runtime = init_runtime
        self.parse_form_urlencoded = parse_form_urlencoded
        self.webhook_auth_result = webhook_auth_result
        self.resolve_inbound_connector = resolve_inbound_connector
        self.validate_signature = validate_signature
        self.ingest_webhook = ingest_webhook
        self.twiml_response = twiml_response
        self.error_response = error_response
        self.enabled = bool(enabled)
        self.configured_secret = str(configured_secret or "").strip()

    def handle_webhook(
        self,
        *,
        raw_body: bytes,
        request_url: str = "",
        twilio_signature: str = "",
        query_secret: str = "",
        header_secret: str = "",
    ) -> Any:
        self.init_runtime()
        form = self.parse_form_urlencoded(raw_body)
        auth_result = self.webhook_auth_result(
            enabled=self.enabled,
            configured_secret=self.configured_secret,
            query_secret=query_secret,
            header_secret=header_secret,
        )
        if int(auth_result.get("status_code") or 200) != 200:
            return self.error_response(
                int(auth_result.get("status_code") or 500),
                str(auth_result.get("content") or "forbidden"),
            )
        matched = self.resolve_inbound_connector(form)
        if not isinstance(matched, dict) or not isinstance(matched.get("secret"), dict):
            return self.error_response(404, "WhatsApp connector is not configured for this inbound route.")
        auth_token = str((matched.get("secret") or {}).get("auth_token") or "").strip()
        if not auth_token:
            return self.error_response(503, "WhatsApp connector is not configured for Twilio signature validation.")
        provided_signature = str(twilio_signature or "").strip()
        if not provided_signature:
            return self.error_response(401, "X-Twilio-Signature header is required.")
        if not self.validate_signature(str(request_url or "").strip(), form, provided_signature, auth_token):
            return self.error_response(403, "Twilio signature is invalid.")
        return self.twiml_response(str(self.ingest_webhook(form, matched=matched) or ""))
