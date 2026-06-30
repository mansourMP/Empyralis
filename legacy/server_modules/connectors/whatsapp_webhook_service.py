from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class WhatsAppWebhookService:
    def __init__(
        self,
        *,
        ingress_service: Callable[[], Any],
    ) -> None:
        self.ingress_service = ingress_service

    def parse_form_urlencoded(self, raw: bytes) -> Dict[str, str]:
        return self.ingress_service().parse_form_urlencoded(raw)

    def handle_inbound(self, form: Dict[str, str], *, matched: Optional[Dict[str, Any]] = None) -> str:
        return str(self.ingress_service().ingest_webhook(form, matched=matched) or "")
