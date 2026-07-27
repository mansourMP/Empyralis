"""MAN-109: web_tools._fetch_url validated the URL once up front
(assert_safe_outbound_url) and then followed redirects unconditionally --
urllib's default HTTPRedirectHandler trusts any Location header, and the
`curl -L` fallback does the same at the process level. A URL that passes
the initial check (a public host) could still 302 to a blocked address
(loopback/link-local/private/cloud-metadata) and the response would come
back as if it were the original target.

These tests run a REAL local HTTP server and drive _fetch_url's two real
code paths against it end to end:
  - the primary path (urllib.request via _VALIDATING_OPENER, whose redirect
    handler is _ValidatingRedirectHandler)
  - the curl fallback path (_curl_fetch_with_redirect_guard, called
    directly here to exercise it deterministically rather than relying on
    urlopen failing first)

assert_safe_outbound_url is monkeypatched with a controllable fake so the
tests can decide which of the (loopback, by construction) test server paths
count as "safe" vs "blocked" -- assert_safe_outbound_url's own real-world
correctness (what counts as a private/loopback/link-local address) is
already covered by test_url_security.py; what's under test here is that
web_tools calls it on every redirect hop, not just the first request.
"""

from __future__ import annotations

import http.server
import threading
from typing import Callable

import pytest

from server_modules import web_tools


class _RequestLog:
    def __init__(self) -> None:
        self.paths: list[str] = []


def _make_server(log: _RequestLog, *, redirect_target: str) -> tuple[http.server.ThreadingHTTPServer, int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            log.paths.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", redirect_target)
                self.end_headers()
                return
            if self.path == "/blocked-target":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"SHOULD NOT BE REACHED")
                return
            if self.path == "/safe-final":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok final content")
                return
            self.send_response(404)
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _validator_blocking_path(blocked_substring: str) -> Callable[[str], None]:
    """Stand-in for assert_safe_outbound_url: raises for any URL whose path
    contains *blocked_substring*, otherwise allows it -- lets the test
    control which of the (all-loopback, by construction) server paths are
    "safe" vs "blocked" without needing real non-loopback hosts."""

    def _validate(url: str) -> None:
        if blocked_substring in url:
            raise RuntimeError(f"Outbound HTTP URL is not allowed: test-blocked ({url}).")

    return _validate


@pytest.fixture
def redirect_to_blocked_server(monkeypatch: pytest.MonkeyPatch):
    log = _RequestLog()
    server, port = _make_server(log, redirect_target="/blocked-target")
    monkeypatch.setattr(web_tools, "assert_safe_outbound_url", _validator_blocking_path("blocked-target"))
    try:
        yield f"http://127.0.0.1:{port}/start", log
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def redirect_to_safe_server(monkeypatch: pytest.MonkeyPatch):
    log = _RequestLog()
    server, port = _make_server(log, redirect_target="/safe-final")
    monkeypatch.setattr(web_tools, "assert_safe_outbound_url", _validator_blocking_path("blocked-target"))
    try:
        yield f"http://127.0.0.1:{port}/start", log
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_url_blocks_redirect_to_disallowed_target(redirect_to_blocked_server) -> None:
    start_url, log = redirect_to_blocked_server
    with pytest.raises(Exception):
        web_tools._fetch_url(start_url)
    assert "/blocked-target" not in log.paths, (
        "the redirect target must never actually be requested once it fails validation"
    )
    # _fetch_url tries urlopen first, then falls back to curl on failure --
    # both paths independently hit the redirect guard and get blocked at
    # /start, so /start may be requested once (urlopen) or twice (urlopen
    # then the curl fallback repeats it); what matters is /blocked-target
    # is never reached by either path.
    assert log.paths and set(log.paths) == {"/start"}


def test_fetch_url_allows_legitimate_redirect(redirect_to_safe_server) -> None:
    start_url, log = redirect_to_safe_server
    body = web_tools._fetch_url(start_url)
    assert body == "ok final content"
    assert log.paths == ["/start", "/safe-final"], (
        "both hops of a legitimate redirect must be requested"
    )


def test_curl_fetch_with_redirect_guard_blocks_redirect_to_disallowed_target(
    redirect_to_blocked_server,
) -> None:
    start_url, log = redirect_to_blocked_server
    with pytest.raises(Exception):
        web_tools._curl_fetch_with_redirect_guard(start_url, timeout=5)
    assert "/blocked-target" not in log.paths
    assert log.paths == ["/start"]


def test_curl_fetch_with_redirect_guard_allows_legitimate_redirect(redirect_to_safe_server) -> None:
    start_url, log = redirect_to_safe_server
    body = web_tools._curl_fetch_with_redirect_guard(start_url, timeout=5)
    assert body == "ok final content"
    assert log.paths == ["/start", "/safe-final"]


def test_parse_curl_status_and_location_extracts_redirect() -> None:
    headers_text = (
        "HTTP/1.1 302 Found\r\n"
        "Server: test\r\n"
        "Location: /safe-final\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    )
    status, location = web_tools._parse_curl_status_and_location(headers_text)
    assert status == 302
    assert location == "/safe-final"


def test_parse_curl_status_and_location_no_redirect() -> None:
    headers_text = "HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n"
    status, location = web_tools._parse_curl_status_and_location(headers_text)
    assert status == 200
    assert location is None


def test_fetch_url_still_validates_the_initial_url_up_front(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity check that the pre-existing up-front check wasn't lost while
    wiring in the per-hop guard."""
    calls: list[str] = []

    def _validate(url: str) -> None:
        calls.append(url)
        raise RuntimeError("blocked up front")

    monkeypatch.setattr(web_tools, "assert_safe_outbound_url", _validate)
    with pytest.raises(RuntimeError):
        web_tools._fetch_url("http://127.0.0.1:1/whatever")
    assert calls == ["http://127.0.0.1:1/whatever"]
