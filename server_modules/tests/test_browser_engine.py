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

    async def evaluate(self, _script: str):
        return self._html


class _FakePage:
    def __init__(self):
        self.url = "https://example.com/"
        self._title = "Example Domain"
        self._body_text = "Example Domain\nThis domain is for use in illustrative examples."

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
        return "<html><body><h1>Example Domain</h1><script>evil()</script></body></html>"

    def is_closed(self):
        return False


class BrowserEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        BrowserEngine._instance = None
        self.engine = BrowserEngine()
        self.page = _FakePage()
        self.engine._context = object()
        self.engine._page = self.page
        self.engine._ensure_started = self._noop  # type: ignore[method-assign]
        self._tmpdir = tempfile.TemporaryDirectory(prefix="browser-engine-")
        self.engine.profile_dir = Path(self._tmpdir.name) / "profile"
        self.engine.profile_dir.mkdir(parents=True, exist_ok=True)

    async def asyncTearDown(self):
        self._tmpdir.cleanup()
        BrowserEngine._instance = None

    async def _noop(self):
        return None

    async def test_navigate_returns_title(self):
        result = await self.engine.navigate("https://example.com")
        self.assertEqual(result["title"], "Example Domain")
        self.assertEqual(result["status_code"], 200)

    async def test_extract_text_returns_content(self):
        text = await self.engine.extract_text()
        self.assertIn("Example Domain", text)

    async def test_extract_text_with_selector(self):
        text = await self.engine.extract_text("h1")
        self.assertEqual(text, "Example Domain")

    async def test_extract_dom_returns_html_without_scripts(self):
        html = await self.engine.extract_dom()
        self.assertIn("Example Domain", html)
        self.assertNotIn("evil()", html)

    async def test_env_override_changes_engine_root(self):
        with tempfile.TemporaryDirectory(prefix="browser-engine-root-") as tmpdir:
            BrowserEngine._instance = None
            with patch.dict("os.environ", {"ORION_BROWSER_ENGINE_ROOT": tmpdir}, clear=False):
                engine = BrowserEngine()
            self.assertEqual(engine.profile_dir, (Path(tmpdir) / "browser-profile").resolve())
            BrowserEngine._instance = None


if __name__ == "__main__":
    unittest.main()
