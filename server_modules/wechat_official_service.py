"""Official WeChat (WeChat Official Account / 公众号, and WeChat Work / WeCom /
企业微信) — the CLOUD-resident channel service.

Context (see docs/design/reliability-audit-2-channels.md for the full audit
this module closes a gap from): a prior pass built the official-WeChat wire
protocol -- signature verification, inbound XML callback parsing, access-
token management, outbound send -- inside the GATEWAY, at
empyralis-gateway/src/channels/wechat/. That was the wrong home: the Gateway's
only sanctioned network posture is an outbound WSS reverse tunnel with "no
inbound holes" (docs/PLATFORM-MAP.md's Key Contract 7), but Tencent's
WeChat/WeCom callback contract REQUIRES a publicly reachable HTTPS URL Tencent
itself calls (GET to verify, POST per inbound message) -- the Gateway
structurally cannot receive that. Every other business/bot channel that needs
a real inbound webhook (Telegram-bot, Discord-bot, Slack) already lives
server-side for exactly this reason -- see sage_telegram_hosted_service.py's
module (webhook mounted in the cloud control-plane process) and
connectors_actions.py's slack_events_webhook. This module ports the SAME wire
protocol into that same home, mirroring the closest precedent:
hosted_bot_provisioning_service.py's per-agent BYO Telegram bot model
(agent_install_id-keyed public webhook, agent-scoped vault credential,
agent_channel_bindings row with is_inbound_owner=True) -- WeChat/WeCom
credentials are a workspace's own AppID+AppSecret (or CorpID+CorpSecret+
AgentId) pair, not a first-party pool the platform owns, so the BYO-per-agent
shape fits better than sage_telegram_hosted_service.py's single-hosted-bot
pairing-code model.

The gateway module at empyralis-gateway/src/channels/wechat/ is NOT deleted or
superseded by this file -- it is left in place per the porting task's own
instruction. Its module doc (types.ts) already documents the same conclusion
this module acts on ("putting it into production requires an explicit,
separate decision to open an inbound port... on whatever host runs it" --
this module IS that decision, made for the cloud control-plane process, which
already has a public HTTPS ingress every other business-channel webhook uses).

Algorithms below (signature, XML parsing, token fetch, outbound send) are
ported FAITHFULLY from the gateway TypeScript, not re-derived -- see each
section's doc comment for the exact source file this mirrors.

NOT implemented here (same documented gaps as the gateway prototype):
  - "safe mode" (encodingAesKey / msg_signature over an AES-256-CBC encrypted
    body) -- see signature.ts's module doc. If an account has message
    encryption turned on, inbound bodies won't be plaintext XML and the
    signature check alone is insufficient.
  - Non-text inbound message types (image/voice/video/location/link) and
    media replies -- each needs a separate authenticated media-download /
    upload-then-reference-by-media_id call this pass doesn't add.
  - Typing indicators -- WeChat/WeCom's customer-service/app-message send
    APIs have no typing-indicator primitive.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from server_modules import agent_bindings_repository as bindings
from server_modules.channel_transport import ChannelTransport

LOGGER = logging.getLogger(__name__)

# ── Account kinds ──
# Both official variants share one wire protocol (signature algorithm,
# inbound XML shape); they differ in token-fetch/send endpoints and in the
# credential field name (appid+secret vs corpid+corpsecret) -- mirrors
# types.ts's WeChatAccountKind.
ACCOUNT_KIND_OFFICIAL = "official_account"
ACCOUNT_KIND_WECOM = "wecom"
_VALID_ACCOUNT_KINDS = {ACCOUNT_KIND_OFFICIAL, ACCOUNT_KIND_WECOM}

# Single channel_key for both account kinds (account_kind lives in the
# binding's own metadata) -- mirrors CHANNEL_KEY_TELEGRAM /
# CHANNEL_KEY_DISCORD being one key regardless of BYO variant.
CHANNEL_KEY_WECHAT = "wechat_official"
WECHAT_VAULT_PROVIDER = "wechat_official"

OFFICIAL_ACCOUNT_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
OFFICIAL_ACCOUNT_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/custom/send"
WECOM_SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"

# Refresh this many seconds before Tencent's own expiry so an in-flight
# request never uses a token that expires mid-request. Ported from
# token-manager.ts's REFRESH_SKEW_SECONDS.
REFRESH_SKEW_SECONDS = 300

# errcodes Tencent returns for an expired/invalid access_token -- worth one
# invalidate-and-retry rather than a hard failure. Ported verbatim from
# outbound.ts's TOKEN_INVALID_ERRCODES.
TOKEN_INVALID_ERRCODES = {40001, 40014, 41001, 42001}

# Tencent's own text msgtype content length ceiling.
_WECHAT_MAX_MESSAGE_LENGTH = 2048


def _text(value: Any, fallback: str = "") -> str:
    token = str(value or "").strip()
    return token or fallback


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════ Signature verification ═══════════════════════
# Ported from empyralis-gateway/src/channels/wechat/signature.ts (both the
# GET server-verification handshake and every inbound POST carry the same
# `timestamp`/`nonce`/`signature` query params). Tencent's documented
# algorithm (identical for Official Account and WeCom's non-"safe mode"
# callbacks):
#   1. Take [token, timestamp, nonce], sort lexicographically, join with no
#      separator.
#   2. sha1() the joined string, hex-encode.
#   3. Compare to the `signature` query param.

def compute_wechat_signature(token: str, timestamp: str, nonce: str) -> str:
    parts = sorted(str(part or "") for part in (token, timestamp, nonce))
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def verify_wechat_server_signature(
    *, token: str, timestamp: str, nonce: str, signature: str,
) -> bool:
    """Verifies a request's `signature` against `token`+`timestamp`+`nonce`.

    Never raises for missing/malformed params -- returns False so callers
    can treat any failure uniformly as "reject the request", matching
    signature.ts's verifyWeChatServerSignature contract.
    """
    token = _text(token)
    timestamp = _text(timestamp)
    nonce = _text(nonce)
    signature = _text(signature)
    if not token or not timestamp or not nonce or not signature:
        return False
    expected = compute_wechat_signature(token, timestamp, nonce)
    # Constant-time compare -- mirrors signature.ts's use of timingSafeEqual.
    return hmac.compare_digest(expected, signature)


# ═══════════════════════════ XML parsing ═══════════════════════════
# Ported from empyralis-gateway/src/channels/wechat/xml.ts. WeChat's/WeCom's
# callback body is always a single flat <xml> element whose children are
# simple leaf tags, each either a bare text node or a <![CDATA[...]]>-wrapped
# one -- never nested elements, attributes, or external entities. A small
# regex is used deliberately instead of a general-purpose XML/DOM parser:
# this fixed, flat, attribute-free shape doesn't need one, and skipping one
# sidesteps XXE / entity-expansion vulnerability classes entirely.
_LEAF_TAG_PATTERN = re.compile(
    r"<([A-Za-z_][\w.-]*)>(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))</\1>",
    re.DOTALL,
)


def parse_wechat_xml(raw: str) -> Dict[str, str]:
    """Parses a flat WeChat/WeCom XML callback body into a plain string map.

    Returns an empty dict for empty/non-XML input rather than raising --
    matches xml.ts's parseWeChatXml contract.
    """
    fields: Dict[str, str] = {}
    for match in _LEAF_TAG_PATTERN.finditer(str(raw or "")):
        tag, cdata_value, plain_value = match.group(1), match.group(2), match.group(3)
        value = cdata_value if cdata_value is not None else (plain_value or "")
        fields[tag] = value.strip()
    return fields


# ═══════════════════════ Access-token fetch/cache/refresh ═══════════════════════
# Ported from empyralis-gateway/src/channels/wechat/token-manager.ts.
# Endpoints (both documented, stable, versioned Tencent APIs):
#   - Official Account: GET https://api.weixin.qq.com/cgi-bin/token
#       ?grant_type=client_credential&appid=APPID&secret=APPSECRET
#   - WeCom:             GET https://qyapi.weixin.qq.com/cgi-bin/gettoken
#       ?corpid=CORPID&corpsecret=CORPSECRET
# Both return {"access_token": "...", "expires_in": 7200} on success, or
# {"errcode": N, "errmsg": "..."} (errcode != 0) on failure.

class WeChatAccessTokenError(RuntimeError):
    def __init__(self, message: str, errcode: Optional[int] = None) -> None:
        super().__init__(message)
        self.errcode = errcode


def _build_token_url(account_kind: str, app_id: str, app_secret: str) -> str:
    if account_kind == ACCOUNT_KIND_WECOM:
        return f"{WECOM_TOKEN_URL}?corpid={app_id}&corpsecret={app_secret}"
    return f"{OFFICIAL_ACCOUNT_TOKEN_URL}?grant_type=client_credential&appid={app_id}&secret={app_secret}"


class WeChatAccessTokenManager:
    """In-memory access_token cache/refresh for one credential pair.

    Ported from token-manager.ts's WeChatAccessTokenManager class: fetches a
    fresh token only when the cache is empty or within REFRESH_SKEW_SECONDS
    of expiry, and coalesces concurrent callers during a refresh onto one
    in-flight fetch (a Lock here, a shared Promise there).
    """

    def __init__(self, account_kind: str, app_id: str, app_secret: str) -> None:
        self.account_kind = account_kind
        self.app_id = app_id
        self.app_secret = app_secret
        self._cached_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_access_token(self) -> str:
        now = time.time()
        if self._cached_token and self._expires_at > now:
            return self._cached_token
        async with self._lock:
            now = time.time()
            if self._cached_token and self._expires_at > now:
                return self._cached_token
            return await self._fetch_and_cache()

    def invalidate(self) -> None:
        """Drops the cached token -- call after a send fails with an
        invalid/expired-token errcode so the manager doesn't keep handing
        out a token Tencent has already rejected."""
        self._cached_token = None
        self._expires_at = 0.0

    async def _fetch_and_cache(self) -> str:
        url = _build_token_url(self.account_kind, self.app_id, self.app_secret)
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(url)
        if response.status_code != 200:
            raise WeChatAccessTokenError(f"WeChat token endpoint returned HTTP {response.status_code}.")
        try:
            body = response.json()
        except Exception as exc:
            raise WeChatAccessTokenError("WeChat token endpoint returned a non-JSON body.") from exc
        errcode = body.get("errcode")
        if errcode and errcode != 0:
            raise WeChatAccessTokenError(
                f"WeChat token fetch failed: {errcode} {body.get('errmsg', '')}".strip(), errcode,
            )
        access_token = _text(body.get("access_token"))
        expires_in = body.get("expires_in")
        if not access_token or not isinstance(expires_in, (int, float)) or expires_in <= 0:
            raise WeChatAccessTokenError("WeChat token fetch returned no usable access_token.")
        ttl_seconds = max(expires_in - REFRESH_SKEW_SECONDS, 30)
        self._cached_token = access_token
        self._expires_at = time.time() + ttl_seconds
        return access_token


# credential_id -> WeChatAccessTokenManager, so repeated inbound callbacks
# and outbound sends for the same bound agent reuse one cached token instead
# of re-minting on every call (Tencent rate-limits token minting per
# credential per day).
_TOKEN_MANAGERS: Dict[str, WeChatAccessTokenManager] = {}


def _get_token_manager(credential_id: str, account_kind: str, app_id: str, app_secret: str) -> WeChatAccessTokenManager:
    manager = _TOKEN_MANAGERS.get(credential_id)
    if manager is None:
        manager = WeChatAccessTokenManager(account_kind, app_id, app_secret)
        _TOKEN_MANAGERS[credential_id] = manager
    return manager


# ═══════════════════════════ Outbound send ═══════════════════════════
# Ported from empyralis-gateway/src/channels/wechat/outbound.ts. Both are
# plain access-token-authenticated POSTs; text is the only msgtype
# implemented (same gap outbound.ts documents).
#   - Official Account: POST .../cgi-bin/message/custom/send?access_token=TOKEN
#       body: {"touser":"<openid>","msgtype":"text","text":{"content":"..."}}
#     Only usable within Tencent's 48h customer-service reply window after
#     the user's last inbound message -- Tencent's constraint, not this
#     code's; sending outside it returns errcode 45015.
#   - WeCom:             POST .../cgi-bin/message/send?access_token=TOKEN
#       body: {"touser":"<userid>","msgtype":"text","agentid":<AgentId>,"text":{"content":"..."}}
#     No 48h window restriction -- WeCom is an internal/enterprise channel.

def _build_send_url(account_kind: str, access_token: str) -> str:
    base = WECOM_SEND_URL if account_kind == ACCOUNT_KIND_WECOM else OFFICIAL_ACCOUNT_SEND_URL
    return f"{base}?access_token={access_token}"


def _build_send_body(account_kind: str, agent_id: Optional[str], remote_jid: str, text: str) -> Dict[str, Any]:
    if account_kind == ACCOUNT_KIND_WECOM:
        body: Dict[str, Any] = {"touser": remote_jid, "msgtype": "text", "text": {"content": text}}
        if agent_id:
            try:
                body["agentid"] = int(agent_id)
            except (TypeError, ValueError):
                LOGGER.warning("WeCom agent_id %r is not an integer; sending without agentid.", agent_id)
        return body
    return {"touser": remote_jid, "msgtype": "text", "text": {"content": text}}


async def _post_send(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        response = await client.post(url, json=body)
    try:
        return response.json()
    except Exception:
        return {}


async def send_wechat_text_message(
    *,
    account_kind: str,
    agent_id: Optional[str],
    token_manager: WeChatAccessTokenManager,
    remote_jid: str,
    text: str,
) -> Dict[str, Any]:
    """Sends one plaintext message and returns a normalized result dict.

    Ported from outbound.ts's sendWeChatTextMessage: never raises for a
    Tencent-side rejection (errcode != 0), only for a network/transport
    failure or an inability to obtain an access_token at all
    (WeChatAccessTokenError propagates). On a token-invalid errcode,
    invalidates the cached token and retries once with a freshly fetched
    one before giving up.
    """
    remote_jid = _text(remote_jid)
    text = _text(text)[:_WECHAT_MAX_MESSAGE_LENGTH]
    if not remote_jid or not text:
        return {"ok": False, "errmsg": "remote_jid and text are required."}

    body = _build_send_body(account_kind, agent_id, remote_jid, text)
    access_token = await token_manager.get_access_token()
    raw = await _post_send(_build_send_url(account_kind, access_token), body)
    errcode = raw.get("errcode")

    if errcode and errcode in TOKEN_INVALID_ERRCODES:
        token_manager.invalidate()
        access_token = await token_manager.get_access_token()
        raw = await _post_send(_build_send_url(account_kind, access_token), body)
        errcode = raw.get("errcode")

    if errcode and errcode != 0:
        return {"ok": False, "errcode": errcode, "errmsg": raw.get("errmsg"), "raw": raw}
    return {"ok": True, "errcode": 0, "raw": raw}


# ═══════════════════════ Inbound message mapping ═══════════════════════
# Ported from empyralis-gateway/src/channels/wechat/message-mapper.ts.

def _channel_origin(account_kind: str) -> str:
    """Distinguishes which official-WeChat variant a message came through,
    for channel_origin tagging (dispatch_command / dispatch_sage_reply_safe's
    channel_origin param) -- mirrors message-mapper.ts's wechatChannelKey,
    repurposed here since the DB binding itself uses one shared
    CHANNEL_KEY_WECHAT regardless of account_kind."""
    return "wechat_work" if account_kind == ACCOUNT_KIND_WECOM else "wechat_official_account"


def _epoch_seconds_to_iso(value: Any) -> str:
    try:
        seconds = int(str(value or "").strip())
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return _now_iso()
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def map_wechat_inbound_message(fields: Dict[str, str], account_kind: str) -> Optional[Dict[str, Any]]:
    """Maps one parsed inbound XML message to a plain dict, or None when
    there's nothing worth routing to an agent.

    Ported from message-mapper.ts's mapWeChatInboundMessage. Returns None
    when:
      - MsgType is anything other than "text" (image/voice/video/location/
        link require a separate authenticated media-download call this pass
        doesn't implement; "event" covers subscribe/unsubscribe/menu-click
        callbacks, which are account lifecycle signals, not chat messages).
      - Content is empty after trimming.
      - FromUserName (the sender's OpenID/UserID -- becomes remote_jid, the
        id every downstream reply is addressed to) is missing.
    """
    msg_type = _text(fields.get("MsgType")).lower()
    if msg_type != "text":
        return None
    remote_jid = _text(fields.get("FromUserName"))
    text = _text(fields.get("Content"))
    if not remote_jid or not text:
        return None
    external_message_id = _text(fields.get("MsgId")) or f"{remote_jid}:{_text(fields.get('CreateTime'))}"
    return {
        "channel_origin": _channel_origin(account_kind),
        "external_message_id": external_message_id,
        "remote_jid": remote_jid,
        "sender_jid": remote_jid,
        "text": text,
        "received_at": _epoch_seconds_to_iso(fields.get("CreateTime")),
        "from_me": False,
    }


# ═══════════════════════ Vault credential storage ═══════════════════════
# Mirrors hosted_bot_provisioning_service.py's store_byo_bot_credential /
# resolve_bot_token / delete_vault_credential_by_id exactly, generalized for
# WeChat's 3-field credential (app_secret/corpsecret + verify_token, app_id/
# corpid stored unencrypted in binding metadata just like Telegram's
# bot_username).

def store_wechat_credential(
    *,
    workspace_id: str,
    agent_install_id: str,
    account_kind: str,
    app_id: str,
    app_secret: str,
    verify_token: str,
    agent_id: Optional[str] = None,
) -> str:
    """Store WeChat/WeCom credentials as an AGENT-scoped vault credential."""
    from server_modules.vault_store import add_credential, _openssl_encrypt

    cred_id = str(uuid.uuid4())
    now = _now_iso()
    label = "WeCom" if account_kind == ACCOUNT_KIND_WECOM else "WeChat Official Account"
    entry = {
        "id": cred_id,
        "label": f"{label} ({app_id})",
        "provider": WECHAT_VAULT_PROVIDER,
        "workspace_id": _text(workspace_id) or None,
        "agent_install_id": _text(agent_install_id),
        "account_label": "default",
        "mode": "byok",
        "metadata": {
            "account_kind": account_kind,
            "app_id": app_id,
            "agent_id": _text(agent_id) or None,
        },
        "created_at": now,
        "updated_at": now,
        "encrypted_secret": _openssl_encrypt(json.dumps({
            "app_secret": _text(app_secret),
            "verify_token": _text(verify_token),
        }, separators=(",", ":"))),
    }
    add_credential(entry)
    return cred_id


def resolve_wechat_credential(credential_id: str) -> Dict[str, str]:
    """Decrypt and return {app_secret, verify_token} for a vault credential id."""
    from server_modules.vault_store import get_credential, _openssl_decrypt

    entry = get_credential(_text(credential_id))
    if entry is None:
        raise RuntimeError("WeChat credential not found.")
    enc = entry.get("encrypted_secret")
    if not isinstance(enc, str) or not enc:
        raise RuntimeError("Credential payload missing.")
    payload = json.loads(_openssl_decrypt(enc))
    return {
        "app_secret": _text(payload.get("app_secret")),
        "verify_token": _text(payload.get("verify_token")),
    }


def delete_vault_credential_by_id(credential_id: str) -> bool:
    from server_modules.vault_store import delete_credential
    cid = _text(credential_id)
    if not cid:
        return False
    return delete_credential(cid)


# ═══════════════════════════ Webhook URL helpers ═══════════════════════════

def webhook_base_url() -> str:
    for key in ("EMPYRALIS_PUBLIC_BASE_URL", "PUBLIC_BASE_URL"):
        val = _text(os.getenv(key)).rstrip("/")
        if val:
            return val
    return ""


def agent_wechat_webhook_url(agent_install_id: str) -> str:
    base = webhook_base_url()
    if not base:
        return ""
    return f"{base}/api/channels/wechat/webhook/{agent_install_id}"


class WeChatAlreadyBoundError(RuntimeError):
    """Raised when a WeChat/WeCom AppID/CorpID is already bound to another agent."""


def is_valid_account_kind(value: str) -> bool:
    return _text(value).lower() in _VALID_ACCOUNT_KINDS


# ═══════════════════════════ Assignment / release ═══════════════════════════
# Mirrors hosted_bot_provisioning_service.assign_byo_bot's structure: real
# credential validation (a live token fetch here, get_me() there), a soft
# find_inbound_owner_conflict pre-check for a friendly error, and the DB
# unique index as the race-free backstop.
#
# KNOWN GAP, explicitly flagged (not fixed by this pass, out of this task's
# file scope -- see docs/design/reliability-audit-2-channels.md's Slack
# section for the identical bug class this branch's own commit b7f17d367
# already fixed for 'slack' and a follow-up fixed for 'github'):
# CHANNEL_KEY_WECHAT ('wechat_official') is NOT currently included in
# control_plane_repository.py's uq_agent_channel_bindings_inbound_owner_v2
# predicate. Until it's added there (and, for an already-provisioned
# database, via a hand-run migration mirroring
# migrations/fix_slack_channel_uniqueness.sql), the soft pre-check below is
# advisory only -- two concurrent binds of the same AppID/CorpID to
# different agents are not yet blocked at the DB level for this channel_key.

async def assign_wechat_official(
    *,
    agent_install_id: str,
    workspace_id: str,
    tenant_id: str,
    account_kind: str,
    app_id: str,
    app_secret: str,
    verify_token: str,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate WeChat/WeCom credentials by fetching a real access_token,
    store them agent-scoped, and write the enabled channel binding."""
    account_kind = _text(account_kind).lower() or ACCOUNT_KIND_OFFICIAL
    if account_kind not in _VALID_ACCOUNT_KINDS:
        raise RuntimeError(f"Unknown WeChat account_kind: {account_kind!r}")
    app_id = _text(app_id)
    app_secret = _text(app_secret)
    verify_token = _text(verify_token)
    if not app_id or not app_secret or not verify_token:
        raise RuntimeError(
            "AppID/CorpID, AppSecret/CorpSecret, and the verify token are all required."
        )
    if account_kind == ACCOUNT_KIND_WECOM and not _text(agent_id):
        raise RuntimeError("agent_id (WeCom app AgentId) is required for a WeCom account.")

    # MAN-300: agent_install_id is caller-supplied and must be confirmed to
    # belong to THIS (tenant_id, workspace_id) before anything is written --
    # see agent_bindings_repository.agent_install_in_scope for why RLS alone
    # does not catch a cross-workspace id here (same MAN-206 gap: the
    # inbound webhook below resolves purely by agent_install_id via
    # get_channel_binding_by_agent_unscoped, so a planted binding would
    # route real inbound WeChat/WeCom traffic to another tenant's agent).
    if not await bindings.agent_install_in_scope(
        agent_install_id, tenant_id=tenant_id, workspace_id=workspace_id,
    ):
        raise bindings.AgentInstallNotInScopeError(
            "This agent does not belong to your workspace."
        )

    # Validate credentials for real -- a bad AppID/Secret pair fails here,
    # exactly like assign_byo_bot's get_me() call validates a Telegram token.
    probe_manager = WeChatAccessTokenManager(account_kind, app_id, app_secret)
    await probe_manager.get_access_token()

    conflict = await bindings.find_inbound_owner_conflict(
        tenant_id=tenant_id, workspace_id=workspace_id,
        channel_key=CHANNEL_KEY_WECHAT, endpoint_key=app_id,
        exclude_agent_install_id=agent_install_id,
    )
    if conflict is not None:
        raise WeChatAlreadyBoundError(
            "This WeChat/WeCom AppID/CorpID is already bound to another agent in this workspace."
        )

    cred_id = store_wechat_credential(
        workspace_id=workspace_id, agent_install_id=agent_install_id, account_kind=account_kind,
        app_id=app_id, app_secret=app_secret, verify_token=verify_token, agent_id=agent_id,
    )

    webhook_url = agent_wechat_webhook_url(agent_install_id)
    try:
        await bindings.upsert_channel_binding(
            tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id,
            channel_key=CHANNEL_KEY_WECHAT, enabled=True,
            binding={
                "endpoint_key": app_id,
                "is_inbound_owner": True,
                "source": "byo",
                "credential_id": cred_id,
                "account_kind": account_kind,
                "app_id": app_id,
                "agent_id": _text(agent_id) or None,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- translate the structural guarantee
        delete_vault_credential_by_id(cred_id)
        if bindings.is_inbound_owner_conflict(exc):
            raise WeChatAlreadyBoundError(
                "This WeChat/WeCom AppID/CorpID is already bound to another agent in this workspace."
            ) from exc
        raise
    _TOKEN_MANAGERS[cred_id] = probe_manager  # reuse the already-warmed token cache
    return {
        "source": "byo",
        "credential_id": cred_id,
        "account_kind": account_kind,
        "app_id": app_id,
        "webhook_url": webhook_url,
    }


async def release_agent_wechat(*, agent_install_id: str, workspace_id: str, tenant_id: str) -> Dict[str, Any]:
    """Release an agent's WeChat/WeCom binding: clear the binding and delete
    the agent-scoped credential. No remote "delete webhook" call exists on
    Tencent's side (unlike Telegram's deleteWebhook) -- the callback URL
    stays registered in the WeChat/WeCom admin console until the user
    manually clears it there; this only stops this platform from answering it."""
    agent_bindings = await bindings.list_agent_channel_bindings(
        tenant_id=tenant_id, workspace_id=workspace_id, agent_install_id=agent_install_id, enabled_only=False,
    )
    target = next((b for b in agent_bindings if b.get("key") == CHANNEL_KEY_WECHAT), None)
    if target is None:
        return {"released": False, "reason": "no wechat binding for this agent"}

    binding_meta = target.get("binding") or {}
    cred_id = _text(binding_meta.get("credential_id"))
    credential_deleted = False
    if cred_id:
        credential_deleted = delete_vault_credential_by_id(cred_id)
        _TOKEN_MANAGERS.pop(cred_id, None)

    await bindings.delete_channel_binding(
        tenant_id=tenant_id, workspace_id=workspace_id,
        agent_install_id=agent_install_id, channel_key=CHANNEL_KEY_WECHAT,
    )
    return {"released": True, "credential_deleted": credential_deleted}


# ═══════════════════════ ChannelTransport implementation ═══════════════════════
# Mirrors TelegramHostedTransport (sage_telegram_hosted_service.py:568-641):
# the shared-core SageReplyDispatcher owns all reliability logic (guaranteed
# response, error classification, message splitting); this only implements
# the send/typing/format primitives.

class WeChatOfficialTransport(ChannelTransport):
    max_message_length: int = _WECHAT_MAX_MESSAGE_LENGTH
    # No typing-indicator primitive exists on WeChat's/WeCom's send APIs.
    supports_typing_indicator: bool = False

    def __init__(
        self,
        *,
        account_kind: str,
        agent_id: Optional[str],
        remote_jid: str,
        token_manager: WeChatAccessTokenManager,
    ) -> None:
        self.account_kind = account_kind
        self.agent_id = agent_id
        self.remote_jid = remote_jid
        self._token_manager = token_manager

    async def send_message(self, text: str, *, reply_to_id: Optional[str] = None) -> bool:
        # WeChat/WeCom's send API has no reply-to-message-id concept --
        # reply_to_id is accepted only for ChannelTransport interface
        # compatibility and is otherwise unused, same as it would be for any
        # channel without threaded replies.
        if not _text(text):
            return False
        try:
            result = await send_wechat_text_message(
                account_kind=self.account_kind, agent_id=self.agent_id,
                token_manager=self._token_manager, remote_jid=self.remote_jid, text=text,
            )
            return bool(result.get("ok"))
        except Exception as exc:
            LOGGER.warning(
                "WeChatOfficialTransport.send_message failed for remote_jid=%s: %s",
                self.remote_jid, exc,
            )
            return False


# ═══════════════════════ Inbound webhook handling ═══════════════════════
# Mirrors routes_sage_telegram_hosted.py's telegram_agent_byo_webhook +
# hosted_bot_provisioning_service.route_agent_inbound: the webhook URL
# carries only agent_install_id (no session), so the binding lookup is an
# intentional bypass_rls query keyed on that id
# (agent_bindings_repository.get_channel_binding_by_agent_unscoped).

async def _resolve_binding_and_credential(
    agent_install_id: str,
) -> Optional[tuple]:
    binding = await bindings.get_channel_binding_by_agent_unscoped(
        agent_install_id=agent_install_id, channel_key=CHANNEL_KEY_WECHAT,
    )
    if binding is None:
        return None
    binding_meta = binding.get("binding") or {}
    cred_id = _text(binding_meta.get("credential_id"))
    try:
        creds = resolve_wechat_credential(cred_id)
    except Exception as exc:
        LOGGER.warning(
            "wechat webhook: could not resolve credential for agent=%s: %s",
            agent_install_id, exc,
        )
        return None
    return binding, binding_meta, cred_id, creds


async def handle_server_verification(
    *, agent_install_id: str, timestamp: str, nonce: str, signature: str, echostr: str,
) -> Optional[str]:
    """GET handshake: returns echostr iff the signature checks out against
    this agent's own stored verify_token, else None (caller returns 403/404).
    """
    resolved = await _resolve_binding_and_credential(agent_install_id)
    if resolved is None:
        return None
    _binding, _meta, _cred_id, creds = resolved
    if not verify_wechat_server_signature(
        token=creds["verify_token"], timestamp=timestamp, nonce=nonce, signature=signature,
    ):
        return None
    return echostr


async def handle_inbound_callback(
    *, agent_install_id: str, timestamp: str, nonce: str, signature: str, raw_body: str,
) -> Dict[str, Any]:
    """POST callback: verify signature, parse XML, map to a chat message,
    and run a real turn as the bound agent -- its own persona, model
    binding, and memory scope, replying via ITS OWN WeChat/WeCom credentials.

    Returns a dict describing what happened; never raises (mirrors
    dispatch_sage_reply_safe's "the caller must not raise" contract) so the
    route layer can always ACK Tencent's callback within its ~5s window.
    """
    resolved = await _resolve_binding_and_credential(agent_install_id)
    if resolved is None:
        return {"routed": False, "reason": "no active wechat binding for this agent"}
    binding, binding_meta, cred_id, creds = resolved

    if not verify_wechat_server_signature(
        token=creds["verify_token"], timestamp=timestamp, nonce=nonce, signature=signature,
    ):
        return {"routed": False, "reason": "invalid_signature"}

    workspace_id = _text(binding.get("workspace_id"))
    account_kind = _text(binding_meta.get("account_kind")) or ACCOUNT_KIND_OFFICIAL
    app_id = _text(binding_meta.get("app_id"))
    agent_id = binding_meta.get("agent_id")

    fields = parse_wechat_xml(raw_body)
    mapped = map_wechat_inbound_message(fields, account_kind)
    if mapped is None:
        # Non-text MsgType / event / empty content -- nothing to route, but
        # Tencent still needs a 200 "success" ack (the route layer sends it
        # unconditionally past signature verification, same as server.ts's
        # module doc explains, to avoid a retry storm).
        return {"routed": True, "processed": False, "reason": "not_a_text_message"}

    try:
        token_manager = _get_token_manager(cred_id, account_kind, app_id, creds["app_secret"])
        transport = WeChatOfficialTransport(
            account_kind=account_kind, agent_id=agent_id,
            remote_jid=mapped["remote_jid"], token_manager=token_manager,
        )

        # ── Canonical inbound envelope (docs/design/inbound-envelope-design.md) ──
        # Every WeChat Official/WeCom sender is an external customer talking
        # to the bound agent 1:1 — Tencent's callback contract has no group
        # concept and this channel has never had an owner-linkage mechanism
        # (docs/design/inbound-attribution-audit.md §1). is_owner is always
        # False, never inferred — this is also what keeps "/" text from ever
        # being treated as an owner command below (envelope_allows_owner_commands
        # requires a verified owner on a private surface; False fails closed).
        from server_modules.inbound_envelope import (
            InboundEnvelope, EnvelopeSender, SurfaceKind, envelope_allows_owner_commands,
        )
        envelope = InboundEnvelope(
            platform="wechat_official",
            surface=SurfaceKind.DM,
            sender=EnvelopeSender(
                id=mapped["sender_jid"],
                # No nickname is available from the inbound XML callback —
                # WeChat's Official Account API only returns one via a
                # separate, authenticated user-info call this pass doesn't
                # make. render_envelope_header falls back to the id when
                # display_name is empty.
                display_name="",
                is_owner=False,
            ),
        )

        # ── FIX: per-customer thread scoping (was thread_id="sage-main" for
        # EVERY distinct customer — see this module's own docstring/the audit
        # for the cross-customer SQL-thread + turn-lock collapse this closes).
        # Mirrors sage_command_dispatcher.agent_sender_thread_id's per-
        # (agent, sender) keying, the same mechanism a resolved specialist
        # turn already uses on every other channel — deterministic, no DB
        # lookup, and this binding is always agent-scoped (one agent per
        # WeChat/WeCom AppID/CorpID) so agent_install_id is always the right
        # scoping key here.
        from server_modules.sage_command_dispatcher import agent_sender_thread_id
        _thread_id = agent_sender_thread_id(agent_install_id, mapped["sender_jid"])

        # ── Durable per-agent conversation memory (agent_conversation_memory) ──
        # The SQL thread store this module's own _thread_id keys into is
        # dead under the SQLite-fallback deployment path
        # (agent_conversation_memory.py's own module doc;
        # docs/design/audit-history-memory.md Part 1A/5) — this is the
        # durable read+write source of a customer's history instead. Keyed
        # per (agent, customer OpenID), same scoping as _thread_id above,
        # in the "{surface}:{remote_jid}" shape
        # personal_channel_sage_bridge_service.py's own per-silo keys use.
        from server_modules import agent_conversation_memory
        _mem_key = f"wechat_official:{mapped['sender_jid']}"
        try:
            _mem_prior = agent_conversation_memory.load_recent_turns(
                workspace_id=workspace_id, agent_id=agent_install_id, conversation_key=_mem_key,
            )
        except Exception:
            _mem_prior = []

        # "/" text from a WeChat customer must never be treated as a command
        # — envelope_allows_owner_commands is always False here (is_owner is
        # always False), so this is a structural no-op today, but the check
        # is explicit (not a hardcoded skip) so this stays correct if WeChat
        # ever gains a real owner-linkage mechanism.
        if envelope_allows_owner_commands(envelope):
            from server_modules.sage_command_dispatcher import dispatch_command
            cmd_reply = await dispatch_command(
                command=mapped["text"], workspace_id=workspace_id, thread_id=_thread_id,
                channel_origin=mapped["channel_origin"], sender_id=mapped["sender_jid"],
            )
            if cmd_reply is not None:
                delivered = await transport.send_message(cmd_reply)
                return {"routed": True, "processed": True, "reply_sent": delivered}

        from server_modules.sage_reply_dispatcher import dispatch_sage_reply_safe
        delivered = await dispatch_sage_reply_safe(
            transport=transport, workspace_id=workspace_id, message=mapped["text"],
            channel_origin=mapped["channel_origin"], sender_id=mapped["sender_jid"],
            reply_to_id=mapped["external_message_id"], thread_id=_thread_id,
            envelope=envelope,
            channel_prior_messages=_mem_prior,
            conversation_memory={
                "workspace_id": workspace_id, "agent_id": agent_install_id,
                "conversation_key": _mem_key,
            },
        )
        return {"routed": True, "processed": True, "reply_sent": delivered}
    except Exception:
        LOGGER.exception(
            "wechat inbound handling failed for agent=%s app_id=%s", agent_install_id, app_id,
        )
        return {"routed": True, "processed": False, "reason": "internal_error"}
