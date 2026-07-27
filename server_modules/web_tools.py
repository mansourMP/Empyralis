from __future__ import annotations

import html
import http.client
import base64
import json
import re
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse as urlparse
from urllib import request as urlrequest

from server_modules.url_security import assert_safe_outbound_url


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)

# MAN-109: both fetch paths below only ever validated the URL once, up
# front, then followed redirects unconditionally -- urlopen()'s default
# HTTPRedirectHandler trusts any Location header, and `curl -L` does the
# same at the process level. A URL that passes assert_safe_outbound_url
# (a public host) can still 302 to a loopback/link-local/private/cloud-
# metadata address, and the response comes back as if it were the original
# target. _ValidatingRedirectHandler and _curl_fetch_with_redirect_guard
# close that gap by re-validating every redirect hop, not just the first
# request, mirroring the same fix applied to the MCP client in
# mcp_registry_service.py's _mcp_redirect_guard_request_hook.
_MAX_REDIRECT_HOPS = 10
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class _ValidatingRedirectHandler(urlrequest.HTTPRedirectHandler):
    """Re-validates every redirect target against assert_safe_outbound_url
    before urllib.request follows it. The stdlib HTTPRedirectHandler blindly
    trusts whatever Location header the server sends; raising here aborts
    the redirect (and the whole request) before urllib ever connects to the
    unsafe target."""

    def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # noqa: N802 (stdlib signature)
        assert_safe_outbound_url(newurl)
        return super().redirect_request(req, fp, code, msg, hdrs, newurl)


_VALIDATING_OPENER = urlrequest.build_opener(_ValidatingRedirectHandler)


def _parse_curl_status_and_location(headers_text: str) -> Tuple[int, Optional[str]]:
    """Parse the status code and (if present) Location header out of the
    raw header block curl writes via `-D`. A single curl invocation with no
    -L makes exactly one request, so there is exactly one status line/header
    block to parse here -- no need to handle multiple hops' worth of
    headers in one blob."""
    status = 0
    location: Optional[str] = None
    for line in headers_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if status == 0:
            parts = stripped.split(None, 2)
            if len(parts) >= 2 and parts[1].isdigit():
                status = int(parts[1])
            continue
        if stripped.lower().startswith("location:"):
            location = stripped.split(":", 1)[1].strip()
    return status, location


def _curl_fetch_with_redirect_guard(url: str, *, timeout: int) -> str:
    """curl fallback for _fetch_url, with redirects followed manually (curl
    invoked WITHOUT -L) so each hop's target can be re-validated against
    assert_safe_outbound_url before it is requested -- see the MAN-109 note
    above _ValidatingRedirectHandler."""
    current_url = str(url or "").strip()
    for _ in range(_MAX_REDIRECT_HOPS + 1):
        assert_safe_outbound_url(current_url)
        with tempfile.NamedTemporaryFile(mode="r", suffix=".headers") as headers_file:
            completed = subprocess.run(
                [
                    "curl",
                    "--max-time",
                    str(timeout),
                    "-sS",
                    "-D",
                    headers_file.name,
                    "-A",
                    DEFAULT_USER_AGENT,
                    current_url,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            headers_text = headers_file.read()
        status, location = _parse_curl_status_and_location(headers_text)
        if status in _REDIRECT_STATUS_CODES and location:
            current_url = urlparse.urljoin(current_url, location)
            continue
        return completed.stdout
    raise RuntimeError(f"Too many redirects while fetching {url}")


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
        with _VALIDATING_OPENER.open(request, timeout=timeout) as response:
            try:
                payload = response.read()
            except http.client.IncompleteRead as exc:
                payload = exc.partial
            return payload.decode("utf-8", "ignore")
    except Exception:
        return _curl_fetch_with_redirect_guard(normalized_url, timeout=timeout)


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
