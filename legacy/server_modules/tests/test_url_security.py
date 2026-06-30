from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from server_modules.url_security import assert_safe_outbound_url, obvious_private_url_reason


def test_obvious_private_url_reason_blocks_local_and_private_hosts() -> None:
    assert obvious_private_url_reason("http://localhost:3000") == "loopback_host"
    assert obvious_private_url_reason("http://127.0.0.1:8000") == "loopback"
    assert obvious_private_url_reason("http://service.internal") == "private_host"
    assert obvious_private_url_reason("file:///etc/passwd") == "unsupported_scheme"


def test_assert_safe_outbound_url_rejects_dns_resolution_to_private_ip() -> None:
    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]
    with patch("server_modules.url_security.socket.getaddrinfo", return_value=resolved):
        with pytest.raises(RuntimeError) as exc_info:
            assert_safe_outbound_url("https://public.example.com")

    assert "private" in str(exc_info.value)


def test_assert_safe_outbound_url_rejects_dns_resolution_failure() -> None:
    with patch("server_modules.url_security.socket.getaddrinfo", side_effect=socket.gaierror):
        with pytest.raises(RuntimeError) as exc_info:
            assert_safe_outbound_url("https://unresolvable.example.com")

    assert "could not be resolved" in str(exc_info.value)
