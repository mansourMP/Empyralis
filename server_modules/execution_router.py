from __future__ import annotations

"""Authorized execution adapter for Python-owned headless-fetch rendering.

Rust owns the trusted local device-control boundary. Rendering a JS page
remains intentionally Python-owned, but all live execution must flow through
this adapter so capability_registry and policy_service gating remain the
single chokepoint.
"""

from typing import Any, Dict, Optional


_ALLOWED_BROWSER_METHODS = {
    "navigate",
    "extract_text",
    "extract_dom",
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

    async def extract_text(self, selector: Optional[str] = None) -> str:
        return await self.call("extract_text", selector)

    async def extract_dom(self, selector: Optional[str] = None) -> str:
        return await self.call("extract_dom", selector)

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
