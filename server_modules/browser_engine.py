from __future__ import annotations

import asyncio
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

# Python-owned headless-fetch adapter. This is the permanent DOM-aware
# rendering exception boundary and must only be called through the
# capability-gated execution router. Direct imports outside execution_router
# are forbidden.
#
# Scope is deliberately narrow: render a JS page and return its text/content.
# No click/type/session control — for anything else, use the shell/curl
# (http_request) tool.

from server_modules.url_security import assert_safe_outbound_url


def enforce_browser_automation_gate(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    target: str = "local_companion",
) -> None:
    from server_modules.capability_registry import resolve_capability
    from server_modules import policy_service

    if resolve_capability("browser_automation.interactive") is None:
        raise RuntimeError("Browser automation capability is disabled.")
    evaluation = policy_service.evaluate_tool_policy_decision(
        tool_id="browser_automation.interactive",
        trust_mode=str((metadata or {}).get("trust_mode") or "reviewed"),
        target=target,
        metadata=metadata or {},
    )
    if str(evaluation.get("execution_decision") or "").strip().lower() == "deny":
        raise RuntimeError(str(evaluation.get("reason") or "Browser automation is blocked by policy."))

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _engine_root() -> Path:
    configured = str(os.environ.get("ORION_BROWSER_ENGINE_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (_repo_root() / ".orion-stack").resolve()


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 21)].rstrip() + "\n\n[truncated]"


class BrowserEngine:
    _instance: Optional["BrowserEngine"] = None
    _runner_loop: Any = None
    _runner_thread: Optional[threading.Thread] = None
    _runner_lock = threading.Lock()
    _runner_ready = threading.Event()

    def __new__(cls, *args: Any, **kwargs: Any) -> "BrowserEngine":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self.headless = not (os.environ.get("DISPLAY") or os.environ.get("TAURI_ENV"))
        engine_root = _engine_root()
        self.profile_dir = (engine_root / "browser-profile").resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._initialized = True

    def _load_playwright(self):
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError(
                "Playwright is not installed. Install it with `pip install playwright` and then run "
                "`python -m playwright install chromium`."
            ) from exc
        return async_playwright

    @classmethod
    def _ensure_runner_loop(cls) -> Any:
        with cls._runner_lock:
            loop = cls._runner_loop
            thread = cls._runner_thread
            if loop is not None and thread is not None and thread.is_alive():
                return loop
            cls._runner_ready = threading.Event()

            def _runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                cls._runner_loop = loop
                cls._runner_ready.set()
                loop.run_forever()
                pending = list(asyncio.all_tasks(loop))
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

            cls._runner_thread = threading.Thread(
                target=_runner,
                name="BrowserEngineLoop",
                daemon=True,
            )
            cls._runner_thread.start()
        cls._runner_ready.wait(timeout=5)
        if cls._runner_loop is None:
            raise RuntimeError("Browser runner loop failed to start.")
        return cls._runner_loop

    def run_sync(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        loop = self._ensure_runner_loop()

        async def _invoke() -> Any:
            method = getattr(self, method_name)
            return await method(*args, **kwargs)

        future = asyncio.run_coroutine_threadsafe(_invoke(), loop)
        return future.result()

    def close_sync(self) -> None:
        loop = self._runner_loop
        thread = self._runner_thread
        if loop is None or thread is None:
            return
        future = asyncio.run_coroutine_threadsafe(self.close(), loop)
        future.result()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        with self._runner_lock:
            self.__class__._runner_loop = None
            self.__class__._runner_thread = None

    async def _reset_runtime(self) -> None:
        context = self._context
        browser = self._browser
        playwright = self._playwright
        self._context = None
        self._browser = None
        self._playwright = None
        self._page = None
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def _ensure_started(self) -> None:
        if self._context is not None:
            return
        async_playwright = self._load_playwright()
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=bool(self.headless),
                accept_downloads=False,
                viewport={"width": 1440, "height": 960},
            )
        except Exception:
            await self._reset_runtime()
            raise
        pages = list(getattr(self._context, "pages", []) or [])
        self._page = pages[0] if pages else await self._context.new_page()

    async def _active_page(self) -> Any:
        await self._ensure_started()
        if self._page is not None and not self._page.is_closed():
            return self._page
        self._page = await self._context.new_page()
        return self._page

    async def _resolve_locator(self, page: Any, selector: str) -> Any:
        target = str(selector or "").strip()
        if not target:
            raise RuntimeError("Selector is required.")
        if target.startswith("//") or target.startswith("(//"):
            return page.locator(f"xpath={target}")
        locator = page.locator(target)
        try:
            if await locator.count() > 0:
                return locator.first
        except Exception:
            pass
        text_locator = page.get_by_text(target, exact=False)
        try:
            if await text_locator.count() > 0:
                return text_locator.first
        except Exception:
            pass
        return locator.first

    async def navigate(self, url: str) -> Dict[str, Any]:
        target_url = str(url or "").strip()
        assert_safe_outbound_url(target_url)  # defense-in-depth
        page = await self._active_page()
        response = await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")
        final_url = str(getattr(page, "url", target_url) or target_url).strip()
        title = await page.title()
        status_code = int(response.status) if response is not None else 0
        return {"url": final_url, "title": title, "status_code": status_code}

    async def extract_text(self, selector: Optional[str] = None) -> str:
        page = await self._active_page()
        if selector:
            locator = await self._resolve_locator(page, selector)
            return str(await locator.inner_text())
        return str(await page.locator("body").inner_text())

    async def extract_dom(self, selector: Optional[str] = None) -> str:
        page = await self._active_page()
        if selector:
            locator = await self._resolve_locator(page, selector)
            html = str(await locator.evaluate("(node) => node.outerHTML"))
        else:
            html = str(await page.content())
        cleaned = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<style\b[^>]*>.*?</style>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
        return _truncate_text(cleaned, 50 * 1024)

    async def close(self) -> None:
        await self._reset_runtime()
