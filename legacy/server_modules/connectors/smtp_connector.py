from __future__ import annotations

import imaplib
import mimetypes
import smtplib
import ssl
from email import encoders, policy
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesParser
from email.utils import formatdate, make_msgid
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _parse_port(value: Any, *, default: int) -> int:
    try:
        port = int(value)
    except Exception:
        port = default
    if port <= 0:
        return default
    return port


def _split_tokens(value: str) -> List[str]:
    raw = str(value or "").replace(";", ",").replace("\n", ",")
    out: List[str] = []
    for item in raw.split(","):
        token = str(item or "").strip()
        if token:
            out.append(token)
    return out


def _normalize_recipients(value: Any) -> List[str]:
    if isinstance(value, str):
        recipients = _split_tokens(value)
    elif isinstance(value, Iterable):
        recipients = []
        for item in value:
            recipients.extend(_split_tokens(str(item or "")))
    else:
        recipients = _split_tokens(str(value or ""))
    seen: set[str] = set()
    out: List[str] = []
    for item in recipients:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(item)
    return out


def _smtp_settings(credentials: Dict[str, Any]) -> Dict[str, Any]:
    host = str(credentials.get("host") or "").strip()
    username = str(credentials.get("username") or "").strip()
    password = str(credentials.get("password") or "").strip()
    use_tls = _coerce_bool(credentials.get("use_tls"), default=True)
    port = _parse_port(credentials.get("port"), default=587 if use_tls else 25)
    from_email = str(
        credentials.get("from_email")
        or credentials.get("from")
        or username
        or ""
    ).strip()
    if not host:
        raise RuntimeError("SMTP host is required.")
    if not username:
        raise RuntimeError("SMTP username is required.")
    if not password:
        raise RuntimeError("SMTP password is required.")
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_tls": use_tls,
        "from_email": from_email,
    }


def _guess_imap_host(host: str) -> str:
    normalized = str(host or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("smtp."):
        return f"imap.{normalized[5:]}"
    if ".smtp." in normalized:
        return normalized.replace(".smtp.", ".imap.", 1)
    return normalized


def _imap_settings(credentials: Dict[str, Any]) -> Dict[str, Any]:
    smtp = _smtp_settings(credentials)
    use_ssl = _coerce_bool(credentials.get("use_imap_ssl"), default=True)
    host = str(credentials.get("imap_host") or _guess_imap_host(str(smtp["host"]))).strip()
    username = str(credentials.get("imap_username") or smtp["username"]).strip()
    password = str(credentials.get("imap_password") or smtp["password"]).strip()
    port = _parse_port(credentials.get("imap_port"), default=993 if use_ssl else 143)
    if not host:
        raise RuntimeError("IMAP host is required.")
    if not username:
        raise RuntimeError("IMAP username is required.")
    if not password:
        raise RuntimeError("IMAP password is required.")
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_ssl": use_ssl,
    }


def _attachment_part(item: Any) -> MIMEBase:
    if isinstance(item, (str, Path)):
        payload = {"path": str(item)}
    elif isinstance(item, dict):
        payload = dict(item)
    else:
        raise RuntimeError("Attachment must be a path string or dict.")
    path = Path(str(payload.get("path") or "")).expanduser()
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"Attachment path is invalid: {path}")
    filename = str(payload.get("filename") or payload.get("title") or path.name).strip() or path.name
    mime_type = str(payload.get("mime_type") or "").strip()
    guessed_type = mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    maintype, subtype = guessed_type.split("/", 1) if "/" in guessed_type else ("application", "octet-stream")
    part = MIMEBase(maintype, subtype)
    part.set_payload(path.read_bytes())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=filename)
    return part


def build_email_message(
    to: Any,
    subject: str,
    body: str,
    *,
    sender: str,
    html_body: Optional[str] = None,
    attachments: Optional[Sequence[Any]] = None,
) -> MIMEMultipart:
    recipients = _normalize_recipients(to)
    if not recipients:
        raise RuntimeError("At least one recipient is required.")
    normalized_sender = str(sender or "").strip()
    if not normalized_sender:
        raise RuntimeError("Sender email is required.")
    message = MIMEMultipart("mixed")
    message["To"] = ", ".join(recipients)
    message["From"] = normalized_sender
    message["Subject"] = str(subject or "").strip() or "(no subject)"
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid("empyralist")

    if html_body:
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(str(body or ""), "plain", "utf-8"))
        alternative.attach(MIMEText(str(html_body or ""), "html", "utf-8"))
        message.attach(alternative)
    else:
        message.attach(MIMEText(str(body or ""), "plain", "utf-8"))

    for item in attachments or []:
        message.attach(_attachment_part(item))
    return message


def _smtp_login(connection: Any, settings: Dict[str, Any]) -> None:
    username = str(settings.get("username") or "").strip()
    password = str(settings.get("password") or "").strip()
    if username or password:
        connection.login(username, password)


def validate_smtp_credentials(
    credentials: Dict[str, Any],
    *,
    smtp_cls: Any = smtplib.SMTP,
) -> Dict[str, Any]:
    settings = _smtp_settings(credentials)
    connection = smtp_cls(str(settings["host"]), int(settings["port"]), timeout=30)
    try:
        if hasattr(connection, "ehlo"):
            connection.ehlo()
        if bool(settings["use_tls"]):
            connection.starttls(context=ssl.create_default_context())
            if hasattr(connection, "ehlo"):
                connection.ehlo()
        _smtp_login(connection, settings)
        if hasattr(connection, "noop"):
            connection.noop()
    finally:
        try:
            connection.quit()
        except Exception:
            try:
                connection.close()
            except Exception:
                pass
    return {
        "ok": True,
        "status": 200,
        "message": "SMTP connector is valid.",
        "profile": {
            "host": settings["host"],
            "port": settings["port"],
            "username": settings["username"],
            "from_email": settings["from_email"],
        },
        "smtp_access": True,
    }


def send_email(
    credentials: Dict[str, Any],
    to: Any,
    subject: str,
    body: str,
    *,
    html_body: Optional[str] = None,
    attachments: Optional[Sequence[Any]] = None,
    smtp_cls: Any = smtplib.SMTP,
) -> Dict[str, Any]:
    settings = _smtp_settings(credentials)
    recipients = _normalize_recipients(to)
    attachment_items = list(attachments or [])
    message = build_email_message(
        recipients,
        subject,
        body,
        sender=str(settings["from_email"]),
        html_body=html_body,
        attachments=attachment_items,
    )
    connection = smtp_cls(str(settings["host"]), int(settings["port"]), timeout=30)
    try:
        if hasattr(connection, "ehlo"):
            connection.ehlo()
        if bool(settings["use_tls"]):
            connection.starttls(context=ssl.create_default_context())
            if hasattr(connection, "ehlo"):
                connection.ehlo()
        _smtp_login(connection, settings)
        rejected = connection.sendmail(str(settings["from_email"]), recipients, message.as_string())
    finally:
        try:
            connection.quit()
        except Exception:
            try:
                connection.close()
            except Exception:
                pass
    return {
        "ok": True,
        "smtp_host": settings["host"],
        "smtp_port": settings["port"],
        "from_email": settings["from_email"],
        "to": recipients,
        "subject": str(subject or "").strip() or "(no subject)",
        "message_id": str(message.get("Message-ID") or "").strip(),
        "rejected_recipients": rejected if isinstance(rejected, dict) else {},
        "attachments_count": len(attachment_items),
    }


def validate_imap_credentials(
    credentials: Dict[str, Any],
    *,
    imap_ssl_cls: Any = imaplib.IMAP4_SSL,
    imap_cls: Any = imaplib.IMAP4,
) -> Dict[str, Any]:
    settings = _imap_settings(credentials)
    connection = (
        imap_ssl_cls(str(settings["host"]), int(settings["port"]))
        if bool(settings["use_ssl"])
        else imap_cls(str(settings["host"]), int(settings["port"]))
    )
    try:
        connection.login(str(settings["username"]), str(settings["password"]))
        status, _ = connection.select("INBOX", readonly=True)
        if str(status or "").upper() != "OK":
            raise RuntimeError("IMAP inbox could not be selected.")
    finally:
        try:
            connection.logout()
        except Exception:
            pass
    return {
        "ok": True,
        "status": 200,
        "message": "IMAP inbox is reachable.",
        "imap_access": True,
        "profile": {
            "host": settings["host"],
            "port": settings["port"],
            "username": settings["username"],
        },
    }


def _decode_header_value(value: Any) -> str:
    if value in {None, ""}:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value or "").strip()


def _message_snippet(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if str(part.get_content_type() or "").lower() != "text/plain":
                continue
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"
            if isinstance(payload, bytes):
                return payload.decode(charset, errors="replace").strip()[:500]
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"
            if isinstance(payload, bytes):
                return payload.decode(charset, errors="replace").strip()[:500]
        return ""
    payload = message.get_payload(decode=True)
    charset = message.get_content_charset() or "utf-8"
    if isinstance(payload, bytes):
        return payload.decode(charset, errors="replace").strip()[:500]
    return str(message.get_payload() or "").strip()[:500]


def fetch_emails(
    credentials: Dict[str, Any],
    *,
    folder: str = "INBOX",
    limit: int = 10,
    unread_only: bool = True,
    imap_ssl_cls: Any = imaplib.IMAP4_SSL,
    imap_cls: Any = imaplib.IMAP4,
) -> Dict[str, Any]:
    settings = _imap_settings(credentials)
    safe_limit = max(1, min(int(limit or 10), 50))
    connection = (
        imap_ssl_cls(str(settings["host"]), int(settings["port"]))
        if bool(settings["use_ssl"])
        else imap_cls(str(settings["host"]), int(settings["port"]))
    )
    try:
        connection.login(str(settings["username"]), str(settings["password"]))
        status, _ = connection.select(str(folder or "INBOX"), readonly=True)
        if str(status or "").upper() != "OK":
            raise RuntimeError(f"IMAP folder '{folder}' could not be selected.")
        search_query = "UNSEEN" if unread_only else "ALL"
        status, data = connection.search(None, search_query)
        if str(status or "").upper() != "OK":
            raise RuntimeError("IMAP search failed.")
        raw_ids = data[0] if isinstance(data, list) and data else b""
        message_ids = [item for item in bytes(raw_ids).split() if item][-safe_limit:]
        items: List[Dict[str, Any]] = []
        for message_id in reversed(message_ids):
            fetch_status, fetched = connection.fetch(message_id, "(RFC822)")
            if str(fetch_status or "").upper() != "OK":
                continue
            raw_message = b""
            for part in fetched or []:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw_message = bytes(part[1])
                    break
            if not raw_message:
                continue
            parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
            items.append(
                {
                    "id": message_id.decode("utf-8", errors="replace"),
                    "subject": _decode_header_value(parsed.get("Subject")),
                    "from": _decode_header_value(parsed.get("From")),
                    "to": _decode_header_value(parsed.get("To")),
                    "date": _decode_header_value(parsed.get("Date")),
                    "message_id": _decode_header_value(parsed.get("Message-ID")),
                    "snippet": _message_snippet(parsed),
                    "folder": str(folder or "INBOX"),
                    "unread_only": bool(unread_only),
                }
            )
    finally:
        try:
            connection.logout()
        except Exception:
            pass
    return {
        "ok": True,
        "folder": str(folder or "INBOX"),
        "items": items,
        "count": len(items),
        "unread_only": bool(unread_only),
    }
