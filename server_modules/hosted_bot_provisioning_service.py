"""Per-agent Telegram bot provisioning — BYO token only.

Each agent that wants Telegram gets its OWN bot identity from the user's own
BotFather token (BYO). There is no platform-owned bot pool for specialist
agents — the ONE hosted bot the platform owns is reserved for Sage itself
(see sage_telegram_hosted_service.py + TelegramPairPanel.tsx on the fleet
home page), entirely separate from this module.

Assigning writes an enabled channel binding (Phase 2 `agent_channel_bindings`)
with endpoint_key = bot_username, which is the cloud-side one-bot-one-agent
guarantee (uq_agent_channel_bindings_active_inbound_owner).

Inbound routing is keyed by agent_install_id: an update delivered to an
agent's per-agent webhook resolves to exactly that agent, using a bypass_rls
lookup on agent_channel_bindings (see agent_bindings_repository.
get_channel_binding_by_agent_unscoped) since the webhook URL carries only the
agent_install_id — there is no session to derive a tenant from up front.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from server_modules import agent_bindings_repository as bindings
from server_modules.channel_transport import ChannelTransport

LOGGER = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
CHANNEL_KEY_TELEGRAM = "telegram_bot"
POOL_BOT_VAULT_PROVIDER = "telegram_bot"

# First-contact marketing reply: sent once per (agent, chat_id) the first time
# a not-yet-onboarded stranger messages an agent's BYO bot, if the agent has
# opted in. Off by default — see fleet_tools.py's telegram_first_contact_reply
# config key.
_FIRST_CONTACT_SEEN_SENDERS_CAP = 2000
_FIRST_CONTACT_LINK = "https://empyralis.ai"


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


# ── Vault: agent-scoped BYO tokens ──────────────────────────────────────────

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


def agent_bot_webhook_url(agent_install_id: str) -> str:
    base = webhook_base_url()
    if not base:
        return ""
    return f"{base}/api/sage/telegram-hosted/webhook/byo/{agent_install_id}"


# ── Assignment / release ────────────────────────────────────────────────────

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
    webhook_url = agent_bot_webhook_url(agent_install_id)
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
    and delete the agent-scoped credential."""
    agent_bindings = await bindings.list_agent_channel_bindings(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id, enabled_only=False,
    )
    target = next((b for b in agent_bindings if b.get("key") == CHANNEL_KEY_TELEGRAM), None)
    if target is None:
        return {"released": False, "reason": "no telegram binding for this agent"}

    binding_meta = target.get("binding") or {}
    result: Dict[str, Any] = {"released": True, "source": "byo", "webhook_deleted": False}

    # Best-effort webhook teardown + token cleanup.
    try:
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


class AgentBotTransport(ChannelTransport):
    """ChannelTransport for an agent's own BYO Telegram bot.

    Sends via THAT bot's own token (resolved per-binding from the vault), so
    the reply genuinely comes from the bot the customer is messaging.
    """

    max_message_length: int = 4096
    supports_typing_indicator: bool = True

    def __init__(self, *, token: str, chat_id: str) -> None:
        self.token = str(token or "").strip()
        self.chat_id = str(chat_id)

    async def send_message(self, text: str, *, reply_to_id: Optional[str] = None) -> bool:
        if not str(text or "").strip():
            return False
        reply_to = int(reply_to_id) if reply_to_id else None

        # Reuse the same MarkdownV2 conversion the shared hosted bot uses, so
        # per-agent replies get the same formatting fidelity — just via a
        # different bot token. Falls back to plain text on parse failure.
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown

        body: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": _to_telegram_markdown(text),
            "parse_mode": "MarkdownV2",
        }
        if reply_to is not None:
            body["reply_to_message_id"] = reply_to
        try:
            result = await telegram_api(self.token, "sendMessage", body)
            if result.get("ok"):
                return True
        except Exception:
            pass
        try:
            body.pop("parse_mode", None)
            body["text"] = text[: self.max_message_length]
            result = await telegram_api(self.token, "sendMessage", body)
            return bool(result.get("ok"))
        except Exception as exc:
            LOGGER.warning("AgentBotTransport.send_message failed for chat_id=%s: %s", self.chat_id, exc)
            return False

    async def start_typing(self) -> None:
        # Best-effort single pulse (Telegram typing indicators auto-expire
        # after ~5s) — not the repeating background loop the shared hosted
        # bot uses, to keep this transport's footprint small.
        try:
            await telegram_api(self.token, "sendChatAction", {"chat_id": self.chat_id, "action": "typing"})
        except Exception:
            pass

    async def stop_typing(self) -> None:
        return None

    def format_text(self, text: str) -> str:
        from server_modules.sage_telegram_hosted_service import _to_telegram_markdown

        return _to_telegram_markdown(text)


async def _maybe_send_first_contact_reply(
    *,
    install: Dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    agent_install_id: str,
    chat_id: str,
    agent_label: str,
    transport: "AgentBotTransport",
) -> None:
    """If this agent has opted into the first-contact marketing reply AND this
    chat_id has never messaged it before, send a one-time intro identifying it
    as an AI agent with a link, then remember the chat_id so it never repeats.

    Off by default. Best-effort: any failure here must never block the real
    turn that follows it.
    """
    try:
        meta = dict((install or {}).get("metadata") or {})
        if not bool(meta.get("telegram_first_contact_reply")):
            return
        seen = meta.get("telegram_seen_senders")
        seen_list = [str(s) for s in seen] if isinstance(seen, list) else []
        chat_key = str(chat_id or "").strip()
        if not chat_key or chat_key in seen_list:
            return

        intro = (
            f"👋 Hi! I'm {agent_label}, an AI agent — not a human. "
            f"I'm built on Empyralis. Learn more: {_FIRST_CONTACT_LINK}"
        )
        await transport.send_message(intro)

        seen_list.append(chat_key)
        seen_list = seen_list[-_FIRST_CONTACT_SEEN_SENDERS_CAP:]
        from server_modules import agent_registry_repository as repo
        await repo.update_workspace_agent_install(
            agent_install_id,
            tenant_id=tenant_id, workspace_id=workspace_id,
            metadata={"telegram_seen_senders": seen_list},
        )
    except Exception as exc:
        LOGGER.warning("first-contact reply best-effort failed for agent=%s chat_id=%s: %s", agent_install_id, chat_id, exc)


async def route_agent_inbound(
    *,
    agent_install_id: str,
    chat_id: str,
    message: str,
    sender_id: str = "",
    reply_to_message_id: Optional[int] = None,
    deliver: bool = True,
) -> Dict[str, Any]:
    """Route an inbound message from an agent's own BYO bot and run a REAL
    turn as THAT agent — its own persona, model/provider binding, and memory
    scope, in its own thread — replying via the bot's own token.
    """
    binding = await bindings.get_channel_binding_by_agent_unscoped(
        agent_install_id=agent_install_id, channel_key=CHANNEL_KEY_TELEGRAM,
    )
    if binding is None:
        return {"routed": False, "reason": "no active telegram binding for this agent"}

    workspace_id = str(binding.get("workspace_id") or "")
    tenant_id = str(binding.get("tenant_id") or "")
    binding_meta = binding.get("binding") or {}
    bot_username = str(binding_meta.get("bot_username") or "")
    credential_id = str(binding_meta.get("credential_id") or "")
    label = await _agent_label(agent_install_id, workspace_id, tenant_id)

    if not deliver:
        return {
            "routed": True,
            "agent_install_id": agent_install_id,
            "agent_label": label,
            "workspace_id": workspace_id,
            "bot_username": bot_username,
        }

    try:
        token = resolve_bot_token(credential_id)
    except Exception as exc:
        LOGGER.warning("route_agent_inbound: could not resolve bot token for %s: %s", agent_install_id, exc)
        return {
            "routed": True,
            "agent_install_id": agent_install_id,
            "agent_label": label,
            "workspace_id": workspace_id,
            "bot_username": bot_username,
            "reply_sent": False,
        }

    from server_modules import specialist_runtime_context as _src

    try:
        specialist_context = await _src.resolve_specialist_runtime_context(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            active_agent_install_id=agent_install_id,
            metadata={},
        )
    except Exception as exc:
        LOGGER.warning(
            "route_agent_inbound: specialist context resolution failed for %s: %s — falling back to Sage",
            agent_install_id, exc,
        )
        specialist_context = None

    transport = AgentBotTransport(token=token, chat_id=str(chat_id))

    try:
        from server_modules import agent_registry_repository as repo
        install = await repo.get_workspace_agent_install_bundle(
            agent_install_id, tenant_id=tenant_id, workspace_id=workspace_id,
        )
        await _maybe_send_first_contact_reply(
            install=install or {}, tenant_id=tenant_id, workspace_id=workspace_id,
            agent_install_id=agent_install_id, chat_id=str(chat_id), agent_label=label,
            transport=transport,
        )
    except Exception as exc:
        LOGGER.warning("route_agent_inbound: first-contact check failed (non-fatal) for %s: %s", agent_install_id, exc)

    # Stable per-agent thread so this agent's bot conversation has its own
    # history/memory scope, distinct from the workspace's sage-main thread.
    thread_id = f"thread_agent_{agent_install_id}"

    from server_modules.sage_reply_dispatcher import dispatch_sage_reply_safe

    # The real per-message Telegram user id, never the chat id — a group
    # chat_id is shared by every member (a BYO bot can be added to a group
    # by anyone since it's a real, discoverable Telegram bot), so
    # substituting it collapsed every distinct sender into the same
    # identity. Falls back to chat_id only if the caller has no sender_id
    # (Telegram omitted `from` entirely — never a real 1:1 DM).
    real_sender_id = str(sender_id or "").strip() or str(chat_id)

    delivered = await dispatch_sage_reply_safe(
        transport=transport,
        workspace_id=workspace_id,
        message=str(message or ""),
        channel_origin="telegram_agent_byo",
        sender_id=real_sender_id,
        thread_id=thread_id,
        reply_to_id=str(reply_to_message_id or "") or None,
        specialist_context=specialist_context,
    )

    return {
        "routed": True,
        "agent_install_id": agent_install_id,
        "agent_label": label,
        "workspace_id": workspace_id,
        "bot_username": bot_username,
        "reply_sent": delivered,
    }
