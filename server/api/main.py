"""Empyralis v2 API server — FastAPI.

Session cookie (itsdangerous-signed) → API key in vault → Sage streaming via SSE.
Run: python -m server.api"""

import asyncio
import json
import os
import secrets
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from itsdangerous import URLSafeSerializer

from server.agent import Runner
from server.agent.providers import resolve_provider
from server.channels.router import route
from server.cli import SAGE_MANIFEST
from server.conversations import store
from server.mcp.apps import APPS
from server.mcp.client import discover_mcp_tools
from server.memory.service import memory_list, memory_read, memory_write
from server.oauth.exchange import exchange_code
from server.oauth.provider_configs import PROVIDERS, get_provider
from server.oauth.refresh import resolve_credential
from server.tools.shell import run as shell_run
from server.vault.store import get_credential, load_vault, save_vault, set_credential

WORKSPACE = "default"
CHANNEL = "web"

_BASE = os.getenv("EMPYRALIS_BASE_URL", "").strip().rstrip("/")
# In production, frontend and API share the same origin (nginx reverse proxy).
# In dev, they're on separate ports.
FRONTEND_URL = _BASE or "http://localhost:3000"
REDIRECT_URI = f"{_BASE or 'http://localhost:8000'}/api/oauth/callback"
CORS_ORIGINS = [FRONTEND_URL, "http://localhost:3000"]

app = FastAPI(title="Empyralis v2", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── session secret ──────────────────────────────────────────────────────────

def _get_session_secret() -> str:
    secret = os.getenv("SESSION_SECRET", "").strip()
    if secret:
        return secret
    generated = secrets.token_urlsafe(32)
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    line = f"\nSESSION_SECRET={generated}\n"
    with open(env_path, "a") as f:
        f.write(line)
    os.environ["SESSION_SECRET"] = generated
    return generated

_serializer = URLSafeSerializer(_get_session_secret())


def _get_session(request: Request) -> dict | None:
    cookie = request.cookies.get("empyralis_session")
    if not cookie:
        return None
    try:
        return _serializer.loads(cookie)
    except Exception:
        return None


def _set_session_cookie(response: JSONResponse, session_data: dict) -> None:
    response.set_cookie(
        "empyralis_session",
        _serializer.dumps(session_data),
        httponly=True, samesite="lax", max_age=86400 * 7,
    )


def _get_api_key() -> str | None:
    vault = load_vault()
    cred = get_credential(vault, "byok:api_key")
    if cred:
        return cred.get("api_key")
    return None


# ── runner wiring ───────────────────────────────────────────────────────────

def _register_builtins(runner: Runner) -> None:
    runner.register("shell", shell_run)
    runner.register("memory_list", partial(memory_list, workspace="default", agent="sage"))
    runner.register("memory_read", partial(memory_read, workspace="default", agent="sage"))
    runner.register("memory_write", partial(memory_write, workspace="default", agent="sage"))


async def _register_mcp_tools(runner: Runner) -> None:
    vault = load_vault()
    runner.set_vault(vault)
    for provider_key, apps in APPS.items():
        cred_id = f"mcp:{provider_key}"
        credential = resolve_credential(vault, cred_id)
        for app in apps:
            try:
                tools = await discover_mcp_tools(app.endpoint, credential=credential)
                for tool in tools:
                    name = tool.get("name", "")
                    if not name:
                        continue
                    runner.register_mcp_tool(
                        server_id=app.server_id, tool_name=name,
                        endpoint=app.endpoint,
                        input_schema=tool.get("input_schema"),
                        credential_id=cred_id,
                    )
            except Exception:
                pass


# ── routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/session")
async def create_session(request: Request):
    body = await request.json() if await request.body() else {}
    api_key = (body.get("api_key") or "").strip()

    if not api_key:
        raise HTTPException(400, "api_key is required — get one at https://console.anthropic.com/settings/keys")

    import anthropic
    try:
        base_url, model = resolve_provider(api_key, "claude-sonnet-4-6")
        c = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)
        await c.messages.create(
            model=model, max_tokens=1,
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        )
    except Exception as exc:
        raise HTTPException(400, f"Invalid API key: {exc}") from exc

    vault = load_vault()
    set_credential(vault, "byok:api_key", {"api_key": api_key})
    save_vault(vault)

    session_data = {"session_id": str(uuid.uuid4()), "created": time.time()}
    resp = JSONResponse({"authenticated": True, "last_four": api_key[-4:]})
    _set_session_cookie(resp, session_data)
    return resp


@app.get("/session")
async def get_session(request: Request):
    session = _get_session(request)
    api_key = _get_api_key()
    if not session or not api_key:
        return {"authenticated": False}

    connected = []
    vault = load_vault()
    for provider_key in APPS:
        cred = get_credential(vault, f"mcp:{provider_key}")
        if cred:
            connected.append(provider_key)

    return {"authenticated": True, "connected_apps": connected, "last_four": api_key[-4:]}


@app.delete("/session")
async def delete_session():
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie("empyralis_session")
    return resp


@app.post("/chat")
async def chat(request: Request):
    session = _get_session(request)
    api_key = _get_api_key()
    if not session or not api_key:
        raise HTTPException(401, "Not authenticated")

    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")

    chat_id = str(session["session_id"])
    history = store.load_window(WORKSPACE, CHANNEL, chat_id)
    store.append(WORKSPACE, CHANNEL, chat_id, "user", message)

    agent = await route(CHANNEL, chat_id)
    runner = Runner(agent, api_key=api_key)
    _register_builtins(runner)
    await _register_mcp_tools(runner)

    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_token(chunk: str) -> None:
        await queue.put(chunk)

    async def run_agent() -> str:
        try:
            return await runner.run(message, message_history=history, on_token=on_token)
        finally:
            await queue.put(None)

    task = asyncio.create_task(run_agent())

    async def event_stream():
        full_chunks: list[str] = []
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                full_chunks.append(chunk)
                yield f"data: {json.dumps({'token': chunk})}\n\n"
            result = await task
            final_text = result or "".join(full_chunks)
            store.append(WORKSPACE, CHANNEL, chat_id, "assistant", final_text)
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/oauth/start")
async def oauth_start(app: str = ""):
    provider_key = app.strip().lower()
    if provider_key not in PROVIDERS and provider_key not in APPS:
        raise HTTPException(400, f"Unknown app: {app}")

    config = get_provider(provider_key)
    client_id = os.getenv(config.client_id_env, "").strip()
    if not client_id:
        raise HTTPException(400, f"Missing {config.client_id_env}. Set it in .env")

    scopes = " ".join(config.scopes) if config.scopes else ""
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": scopes,
        "state": provider_key,
        **config.auth_params,
    }
    if provider_key == "slack":
        params["user_scope"] = scopes
        params["scope"] = ""

    import urllib.parse
    url = f"{config.auth_url}?{urllib.parse.urlencode(params)}"
    return {"url": url}


@app.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = ""):
    if not code:
        return JSONResponse({"error": "No authorization code received"}, status_code=400)

    # Determine provider from state or try all
    # For MVP, state is just the provider key
    provider_key = state.strip().lower() if state else ""
    if provider_key not in APPS:
        # Try to infer from which providers are configured
        for pk in APPS:
            if os.getenv(get_provider(pk).client_id_env, "").strip():
                provider_key = pk
                break
        if provider_key not in APPS:
            return JSONResponse({"error": "Cannot determine OAuth provider"}, status_code=400)

    try:
        creds = exchange_code(provider_key, code, REDIRECT_URI)
        vault = load_vault()
        set_credential(vault, f"mcp:{provider_key}", creds)
        save_vault(vault)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return RedirectResponse(
        f"{FRONTEND_URL}/oauth/callback?status=connected&app={provider_key}"
    )


@app.get("/apps")
async def list_connected_apps(request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(401, "Not authenticated")

    vault = load_vault()
    result = []
    for provider_key, apps in APPS.items():
        cred = get_credential(vault, f"mcp:{provider_key}")
        for app in apps:
            result.append({
                "id": app.server_id,
                "label": app.label,
                "provider": provider_key,
                "connected": cred is not None,
            })
    return {"apps": result}
