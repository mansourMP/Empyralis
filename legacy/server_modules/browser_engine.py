from __future__ import annotations

import asyncio
import base64
import fnmatch
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Python-owned browser automation adapter. This is the permanent DOM-aware
# browser/session exception boundary and must only be called through the
# capability-gated execution router. Direct imports outside execution_router
# are forbidden.

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


def _now_token() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 21)].rstrip() + "\n\n[truncated]"


BROWSER_SESSION_MODE_MANAGED_PROFILE = "managed_profile"
BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH = "existing_session_attach"


def _normalize_browser_session_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH:
        return BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH
    return BROWSER_SESSION_MODE_MANAGED_PROFILE


def _resolve_attach_endpoint_url(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    if token:
        return token
    env_token = str(os.environ.get("ORION_BROWSER_ATTACH_CDP_URL") or "").strip()
    return env_token or None


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
        self.screenshots_dir = (engine_root / "screenshots").resolve()
        self.downloads_dir = (engine_root / "downloads").resolve()
        self.pdf_dir = (engine_root / "pdf").resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._tabs: Dict[int, Any] = {}
        self._page_to_tab_id: Dict[int, int] = {}
        self._active_tab_id: Optional[int] = None
        self._next_tab_id = 1
        self._intercepts: Dict[str, List[Dict[str, Any]]] = {}
        self._active_intercept_pattern: Optional[str] = None
        self._response_listener_registered = False
        self._console_listener_registered = False
        self._network_listener_registered = False
        self._console_entries: List[Dict[str, Any]] = []
        self._network_failures: List[Dict[str, Any]] = []
        self._credential_injections: List[Dict[str, str]] = []
        self._session_mode = BROWSER_SESSION_MODE_MANAGED_PROFILE
        self._attach_endpoint_url: Optional[str] = None
        self._attach_state = "not_attached"
        self._attach_failure: Optional[str] = None
        self._emulation: Dict[str, Any] = {}
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

    async def _reset_runtime(self, *, reset_session_mode: bool = False) -> None:
        session_mode = self._session_mode
        context = self._context
        browser = self._browser
        playwright = self._playwright
        self._context = None
        self._browser = None
        self._playwright = None
        self._tabs = {}
        self._page_to_tab_id = {}
        self._active_tab_id = None
        self._intercepts = {}
        self._active_intercept_pattern = None
        self._response_listener_registered = False
        if context is not None and session_mode == BROWSER_SESSION_MODE_MANAGED_PROFILE:
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
        if reset_session_mode:
            self._session_mode = BROWSER_SESSION_MODE_MANAGED_PROFILE
            self._attach_endpoint_url = None
            self._attach_state = "not_attached"
            self._attach_failure = None

    async def configure_session(
        self,
        session_mode: Optional[str] = None,
        attach_endpoint_url: Optional[str] = None,
        emulation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_mode = _normalize_browser_session_mode(session_mode)
        resolved_attach_endpoint = (
            _resolve_attach_endpoint_url(attach_endpoint_url)
            if resolved_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH
            else None
        )
        self._emulation = dict(emulation or {})
        mode_changed = resolved_mode != self._session_mode
        endpoint_changed = resolved_attach_endpoint != self._attach_endpoint_url
        if mode_changed or endpoint_changed:
            await self._reset_runtime(reset_session_mode=False)
        self._session_mode = resolved_mode
        self._attach_endpoint_url = resolved_attach_endpoint
        self._attach_failure = None
        self._attach_state = "not_attached" if resolved_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH else "not_attached"
        if resolved_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH and not resolved_attach_endpoint:
            self._attach_state = "attach_required"
            return await self.session_state()
        try:
            await self._ensure_started()
        except Exception as exc:
            if resolved_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH and self._attach_state not in {
                "attach_required",
                "not_attached",
            }:
                self._attach_state = "attach_failed"
            self._attach_failure = str(exc)
            return await self.session_state()
        if resolved_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH:
            self._attach_state = "attached"
            self._attach_failure = None
        return await self.session_state()

    async def session_state(self) -> Dict[str, Any]:
        current_url: Optional[str] = None
        if self._context is not None:
            try:
                page = await self._active_page()
                current_url = str(getattr(page, "url", "") or "").strip() or None
            except Exception:
                current_url = None
        if self._session_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH:
            status = self._attach_state
        else:
            status = "active"
        return {
            "session_mode": self._session_mode,
            "attach_endpoint_url": self._attach_endpoint_url,
            "attach_state": self._attach_state,
            "attach_failure": self._attach_failure,
            "status": status,
            "current_url": current_url,
            "emulation": dict(self._emulation),
        }

    async def _ensure_started(self) -> None:
        if self._context is not None:
            if self._session_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH and self._browser is not None:
                try:
                    if not self._browser.is_connected():
                        self._attach_state = "not_attached"
                        self._attach_failure = "Attached browser session is no longer connected."
                        await self._reset_runtime(reset_session_mode=False)
                    else:
                        return
                except Exception:
                    self._attach_state = "not_attached"
                    self._attach_failure = "Attached browser session is no longer connected."
                    await self._reset_runtime(reset_session_mode=False)
            else:
                return
        async_playwright = self._load_playwright()
        self._playwright = await async_playwright().start()
        try:
            if self._session_mode == BROWSER_SESSION_MODE_EXISTING_SESSION_ATTACH:
                endpoint_url = str(self._attach_endpoint_url or "").strip()
                if not endpoint_url:
                    self._attach_state = "attach_required"
                    raise RuntimeError(
                        "Existing-session browser attach requires a Chrome DevTools endpoint. "
                        "Provide attach_endpoint_url or ORION_BROWSER_ATTACH_CDP_URL."
                    )
                try:
                    self._browser = await self._playwright.chromium.connect_over_cdp(
                        endpoint_url,
                        timeout=5_000,
                    )
                except Exception as exc:
                    self._attach_state = "attach_failed"
                    raise RuntimeError(
                        f"Failed to attach to existing browser session at {endpoint_url}."
                    ) from exc
                if self._browser is None or not self._browser.is_connected():
                    self._attach_state = "not_attached"
                    raise RuntimeError("Existing browser session is not currently attachable.")
                contexts = list(getattr(self._browser, "contexts", []) or [])
                if not contexts:
                    self._attach_state = "not_attached"
                    raise RuntimeError("Attached browser did not expose a default context.")
                self._context = contexts[0]
                self._attach_state = "attached"
                self._attach_failure = None
            else:
                viewport = {
                    "width": int(self._emulation.get("viewport_width", 1440)),
                    "height": int(self._emulation.get("viewport_height", 960)),
                }
                color_scheme = str(self._emulation.get("color_scheme", "")).strip().lower()
                if color_scheme not in ("dark", "light", "no-preference"):
                    color_scheme = ""
                context_kwargs: Dict[str, Any] = {
                    "user_data_dir": str(self.profile_dir),
                    "headless": bool(self.headless),
                    "accept_downloads": True,
                    "viewport": viewport,
                }
                if color_scheme:
                    context_kwargs["color_scheme"] = color_scheme
                locale = str(self._emulation.get("locale", "")).strip()
                if locale:
                    context_kwargs["locale"] = locale
                timezone_id = str(self._emulation.get("timezone", "")).strip()
                if timezone_id:
                    context_kwargs["timezone_id"] = timezone_id
                geolocation = self._emulation.get("geolocation")
                if isinstance(geolocation, dict) and geolocation.get("latitude") is not None:
                    context_kwargs["geolocation"] = {
                        "latitude": float(geolocation["latitude"]),
                        "longitude": float(geolocation.get("longitude", 0)),
                        "accuracy": int(geolocation.get("accuracy", 100)),
                    }
                device_scale_factor = self._emulation.get("device_scale_factor")
                if device_scale_factor is not None:
                    context_kwargs["device_scale_factor"] = float(device_scale_factor)
                user_agent = str(self._emulation.get("user_agent", "")).strip()
                if user_agent:
                    context_kwargs["user_agent"] = user_agent
                extra_headers = self._emulation.get("extra_http_headers")
                if isinstance(extra_headers, dict) and extra_headers:
                    context_kwargs["extra_http_headers"] = {
                        str(k): str(v) for k, v in extra_headers.items()
                    }
                offline = bool(self._emulation.get("offline", False))
                if offline:
                    context_kwargs["offline"] = True
                self._context = await self._playwright.chromium.launch_persistent_context(**context_kwargs)
                self._attach_state = "not_attached"
                self._attach_failure = None
        except Exception:
            await self._reset_runtime(reset_session_mode=False)
            raise
        for page in list(getattr(self._context, "pages", []) or []):
            self._register_page(page)
        if self._active_tab_id is None:
            page = await self._context.new_page()
            self._register_page(page)
        if not self._response_listener_registered:
            self._context.on("response", self._schedule_response_capture)
            self._response_listener_registered = True

    def _schedule_response_capture(self, response: Any) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._capture_response(response))

    async def _capture_response(self, response: Any) -> None:
        url = str(getattr(response, "url", "") or "").strip()
        if not url:
            return
        request = response.request
        method = str(getattr(request, "method", "") or "").strip()
        status = int(getattr(response, "status", 0) or 0)
        matched_patterns = [
            pattern
            for pattern in self._intercepts
            if pattern and (pattern == "*" or fnmatch.fnmatch(url, pattern) or pattern in url)
        ]
        if not matched_patterns:
            return
        body_text = ""
        try:
            body_text = await response.text()
        except Exception:
            body_text = ""
        record = {
            "url": url,
            "method": method,
            "status": status,
            "response_body": _truncate_text(body_text, 10 * 1024),
        }
        for pattern in matched_patterns:
            self._intercepts.setdefault(pattern, []).append(record)

    def _register_page(self, page: Any) -> int:
        page_key = id(page)
        tab_id = self._page_to_tab_id.get(page_key)
        if tab_id is not None:
            self._tabs[tab_id] = page
            if self._active_tab_id is None:
                self._active_tab_id = tab_id
            return tab_id
        tab_id = self._next_tab_id
        self._next_tab_id += 1
        self._tabs[tab_id] = page
        self._page_to_tab_id[page_key] = tab_id
        self._active_tab_id = tab_id

        if not self._console_listener_registered:
            page.on("console", self._on_console_message)
            self._console_listener_registered = True

        if not self._network_listener_registered:
            page.on("requestfailed", self._on_request_failed)
            self._network_listener_registered = True

        return tab_id

    async def _active_page(self) -> Any:
        await self._ensure_started()
        if self._active_tab_id is not None:
            page = self._tabs.get(self._active_tab_id)
            if page is not None and not page.is_closed():
                return page
        for tab_id, page in list(self._tabs.items()):
            if page is not None and not page.is_closed():
                self._active_tab_id = tab_id
                return page
        page = await self._context.new_page()
        self._register_page(page)
        return page

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

    async def _apply_saved_credentials(self, page: Any, url: str) -> None:
        for item in self._credential_injections:
            pattern = str(item.get("url_pattern") or "").strip()
            if not pattern or pattern not in url:
                continue
            username = str(item.get("username") or "").strip()
            password = str(item.get("password") or "").strip()
            if username:
                for selector in (
                    'input[type="email"]',
                    'input[name="email"]',
                    'input[name="username"]',
                    'input[name="user"]',
                    'input[autocomplete="username"]',
                ):
                    try:
                        locator = page.locator(selector).first
                        if await locator.count() > 0:
                            await locator.fill(username)
                            break
                    except Exception:
                        continue
            if password:
                for selector in (
                    'input[type="password"]',
                    'input[name="password"]',
                    'input[autocomplete="current-password"]',
                ):
                    try:
                        locator = page.locator(selector).first
                        if await locator.count() > 0:
                            await locator.fill(password)
                            break
                    except Exception:
                        continue

    async def navigate(self, url: str) -> Dict[str, Any]:
        target_url = str(url or "").strip()
        assert_safe_outbound_url(target_url)  # defense-in-depth
        page = await self._active_page()
        response = await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")
        final_url = str(getattr(page, "url", target_url) or target_url).strip()
        await self._apply_saved_credentials(page, final_url)
        title = await page.title()
        status_code = int(response.status) if response is not None else 0
        return {"url": final_url, "title": title, "status_code": status_code}

    async def screenshot(self, selector: Optional[str] = None) -> str:
        page = await self._active_page()
        target = self.screenshots_dir / f"browser_{_now_token()}.png"
        if selector:
            locator = await self._resolve_locator(page, selector)
            await locator.screenshot(path=str(target))
        else:
            await page.screenshot(path=str(target), full_page=True)
        return str(target)

    async def click(self, selector: str) -> Dict[str, Any]:
        page = await self._active_page()
        locator = await self._resolve_locator(page, selector)
        text = ""
        try:
            text = (await locator.inner_text(timeout=1500)).strip()
        except Exception:
            text = ""
        await locator.click()
        return {"clicked": True, "element_text": text}

    async def wait_for_selector(self, selector: str, timeout_ms: int = 45_000) -> Dict[str, Any]:
        page = await self._active_page()
        target = str(selector or "").strip()
        if not target:
            raise RuntimeError("Selector is required.")
        await page.wait_for_selector(target, timeout=max(1000, int(timeout_ms or 45_000)))
        return {"waited": True, "selector": target}

    async def fill(self, selector: str, value: str) -> Dict[str, Any]:
        page = await self._active_page()
        locator = await self._resolve_locator(page, selector)
        await locator.fill(str(value or ""))
        return {"filled": True, "value": str(value or "")}

    async def select(self, selector: str, value: str) -> Dict[str, Any]:
        page = await self._active_page()
        locator = await self._resolve_locator(page, selector)
        await locator.select_option(str(value or ""))
        return {"selected": True, "value": str(value or "")}

    async def upload_files(self, selector: str, paths: List[str]) -> Dict[str, Any]:
        page = await self._active_page()
        locator = await self._resolve_locator(page, selector)
        await locator.set_input_files(paths)
        return {"uploaded": True, "paths": list(paths)}

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

    async def execute_js(self, script: str) -> Any:
        page = await self._active_page()
        return await page.evaluate(str(script or ""))

    async def get_page_state(self) -> Dict[str, Any]:
        page = await self._active_page()
        title = await page.title()
        text = await page.locator("body").inner_text()
        interactive_elements = await page.evaluate(
            """() => Array.from(
                document.querySelectorAll('a,button,input,textarea,select,[role="button"]')
            ).slice(0, 80).map((el, index) => ({
                index,
                tag: (el.tagName || '').toLowerCase(),
                text: (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 200),
                type: (el.getAttribute('type') || '').trim(),
                href: (el instanceof HTMLAnchorElement ? (el.href || '') : ''),
                name: (el.getAttribute('name') || '').trim(),
                placeholder: (el.getAttribute('placeholder') || '').trim(),
                id: (el.getAttribute('id') || '').trim(),
            }))"""
        )
        return {
            "url": str(page.url or "").strip(),
            "title": title,
            "text_preview": _truncate_text(text, 2000),
            "interactive_elements": interactive_elements if isinstance(interactive_elements, list) else [],
        }

    async def observe(self) -> Dict[str, Any]:
        state = await self.get_page_state()
        screenshot_path = await self.screenshot()
        screenshot_base64 = ""
        try:
            screenshot_base64 = base64.b64encode(Path(screenshot_path).read_bytes()).decode("ascii")
        except Exception:
            screenshot_base64 = ""
        return {
            "url": state.get("url"),
            "title": state.get("title"),
            "text": _truncate_text(state.get("text_preview") or "", 3000),
            "interactive": list(state.get("interactive_elements") or []),
            "interactive_elements": list(state.get("interactive_elements") or []),
            "screenshot_path": screenshot_path,
            "screenshot_base64": screenshot_base64,
        }

    async def list_links(self) -> List[Dict[str, str]]:
        page = await self._active_page()
        links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).slice(0, 100).map((el) => ({
                href: (el.href || '').trim(),
                text: (el.innerText || el.textContent || '').trim().slice(0, 200),
                title: (el.getAttribute('title') || '').trim().slice(0, 200),
            }))"""
        )
        return links if isinstance(links, list) else []

    async def list_tabs(self) -> List[Dict[str, Any]]:
        await self._ensure_started()
        snapshots: List[Dict[str, Any]] = []
        for tab_id, page in list(self._tabs.items()):
            if page is None or page.is_closed():
                continue
            try:
                title = await page.title()
            except Exception:
                title = ""
            snapshots.append(
                {
                    "tabId": str(tab_id),
                    "url": str(page.url or "").strip(),
                    "title": title,
                    "active": tab_id == self._active_tab_id,
                }
            )
        return snapshots

    async def new_tab(self, url: Optional[str] = None) -> int:
        await self._ensure_started()
        page = await self._context.new_page()
        tab_id = self._register_page(page)
        if url:
            target_url = str(url).strip()
            assert_safe_outbound_url(target_url)  # defense-in-depth
            await page.goto(target_url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle")
        return tab_id

    async def switch_tab(self, tab_id: int) -> None:
        await self._ensure_started()
        target_id = int(tab_id)
        page = self._tabs.get(target_id)
        if page is None or page.is_closed():
            raise RuntimeError(f"Tab {target_id} is not available.")
        self._active_tab_id = target_id
        await page.bring_to_front()

    async def close_tab(self, tab_id: int) -> None:
        await self._ensure_started()
        target_id = int(tab_id)
        page = self._tabs.pop(target_id, None)
        if page is None:
            return
        self._page_to_tab_id.pop(id(page), None)
        if not page.is_closed():
            await page.close()
        if self._active_tab_id == target_id:
            self._active_tab_id = next(iter(self._tabs.keys()), None)

    async def download_file(self, url: str, save_path: Optional[str] = None) -> str:
        page = await self._active_page()
        async with page.expect_download() as download_info:
            await page.goto(str(url or "").strip(), wait_until="domcontentloaded")
        download = await download_info.value
        target = Path(save_path).expanduser() if save_path else self.downloads_dir / (download.suggested_filename or f"download_{_now_token()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(str(target))
        return str(target.resolve())

    async def start_intercept(self, url_pattern: str = "*") -> None:
        await self._ensure_started()
        token = str(url_pattern or "*").strip() or "*"
        self._active_intercept_pattern = token
        self._intercepts[token] = []

    async def stop_intercept(self) -> List[Dict[str, Any]]:
        await self._ensure_started()
        token = str(self._active_intercept_pattern or "").strip()
        if not token:
            return []
        records = list(self._intercepts.get(token) or [])
        self._intercepts.pop(token, None)
        self._active_intercept_pattern = None
        return records

    async def intercept_requests(self, url_pattern: str) -> List[Dict[str, Any]]:
        await self._ensure_started()
        token = str(url_pattern or "").strip()
        if token and token not in self._intercepts:
            self._intercepts[token] = []
        return list(self._intercepts.get(token) or [])

    async def print_to_pdf(self, output_path: Optional[str] = None) -> str:
        page = await self._active_page()
        target = Path(output_path).expanduser() if output_path else self.pdf_dir / f"browser_{_now_token()}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        await page.pdf(path=str(target))
        return str(target.resolve())

    async def save_pdf(self, output_path: Optional[str] = None) -> str:
        return await self.print_to_pdf(output_path)

    async def inject_credentials(self, url_pattern: str, username: str, password: str) -> None:
        token = str(url_pattern or "").strip()
        if not token:
            raise RuntimeError("url_pattern is required.")
        self._credential_injections = [
            item for item in self._credential_injections if str(item.get("url_pattern") or "").strip() != token
        ]
        self._credential_injections.append(
            {
                "url_pattern": token,
                "username": str(username or ""),
                "password": str(password or ""),
            }
        )

    # ── Runtime introspection ──────────────────────────────────────────

    def _on_console_message(self, msg: Any) -> None:
        try:
            entry = {
                "type": str(getattr(msg, "type", "") or ""),
                "text": str(getattr(msg, "text", "") or "")[:4000],
                "location": str(getattr(msg, "location", "") or ""),
                "timestamp": _now_token(),
            }
            self._console_entries.append(entry)
            if len(self._console_entries) > 200:
                self._console_entries = self._console_entries[-100:]
        except Exception:
            pass

    def _on_request_failed(self, request: Any) -> None:
        try:
            failure = request.failure if hasattr(request, "failure") else None
            failure_text = str(failure or "") if failure else "unknown"
            entry = {
                "url": str(getattr(request, "url", "") or "")[:2000],
                "method": str(getattr(request, "method", "") or ""),
                "failure": failure_text[:500],
                "timestamp": _now_token(),
            }
            self._network_failures.append(entry)
            if len(self._network_failures) > 100:
                self._network_failures = self._network_failures[-50:]
        except Exception:
            pass

    def console_logs(self, *, level: str = "") -> List[Dict[str, Any]]:
        entries = list(self._console_entries)
        if level:
            return [e for e in entries if e.get("type") == str(level).strip().lower()]
        return entries

    def network_failures(self) -> List[Dict[str, Any]]:
        return list(self._network_failures)

    # ── Cookies & Storage ─────────────────────────────────────────────

    async def get_cookies(self, url: str = "") -> List[Dict[str, Any]]:
        await self._ensure_started()
        cookies = await self._context.cookies(url or None) if self._context else []
        return [
            {
                "name": str(c.get("name", "")),
                "value": str(c.get("value", "")),
                "domain": str(c.get("domain", "")),
                "path": str(c.get("path", "/")),
                "http_only": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", False)),
            }
            for c in (cookies or [])
        ]

    async def set_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        await self._ensure_started()
        if self._context:
            await self._context.add_cookies([
                {
                    "name": str(c.get("name", "")),
                    "value": str(c.get("value", "")),
                    "domain": str(c.get("domain", "")),
                    "path": str(c.get("path", "/")),
                    "httpOnly": bool(c.get("http_only", False)),
                    "secure": bool(c.get("secure", False)),
                }
                for c in cookies
                if c.get("name") and c.get("domain")
            ])

    async def clear_cookies(self) -> None:
        await self._ensure_started()
        if self._context:
            await self._context.clear_cookies()

    async def get_storage(self, kind: str = "local") -> Dict[str, Any]:
        page = await self._active_page()
        if kind == "session":
            raw = await page.evaluate("() => JSON.stringify(sessionStorage)")
        else:
            raw = await page.evaluate("() => JSON.stringify(localStorage)")
        import json as _json
        try:
            return _json.loads(str(raw or "{}"))
        except Exception:
            return {}

    async def set_storage_item(self, key: str, value: str, kind: str = "local") -> None:
        page = await self._active_page()
        safe_key = str(key or "").strip()
        safe_value = str(value or "")
        storage_api = "sessionStorage" if kind == "session" else "localStorage"
        await page.evaluate(
            f"() => {{ {storage_api}.setItem({_json_dumps(safe_key)}, {_json_dumps(safe_value)}) }}"
        )

    async def clear_storage(self, kind: str = "local") -> None:
        page = await self._active_page()
        storage_api = "sessionStorage" if kind == "session" else "localStorage"
        await page.evaluate(f"() => {{ {storage_api}.clear() }}")

    # ── Accessibility ──────────────────────────────────────────────────

    async def snapshot_accessibility_tree(self) -> Dict[str, Any]:
        page = await self._active_page()
        try:
            tree = await page.accessibility.snapshot()
        except Exception:
            tree = None
        return {
            "available": tree is not None,
            "tree": tree if tree is not None else {},
        }

    async def close(self) -> None:
        await self._reset_runtime(reset_session_mode=True)


def _json_dumps(value: str) -> str:
    import json as _json
    return _json.dumps(str(value))
