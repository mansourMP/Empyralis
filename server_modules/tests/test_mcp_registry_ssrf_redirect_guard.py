"""MAN-109: the MCP client's SSRF guard was "validate-once, connect-many" --
_validate_mcp_endpoint only ran at server-registration time
(upsert_workspace_mcp_server{,_async}), while every actual outbound
connection (tool discovery, tool invocation) reused the MCP SDK's
create_mcp_http_client(), which hardcodes follow_redirects=True with no
re-validation. A workspace owner could register an innocuous-looking public
MCP endpoint that later 302s to a blocked address (loopback/link-local/
private/cloud-metadata) and the redirect would be followed transparently.

These tests exercise the REAL fix -- _build_mcp_http_client's httpx
"request" event hook (_mcp_redirect_guard_request_hook) -- against a real
httpx.AsyncClient with its transport swapped for an httpx.MockTransport, so
the actual httpx redirect-following machinery is what's under test, not a
reimplementation of it. Literal IP addresses are used throughout so the
tests are hermetic (no real DNS/network I/O): socket.getaddrinfo resolves a
literal IP locally without a network round-trip, so assert_safe_outbound_url
(called from _validate_mcp_endpoint) still exercises its real DNS-resolution
check path without needing a mock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from server_modules import mcp_registry_service


def _client_with_mock_transport(handler, *, credential=None) -> httpx.AsyncClient:
    """Build a client the same way production code does
    (_build_mcp_http_client), then swap in an httpx.MockTransport so no real
    socket connection is ever attempted for the requests the guard allows
    through."""
    client = mcp_registry_service._build_mcp_http_client(credential)
    assert client is not None, "MCP SDK must be installed for these tests"
    client._transport = httpx.MockTransport(handler)
    return client


class _RecordingHandler:
    """Records every URL the transport was asked to fetch, so a test can
    assert the blocked hop's target was never actually requested."""

    def __init__(self, routes: dict[str, httpx.Response]) -> None:
        self.routes = routes
        self.requested_urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requested_urls.append(str(request.url))
        key = f"{request.url.scheme}://{request.url.host}"
        response = self.routes.get(key)
        if response is None:
            raise AssertionError(f"unexpected request to {request.url}")
        return response


@pytest.mark.asyncio
async def test_build_mcp_http_client_blocks_redirect_to_link_local_metadata_ip() -> None:
    """A registered endpoint (a public IP) 302s to the cloud metadata
    address (169.254.169.254, link-local) -- the redirect must be rejected
    and the metadata endpoint must never actually be requested."""
    handler = _RecordingHandler(
        {
            "https://1.1.1.1": httpx.Response(
                302, headers={"Location": "https://169.254.169.254/latest/meta-data/"}
            ),
            "https://169.254.169.254": httpx.Response(200, text="SHOULD NOT BE REACHED"),
        }
    )
    client = _client_with_mock_transport(handler)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await client.get("https://1.1.1.1/mcp")
    finally:
        await client.aclose()

    assert handler.requested_urls == ["https://1.1.1.1/mcp"], (
        "the blocked redirect target must never be requested"
    )


@pytest.mark.asyncio
async def test_build_mcp_http_client_blocks_redirect_to_loopback() -> None:
    handler = _RecordingHandler(
        {
            "https://1.1.1.1": httpx.Response(
                302, headers={"Location": "http://127.0.0.1:8000/admin"}
            ),
            "http://127.0.0.1": httpx.Response(200, text="SHOULD NOT BE REACHED"),
        }
    )
    client = _client_with_mock_transport(handler)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await client.get("https://1.1.1.1/mcp")
    finally:
        await client.aclose()

    assert handler.requested_urls == ["https://1.1.1.1/mcp"]


@pytest.mark.asyncio
async def test_build_mcp_http_client_blocks_redirect_to_private_range() -> None:
    handler = _RecordingHandler(
        {
            "https://1.1.1.1": httpx.Response(
                302, headers={"Location": "https://10.0.0.5/internal"}
            ),
            "https://10.0.0.5": httpx.Response(200, text="SHOULD NOT BE REACHED"),
        }
    )
    client = _client_with_mock_transport(handler)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await client.get("https://1.1.1.1/mcp")
    finally:
        await client.aclose()

    assert handler.requested_urls == ["https://1.1.1.1/mcp"]


@pytest.mark.asyncio
async def test_build_mcp_http_client_allows_legitimate_external_redirect() -> None:
    """A real-world case this fix must NOT break: a registered endpoint
    redirects to a DIFFERENT but equally public host. The redirect must
    still be followed and the final response returned normally."""
    handler = _RecordingHandler(
        {
            "https://1.1.1.1": httpx.Response(
                302, headers={"Location": "https://8.8.8.8/mcp-canonical"}
            ),
            "https://8.8.8.8": httpx.Response(200, text="ok from the canonical host"),
        }
    )
    client = _client_with_mock_transport(handler)
    try:
        response = await client.get("https://1.1.1.1/mcp")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.text == "ok from the canonical host"
    assert handler.requested_urls == ["https://1.1.1.1/mcp", "https://8.8.8.8/mcp-canonical"], (
        "both hops of a legitimate external-to-external redirect must be requested"
    )


@pytest.mark.asyncio
async def test_build_mcp_http_client_allows_direct_request_with_no_redirect() -> None:
    """Sanity check: the common case (no redirect at all) is unaffected."""
    handler = _RecordingHandler({"https://1.1.1.1": httpx.Response(200, text="direct ok")})
    client = _client_with_mock_transport(handler)
    try:
        response = await client.get("https://1.1.1.1/mcp")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.text == "direct ok"


def test_build_mcp_http_client_never_returns_none_even_without_credential() -> None:
    """Before MAN-109's fix, _build_mcp_http_client returned None whenever
    there was no credential (no auth headers) -- and the MCP SDK's
    streamable_http_client then built its OWN default client with no guard
    hook attached at all, meaning unauthenticated MCP servers had zero
    per-connection re-validation. The guard hook must be attached
    regardless of whether a credential is present."""
    client = mcp_registry_service._build_mcp_http_client(None)
    assert client is not None
    assert mcp_registry_service._mcp_redirect_guard_request_hook in client.event_hooks["request"]


def test_build_mcp_http_client_still_attaches_guard_with_credential() -> None:
    client = mcp_registry_service._build_mcp_http_client({"access_token": "secret-token"})
    assert client is not None
    assert mcp_registry_service._mcp_redirect_guard_request_hook in client.event_hooks["request"]
    assert client.headers.get("authorization") == "Bearer secret-token"


@pytest.mark.asyncio
async def test_maybe_close_mcp_http_client_closes_a_real_client() -> None:
    client = mcp_registry_service._build_mcp_http_client(None)
    assert client.is_closed is False
    await mcp_registry_service._maybe_close_mcp_http_client(client)
    assert client.is_closed is True


@pytest.mark.asyncio
async def test_maybe_close_mcp_http_client_tolerates_none_and_plain_objects() -> None:
    # None (SDK-not-installed path): must not raise.
    await mcp_registry_service._maybe_close_mcp_http_client(None)
    # An object with no aclose() (e.g. a bare test double): must not raise.
    await mcp_registry_service._maybe_close_mcp_http_client(object())


class _FakeSession:
    def __init__(self, *, tools_result=None, raise_exc: Exception | None = None) -> None:
        self._tools_result = tools_result
        self._raise_exc = raise_exc

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def initialize(self) -> None:
        return None

    async def list_tools(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._tools_result

    async def call_tool(self, name, arguments):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._tools_result


class _FakeStream:
    async def __aenter__(self):
        return (None, None, None)

    async def __aexit__(self, *exc_info) -> bool:
        return False


def _fake_streamable_http_client_fn(endpoint, http_client=None):
    return _FakeStream()


@pytest.mark.asyncio
async def test_list_tools_closes_owned_http_client_after_a_successful_call() -> None:
    """Regression guard for the resource-cleanup side effect of the
    MAN-109 fix: _build_mcp_http_client() now ALWAYS returns a client (so
    the guard hook covers every connection, not just credentialed ones),
    which means the streamable_http transport itself never closes it (it
    only closes a client it created). _list_tools_streamable_http_async
    must close the client itself once it's done with it."""
    fake_client = AsyncMock()
    fake_client.aclose = AsyncMock()

    result = await mcp_registry_service._list_tools_streamable_http_async(
        endpoint="https://example.com/mcp",
        http_client=fake_client,
        client_session_cls=lambda *a, **k: _FakeSession(tools_result={"tools": []}),
        streamable_http_client_fn=_fake_streamable_http_client_fn,
    )

    assert result == []
    fake_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_tools_closes_owned_http_client_even_on_failure() -> None:
    fake_client = AsyncMock()
    fake_client.aclose = AsyncMock()

    with pytest.raises(RuntimeError):
        await mcp_registry_service._list_tools_streamable_http_async(
            endpoint="https://example.com/mcp",
            http_client=fake_client,
            client_session_cls=lambda *a, **k: _FakeSession(raise_exc=RuntimeError("not found: nope")),
            streamable_http_client_fn=_fake_streamable_http_client_fn,
        )

    fake_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_tool_closes_owned_http_client_after_a_successful_call() -> None:
    fake_client = AsyncMock()
    fake_client.aclose = AsyncMock()

    result = await mcp_registry_service._call_streamable_http_tool_async(
        endpoint="https://example.com/mcp",
        tool_name="lookup_stock",
        arguments={},
        http_client=fake_client,
        client_session_cls=lambda *a, **k: _FakeSession(tools_result={"ok": True}),
        streamable_http_client_fn=_fake_streamable_http_client_fn,
    )

    assert result == {"ok": True}
    fake_client.aclose.assert_awaited_once()
