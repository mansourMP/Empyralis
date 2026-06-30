import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules.browser_engine import BrowserEngine


class _FakeLocator:
    def __init__(self, text: str = "", html: str = "", count: int = 1):
        self._text = text
        self._html = html or f"<div>{text}</div>"
        self._count = count
        self.first = self

    async def count(self):
        return self._count

    async def inner_text(self, timeout: int = 0):
        return self._text

    async def screenshot(self, path: str):
        Path(path).write_bytes(b"fake-image")

    async def fill(self, value: str):
        self._text = value

    async def click(self):
        return None

    async def evaluate(self, _script: str):
        return self._html

    async def select_option(self, _value: str):
        return None

    async def set_input_files(self, _paths):
        return None


class _FakePage:
    def __init__(self):
        self.url = "https://example.com/"
        self._title = "Example Domain"
        self._body_text = "Example Domain\nThis domain is for use in illustrative examples."
        self._interactive = [
            {"index": 0, "tag": "a", "text": "More information", "type": "", "href": "https://www.iana.org/domains/example", "name": "", "placeholder": "", "id": ""},
            {"index": 1, "tag": "h1", "text": "Example Domain", "type": "", "href": "", "name": "", "placeholder": "", "id": "heading"},
        ]

    async def goto(self, url: str, wait_until: str = "domcontentloaded"):
        self.url = url

        class _Response:
            status = 200

        return _Response()

    async def wait_for_load_state(self, _state: str):
        return None

    async def title(self):
        return self._title

    def locator(self, selector: str):
        if selector == "body":
            return _FakeLocator(text=self._body_text, html=f"<body><h1>Example Domain</h1><p>{self._body_text}</p></body>")
        if selector == "h1":
            return _FakeLocator(text="Example Domain", html="<h1>Example Domain</h1>")
        return _FakeLocator(text="target")

    def get_by_text(self, selector: str, exact: bool = False):
        return _FakeLocator(text=selector)

    async def content(self):
        return "<html><body><h1>Example Domain</h1></body></html>"

    async def evaluate(self, script: str):
        if "querySelectorAll('a,button,input,textarea,select,[role=\"button\"]')" in script:
            return self._interactive
        if "querySelectorAll('a[href]')" in script:
            return [{"href": "https://www.iana.org/domains/example", "text": "More information", "title": ""}]
        return {"ok": True}

    async def screenshot(self, path: str, full_page: bool = True):
        Path(path).write_bytes(b"fake-image")

    def is_closed(self):
        return False

    async def bring_to_front(self):
        return None

    async def pdf(self, path: str):
        Path(path).write_bytes(b"%PDF-1.4")


class _FakeContext:
    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.events = []

    def on(self, event_name: str, _handler):
        self.events.append(event_name)

    async def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page

    async def close(self):
        return None


class _FakeBrowser:
    def __init__(self, contexts=None, connected: bool = True):
        self.contexts = list(contexts or [])
        self._connected = connected
        self.closed = False

    def is_connected(self):
        return self._connected

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, *, context=None, browser=None):
        self.context = context
        self.browser = browser
        self.launch_calls = []
        self.connect_calls = []

    async def launch_persistent_context(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self.context

    async def connect_over_cdp(self, endpoint_url: str, timeout: int = 0):
        self.connect_calls.append({"endpoint_url": endpoint_url, "timeout": timeout})
        if self.browser is None:
            raise RuntimeError("browser not configured")
        return self.browser


class _FakePlaywrightRuntime:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _FakePlaywrightBootstrap:
    def __init__(self, runtime):
        self.runtime = runtime

    async def start(self):
        return self.runtime


class _FakeRequest:
    method = "GET"


class _FakeResponse:
    def __init__(self, url: str, status: int = 200, body: str = '{"ok":true}'):
        self.url = url
        self.status = status
        self.request = _FakeRequest()
        self._body = body

    async def text(self):
        return self._body


class BrowserEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        BrowserEngine._instance = None
        self.engine = BrowserEngine()
        self.page = _FakePage()
        self.engine._context = object()
        self.engine._tabs = {1: self.page}
        self.engine._page_to_tab_id = {id(self.page): 1}
        self.engine._active_tab_id = 1
        self.engine._ensure_started = self._noop  # type: ignore[method-assign]
        self._tmpdir = tempfile.TemporaryDirectory(prefix="browser-engine-")
        self.engine.screenshots_dir = Path(self._tmpdir.name) / "shots"
        self.engine.downloads_dir = Path(self._tmpdir.name) / "downloads"
        self.engine.pdf_dir = Path(self._tmpdir.name) / "pdf"
        self.engine.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.engine.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.engine.pdf_dir.mkdir(parents=True, exist_ok=True)

    async def asyncTearDown(self):
        self._tmpdir.cleanup()
        BrowserEngine._instance = None

    async def _noop(self):
        return None

    async def test_navigate_returns_title(self):
        result = await self.engine.navigate("https://example.com")
        self.assertEqual(result["title"], "Example Domain")
        self.assertEqual(result["status_code"], 200)

    async def test_screenshot_creates_file(self):
        path = await self.engine.screenshot()
        self.assertTrue(Path(path).exists())

    async def test_extract_text_returns_content(self):
        text = await self.engine.extract_text()
        self.assertIn("Example Domain", text)

    async def test_get_page_state_includes_interactive_elements(self):
        state = await self.engine.get_page_state()
        self.assertEqual(state["title"], "Example Domain")
        self.assertTrue(any(item.get("tag") == "a" for item in state["interactive_elements"]))

    async def test_observe_returns_screenshot_and_text(self):
        observation = await self.engine.observe()
        self.assertEqual(observation["title"], "Example Domain")
        self.assertIn("Example Domain", observation["text"])
        self.assertTrue(Path(observation["screenshot_path"]).exists())
        self.assertTrue(observation["screenshot_base64"])

    async def test_observe_includes_interactive_elements(self):
        observation = await self.engine.observe()
        self.assertTrue(any(item.get("tag") == "a" for item in observation["interactive"]))

    async def test_intercept_captures_requests(self):
        await self.engine.start_intercept("*")
        await self.engine._capture_response(_FakeResponse("https://example.com/api/data"))
        records = await self.engine.stop_intercept()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], 200)
        self.assertEqual(records[0]["method"], "GET")

    async def test_save_pdf_creates_file(self):
        path = await self.engine.save_pdf()
        self.assertTrue(Path(path).exists())

    async def test_env_override_changes_engine_root(self):
        with tempfile.TemporaryDirectory(prefix="browser-engine-root-") as tmpdir:
            BrowserEngine._instance = None
            with patch.dict("os.environ", {"ORION_BROWSER_ENGINE_ROOT": tmpdir}, clear=False):
                engine = BrowserEngine()
            self.assertEqual(engine.profile_dir, (Path(tmpdir) / "browser-profile").resolve())
            self.assertEqual(engine.screenshots_dir, (Path(tmpdir) / "screenshots").resolve())
            BrowserEngine._instance = None

    async def test_configure_session_returns_attach_required_without_endpoint(self):
        state = await self.engine.configure_session("existing_session_attach", None)
        self.assertEqual(state["session_mode"], "existing_session_attach")
        self.assertEqual(state["status"], "attach_required")
        self.assertEqual(state["attach_state"], "attach_required")
        self.assertIsNone(self.engine._context)

    async def test_configure_session_uses_connect_over_cdp_for_existing_session_attach(self):
        BrowserEngine._instance = None
        engine = BrowserEngine()
        fake_context = _FakeContext([_FakePage()])
        fake_browser = _FakeBrowser([fake_context])
        fake_chromium = _FakeChromium(browser=fake_browser)
        fake_runtime = _FakePlaywrightRuntime(fake_chromium)

        engine._load_playwright = lambda: (lambda: _FakePlaywrightBootstrap(fake_runtime))  # type: ignore[method-assign]
        state = await engine.configure_session("existing_session_attach", "http://127.0.0.1:9222")

        self.assertEqual(state["session_mode"], "existing_session_attach")
        self.assertEqual(state["status"], "attached")
        self.assertEqual(state["attach_state"], "attached")
        self.assertEqual(fake_chromium.connect_calls[0]["endpoint_url"], "http://127.0.0.1:9222")
        self.assertEqual(engine._context, fake_context)
        self.assertIn("response", fake_context.events)

        await engine.close()
        BrowserEngine._instance = None

    async def test_configure_session_reports_attach_failed_when_cdp_connect_fails(self):
        BrowserEngine._instance = None
        engine = BrowserEngine()
        fake_chromium = _FakeChromium(browser=None)
        fake_runtime = _FakePlaywrightRuntime(fake_chromium)
        engine._load_playwright = lambda: (lambda: _FakePlaywrightBootstrap(fake_runtime))  # type: ignore[method-assign]

        state = await engine.configure_session("existing_session_attach", "http://127.0.0.1:9222")

        self.assertEqual(state["status"], "attach_failed")
        self.assertEqual(state["attach_state"], "attach_failed")
        self.assertIn("Failed to attach", state["attach_failure"])
        BrowserEngine._instance = None

    async def test_configure_session_reports_not_attached_when_browser_is_disconnected(self):
        BrowserEngine._instance = None
        engine = BrowserEngine()
        fake_context = _FakeContext([_FakePage()])
        fake_browser = _FakeBrowser([fake_context], connected=False)
        fake_chromium = _FakeChromium(browser=fake_browser)
        fake_runtime = _FakePlaywrightRuntime(fake_chromium)
        engine._load_playwright = lambda: (lambda: _FakePlaywrightBootstrap(fake_runtime))  # type: ignore[method-assign]

        state = await engine.configure_session("existing_session_attach", "http://127.0.0.1:9222")

        self.assertEqual(state["status"], "not_attached")
        self.assertEqual(state["attach_state"], "not_attached")
        self.assertIn("not currently attachable", state["attach_failure"])
        BrowserEngine._instance = None
