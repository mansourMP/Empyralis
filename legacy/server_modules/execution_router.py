from __future__ import annotations

"""Authorized execution adapter for Python-owned browser automation.

Rust owns the trusted local device-control boundary. Browser automation remains
intentionally Python-owned, but all live execution must flow through this
adapter so capability_registry and policy_service gating remain the single
chokepoint.
"""

from typing import Any, Dict, Optional


_ALLOWED_BROWSER_METHODS = {
    "configure_session",
    "navigate",
    "screenshot",
    "observe",
    "click",
    "wait_for_selector",
    "fill",
    "select",
    "upload_files",
    "extract_text",
    "extract_dom",
    "execute_js",
    "get_page_state",
    "list_links",
    "list_tabs",
    "new_tab",
    "switch_tab",
    "close_tab",
    "download_file",
    "start_intercept",
    "stop_intercept",
    "intercept_requests",
    "print_to_pdf",
    "save_pdf",
    "inject_credentials",
    "close",
}


def browser_execution_binding_argv() -> list[str]:
    return ["-m", "server_modules.execution_router"]


def enforce_browser_execution_gate(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    target: str = "local_companion",
) -> None:
    from server_modules.browser_engine import enforce_browser_automation_gate

    enforce_browser_automation_gate(metadata, target=target)


class BrowserExecutionAdapter:
    __empyralis_browser_adapter__ = True

    def __init__(
        self,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        target: str = "local_companion",
    ) -> None:
        self._metadata = dict(metadata or {})
        self._target = str(target or "local_companion").strip() or "local_companion"
        self._engine: Any = None

    def _require_allowed_method(self, method_name: str) -> str:
        token = str(method_name or "").strip()
        if token not in _ALLOWED_BROWSER_METHODS:
            raise RuntimeError(f"Unsupported browser adapter action '{token}'.")
        return token

    def _ensure_engine(self) -> Any:
        enforce_browser_execution_gate(self._metadata, target=self._target)
        if self._engine is None:
            from server_modules.browser_engine import BrowserEngine

            self._engine = BrowserEngine()
        return self._engine

    def run_sync(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        token = self._require_allowed_method(method_name)
        return self._ensure_engine().run_sync(token, *args, **kwargs)

    async def call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        token = self._require_allowed_method(method_name)
        method = getattr(self._ensure_engine(), token)
        return await method(*args, **kwargs)

    async def navigate(self, url: str) -> Dict[str, Any]:
        return await self.call("navigate", url)

    async def configure_session(
        self,
        session_mode: Optional[str] = None,
        attach_endpoint_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.call("configure_session", session_mode, attach_endpoint_url)

    async def screenshot(self, selector: Optional[str] = None) -> str:
        return await self.call("screenshot", selector)

    async def observe(self) -> Dict[str, Any]:
        return await self.call("observe")

    async def click(self, selector: str) -> Dict[str, Any]:
        return await self.call("click", selector)

    async def wait_for_selector(self, selector: str, timeout_ms: int = 45_000) -> Dict[str, Any]:
        return await self.call("wait_for_selector", selector, timeout_ms)

    async def fill(self, selector: str, value: str) -> Dict[str, Any]:
        return await self.call("fill", selector, value)

    async def select(self, selector: str, value: str) -> Dict[str, Any]:
        return await self.call("select", selector, value)

    async def upload_files(self, selector: str, paths: list[str]) -> Dict[str, Any]:
        return await self.call("upload_files", selector, paths)

    async def extract_text(self, selector: Optional[str] = None) -> str:
        return await self.call("extract_text", selector)

    async def extract_dom(self, selector: Optional[str] = None) -> str:
        return await self.call("extract_dom", selector)

    async def get_page_state(self) -> Dict[str, Any]:
        return await self.call("get_page_state")

    async def list_links(self) -> list[dict[str, str]]:
        return await self.call("list_links")

    async def list_tabs(self) -> list[dict[str, Any]]:
        return await self.call("list_tabs")

    async def execute_js(self, script: str) -> Any:
        return await self.call("execute_js", script)

    async def new_tab(self, url: Optional[str] = None) -> int:
        return await self.call("new_tab", url)

    async def switch_tab(self, tab_id: int) -> None:
        await self.call("switch_tab", tab_id)

    async def close_tab(self, tab_id: int) -> None:
        await self.call("close_tab", tab_id)

    async def download_file(self, url: str, save_path: Optional[str] = None) -> str:
        return await self.call("download_file", url, save_path)

    async def start_intercept(self, url_pattern: str = "*") -> None:
        await self.call("start_intercept", url_pattern)

    async def stop_intercept(self) -> list[dict[str, Any]]:
        return await self.call("stop_intercept")

    async def intercept_requests(self, url_pattern: str) -> list[dict[str, Any]]:
        return await self.call("intercept_requests", url_pattern)

    async def print_to_pdf(self, output_path: Optional[str] = None) -> str:
        return await self.call("print_to_pdf", output_path)

    async def save_pdf(self, output_path: Optional[str] = None) -> str:
        return await self.call("save_pdf", output_path)

    async def inject_credentials(self, url_pattern: str, username: str, password: str) -> None:
        await self.call("inject_credentials", url_pattern, username, password)

    async def close(self) -> None:
        await self.call("close")
        self._engine = None

    def close_sync(self) -> None:
        engine = self._engine
        if engine is None:
            return
        engine.close_sync()
        self._engine = None


def get_browser_adapter(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    target: str = "local_companion",
) -> BrowserExecutionAdapter:
    return BrowserExecutionAdapter(metadata, target=target)


def get_browser_engine(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    target: str = "local_companion",
) -> Any:
    return get_browser_adapter(metadata, target=target)
