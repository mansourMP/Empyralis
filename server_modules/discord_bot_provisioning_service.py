"""Phase 3D: Per-agent Discord bot provisioning (BYO only).

Mirrors the Phase 3B Telegram per-agent model for Discord. Each agent gets its
OWN Discord bot identity via the user's own bot token (BYO). There is no hosted
Discord pool yet — BYO only.

Assigning:
  - validates the bot token against the Discord API (GET /users/@me),
  - stores the token as an AGENT-scoped vault credential with
    ``provider = "discord_bot"`` (the shape ``connectors.discord_bot_runtime_service``
    discovers and connects with), and
  - writes an enabled channel binding (``agent_channel_bindings``,
    ``channel_key = "discord_bot"``) carrying ``endpoint_key`` = the bot's user id
    and ``is_inbound_owner = true``.

The ``is_inbound_owner`` flag is what arms the partial unique index
``uq_agent_channel_bindings_active_inbound_owner`` — that index is the cloud-side
one-bot-one-agent guarantee (a second agent cannot bind the same bot). The
on-host counterpart is the Phase 3A credential file-lock already held by
``discord_bot_runtime_service`` while a bot is running. Both layers run together.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from server_modules import agent_bindings_repository as bindings

LOGGER = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"
CHANNEL_KEY_DISCORD = "discord_bot"
DISCORD_VAULT_PROVIDER = "discord_bot"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Token-parametric Discord Bot API ────────────────────────────────────────

async def discord_get_me(token: str) -> Dict[str, Any]:
    """Validate a bot token and return the bot's own user object.

    Raises RuntimeError on an invalid/unauthorized token.
    """
    token = str(token or "").strip()
    if not token:
        raise RuntimeError("A Discord bot token is required for a BYO Discord bot.")
    url = f"{DISCORD_API_BASE}/users/@me"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(url, headers={"Authorization": f"Bot {token}"})
    if resp.status_code == 401:
        raise RuntimeError("Discord rejected this bot token (401 Unauthorized).")
    if resp.status_code != 200:
        detail = (resp.text or "")[:200]
        raise RuntimeError(f"Discord getMe failed ({resp.status_code}): {detail}")
    data = resp.json()
    if not isinstance(data, dict) or not str(data.get("id") or "").strip():
        raise RuntimeError("Discord getMe returned no bot id for this token.")
    return data


# ── Vault credential (agent-scoped) ─────────────────────────────────────────

def store_byo_discord_credential(
    *, workspace_id: str, agent_install_id: str, token: str, bot_username: str, bot_id: str
) -> str:
    """Store a BYO Discord bot token as an AGENT-scoped vault credential.

    Shape matches ``connectors.discord_bot_runtime_service._connector_rows``
    (``provider = "discord_bot"``) so the runtime discovers it, and carries
    ``metadata.discord_endpoint_key`` so the runtime's ``_endpoint_key`` resolves
    to the same identity used in the channel binding.
    """
    from server_modules.vault_store import add_credential, _openssl_encrypt

    cred_id = str(uuid.uuid4())
    now = _now_iso()
    entry = {
        "id": cred_id,
        "label": f"Discord {str(bot_username or '').strip() or bot_id}",
        "provider": DISCORD_VAULT_PROVIDER,
        "workspace_id": str(workspace_id or "").strip() or None,
        "agent_install_id": str(agent_install_id or "").strip(),
        "account_label": "default",
        "mode": "byok",
        "metadata": {
            "bot_username": str(bot_username or "").strip(),
            "bot_id": str(bot_id or "").strip(),
            "discord_endpoint_key": str(bot_id or "").strip(),
        },
        "created_at": now,
        "updated_at": now,
        "encrypted_secret": _openssl_encrypt(
            json.dumps({"bot_token": str(token or "").strip()}, separators=(",", ":"))
        ),
    }
    add_credential(entry)   # Phase 3C: single-row INSERT
    return cred_id


def delete_vault_credential_by_id(credential_id: str) -> bool:
    from server_modules.vault_store import delete_credential

    cid = str(credential_id or "").strip()
    if not cid:
        return False
    return delete_credential(cid)   # Phase 3C: single-row DELETE


# ── Assignment / release ────────────────────────────────────────────────────

def _is_inbound_owner_conflict(exc: BaseException) -> bool:
    """True when an exception is the inbound-owner unique-index violation.

    Matches by substring so it survives index-name revisions (e.g. the
    ``..._inbound_owner_v2`` rebuild)."""
    constraint = str(getattr(exc, "constraint_name", "") or "")
    return "inbound_owner" in constraint or "inbound_owner" in str(exc)


class DiscordBotAlreadyBoundError(RuntimeError):
    """Raised when a Discord bot is already bound to a different agent."""


async def assign_agent_discord(
    *, agent_install_id: str, workspace_id: str, tenant_id: str, token: str
) -> Dict[str, Any]:
    """Bind a BYO Discord bot to exactly one agent.

    Validates the token, stores it agent-scoped, and writes the enabled
    inbound-owner channel binding. The one-bot-one-agent guarantee is enforced
    structurally by ``uq_agent_channel_bindings_active_inbound_owner`` — a second
    agent binding the same bot raises :class:`DiscordBotAlreadyBoundError`.
    """
    me = await discord_get_me(token)  # validates; raises on failure
    bot_id = str(me.get("id") or "").strip()
    bot_username = str(me.get("username") or "").strip()

    # Soft pre-check for a friendly error (the unique index below is the hard,
    # race-free guarantee — we still rely on it even if this check passes).
    existing = await bindings.list_workspace_channel_bindings(
        tenant_id=tenant_id, workspace_id=workspace_id, enabled_only=True,
    )
    for b in existing:
        if str(b.get("key")) != CHANNEL_KEY_DISCORD:
            continue
        meta = b.get("binding") or {}
        same_endpoint = str(meta.get("endpoint_key") or "").strip().lower() == bot_id.lower()
        other_agent_id = str(b.get("agent_install_id") or "").strip()
        other_agent = other_agent_id != str(agent_install_id or "").strip()
        if same_endpoint and str(meta.get("is_inbound_owner") or "").lower() == "true" and other_agent:
            owner_label = await bindings.get_agent_install_label(
                other_agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
            )
            owner_desc = f'"{owner_label}"' if owner_label else "another agent"
            raise DiscordBotAlreadyBoundError(
                f"Discord bot @{bot_username or bot_id} is already bound to {owner_desc} "
                f"in this workspace."
            )

    cred_id = store_byo_discord_credential(
        workspace_id=workspace_id, agent_install_id=agent_install_id,
        token=token, bot_username=bot_username, bot_id=bot_id,
    )
    try:
        await bindings.upsert_channel_binding(
            tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id,
            channel_key=CHANNEL_KEY_DISCORD, enabled=True,
            binding={
                "endpoint_key": bot_id,
                "is_inbound_owner": True,
                "source": "byo",
                "provider": "discord",
                "credential_id": cred_id,
                "bot_username": bot_username,
                "bot_id": bot_id,
            },
        )
    except Exception as exc:  # noqa: BLE001 — translate the structural guarantee
        # Roll back the orphaned credential we just wrote.
        delete_vault_credential_by_id(cred_id)
        if _is_inbound_owner_conflict(exc):
            raise DiscordBotAlreadyBoundError(
                f"Discord bot @{bot_username or bot_id} is already bound to another agent "
                f"in this workspace."
            ) from exc
        raise
    return {
        "source": "byo",
        "channel_key": CHANNEL_KEY_DISCORD,
        "credential_id": cred_id,
        "bot_id": bot_id,
        "bot_username": bot_username,
        "endpoint_key": bot_id,
    }


async def release_agent_discord(
    *, agent_install_id: str, workspace_id: str, tenant_id: str
) -> Dict[str, Any]:
    """Release an agent's Discord bot: delete the agent-scoped credential and
    clear the channel binding."""
    agent_bindings = await bindings.list_agent_channel_bindings(
        tenant_id=tenant_id, workspace_id=workspace_id,
        agent_install_id=agent_install_id, enabled_only=False,
    )
    target = next((b for b in agent_bindings if b.get("key") == CHANNEL_KEY_DISCORD), None)
    if target is None:
        return {"released": False, "reason": "no discord binding for this agent"}

    binding_meta = target.get("binding") or {}
    result: Dict[str, Any] = {"released": True, "source": str(binding_meta.get("source") or "")}
    try:
        cred_id = str(binding_meta.get("credential_id") or "").strip()
        if cred_id:
            delete_vault_credential_by_id(cred_id)
            result["credential_deleted"] = True
    finally:
        await bindings.delete_channel_binding(
            tenant_id=tenant_id, workspace_id=workspace_id,
            agent_install_id=agent_install_id, channel_key=CHANNEL_KEY_DISCORD,
        )
    return result
