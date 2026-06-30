from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import ssl
from typing import Any, Dict, Optional
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote_plus, urlencode

import certifi

try:
    from fastapi import Response
except Exception:  # pragma: no cover - test fallback when FastAPI is unavailable
    class Response:  # type: ignore[override]
        def __init__(self, content: str = "", media_type: Optional[str] = None):
            self.body = str(content).encode("utf-8")
            self.media_type = media_type


class WhatsAppTransportService:
    def normalize_number(self, raw_value: Any) -> str:
        value = str(raw_value or "").strip().replace(" ", "")
        if not value:
            return ""
        if value.lower() in {"*", "whatsapp:*"}:
            return "whatsapp:*"
        if value.lower().startswith("whatsapp:"):
            suffix = value.split(":", 1)[1]
            return f"whatsapp:{suffix}"
        if value.startswith("+"):
            return f"whatsapp:{value}"
        return value.lower()

    def twiml_response(self, message: Optional[str] = None) -> Response:
        if message and str(message).strip():
            safe = html.escape(str(message), quote=False)
            xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>'
        else:
            xml = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
        return Response(content=xml, media_type="application/xml")

    def validate_webhook_signature(
        self,
        *,
        request_url: str,
        form: Dict[str, Any],
        signature: str,
        auth_token: str,
    ) -> bool:
        url = str(request_url or "").strip()
        provided_signature = str(signature or "").strip()
        token = str(auth_token or "").strip()
        if not url or not provided_signature or not token:
            return False
        parts = [url]
        for key in sorted(str(item) for item in form.keys()):
            parts.append(key)
            parts.append(str(form.get(key) or ""))
        payload = "".join(parts).encode("utf-8")
        digest = hmac.new(token.encode("utf-8"), payload, hashlib.sha1).digest()
        expected_signature = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected_signature, provided_signature)

    def send_message(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
        body: str,
    ) -> Dict[str, Any]:
        sid = str(account_sid or "").strip()
        token = str(auth_token or "").strip()
        sender = self.normalize_number(from_number)
        receiver = self.normalize_number(to_number)
        if not sid or not token:
            raise RuntimeError("Twilio account_sid/auth_token are required.")
        if not sender or not receiver:
            raise RuntimeError("Twilio From/To numbers are required.")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{quote_plus(sid)}/Messages.json"
        basic = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
        payload = urlencode({"From": sender, "To": receiver, "Body": str(body or "")}).encode("utf-8")
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        req = urlrequest.Request(url, data=payload, headers=headers, method="POST")
        context = ssl.create_default_context(cafile=certifi.where())
        try:
            with urlrequest.urlopen(req, timeout=15, context=context) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                parsed = json.loads(raw) if raw else {}
                if resp.status not in {200, 201}:
                    raise RuntimeError(f"Twilio send failed: status {resp.status}")
                return parsed if isinstance(parsed, dict) else {}
        except urlerror.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            detail = raw or str(exc)
            raise RuntimeError(f"Twilio send failed: HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
