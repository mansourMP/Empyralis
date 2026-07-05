"""Phase 3B: Per-agent Telegram bot provisioning + hosted bot pool.

Each agent gets its OWN Telegram bot identity. Two sources:

  - POOL: a platform-owned bot claimed from `hosted_bot_pool`. The token lives
    in the vault platform-scoped (workspace_id NULL); the pool row references it.
  - BYO:  the user's own BotFather token, stored as an AGENT-scoped vault
    credential (the Phase 2 agent_install_id shape).

Either way, assigning writes an enabled channel binding (Phase 2
`agent_channel_bindings`) with endpoint_key = bot_username, which is the
cloud-side one-bot-one-agent guarantee (uq_agent_channel_bindings_active_inbound_owner).

Inbound routing is keyed by the bot: an update delivered to bot X's per-bot
webhook resolves to exactly the agent bound to bot X. No shared webhook, no
chat→workspace pairing ambiguity.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from server_modules import agent_bindings_repository as bindings
from server_modules import hosted_bot_pool_repository as pool_repo

LOGGER = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
CHANNEL_KEY_TELEGRAM = "telegram_bot"
POOL_BOT_VAULT_PROVIDER = "telegram_bot"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_secret() -> str:
    return secrets.token_urlsafe(24)


# ── Token-parametric Telegram Bot API ───────────────────────────────────────

async def telegram_api(token: str, method: str, body: Optional[dict] = None) -> dict:
    token = str(token or "").strip()
    if not token:
        raise RuntimeError("Telegram bot token is required.")
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(url, json=body or {})
        try:
            data = resp.json()
        except Exception:
            data = {"ok": False, "description": (resp.text or "")[:200]}
        if not data.get("ok"):
            LOGGER.warning("Telegram API %s failed: %s", method, data.get("description", "unknown"))
        return data


async def get_me(token: str) -> dict:
    """Validate a token against Telegram getMe. Returns the `result` dict
    ({id, username, first_name, ...}) or raises on failure."""
    data = await telegram_api(token, "getMe")
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getMe rejected the token: {data.get('description', 'invalid token')}")
    return data.get("result") or {}


async def set_webhook(token: str, *, url: str, secret_token: str) -> dict:
    return await telegram_api(token, "setWebhook", {
        "url": url,
        "secret_token": secret_token,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    })


async def delete_webhook(token: str) -> dict:
    return await telegram_api(token, "deleteWebhook", {"drop_pending_updates": True})


async def send_text(token: str, chat_id: str, text: str, *, reply_to_message_id: Optional[int] = None) -> dict:
    body: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        body["reply_to_message_id"] = reply_to_message_id
    return await telegram_api(token, "sendMessage", body)


# ── Vault: platform-scoped pool tokens & agent-scoped BYO tokens ────────────

def store_pool_bot_credential(*, token: str, bot_username: str, bot_id: Optional[str] = None) -> str:
    """Store a pool bot token in the vault PLATFORM-scoped (workspace_id=None).
    Returns the credential_id. Not visible to any workspace-scoped connector
    query (workspace_visible(None, <ws>) is False)."""
    from server_modules.vault_store import add_credential, _openssl_encrypt
    import json as _json

    cred_id = str(uuid.uuid4())
    now = _now_iso()
    entry = {
        "id": cred_id,
        "label": f"Hosted pool bot @{str(bot_username or '').lstrip('@')}",
        "provider": POOL_BOT_VAULT_PROVIDER,
        "workspace_id": None,           # platform-scoped
        "platform_scoped": True,
        "mode": "hosted_pool",
        "metadata": {"bot_username": str(bot_username or "").lstrip("@"), "bot_id": str(bot_id or "")},
        "created_at": now,
        "updated_at": now,
        "encrypted_secret": _openssl_encrypt(_json.dumps({"bot_token": str(token or "").strip()}, separators=(",", ":"))),
    }
    add_credential(entry)   # Phase 3C: single-row INSERT
    return cred_id


def store_byo_bot_credential(*, workspace_id: str, agent_install_id: str, token: str, bot_username: str, bot_id: Optional[str] = None) -> str:
    """Store a BYO bot token as an AGENT-scoped vault credential (Phase 2 shape)."""
    from server_modules.vault_store import add_credential, _openssl_encrypt
    import json as _json

    cred_id = str(uuid.uuid4())
    now = _now_iso()
    entry = {
        "id": cred_id,
        "label": f"Telegram @{str(bot_username or '').lstrip('@')}",
        "provider": POOL_BOT_VAULT_PROVIDER,
        "workspace_id": str(workspace_id or "").strip() or None,
        "agent_install_id": str(agent_install_id or "").strip(),   # Phase 2 agent scope
        "account_label": "default",
        "mode": "byok",
        "metadata": {"bot_username": str(bot_username or "").lstrip("@"), "bot_id": str(bot_id or "")},
        "created_at": now,
        "updated_at": now,
        "encrypted_secret": _openssl_encrypt(_json.dumps({"bot_token": str(token or "").strip()}, separators=(",", ":"))),
    }
    add_credential(entry)   # Phase 3C: single-row INSERT
    return cred_id


def resolve_bot_token(credential_id: str, *, workspace_id: Optional[str] = None) -> str:
    """Decrypt and return the bot_token for a vault credential id."""
    from server_modules.vault_store import get_credential, _openssl_decrypt
    import json as _json

    entry = get_credential(str(credential_id or "").strip())   # Phase 3C: single-row read
    if entry is None:
        raise RuntimeError("Bot credential not found.")
    enc = entry.get("encrypted_secret")
    if not isinstance(enc, str) or not enc:
        raise RuntimeError("Credential payload missing.")
    payload = _json.loads(_openssl_decrypt(enc))
    return str(payload.get("bot_token") or "").strip()


def delete_vault_credential_by_id(credential_id: str) -> bool:
    from server_modules.vault_store import delete_credential
    cid = str(credential_id or "").strip()
    if not cid:
        return False
    return delete_credential(cid)   # Phase 3C: single-row DELETE


# ── Webhook URL helpers ─────────────────────────────────────────────────────

def webhook_base_url() -> str:
    for key in ("ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL", "EMPYRALIS_PUBLIC_BASE_URL", "PUBLIC_BASE_URL"):
        val = str(os.getenv(key) or "").strip().rstrip("/")
        if val:
            return val
    return ""


def pool_bot_webhook_url(pool_bot_id: str) -> str:
    base = webhook_base_url()
    if not base:
        return ""
    return f"{base}/api/sage/telegram-hosted/webhook/{pool_bot_id}"


# ── Assignment / release ────────────────────────────────────────────────────

async def assign_pool_bot(*, agent_install_id: str, workspace_id: str, tenant_id: str) -> Dict[str, Any]:
    """Claim a free pool bot for the agent: set its webhook, write the enabled
    channel binding, return the bot identity."""
    claimed = await pool_repo.claim_free_bot(
        agent_install_id=agent_install_id, workspace_id=workspace_id, tenant_id=tenant_id,
    )
    if claimed is None:
        raise RuntimeError("No free hosted bot available in the pool. Add capacity with scripts/manage_bot_pool.py.")

    bot_username = claimed["bot_username"]
    # Ensure a webhook secret exists (a bot added via manage_bot_pool already
    # has one; rotate in only if somehow missing).
    secret = claimed.get("webhook_secret") or _new_secret()
    if not claimed.get("webhook_secret"):
        from server_modules import control_plane_repository as cpr
        pool = await cpr.ensure_control_plane_schema()
        if pool is not None:
            await pool.execute("UPDATE hosted_bot_pool SET webhook_secret=$2, updated_at=NOW() WHERE id=$1", claimed["id"], secret)

    token = resolve_bot_token(claimed["credential_id"])
    webhook_set = False
    webhook_url = pool_bot_webhook_url(claimed["id"])
    if webhook_url:
        try:
            res = await set_webhook(token, url=webhook_url, secret_token=secret)
            webhook_set = bool(res.get("ok"))
        except Exception as exc:
            LOGGER.warning("assign_pool_bot: setWebhook best-effort failed: %s", exc)

    await bindings.upsert_channel_binding(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id,
        channel_key=CHANNEL_KEY_TELEGRAM, enabled=True,
        binding={
            "endpoint_key": bot_username,
            "is_inbound_owner": True,
            "source": "pool",
            "pool_bot_id": claimed["id"],
            "bot_username": bot_username,
        },
    )
    return {
        "source": "pool",
        "pool_bot_id": claimed["id"],
        "bot_username": bot_username,
        "webhook_url": webhook_url,
        "webhook_set": webhook_set,
    }


async def assign_byo_bot(*, agent_install_id: str, workspace_id: str, tenant_id: str, token: str) -> Dict[str, Any]:
    """Validate a user's BotFather token, store it agent-scoped, register its
    webhook, and write the enabled channel binding."""
    token = str(token or "").strip()
    if not token:
        raise RuntimeError("A bot token is required for a BYO Telegram bot.")
    me = await get_me(token)  # validates the token; raises on failure
    bot_username = str(me.get("username") or "").strip()
    bot_id = str(me.get("id") or "").strip()
    if not bot_username:
        raise RuntimeError("Telegram getMe returned no username for this token.")

    cred_id = store_byo_bot_credential(
        workspace_id=workspace_id, agent_install_id=agent_install_id,
        token=token, bot_username=bot_username, bot_id=bot_id,
    )

    secret = _new_secret()
    webhook_set = False
    base = webhook_base_url()
    webhook_url = f"{base}/api/sage/telegram-hosted/webhook/byo/{agent_install_id}" if base else ""
    if webhook_url:
        try:
            res = await set_webhook(token, url=webhook_url, secret_token=secret)
            webhook_set = bool(res.get("ok"))
        except Exception as exc:
            LOGGER.warning("assign_byo_bot: setWebhook best-effort failed: %s", exc)

    await bindings.upsert_channel_binding(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id,
        channel_key=CHANNEL_KEY_TELEGRAM, enabled=True,
        binding={
            "endpoint_key": bot_username,
            "is_inbound_owner": True,
            "source": "byo",
            "credential_id": cred_id,
            "bot_username": bot_username,
            "webhook_secret": secret,
        },
    )
    return {
        "source": "byo",
        "credential_id": cred_id,
        "bot_username": bot_username,
        "webhook_url": webhook_url,
        "webhook_set": webhook_set,
    }


async def release_agent_telegram(*, agent_install_id: str, workspace_id: str, tenant_id: str) -> Dict[str, Any]:
    """Release an agent's Telegram bot: delete the webhook, clear the binding,
    and (pool) return the bot to the pool with a rotated secret, or (byo) delete
    the agent-scoped credential."""
    agent_bindings = await bindings.list_agent_channel_bindings(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id, enabled_only=False,
    )
    target = next((b for b in agent_bindings if b.get("key") == CHANNEL_KEY_TELEGRAM), None)
    if target is None:
        return {"released": False, "reason": "no telegram binding for this agent"}

    binding_meta = target.get("binding") or {}
    source = str(binding_meta.get("source") or "").strip()
    result: Dict[str, Any] = {"released": True, "source": source, "webhook_deleted": False}

    # Best-effort webhook teardown + token cleanup.
    try:
        if source == "pool":
            pool_bot_id = str(binding_meta.get("pool_bot_id") or "").strip()
            bot = await pool_repo.get_bot(pool_bot_id) if pool_bot_id else None
            if bot is not None:
                try:
                    token = resolve_bot_token(bot["credential_id"])
                    res = await delete_webhook(token)
                    result["webhook_deleted"] = bool(res.get("ok"))
                except Exception as exc:
                    LOGGER.warning("release: pool deleteWebhook best-effort failed: %s", exc)
                await pool_repo.release_bot(pool_bot_id=pool_bot_id, new_webhook_secret=_new_secret())
                result["pool_bot_id"] = pool_bot_id
        elif source == "byo":
            cred_id = str(binding_meta.get("credential_id") or "").strip()
            if cred_id:
                try:
                    token = resolve_bot_token(cred_id, workspace_id=workspace_id)
                    res = await delete_webhook(token)
                    result["webhook_deleted"] = bool(res.get("ok"))
                except Exception as exc:
                    LOGGER.warning("release: byo deleteWebhook best-effort failed: %s", exc)
                delete_vault_credential_by_id(cred_id)
                result["credential_deleted"] = True
    finally:
        await bindings.delete_channel_binding(
            tenant_id=tenant_id, workspace_id=workspace_id,
            agent_install_id=agent_install_id, channel_key=CHANNEL_KEY_TELEGRAM,
        )
    return result


# ── Inbound routing (bot → agent) ───────────────────────────────────────────

async def resolve_inbound_agent(pool_bot_id: str) -> Optional[Dict[str, Any]]:
    """Given the pool bot an update was delivered to, resolve exactly the agent
    it is assigned to. Pure routing — no LLM. Returns None if the bot is
    unassigned (free/quarantined)."""
    bot = await pool_repo.get_bot(str(pool_bot_id or "").strip())
    if bot is None:
        return None
    agent_install_id = bot.get("assigned_agent_install_id")
    if not agent_install_id or bot.get("status") != "assigned":
        return None
    return {
        "pool_bot_id": bot["id"],
        "agent_install_id": agent_install_id,
        "workspace_id": bot.get("assigned_workspace_id"),
        "tenant_id": bot.get("assigned_tenant_id"),
        "bot_username": bot.get("bot_username"),
        "credential_id": bot.get("credential_id"),
        "webhook_secret": bot.get("webhook_secret"),
        "source": "pool",
    }


async def _agent_label(agent_install_id: str, workspace_id: str, tenant_id: str) -> str:
    try:
        from server_modules import agent_registry_repository as repo
        installs = await repo.list_workspace_agent_installs(
            tenant_id=tenant_id, workspace_id=workspace_id, include_master=True,
        )
        for inst in installs or []:
            if str(inst.get("id") or "") == str(agent_install_id or ""):
                return str(inst.get("label") or "").strip() or "Agent"
    except Exception:
        pass
    return "Agent"


async def route_hosted_inbound(
    *,
    pool_bot_id: str,
    chat_id: str,
    message: str,
    reply_to_message_id: Optional[int] = None,
    deliver: bool = True,
) -> Dict[str, Any]:
    """Route an inbound message from a pool bot to its assigned agent and send
    an attributed reply via THAT bot's token.

    Per the Phase 3B scope decision: routing + attributed reply. The reply is
    attributed to the resolved agent so the two-agent proof is real; swapping in
    the full specialist turn runtime is a later phase.
    """
    routed = await resolve_inbound_agent(pool_bot_id)
    if routed is None:
        return {"routed": False, "reason": "bot is not assigned to any agent"}

    label = await _agent_label(routed["agent_install_id"], routed["workspace_id"], routed["tenant_id"])
    reply_text = f"[{label}] received: {message}".strip()

    sent = False
    if deliver:
        try:
            token = resolve_bot_token(routed["credential_id"])
            res = await send_text(token, str(chat_id), reply_text, reply_to_message_id=reply_to_message_id)
            sent = bool(res.get("ok"))
        except Exception as exc:
            LOGGER.warning("route_hosted_inbound: send best-effort failed: %s", exc)

    return {
        "routed": True,
        "agent_install_id": routed["agent_install_id"],
        "agent_label": label,
        "workspace_id": routed["workspace_id"],
        "bot_username": routed["bot_username"],
        "reply_text": reply_text,
        "reply_sent": sent,
    }
