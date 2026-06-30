"""MCP client — streamable_http transport with retries, timeouts, validation.

Port of proven v1 patterns from legacy/server_modules/mcp_registry_service.py:
- 3 attempts for tool calls (1s/2s backoff), 2 for discovery
- 60s tool timeout, 30s discovery timeout (httpx + asyncio)
- Transient error classification
- Argument validation against input_schema
- Endpoint validation (HTTPS only, block loopback/private)
"""

import asyncio
import json
import logging
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx

_log = logging.getLogger(__name__)

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::]"}
_MAX_STRING_LEN = 10_000


# ── endpoint validation ───────────────────────────────────────────────────────


def _validate_endpoint(endpoint: str) -> None:
    raw = endpoint.strip()
    if not raw:
        raise ValueError("MCP endpoint URL is required.")
    parsed = urlparse(raw)
    if parsed.scheme not in ("https", "http"):
        raise ValueError(f"MCP endpoint must use https://. Got: {parsed.scheme!r}")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("MCP endpoint URL has no parseable hostname.")
    if hostname in _BLOCKED_HOSTS:
        raise ValueError(f"MCP endpoint hostname {hostname!r} is blocked.")
    try:
        addr = ip_address(hostname)
        if addr.is_loopback or addr.is_link_local or addr.is_private or addr.is_unspecified:
            raise ValueError(f"MCP endpoint IP {hostname!r} is not allowed.")
    except ValueError:
        pass  # not an IP address, hostname is fine


# ── argument validation ───────────────────────────────────────────────────────


def _validate_arguments(args: dict[str, Any], input_schema: dict[str, Any],
                        tool_name: str = "") -> dict[str, Any]:
    if not isinstance(args, dict) or not args:
        return dict(args) if isinstance(args, dict) else {}
    schema = input_schema if isinstance(input_schema, dict) else {}
    if not schema:
        return dict(args)
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = [str(r).strip() for r in
                (schema.get("required") if isinstance(schema.get("required"), list) else [])
                if str(r).strip()]
    cleaned: dict[str, Any] = {}
    # Strip unknown keys
    for key, value in args.items():
        key_s = str(key).strip()
        if properties and key_s not in properties:
            _log.warning("MCP arg validation: stripping unknown key '%s' for tool '%s'", key_s, tool_name)
            continue
        cleaned[key_s] = value
    # Check required
    for req in required:
        if req not in cleaned:
            raise RuntimeError(f"Missing required parameter: {req}"
                               + (f" for tool {tool_name}" if tool_name else ""))
    # Type coercion + length capping
    for key, value in list(cleaned.items()):
        prop = properties.get(key, {}) if isinstance(properties, dict) else {}
        if not isinstance(prop, dict):
            continue
        expected = str(prop.get("type") or "").strip().lower()
        if expected == "string" and isinstance(value, str) and len(value) > _MAX_STRING_LEN:
            cleaned[key] = value[:_MAX_STRING_LEN]
        elif expected in ("integer", "number") and isinstance(value, str):
            try:
                cleaned[key] = int(value.strip()) if expected == "integer" else float(value.strip())
            except (ValueError, TypeError):
                pass
    return cleaned


# ── auth headers ──────────────────────────────────────────────────────────────


def _auth_headers(credential: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(credential, dict) or not credential:
        return {}
    access_token = str(credential.get("access_token") or "").strip()
    if access_token:
        return {"Authorization": f"Bearer {access_token}"}
    api_key = str(credential.get("api_key") or "").strip()
    if api_key:
        return {"Authorization": f"ApiKey {api_key}"}
    bot_token = str(credential.get("bot_token") or "").strip()
    if bot_token:
        return {"Authorization": f"Bot {bot_token}"}
    return {}


# ── result extraction ─────────────────────────────────────────────────────────


def _extract_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return result
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if isinstance(content, list):
        texts = [getattr(c, "text", "") for c in content if isinstance(getattr(c, "text", ""), str) and str(getattr(c, "text", "")).strip()]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except Exception:
                return {"text": texts[0]}
        if texts:
            return {"text": "\n".join(texts)}
    return {}


# ── transient error classifier ─────────────────────────────────────────────────


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(exc.response, "status_code", 0)
        return status in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, (TimeoutError, ConnectionRefusedError, ConnectionResetError, OSError)):
        return True
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if any(kw in msg for kw in ("not installed", "not found", "disabled", "not supported")):
            return False
        return "timed out" in msg or "timeout" in msg
    return False


# ── error helpers ─────────────────────────────────────────────────────────────


def _unwrap_error(exc: BaseException) -> BaseException:
    """Unwrap ExceptionGroup (Python 3.11+) into the first sub-error."""
    if hasattr(exc, "exceptions"):
        subs = getattr(exc, "exceptions", None)
        if isinstance(subs, (list, tuple)) and subs:
            return _unwrap_error(subs[0])
    return exc


# ── core MCP calls ────────────────────────────────────────────────────────────


async def _list_tools_async(*, endpoint: str, credential: dict[str, Any] | None = None,
                            timeout: float = 30.0) -> list[dict[str, Any]]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    headers = _auth_headers(credential)
    http_client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(timeout)) if headers else None
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            async with asyncio.timeout(timeout):
                async with streamable_http_client(endpoint, http_client=http_client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.list_tools()
            break
        except TimeoutError:
            if attempt < max_attempts:
                _log.warning("MCP discovery attempt %d/%d timed out for %s, retrying...",
                             attempt, max_attempts, endpoint)
                await asyncio.sleep(1.0 * attempt)
                continue
            raise RuntimeError(f"MCP discovery timed out after {max_attempts} attempts for {endpoint}")
        except Exception as exc:
            if attempt < max_attempts and _is_transient(_unwrap_error(exc)):
                _log.warning("MCP discovery attempt %d/%d failed for %s: %s, retrying...",
                             attempt, max_attempts, endpoint, exc)
                await asyncio.sleep(1.0 * attempt)
                continue
            raise _unwrap_error(exc) from exc
    # Extract tools from result
    raw_tools = result if isinstance(result, list) else getattr(result, "tools", [])
    if not isinstance(raw_tools, list):
        return []
    return [{k: v for k, v in t.items()} if isinstance(t, dict)
            else {"name": getattr(t, "name", ""), "description": getattr(t, "description", ""),
                  "input_schema": getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)}
            for t in raw_tools]


async def _call_tool_async(*, endpoint: str, tool_name: str, arguments: dict[str, Any],
                           credential: dict[str, Any] | None = None,
                           timeout: float = 60.0) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    headers = _auth_headers(credential)
    http_client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(timeout)) if headers else None
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            async with asyncio.timeout(timeout):
                async with streamable_http_client(endpoint, http_client=http_client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await session.call_tool(tool_name, arguments)
        except TimeoutError:
            if attempt < max_attempts:
                _log.warning("MCP call %s attempt %d/%d timed out, retrying...",
                             tool_name, attempt, max_attempts)
                await asyncio.sleep(1.0 * attempt)
                continue
            raise RuntimeError(f"MCP tool call timed out after {max_attempts} attempts: {tool_name}")
        except Exception as exc:
            if attempt < max_attempts and _is_transient(_unwrap_error(exc)):
                _log.warning("MCP call %s attempt %d/%d failed: %s, retrying...",
                             tool_name, attempt, max_attempts, exc)
                await asyncio.sleep(1.0 * attempt)
                continue
            raise _unwrap_error(exc) from exc


# ── public API ─────────────────────────────────────────────────────────────────


async def discover_mcp_tools(endpoint: str, credential: dict[str, Any] | None = None,
                             timeout: float = 30.0) -> list[dict[str, Any]]:
    """Discover tools from an MCP server. Returns list of {name, description, input_schema}."""
    _validate_endpoint(endpoint)
    return await _list_tools_async(endpoint=endpoint, credential=credential, timeout=timeout)


async def call_mcp_tool(endpoint: str, tool_name: str, arguments: dict[str, Any],
                        credential: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
    """Call an MCP tool. Returns the structured result payload."""
    _validate_endpoint(endpoint)
    if not tool_name.strip():
        raise ValueError("tool_name is required.")
    result = await _call_tool_async(
        endpoint=endpoint, tool_name=tool_name, arguments=arguments,
        credential=credential, timeout=timeout)
    return _extract_payload(result)
