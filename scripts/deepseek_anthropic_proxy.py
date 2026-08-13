#!/usr/bin/env python3
"""Thin proxy: Anthropic Messages API → DeepSeek Chat Completions.

Deploy on the VPS behind PM2.  Claude Code points ANTHROPIC_BASE_URL
at this proxy and talks to it as if it were the Anthropic API.

Requires: fastapi, uvicorn, httpx, openai (already in empyralis .venv)
Env vars:
  DEEPSEEK_API_KEY        – required
  DEEPSEEK_BASE_URL       – default https://api.deepseek.com/v1
  PROXY_PORT              – default 8799
  PROXY_HOST              – default 127.0.0.1
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

# ── Config ──────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8799"))
PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
# "deepseek-chat" was DeepSeek's own model id here until this fix — DeepSeek
# retired it 2026-07-24 (see server_modules/provider_profiles.py's
# "deepseek" catalog entry). "deepseek-v4-flash" is its real successor.
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Reasonable timeout for long generations (up to 10 min)
TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=30.0)

app = FastAPI(title="deepseek-anthropic-proxy")


# ── Helpers ─────────────────────────────────────────────────────────────
def _deepseek_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }


def _safe_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Anthropic → DeepSeek message translation ────────────────────────────
def _convert_anthropic_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Anthropic messages array + optional system → DeepSeek messages."""
    messages: list[dict[str, Any]] = []

    # Anthropic system field → DeepSeek system message
    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        # system can be [{type: "text", text: "..."}, ...]
        parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        text = "\n".join(parts).strip()
        if text:
            messages.append({"role": "system", "content": text})

    # Convert Anthropic messages → OpenAI/DeepSeek messages
    anthropic_msgs = body.get("messages") or []
    for msg in anthropic_msgs:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            # content can be string or array of content blocks
            if isinstance(content, list):
                text_parts = []
                image_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(str(block.get("text", "")))
                        elif block.get("type") == "image":
                            src = block.get("source") or {}
                            image_parts.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}",
                                    "detail": "high" if block.get("cache_control") else "auto",
                                },
                            })
                if image_parts:
                    messages.append({"role": "user", "content": text_parts + image_parts})
                else:
                    messages.append({"role": "user", "content": "\n".join(text_parts)})
            else:
                messages.append({"role": "user", "content": str(content)})

        elif role == "assistant":
            if isinstance(content, list):
                text_parts = []
                tool_calls = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(str(block.get("text", "")))
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": str(block.get("id", uuid.uuid4().hex[:8])),
                                "type": "function",
                                "function": {
                                    "name": str(block.get("name", "")),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                msg_out: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    msg_out["content"] = "\n".join(text_parts)
                if tool_calls:
                    msg_out["tool_calls"] = tool_calls
                messages.append(msg_out)
            else:
                messages.append({"role": "assistant", "content": str(content)})

        elif role == "tool":
            # Anthropic tool_result → DeepSeek tool message
            tool_use_id = ""
            tool_content = ""
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        tool_use_id = str(block.get("tool_use_id", tool_use_id))
                        if block.get("type") == "text":
                            tool_content = str(block.get("text", ""))
            elif isinstance(content, str):
                tool_content = content

            # tool_use_id may also be at top level
            tool_use_id = str(msg.get("tool_use_id", tool_use_id))

            messages.append({
                "role": "tool",
                "tool_call_id": tool_use_id,
                "content": tool_content,
            })

    return messages


def _convert_anthropic_tools(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Convert Anthropic tool definitions → OpenAI/DeepSeek function tools."""
    tools = body.get("tools")
    if not tools:
        return None

    converted = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        converted.append({
            "type": "function",
            "function": {
                "name": str(tool.get("name", "")),
                "description": str(tool.get("description", "")),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return converted if converted else None


def _deepseek_choice_to_anthropic_content(choice: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a DeepSeek choice → Anthropic content blocks."""
    content: list[dict[str, Any]] = []
    msg = choice.get("message") or {}

    # Text content
    text = str(msg.get("content") or "").strip()
    if text:
        content.append({"type": "text", "text": text})

    # Tool calls
    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        func = tc.get("function") or {}
        try:
            tool_input = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            tool_input = {}
        content.append({
            "type": "tool_use",
            "id": str(tc.get("id", uuid.uuid4().hex[:8])),
            "name": str(func.get("name", "")),
            "input": tool_input,
        })

    # If completely empty, add a stop_reason content
    if not content:
        finish = str(choice.get("finish_reason") or "stop")
        if finish == "stop":
            content.append({"type": "text", "text": ""})

    return content


def _build_anthropic_response(body: dict[str, Any], ds_response: dict[str, Any]) -> dict[str, Any]:
    """Build an Anthropic-formatted response from DeepSeek response."""
    model = body.get("model", DEFAULT_MODEL)
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    choice = (ds_response.get("choices") or [{}])[0]
    usage = ds_response.get("usage") or {}

    content = _deepseek_choice_to_anthropic_content(choice)
    finish_reason = str(choice.get("finish_reason") or "stop")

    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
    }
    stop_reason = stop_reason_map.get(finish_reason, "end_turn")

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
        },
    }


# ── SSE streaming helpers ───────────────────────────────────────────────
def _build_anthropic_stream_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# ── Routes ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "provider": "deepseek", "model": DEFAULT_MODEL}


@app.get("/v1/models")
async def list_models():
    """Return Anthropic-formatted model list (Claude Code probes this)."""
    return {
        "data": [
            {
                "id": DEFAULT_MODEL,
                "type": "model",
                "display_name": "DeepSeek V4 Pro",
                "created_at": "2025-01-01T00:00:00Z",
            }
        ],
        "has_more": False,
        "first_id": DEFAULT_MODEL,
        "last_id": DEFAULT_MODEL,
    }


@app.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    return {
        "id": model_id,
        "type": "model",
        "display_name": "DeepSeek V4 Pro",
        "created_at": "2025-01-01T00:00:00Z",
    }


@app.post("/v1/messages")
async def messages(request: Request):
    """Anthropic Messages API endpoint."""
    if not DEEPSEEK_API_KEY:
        return JSONResponse(status_code=500, content={"error": {"message": "DEEPSEEK_API_KEY not configured on proxy"}})

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "invalid JSON body"}})

    model = body.get("model", DEFAULT_MODEL)
    stream = bool(body.get("stream", False))

    # Build DeepSeek request
    ds_body: dict[str, Any] = {
        "model": model,
        "messages": _convert_anthropic_messages(body),
        "max_tokens": int(body.get("max_tokens", 4096)),
        "stream": stream,
    }
    if body.get("temperature") is not None:
        ds_body["temperature"] = float(body["temperature"])
    if body.get("top_p") is not None:
        ds_body["top_p"] = float(body["top_p"])
    if body.get("stop_sequences"):
        ds_body["stop"] = body["stop_sequences"]

    tools = _convert_anthropic_tools(body)
    if tools:
        ds_body["tools"] = tools
        tool_choice = body.get("tool_choice")
        if tool_choice:
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "any":
                ds_body["tool_choice"] = "auto"
            elif isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
                ds_body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice.get("name", "")},
                }

    headers = _deepseek_headers()
    url = f"{DEEPSEEK_BASE}/chat/completions"

    if stream:
        async def stream_gen():
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream("POST", url, headers=headers, json=ds_body) as resp:
                    # Send message_start
                    yield _build_anthropic_stream_event("message_start", {
                        "type": "message_start",
                        "message": {
                            "id": f"msg_{uuid.uuid4().hex[:24]}",
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": model,
                        },
                    })

                    content_block_index = 0
                    current_tool_id: str | None = None
                    current_tool_name: str | None = None
                    text_buf = ""

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break

                        chunk = _safe_json(data_str)
                        if not chunk:
                            continue

                        delta_choice = (chunk.get("choices") or [{}])[0]
                        delta = delta_choice.get("delta") or {}
                        finish_reason = str(delta_choice.get("finish_reason") or "")

                        # Tool calls
                        tool_calls = delta.get("tool_calls") or []
                        for tc in tool_calls:
                            tc_id = str(tc.get("id") or "")
                            func = tc.get("function") or {}
                            tc_name = str(func.get("name") or "")
                            tc_args = str(func.get("arguments") or "")

                            # New tool call starting
                            if tc_id and tc_id != current_tool_id:
                                # Emit content_block_start
                                yield _build_anthropic_stream_event("content_block_start", {
                                    "type": "content_block_start",
                                    "index": content_block_index,
                                    "content_block": {
                                        "type": "tool_use",
                                        "id": tc_id,
                                        "name": tc_name,
                                        "input": {},
                                    },
                                })
                                current_tool_id = tc_id
                                current_tool_name = tc_name
                                text_buf = tc_args
                                content_block_index += 1

                            elif tc_args and current_tool_id:
                                text_buf += tc_args

                        # Text content
                        text = str(delta.get("content") or "")
                        if text:
                            if not text_buf or current_tool_id is None:
                                # Text block
                                if not text_buf:
                                    yield _build_anthropic_stream_event("content_block_start", {
                                        "type": "content_block_start",
                                        "index": content_block_index,
                                        "content_block": {"type": "text", "text": ""},
                                    })
                                    content_block_index += 1
                                yield _build_anthropic_stream_event("content_block_delta", {
                                    "type": "content_block_delta",
                                    "index": content_block_index - 1,
                                    "delta": {"type": "text_delta", "text": text},
                                })
                            else:
                                # Accumulating tool args
                                text_buf += text

                        # Finish: emit final tool input
                        if finish_reason and current_tool_id and text_buf:
                            try:
                                tool_input = json.loads(text_buf)
                            except json.JSONDecodeError:
                                tool_input = {}
                            yield _build_anthropic_stream_event("content_block_delta", {
                                "type": "content_block_delta",
                                "index": content_block_index - 1,
                                "delta": {"type": "input_json_delta", "partial_json": text_buf},
                            })

                            yield _build_anthropic_stream_event("content_block_stop", {
                                "type": "content_block_stop",
                                "index": content_block_index - 1,
                            })

                        # Text delta stop
                        if not current_tool_id and finish_reason and finish_reason != "tool_calls":
                            yield _build_anthropic_stream_event("content_block_stop", {
                                "type": "content_block_stop",
                                "index": content_block_index - 1,
                            })

                    # message_delta
                    stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
                    yield _build_anthropic_stream_event("message_delta", {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": stop_map.get(finish_reason, "end_turn"),
                            "stop_sequence": None,
                        },
                        "usage": {
                            "output_tokens": 0,  # DeepSeek doesn't give per-chunk usage
                        },
                    })

                    yield _build_anthropic_stream_event("message_stop", {
                        "type": "message_stop",
                    })

        return StreamingResponse(stream_gen(), media_type="text/event-stream")

    # Non-streaming
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=ds_body)

    if resp.status_code != 200:
        return JSONResponse(
            status_code=resp.status_code,
            content={"error": {"message": f"DeepSeek upstream error: {resp.text[:500]}"}},
        )

    ds_data = resp.json()
    return JSONResponse(content=_build_anthropic_response(body, ds_data))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=PROXY_HOST, port=PROXY_PORT, log_level="info")
