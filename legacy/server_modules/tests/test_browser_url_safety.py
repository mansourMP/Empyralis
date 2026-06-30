from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from server_modules.gateway_browser_runtime import (
    _validate_attach_endpoint_url,
    _validate_browser_url,
    GatewayBrowserRuntime,
)


# ---------------------------------------------------------------------------
# _validate_browser_url (wraps assert_safe_outbound_url)
# ---------------------------------------------------------------------------


class TestValidateBrowserUrl:
    def test_https_public_url_allowed(self) -> None:
        _validate_browser_url("https://example.com")

    def test_https_url_with_path_allowed(self) -> None:
        _validate_browser_url("https://www.google.com/search?q=test")

    def test_file_scheme_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="unsupported_scheme"):
            _validate_browser_url("file:///etc/passwd")

    def test_http_localhost_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="loopback"):
            _validate_browser_url("http://localhost:8080")

    def test_http_127_0_0_1_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="loopback"):
            _validate_browser_url("http://127.0.0.1:3000")

    def test_http_192_168_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="private"):
            _validate_browser_url("http://192.168.1.1")

    def test_http_169_254_169_254_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="link-local"):
            _validate_browser_url("http://169.254.169.254")

    def test_http_10_0_0_1_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="private"):
            _validate_browser_url("http://10.0.0.1")

    def test_ipv6_loopback_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="loopback"):
            _validate_browser_url("http://[::1]:8080")

    def test_http_0_0_0_0_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="unspecified"):
            _validate_browser_url("http://0.0.0.0:8080")

    def test_172_16_0_1_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="private"):
            _validate_browser_url("http://172.16.0.1")

    def test_empty_url_does_not_raise(self) -> None:
        _validate_browser_url("")

    def test_fe80_link_local_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="link-local"):
            _validate_browser_url("http://169.254.1.1")


# ---------------------------------------------------------------------------
# _validate_attach_endpoint_url
# ---------------------------------------------------------------------------


class TestValidateAttachEndpointUrl:
    """_validate_attach_endpoint_url handles WS URLs that assert_safe_outbound_url rejects."""

    def test_ws_public_url_allowed(self) -> None:
        _validate_attach_endpoint_url("ws://example.com:9222")

    def test_https_public_url_allowed(self) -> None:
        _validate_attach_endpoint_url("https://example.com")

    def test_ws_localhost_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="loopback"):
            _validate_attach_endpoint_url("ws://localhost:9222")

    def test_ws_127_0_0_1_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="loopback"):
            _validate_attach_endpoint_url("ws://127.0.0.1:9222")

    def test_ws_192_168_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="private"):
            _validate_attach_endpoint_url("ws://192.168.1.1:9222")

    def test_ws_169_254_169_254_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="cloud metadata"):
            _validate_attach_endpoint_url("ws://169.254.169.254:9222")

    def test_ws_10_0_0_1_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="private"):
            _validate_attach_endpoint_url("ws://10.0.0.1:9222")

    def test_ws_ipv6_loopback_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="loopback"):
            _validate_attach_endpoint_url("ws://[::1]:9222")

    def test_ws_empty_url_does_not_raise(self) -> None:
        _validate_attach_endpoint_url("")

    @patch("server_modules.gateway_browser_runtime._allow_private_http_hosts", return_value=True)
    def test_ws_localhost_allowed_in_dev_mode(self, mock_allow: object) -> None:
        _validate_attach_endpoint_url("ws://localhost:9222")  # no error

    @patch("server_modules.gateway_browser_runtime._allow_private_http_hosts", return_value=True)
    def test_ws_127_0_0_1_allowed_in_dev_mode(self, mock_allow: object) -> None:
        _validate_attach_endpoint_url("ws://127.0.0.1:9222")  # no error

    def test_ws_0_0_0_0_blocked(self) -> None:
        with pytest.raises(RuntimeError, match="loopback"):
            _validate_attach_endpoint_url("ws://0.0.0.0:9222")


# ---------------------------------------------------------------------------
# GatewayBrowserRuntime integration tests
# ---------------------------------------------------------------------------


class TestBrowserRuntimeNavigate:
    """Verify GatewayBrowserRuntime performs URL validation before adapter calls."""

    @pytest.fixture
    def runtime(self) -> GatewayBrowserRuntime:
        rt = GatewayBrowserRuntime(target="local_companion")
        return rt

    def _setup_session(self, runtime: GatewayBrowserRuntime) -> None:
        runtime._sessions["sess-1"] = {
            "checkpoint": {"next_action_index": 0},
            "session_profile": "test",
        }

    @pytest.mark.asyncio
    async def test_navigate_allowed_url_succeeds(self, runtime: GatewayBrowserRuntime) -> None:
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        adapter.get_page_state = AsyncMock(return_value={"url": "https://example.com"})
        adapter.navigate = AsyncMock(
            return_value={"url": "https://example.com", "title": "Example", "status_code": 200}
        )
        runtime._adapter = adapter

        result = await runtime.perform_action({
            "browser_session_id": "sess-1",
            "action": "navigate",
            "action_args": {"url": "https://example.com"},
        })

        assert result["status"] == "completed"
        adapter.navigate.assert_awaited_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_navigate_blocked_url_raises(self, runtime: GatewayBrowserRuntime) -> None:
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        runtime._adapter = adapter

        with pytest.raises(RuntimeError, match="loopback_host"):
            await runtime.perform_action({
                "browser_session_id": "sess-1",
                "action": "navigate",
                "action_args": {"url": "http://localhost:8080"},
            })

    @pytest.mark.asyncio
    async def test_navigate_file_scheme_raises(self, runtime: GatewayBrowserRuntime) -> None:
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        runtime._adapter = adapter

        with pytest.raises(RuntimeError, match="unsupported_scheme"):
            await runtime.perform_action({
                "browser_session_id": "sess-1",
                "action": "navigate",
                "action_args": {"url": "file:///etc/passwd"},
            })

    @pytest.mark.asyncio
    async def test_new_tab_allowed_url_succeeds(self, runtime: GatewayBrowserRuntime) -> None:
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        adapter.get_page_state = AsyncMock(return_value={})
        adapter.new_tab = AsyncMock(return_value=42)
        runtime._adapter = adapter

        result = await runtime.perform_action({
            "browser_session_id": "sess-1",
            "action": "new_tab",
            "action_args": {"url": "https://example.com"},
        })

        assert result["status"] == "completed"
        adapter.new_tab.assert_awaited_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_new_tab_no_url_succeeds(self, runtime: GatewayBrowserRuntime) -> None:
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        adapter.get_page_state = AsyncMock(return_value={})
        adapter.new_tab = AsyncMock(return_value=1)
        runtime._adapter = adapter

        result = await runtime.perform_action({
            "browser_session_id": "sess-1",
            "action": "new_tab",
            "action_args": {},
        })

        assert result["status"] == "completed"
        adapter.new_tab.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_new_tab_blocked_url_raises(self, runtime: GatewayBrowserRuntime) -> None:
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        runtime._adapter = adapter

        with pytest.raises(RuntimeError, match="loopback"):
            await runtime.perform_action({
                "browser_session_id": "sess-1",
                "action": "new_tab",
                "action_args": {"url": "http://127.0.0.1:3000"},
            })

    @pytest.mark.asyncio
    async def test_download_file_allowed_url_succeeds(self, runtime: GatewayBrowserRuntime) -> None:
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        adapter.get_page_state = AsyncMock(return_value={})
        adapter.download_file = AsyncMock(return_value="/tmp/file.pdf")
        runtime._adapter = adapter

        result = await runtime.perform_action({
            "browser_session_id": "sess-1",
            "action": "download_file",
            "action_args": {"url": "https://example.com/file.pdf", "save_path": "/tmp/file.pdf"},
        })

        assert result["status"] == "completed"
        adapter.download_file.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_file_blocked_url_raises(self, runtime: GatewayBrowserRuntime) -> None:
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        runtime._adapter = adapter

        with pytest.raises(RuntimeError, match="loopback_host"):
            await runtime.perform_action({
                "browser_session_id": "sess-1",
                "action": "download_file",
                "action_args": {"url": "http://localhost:3000/file.pdf", "save_path": "/tmp/file.pdf"},
            })

    @pytest.mark.asyncio
    async def test_start_session_public_url_allowed(self, runtime: GatewayBrowserRuntime) -> None:
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        adapter.navigate = AsyncMock(
            return_value={"url": "https://example.com", "title": "Ex", "status_code": 200}
        )
        adapter.get_page_state = AsyncMock(return_value={"url": "https://example.com"})
        runtime._adapter = adapter

        result = await runtime.start_session({
            "url": "https://example.com",
            "session_profile": "test",
        })

        assert result["status"] == "started"
        adapter.navigate.assert_awaited_once_with("https://example.com")

    @pytest.mark.asyncio
    async def test_start_session_blocked_url_raises(self, runtime: GatewayBrowserRuntime) -> None:
        adapter = AsyncMock()
        runtime._adapter = adapter

        with pytest.raises(RuntimeError, match="loopback_host"):
            await runtime.start_session({
                "url": "http://localhost:3000",
                "session_profile": "test",
            })

    @pytest.mark.asyncio
    async def test_observe_action_not_affected(self, runtime: GatewayBrowserRuntime) -> None:
        """Non-URL actions like observe should still work."""
        self._setup_session(runtime)
        adapter = AsyncMock()
        adapter.configure_session = AsyncMock(return_value={"attach_state": "not_attached"})
        adapter.get_page_state = AsyncMock(return_value={"url": "https://example.com"})
        adapter.observe = AsyncMock(
            return_value={"url": "https://example.com", "title": "Ex", "text": "hello"}
        )
        runtime._adapter = adapter

        result = await runtime.perform_action({
            "browser_session_id": "sess-1",
            "action": "observe",
            "action_args": {},
        })

        assert result["status"] == "completed"
        adapter.observe.assert_awaited_once()
