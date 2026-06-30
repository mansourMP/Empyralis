import unittest
from unittest.mock import patch

from server_modules import web_tools


class WebToolsTests(unittest.TestCase):
    def test_clean_result_url_decodes_duckduckgo_redirect(self) -> None:
        decoded = web_tools._clean_result_url(
            "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle%3Fid%3D1"
        )
        self.assertEqual(decoded, "https://example.com/article?id=1")

    def test_web_search_parses_standard_html_results(self) -> None:
        html = """
        <div class="result">
          <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Falpha">Alpha result</a>
          <a class="result__snippet">First snippet</a>
        </div>
        """
        with patch("server_modules.web_tools._fetch_url", return_value=html):
            results = web_tools.web_search("alpha")
        self.assertEqual(
            results,
            [
                {
                    "title": "Alpha result",
                    "url": "https://example.com/alpha",
                    "snippet": "First snippet",
                }
            ],
        )

    def test_web_search_falls_back_to_lite_markup(self) -> None:
        html = """
        <table>
          <tr>
            <td><a class="result-link" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fbeta">Beta result</a></td>
          </tr>
          <tr>
            <td class="result-snippet">Second snippet</td>
          </tr>
        </table>
        """
        with patch("server_modules.web_tools._fetch_url", return_value=html):
            results = web_tools.web_search("beta")
        self.assertEqual(
            results,
            [
                {
                    "title": "Beta result",
                    "url": "https://example.com/beta",
                    "snippet": "Second snippet",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
