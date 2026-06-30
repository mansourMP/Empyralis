from __future__ import annotations

import ipaddress
import os
from typing import Any, Iterable, Optional


def _configured_proxy_networks() -> list[ipaddress._BaseNetwork]:
    raw = str(os.getenv("EMPYRALIS_TRUSTED_PROXY_CIDRS") or "").strip()
    if not raw:
        return []
    networks: list[ipaddress._BaseNetwork] = []
    for token in raw.split(","):
        item = token.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _parse_ip(value: Any) -> Optional[ipaddress._BaseAddress]:
    token = str(value or "").strip()
    if not token:
        return None
    if token.startswith("[") and "]" in token:
        token = token[1:token.index("]")]
    if ":" in token and token.count(":") == 1:
        host, maybe_port = token.rsplit(":", 1)
        if maybe_port.isdigit():
            token = host
    try:
        return ipaddress.ip_address(token)
    except ValueError:
        return None


def _is_trusted_proxy(peer: Optional[ipaddress._BaseAddress], networks: Iterable[ipaddress._BaseNetwork]) -> bool:
    if peer is None:
        return False
    return any(peer in network for network in networks)


def resolve_client_ip(request: Any) -> str:
    client = getattr(request, "client", None)
    peer_host = str(getattr(client, "host", "") or "").strip()
    peer_ip = _parse_ip(peer_host)
    networks = _configured_proxy_networks()
    forwarded = getattr(getattr(request, "headers", None), "get", lambda *_args, **_kwargs: None)("x-forwarded-for")
    if networks and _is_trusted_proxy(peer_ip, networks) and isinstance(forwarded, str) and forwarded.strip():
        chain = [item.strip() for item in forwarded.split(",") if item.strip()]
        for candidate in reversed(chain):
            candidate_ip = _parse_ip(candidate)
            if candidate_ip is None:
                continue
            if not _is_trusted_proxy(candidate_ip, networks):
                return candidate
        if chain and _parse_ip(chain[0]) is not None:
            return chain[0]
    return peer_host or "unknown"
