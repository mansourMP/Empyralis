from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


def _allow_private_http_hosts() -> bool:
    return str(os.getenv("ORION_ALLOW_PRIVATE_HTTP_HOSTS", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _ip_block_reason(value: str) -> str | None:
    try:
        ip = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_multicast:
        return "multicast"
    if ip.is_private:
        return "private"
    if ip.is_reserved:
        return "reserved"
    return None


def obvious_private_url_reason(url: str) -> str | None:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return "invalid_url"
    if parsed.scheme.lower() not in {"http", "https"}:
        return "unsupported_scheme"
    hostname = str(parsed.hostname or "").strip().lower()
    if not hostname:
        return "missing_host"
    if hostname in {"localhost", "localhost.localdomain"}:
        return "loopback_host"
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        return "private_host"
    return _ip_block_reason(hostname)


def assert_safe_outbound_url(url: str, *, allow_private_host: bool = False) -> None:
    if allow_private_host or _allow_private_http_hosts():
        return
    obvious_reason = obvious_private_url_reason(url)
    if obvious_reason:
        raise RuntimeError(f"Outbound HTTP URL is not allowed: {obvious_reason}.")
    parsed = urlparse(str(url or "").strip())
    hostname = str(parsed.hostname or "").strip()
    if not hostname:
        raise RuntimeError("Outbound HTTP URL is missing a host.")
    try:
        resolved = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError("Outbound HTTP URL host could not be resolved safely.") from exc
    for item in resolved:
        sockaddr = item[4] if len(item) > 4 else ()
        candidate = str(sockaddr[0] if sockaddr else "").strip()
        reason = _ip_block_reason(candidate)
        if reason:
            raise RuntimeError(f"Outbound HTTP URL resolves to a disallowed {reason} address.")
