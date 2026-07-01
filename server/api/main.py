"""Empyralis v2 API server — FastAPI.

Session cookie (itsdangerous-signed) → API key in vault → Sage streaming via SSE.
Run: python -m server.api"""

import asyncio
import hashlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from itsdangerous import URLSafeSerializer

from server.agent import Runner
from server.agent.manifest import SAGE_MANIFEST
from server.agent.providers import resolve_provider
from server.agent.wiring import wire_runner
from server.channels.router import route
from server.conversations import store
from server.mcp.apps import APPS
from server.memory.service import memory_list, memory_read, MEMORY_ROOT
from server.oauth.exchange import exchange_code
from server.oauth.provider_configs import PROVIDERS, get_provider
from server.vault.store import get_credential, load_vault, save_vault, set_credential

CHANNEL = "web"


def _derive_workspace(session_id: str) -> str:
    """Return a stable per-tenant workspace identifier.

    BYOK: hash the API key → same key always maps to same workspace.
    Trial: use the session_id directly (session-scoped — no persistent identity)."""
    vault = load_vault()
    api_key_cred = get_credential(vault, f"session:{session_id}:byok:api_key")
    if api_key_cred and api_key_cred.get("api_key"):
        h = hashlib.sha256(api_key_cred["api_key"].encode()).hexdigest()[:16]
        return f"byok:{h}"

    trial = get_credential(vault, f"session:{session_id}:trial_credits")
    if trial:
        return f"trial:{session_id}"

    # Fallback — should not happen for authenticated sessions
    return f"session:{session_id}"

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
        httponly=True, samesite="lax", max_age=86400 * 7, secure=True,
    )


def _get_api_key(session_id: str) -> str | None:
    vault = load_vault()
    cred = get_credential(vault, f"session:{session_id}:byok:api_key")
    if cred:
        return cred.get("api_key")
    return None


def _get_trial_credits(session_id: str) -> dict | None:
    """Return trial credits record for a session, or None if not a trial session."""
    vault = load_vault()
    return get_credential(vault, f"session:{session_id}:trial_credits")


def _deduct_trial_credits(session_id: str, tokens_used: int) -> int:
    """Subtract tokens from trial balance. Returns new remaining value. Floor at 0."""
    vault = load_vault()
    cred = get_credential(vault, f"session:{session_id}:trial_credits") or {}
    remaining = max(0, cred.get("remaining", 0) - tokens_used)
    cred["remaining"] = remaining
    set_credential(vault, f"session:{session_id}:trial_credits", cred)
    save_vault(vault)
    return remaining


# ── routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/session")
async def create_session(request: Request):
    body = await request.json() if await request.body() else {}
    api_key = (body.get("api_key") or "").strip()

    session_id = str(uuid.uuid4())

    # ── trial session (no key provided) ──────────────────────────────────────
    if not api_key:
        vault = load_vault()
        set_credential(vault, f"session:{session_id}:trial_credits",
                       {"remaining": 10000, "starting": 10000})
        save_vault(vault)

        session_data = {"session_id": session_id, "created": time.time(), "trial": True}
        resp = JSONResponse({"authenticated": True, "trial": True,
                              "session_id": session_id, "credits_remaining": 10000})
        _set_session_cookie(resp, session_data)
        return resp

    # ── BYOK session (key provided) ──────────────────────────────────────────
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
    set_credential(vault, f"session:{session_id}:byok:api_key", {"api_key": api_key})
    save_vault(vault)

    session_data = {"session_id": session_id, "created": time.time()}
    resp = JSONResponse({"authenticated": True, "session_id": session_id,
                          "last_four": api_key[-4:]})
    _set_session_cookie(resp, session_data)
    return resp


@app.get("/session")
async def get_session(request: Request):
    session = _get_session(request)
    if not session:
        return {"authenticated": False}
    sid = session["session_id"]

    # ── trial session ────────────────────────────────────────────────────────
    trial = _get_trial_credits(sid)
    if trial:
        return {"authenticated": True, "trial": True,
                "credits_remaining": trial.get("remaining", 0)}

    # ── BYOK session ─────────────────────────────────────────────────────────
    api_key = _get_api_key(sid)
    if not api_key:
        return {"authenticated": False}

    connected = []
    vault = load_vault()
    for provider_key in APPS:
        cred = get_credential(vault, f"session:{sid}:mcp:{provider_key}")
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
    if not session:
        raise HTTPException(401, "Not authenticated")
    sid = session["session_id"]

    byok_key = _get_api_key(sid)
    trial = _get_trial_credits(sid) if not byok_key else None

    if not byok_key and not trial:
        raise HTTPException(401, "Not authenticated")

    # ── trial: check credits ─────────────────────────────────────────────────
    if trial:
        remaining = trial.get("remaining", 0)
        if remaining <= 0:
            async def exhausted_stream():
                msg = json.dumps({"trial_exhausted": True,
                    "message": "You've used your free trial credits. Add your own API key to keep chatting."})
                yield f"data: {msg}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            return StreamingResponse(exhausted_stream(), media_type="text/event-stream")

    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")

    workspace = _derive_workspace(sid)
    chat_id = str(sid)
    history = store.load_window(workspace, CHANNEL, chat_id)
    store.append(workspace, CHANNEL, chat_id, "user", message)

    # ── select key: BYOK or platform trial key ───────────────────────────────
    if byok_key:
        api_key = byok_key
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise HTTPException(500, "Platform AI key not configured")

    agent = await route(CHANNEL, chat_id)
    runner = Runner(agent, api_key=api_key)
    await wire_runner(runner, scope=f"session:{sid}")

    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_token(chunk: str) -> None:
        await queue.put(chunk)

    # ── usage tracking for trial sessions ────────────────────────────────────
    usage_totals = {"input": 0, "output": 0}

    async def on_usage(inp: int, out: int) -> None:
        usage_totals["input"] += inp
        usage_totals["output"] += out

    async def run_agent() -> str:
        try:
            kwargs = {"message": message, "message_history": history, "on_token": on_token}
            if trial:
                kwargs["on_usage"] = on_usage
            return await runner.run(**kwargs)
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
            store.append(workspace, CHANNEL, chat_id, "assistant", final_text)

            # ── trial: deduct credits ────────────────────────────────────────
            done_payload: dict = {"done": True}
            if trial:
                total_used = usage_totals["input"] + usage_totals["output"]
                new_remaining = _deduct_trial_credits(sid, total_used)
                done_payload["credits_remaining"] = new_remaining
            yield f"data: {json.dumps(done_payload)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/chat/history")
async def chat_history(request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(401, "Not authenticated")
    sid = session["session_id"]
    workspace = _derive_workspace(sid)
    history = store.load_window(workspace, CHANNEL, str(sid))
    return {"messages": history}


@app.get("/oauth/start")
async def oauth_start(app: str = "", request: Request = None):
    session = _get_session(request) if request else None
    if not session:
        raise HTTPException(401, "Not authenticated")

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
        "state": f"{session['session_id']}:{provider_key}",
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

    # Parse session_id:provider_key from state
    session_id = ""
    provider_key = ""
    if state and ":" in state:
        session_id, provider_key = state.split(":", 1)
        provider_key = provider_key.strip().lower()
    else:
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
        cred_id = f"session:{session_id}:mcp:{provider_key}" if session_id else f"mcp:{provider_key}"
        set_credential(vault, cred_id, creds)
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
    sid = session["session_id"]
    result = []
    for provider_key, apps in APPS.items():
        cred = get_credential(vault, f"session:{sid}:mcp:{provider_key}")
        for app in apps:
            result.append({
                "id": app.server_id,
                "label": app.label,
                "provider": provider_key,
                "connected": cred is not None,
            })
    return {"apps": result}


# ── Memory (read-only viewer) ────────────────────────────────────────────────


@app.get("/memory")
async def get_memory(request: Request, agent: str = "sage"):
    """Return all memory entries for the current tenant/agent.
    Read-only — Sage manages its own memory; this is the human viewer."""
    session = _get_session(request)
    if not session:
        raise HTTPException(401, "Not authenticated")
    workspace = _derive_workspace(session["session_id"])
    entries: list[dict[str, str]] = []
    agent_dir = MEMORY_ROOT / workspace / agent

    index_path = agent_dir / "index.md"
    if not index_path.exists():
        return {"items": entries, "workspace": workspace, "agent": agent}

    for line in index_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ["):
            continue
        # Parse markdown link: "- [name](name.md) — description"
        try:
            link_part = stripped.split("](")[1].split(".md")[0]
            name = link_part.split("/")[-1]  # in case of subdirs
            desc_part = stripped.split(" — ", 1)
            description = desc_part[1] if len(desc_part) > 1 else ""
            content = memory_read(name, workspace, agent)
            entries.append({
                "name": name,
                "description": description,
                "content": content,
            })
        except (IndexError, ValueError):
            continue

    return {"items": entries, "workspace": workspace, "agent": agent}


# ── Tasks (activity feed, no engine) ─────────────────────────────────────────


@app.get("/tasks")
async def get_tasks(request: Request, channel: str = "web"):
    """Return a lightweight activity feed from conversation history.
    No task engine exists — this is derived from stored conversation files."""
    session = _get_session(request)
    if not session:
        raise HTTPException(401, "Not authenticated")
    workspace = _derive_workspace(session["session_id"])
    conv_dir = store.CONV_ROOT / workspace / channel
    conversations: list[dict] = []

    if conv_dir.exists():
        for f in sorted(conv_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                chat_id = f.stem
                lines = f.read_text().splitlines()
                msg_count = len([l for l in lines if l.strip()])
                mtime = f.stat().st_mtime
                last_activity = None
                if mtime:
                    from datetime import datetime, timezone
                    last_activity = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                conversations.append({
                    "chat_id": chat_id,
                    "message_count": msg_count,
                    "last_activity": last_activity,
                })
            except OSError:
                continue

    total_messages = sum(c["message_count"] for c in conversations)

    return {
        "conversations": conversations,
        "total_messages": total_messages,
        "workspace": workspace,
        "channel": channel,
    }
