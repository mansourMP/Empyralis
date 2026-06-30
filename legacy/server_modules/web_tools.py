from __future__ import annotations

import html
import http.client
import base64
import json
import re
import subprocess
from typing import Any, Dict, List
from urllib import parse as urlparse
from urllib import request as urlrequest

from server_modules.url_security import assert_safe_outbound_url


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)


def _fetch_url(url: str, *, timeout: int = 15) -> str:
    normalized_url = str(url or "").strip()
    assert_safe_outbound_url(normalized_url)
    request = urlrequest.Request(
        normalized_url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            try:
                payload = response.read()
            except http.client.IncompleteRead as exc:
                payload = exc.partial
            return payload.decode("utf-8", "ignore")
    except Exception:
        completed = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                str(timeout),
                "-sS",
                "-A",
                DEFAULT_USER_AGENT,
                normalized_url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout


def _strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", str(raw_html or ""))
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def web_fetch(url: str) -> str:
    normalized_url = str(url or "").strip()
    if not normalized_url:
        raise RuntimeError("web_fetch requires a URL.")
    assert_safe_outbound_url(normalized_url)
    raw_html = _fetch_url(normalized_url)
    cleaned = _strip_html(raw_html)
    if not cleaned:
        return f"Fetched {normalized_url} but could not extract readable text."
    excerpt = cleaned[:12000].rstrip()
    return f"Source: {normalized_url}\n\n{excerpt}"


def _clean_result_url(url: str) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("//"):
        normalized = f"https:{normalized}"
    parsed = urlparse.urlsplit(normalized)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = urlparse.parse_qs(parsed.query).get("uddg", [""])
        resolved = str(target[0] or "").strip()
        if resolved:
            return urlparse.unquote(resolved)
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/"):
        target = urlparse.parse_qs(parsed.query).get("u", [""])
        encoded = str(target[0] or "").strip()
        if encoded.startswith("a1"):
            encoded = encoded[2:]
        if encoded:
            padding = "=" * (-len(encoded) % 4)
            try:
                decoded = base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8", "ignore")
            except Exception:
                decoded = ""
            if decoded.startswith(("http://", "https://")):
                return decoded
    return normalized


def _parse_html_results(html_text: str) -> List[Dict[str, str]]:
    pattern = re.compile(
        r'(?is)<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>'
    )
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(html_text):
        url = _clean_result_url(html.unescape(match.group("url") or ""))
        title = _strip_html(match.group("title") or "")
        snippet = _strip_html(match.group("snippet") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({"title": title[:240], "url": url[:1200], "snippet": snippet[:600]})
        if len(results) >= 5:
            break
    return results


def _parse_lite_results(html_text: str) -> List[Dict[str, str]]:
    pattern = re.compile(
        r'(?is)<a[^>]*class="[^"]*result-link[^"]*"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<td[^>]*class="[^"]*result-snippet[^"]*"[^>]*>(?P<snippet>.*?)</td>'
    )
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(html_text):
        url = _clean_result_url(html.unescape(match.group("url") or ""))
        title = _strip_html(match.group("title") or "")
        snippet = _strip_html(match.group("snippet") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({"title": title[:240], "url": url[:1200], "snippet": snippet[:600]})
        if len(results) >= 5:
            break
    return results


def _decode_json_string_literal(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(json.loads(f'"{raw}"'))
    except Exception:
        return html.unescape(raw.replace("\\/", "/"))


def _parse_brave_results(html_text: str) -> List[Dict[str, str]]:
    pattern = re.compile(
        r'"title":"(?P<title>(?:\\.|[^"\\])+)".{0,1600}?"url":"(?P<url>https?://(?:\\.|[^"\\])+)".{0,1600}?"description":"(?P<snippet>(?:\\.|[^"\\])*)"',
        flags=re.DOTALL,
    )
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(html_text):
        url = _clean_result_url(_decode_json_string_literal(match.group("url") or ""))
        title = _strip_html(_decode_json_string_literal(match.group("title") or ""))
        snippet = _strip_html(_decode_json_string_literal(match.group("snippet") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({"title": title[:240] or url, "url": url[:1200], "snippet": snippet[:600]})
        if len(results) >= 5:
            break
    if results:
        return results

    fallback_urls: List[str] = []
    for raw_url in re.findall(r'https?://[^"<>\s\\]+', html_text):
        url = _clean_result_url(html.unescape(raw_url))
        parsed_url = urlparse.urlsplit(url)
        host = parsed_url.netloc.lower()
        if (
            not url
            or url in seen
            or host in {"search.brave.com", "www.w3.org", "schema.org"}
            or "cdn.search.brave.com" in url
            or "imgs.search.brave.com" in url
            or "tiles.search.brave.com" in url
            or parsed_url.path.endswith((".css", ".js", ".svg", ".ico", ".png", ".jpg", ".woff2"))
        ):
            continue
        seen.add(url)
        fallback_urls.append(url)
        if len(fallback_urls) >= 5:
            break
    return [{"title": url, "url": url, "snippet": ""} for url in fallback_urls]


def _parse_bing_results(html_text: str) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for block in re.findall(r'(?is)<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>.*?</li>', html_text):
        link = re.search(r'(?is)<h2[^>]*>.*?<a[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>', block)
        if not link:
            continue
        snippet_match = re.search(r'(?is)<p[^>]*>(?P<snippet>.*?)</p>', block)
        url = _clean_result_url(html.unescape(link.group("url") or ""))
        title = _strip_html(link.group("title") or "")
        snippet = _strip_html(snippet_match.group("snippet") if snippet_match else "")
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({"title": title[:240] or url, "url": url[:1200], "snippet": snippet[:600]})
        if len(results) >= 5:
            break
    return results


def web_search(query: str) -> List[Dict[str, str]]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return []
    encoded_query = urlparse.quote_plus(normalized_query)
    last_error: Exception | None = None
    for source, url in (
        ("duckduckgo", f"https://html.duckduckgo.com/html/?q={encoded_query}"),
        ("duckduckgo", f"https://lite.duckduckgo.com/lite/?q={encoded_query}"),
        ("bing", f"https://www.bing.com/search?q={encoded_query}"),
        ("brave", f"https://search.brave.com/search?q={encoded_query}"),
    ):
        try:
            html_text = _fetch_url(url)
        except Exception as exc:
            last_error = exc
            continue
        if "anomaly-modal" in html_text or "Unfortunately, bots use DuckDuckGo too" in html_text:
            continue
        if source == "duckduckgo":
            results = _parse_html_results(html_text) or _parse_lite_results(html_text)
        elif source == "bing":
            results = _parse_bing_results(html_text)
        else:
            results = _parse_brave_results(html_text)
        if results:
            return results
    if last_error is not None:
        raise last_error
    return []
