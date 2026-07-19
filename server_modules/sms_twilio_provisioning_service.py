"""Per-agent SMS number provisioning via the platform's master Twilio account.

"Each agent gets its own phone number to text with." Unlike BYO Discord/
Telegram bots, SMS numbers are bought under ONE platform-held Twilio account
(the no-customer-API-key rule) — customers never paste Twilio keys. The
platform operator sets ``TWILIO_ACCOUNT_SID`` / ``TWILIO_AUTH_TOKEN`` once; if
either is unset this channel reports "not configured on this deployment" and
never crashes (mirrors how the connectors gate on missing credentials).

Provisioning (:func:`assign_agent_sms_number`):
  1. searches Twilio ``AvailablePhoneNumbers`` for an SMS-capable number,
  2. buys it via ``IncomingPhoneNumbers`` with ``SmsUrl`` → our webhook,
  3. stores it as an AGENT-scoped vault credential (provider ``sms_twilio``),
  4. writes the enabled inbound-owner channel binding (``channel_key = "sms"``,
     ``endpoint_key`` = the E.164 number) so
     ``agent_channel_router._resolve_agent_for_inbound`` routes inbound texts
     to THIS agent.

The Twilio Messages API send/receive plumbing itself is REUSED from
``connectors.whatsapp_transport_service.WhatsAppTransportService`` (send_sms,
twiml_response, validate_webhook_signature) — this module only adds number
lifecycle + platform-account credential resolution on top of it.

──────────────────────────────────────────────────────────────────────────────
FOLLOW-UPS EXPLICITLY OUT OF SCOPE (documented, NOT half-built here):

  * BILLING / METERING — per-message + monthly-number cost must be metered
    against workspace credits, exactly like hosted-AI credits. The correct
    hook point is ``usage_events_repository.record_usage_event(...)`` (the
    same normalized per-call ledger hosted-AI turns already write) plus an
    ``entitlements_service`` pre-send balance check. See the ``TODO(billing)``
    markers in :func:`send_platform_sms` (outbound) and the inbound webhook
    (``connectors_actions.sms_twilio_webhook``). Nothing is metered today.

  * US 10DLC / A2P CAMPAIGN REGISTRATION — before US SMS actually DELIVERS,
    the platform's Twilio account must register an A2P Brand + Campaign
    (carrier requirement, ~$2/campaign + a one-time brand vetting fee) and
    associate purchased numbers with a Messaging Service tied to that
    campaign. This is an operator/compliance step, not code. Until it is
    done, purchased US numbers can be bought and will receive inbound, but
    carrier filtering may block outbound A2P traffic. See ACTIVATION below.

ACTIVATION CHECKLIST (what an operator must do to turn this on):
  1. Set env ``TWILIO_ACCOUNT_SID`` and ``TWILIO_AUTH_TOKEN`` (platform master
     account).
  2. Set a public base URL env (``TWILIO_SMS_PUBLIC_BASE_URL`` or the generic
     ``EMPYRALIS_PUBLIC_BASE_URL`` / ``PUBLIC_BASE_URL``) so the webhook URL
     registered on each number is reachable by Twilio.
  3. Complete US 10DLC A2P Brand + Campaign registration and attach numbers to
     a Messaging Service (required for US delivery; non-US may differ).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from server_modules import agent_bindings_repository as bindings

LOGGER = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"

# Routing/binding key — matches the channel_key passed to
# agent_channel_router.route_inbound_channel_message and written into the
# agent_channel_bindings row, so inbound resolution matches (same pattern as
# Slack's "slack"). The catalog/connector identity is "sms_twilio"
# (channel_lane_contract_service), analogous to whatsapp_business → whatsapp.
CHANNEL_KEY_SMS = "sms"
SMS_VAULT_PROVIDER = "sms_twilio"
WEBHOOK_PATH = "/channels/sms/twilio/webhook"

NOT_CONFIGURED_MESSAGE = (
    "SMS (Twilio) is not configured on this deployment. Set TWILIO_ACCOUNT_SID "
    "and TWILIO_AUTH_TOKEN to enable it."
)


class SmsProvisioningError(RuntimeError):
    """Base error for SMS number provisioning."""


class SmsNotConfiguredError(SmsProvisioningError):
    """Raised when the platform Twilio master account env vars are unset."""


class SmsNumberUnavailableError(SmsProvisioningError):
    """Raised when Twilio has no available number matching the search."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Platform master account (env-gated) ──────────────────────────────────────

def platform_twilio_credentials() -> Optional[Dict[str, str]]:
    """The platform-held master Twilio account, or ``None`` when unset.

    ``None`` is the "not configured on this deployment" signal — callers must
    handle it gracefully (never crash), exactly like the connectors do."""
    sid = str(os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = str(os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    if not sid or not token:
        return None
    return {"account_sid": sid, "auth_token": token}


def is_configured() -> bool:
    return platform_twilio_credentials() is not None


def preflight() -> Dict[str, Any]:
    """Deployment status for this channel — safe to call with env unset."""
    configured = is_configured()
    base = webhook_base_url()
    return {
        "channel_key": "sms_twilio",
        "provider": SMS_VAULT_PROVIDER,
        "configured": configured,
        "webhook_url": sms_webhook_url() if (configured and base) else "",
        "public_base_url_set": bool(base),
        "status": "ready" if (configured and base) else "not_configured",
        "reason": None if (configured and base) else (
            NOT_CONFIGURED_MESSAGE if not configured
            else "Set TWILIO_SMS_PUBLIC_BASE_URL (or EMPYRALIS_PUBLIC_BASE_URL) so Twilio can reach the webhook."
        ),
        # Surfaced so the UI never silently blocks delivery: US A2P is a real
        # activation prerequisite handled outside this code.
        "requires_us_10dlc_registration": True,
    }


# ── Webhook URL helpers ──────────────────────────────────────────────────────

def webhook_base_url() -> str:
    for key in ("TWILIO_SMS_PUBLIC_BASE_URL", "EMPYRALIS_PUBLIC_BASE_URL", "PUBLIC_BASE_URL"):
        val = str(os.getenv(key) or "").strip().rstrip("/")
        if val:
            return val
    return ""


def sms_webhook_url() -> str:
    """The public URL registered as each number's ``SmsUrl``. Must equal the
    URL the inbound handler reconstructs for signature validation (base +
    WEBHOOK_PATH, no query) — see connectors_actions._sms_public_request_url."""
    base = webhook_base_url()
    if not base:
        return ""
    return f"{base}{WEBHOOK_PATH}"


# ── Twilio number lifecycle (platform master account) ───────────────────────

def _account_url(account_sid: str, suffix: str) -> str:
    return f"{TWILIO_API_BASE}/Accounts/{account_sid}{suffix}"


async def search_available_numbers(
    *,
    country: str = "US",
    area_code: Optional[str] = None,
    contains: Optional[str] = None,
    sms_enabled: bool = True,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Search the platform account's Twilio ``AvailablePhoneNumbers`` for
    SMS-capable local numbers. Raises :class:`SmsNotConfiguredError` if the
    platform master account env vars are unset."""
    creds = platform_twilio_credentials()
    if creds is None:
        raise SmsNotConfiguredError(NOT_CONFIGURED_MESSAGE)
    normalized_country = str(country or "US").strip().upper() or "US"
    url = _account_url(creds["account_sid"], f"/AvailablePhoneNumbers/{normalized_country}/Local.json")
    params: Dict[str, Any] = {"PageSize": max(1, min(int(limit or 10), 50))}
    if sms_enabled:
        params["SmsEnabled"] = "true"
    if area_code:
        params["AreaCode"] = str(area_code).strip()
    if contains:
        params["Contains"] = str(contains).strip()
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(url, params=params, auth=(creds["account_sid"], creds["auth_token"]))
    if resp.status_code != 200:
        detail = (resp.text or "")[:200]
        raise SmsProvisioningError(f"Twilio number search failed ({resp.status_code}): {detail}")
    data = resp.json() if resp.text else {}
    numbers = data.get("available_phone_numbers") if isinstance(data, dict) else None
    return [n for n in (numbers or []) if isinstance(n, dict)]


async def purchase_number(
    *,
    phone_number: str,
    sms_url: str,
    friendly_name: str = "",
) -> Dict[str, Any]:
    """Buy ``phone_number`` under the platform account with its ``SmsUrl``
    pointed at our webhook. Returns the created IncomingPhoneNumber resource
    (carries ``sid`` = the phone-number SID)."""
    creds = platform_twilio_credentials()
    if creds is None:
        raise SmsNotConfiguredError(NOT_CONFIGURED_MESSAGE)
    number = str(phone_number or "").strip()
    if not number:
        raise SmsProvisioningError("phone_number is required to purchase a Twilio number.")
    url = _account_url(creds["account_sid"], "/IncomingPhoneNumbers.json")
    payload: Dict[str, str] = {"PhoneNumber": number, "SmsMethod": "POST"}
    if sms_url:
        payload["SmsUrl"] = str(sms_url).strip()
    if friendly_name:
        payload["FriendlyName"] = str(friendly_name).strip()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(url, data=payload, auth=(creds["account_sid"], creds["auth_token"]))
    if resp.status_code not in (200, 201):
        detail = (resp.text or "")[:300]
        raise SmsProvisioningError(f"Twilio number purchase failed ({resp.status_code}): {detail}")
    data = resp.json() if resp.text else {}
    return data if isinstance(data, dict) else {}


async def set_number_sms_url(*, phone_number_sid: str, sms_url: str) -> Dict[str, Any]:
    """Re-point an already-owned number's ``SmsUrl`` at our webhook."""
    creds = platform_twilio_credentials()
    if creds is None:
        raise SmsNotConfiguredError(NOT_CONFIGURED_MESSAGE)
    sid = str(phone_number_sid or "").strip()
    if not sid:
        raise SmsProvisioningError("phone_number_sid is required to update SmsUrl.")
    url = _account_url(creds["account_sid"], f"/IncomingPhoneNumbers/{sid}.json")
    payload = {"SmsUrl": str(sms_url or "").strip(), "SmsMethod": "POST"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.post(url, data=payload, auth=(creds["account_sid"], creds["auth_token"]))
    if resp.status_code not in (200, 201):
        detail = (resp.text or "")[:300]
        raise SmsProvisioningError(f"Twilio SmsUrl update failed ({resp.status_code}): {detail}")
    data = resp.json() if resp.text else {}
    return data if isinstance(data, dict) else {}


async def release_number(*, phone_number_sid: str) -> bool:
    """Release (delete) a number from the platform account, stopping its
    monthly charge. Best-effort — returns False on failure rather than
    raising, so binding teardown always proceeds."""
    creds = platform_twilio_credentials()
    if creds is None:
        return False
    sid = str(phone_number_sid or "").strip()
    if not sid:
        return False
    url = _account_url(creds["account_sid"], f"/IncomingPhoneNumbers/{sid}.json")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            resp = await client.delete(url, auth=(creds["account_sid"], creds["auth_token"]))
        return resp.status_code in (200, 204)
    except Exception as exc:  # noqa: BLE001 — teardown must never block on network
        LOGGER.warning("release_number: best-effort delete failed for sid=%s: %s", sid, exc)
        return False


# ── Vault credential (agent-scoped) ─────────────────────────────────────────

def store_sms_credential(
    *,
    workspace_id: str,
    agent_install_id: str,
    phone_number: str,
    phone_number_sid: str,
    account_sid: str,
) -> str:
    """Store the agent's provisioned SMS number as an AGENT-scoped vault
    credential (provider ``sms_twilio``). The auth token is NOT stored — it
    lives only in env on the platform account; outbound send resolves it from
    :func:`platform_twilio_credentials` at send time. ``metadata.phone_number``
    is what the inbound webhook matches the Twilio ``To`` field against."""
    from server_modules.vault_store import add_credential, _openssl_encrypt

    cred_id = str(uuid.uuid4())
    now = _now_iso()
    number = str(phone_number or "").strip()
    entry = {
        "id": cred_id,
        "label": f"SMS {number}",
        "provider": SMS_VAULT_PROVIDER,
        "workspace_id": str(workspace_id or "").strip() or None,
        "agent_install_id": str(agent_install_id or "").strip(),
        "account_label": "default",
        "mode": "platform",
        "metadata": {
            "phone_number": number,
            "phone_number_sid": str(phone_number_sid or "").strip(),
            "account_sid": str(account_sid or "").strip(),
            "sms_endpoint_key": number,
        },
        "created_at": now,
        "updated_at": now,
        "encrypted_secret": _openssl_encrypt(
            json.dumps(
                {
                    "phone_number": number,
                    "phone_number_sid": str(phone_number_sid or "").strip(),
                    "account_sid": str(account_sid or "").strip(),
                },
                separators=(",", ":"),
            )
        ),
    }
    add_credential(entry)
    return cred_id


def delete_vault_credential_by_id(credential_id: str) -> bool:
    from server_modules.vault_store import delete_credential

    cid = str(credential_id or "").strip()
    if not cid:
        return False
    return delete_credential(cid)


# ── Outbound send (agent-initiated) ──────────────────────────────────────────

def send_platform_sms(*, from_number: str, to_number: str, body: str) -> Dict[str, Any]:
    """Send an agent-initiated SMS from ``from_number`` (the agent's own
    provisioned number) to ``to_number``, using the platform master account.

    This is the outbound counterpart to the inbound TwiML reply path: inbound
    replies ride back in the webhook's ``twiml_response``; agent-INITIATED
    messages go through here. Reuses WhatsAppTransportService.send_sms, which
    is send_message minus the ``whatsapp:`` prefix.

    TODO(billing): meter each send against workspace credits before/after
    this call — resolve the workspace from the from_number's vault credential,
    check balance via entitlements_service, then record via
    usage_events_repository.record_usage_event(provider="twilio_sms",
    model="sms", ...). Not metered today (see module docstring).
    """
    creds = platform_twilio_credentials()
    if creds is None:
        raise SmsNotConfiguredError(NOT_CONFIGURED_MESSAGE)
    from server_modules.connectors.whatsapp_transport_service import WhatsAppTransportService

    return WhatsAppTransportService().send_sms(
        account_sid=creds["account_sid"],
        auth_token=creds["auth_token"],
        from_number=from_number,
        to_number=to_number,
        body=body,
    )


# ── Assignment / release (number ↔ agent) ────────────────────────────────────

async def assign_agent_sms_number(
    *,
    agent_install_id: str,
    workspace_id: str,
    tenant_id: str,
    country: str = "US",
    area_code: Optional[str] = None,
    friendly_name: str = "",
) -> Dict[str, Any]:
    """Give an agent its own SMS number: search → buy (SmsUrl → our webhook)
    → store agent-scoped credential → write the enabled inbound-owner channel
    binding. Raises :class:`SmsNotConfiguredError` when the platform Twilio
    account env vars are unset (caller should surface "not configured on this
    deployment", not a 500)."""
    creds = platform_twilio_credentials()
    if creds is None:
        raise SmsNotConfiguredError(NOT_CONFIGURED_MESSAGE)

    webhook_url = sms_webhook_url()
    if not webhook_url:
        raise SmsProvisioningError(
            "No public base URL is configured for the SMS webhook. Set "
            "TWILIO_SMS_PUBLIC_BASE_URL (or EMPYRALIS_PUBLIC_BASE_URL)."
        )

    candidates = await search_available_numbers(country=country, area_code=area_code)
    if not candidates:
        raise SmsNumberUnavailableError(
            f"Twilio has no available SMS number for country={country!r}"
            + (f", area_code={area_code!r}" if area_code else "")
            + "."
        )
    chosen = str(candidates[0].get("phone_number") or "").strip()
    if not chosen:
        raise SmsNumberUnavailableError("Twilio returned a number with no phone_number field.")

    purchased = await purchase_number(
        phone_number=chosen,
        sms_url=webhook_url,
        friendly_name=friendly_name or f"Empyralis agent {agent_install_id}",
    )
    phone_number = str(purchased.get("phone_number") or chosen).strip()
    phone_number_sid = str(purchased.get("sid") or "").strip()

    cred_id = store_sms_credential(
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
        phone_number=phone_number,
        phone_number_sid=phone_number_sid,
        account_sid=creds["account_sid"],
    )
    try:
        await bindings.upsert_channel_binding(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_install_id=agent_install_id,
            channel_key=CHANNEL_KEY_SMS,
            enabled=True,
            binding={
                "endpoint_key": phone_number,
                "is_inbound_owner": True,
                "source": "platform_twilio",
                "provider": SMS_VAULT_PROVIDER,
                "credential_id": cred_id,
                "phone_number": phone_number,
                "phone_number_sid": phone_number_sid,
            },
        )
    except Exception:
        # Roll back the orphaned credential we just wrote so a binding failure
        # doesn't leave a dangling number credential (mirrors discord BYO).
        delete_vault_credential_by_id(cred_id)
        raise
    return {
        "source": "platform_twilio",
        "channel_key": CHANNEL_KEY_SMS,
        "credential_id": cred_id,
        "phone_number": phone_number,
        "phone_number_sid": phone_number_sid,
        "endpoint_key": phone_number,
        "webhook_url": webhook_url,
    }


async def release_agent_sms_number(
    *,
    agent_install_id: str,
    workspace_id: str,
    tenant_id: str,
    release_twilio_number: bool = True,
) -> Dict[str, Any]:
    """Release an agent's SMS number: clear the channel binding, delete the
    agent-scoped credential, and (best-effort) release the Twilio number so it
    stops billing. Set ``release_twilio_number=False`` to keep the number on
    the account (binding + credential are still cleared)."""
    agent_bindings = await bindings.list_agent_channel_bindings(
        tenant_id=tenant_id, workspace_id=workspace_id,
        agent_install_id=agent_install_id, enabled_only=False,
    )
    target = next((b for b in agent_bindings if b.get("key") == CHANNEL_KEY_SMS), None)
    if target is None:
        return {"released": False, "reason": "no sms binding for this agent"}

    binding_meta = target.get("binding") or {}
    result: Dict[str, Any] = {"released": True, "source": "platform_twilio", "number_released": False}
    try:
        if release_twilio_number:
            sid = str(binding_meta.get("phone_number_sid") or "").strip()
            if sid:
                result["number_released"] = await release_number(phone_number_sid=sid)
        cred_id = str(binding_meta.get("credential_id") or "").strip()
        if cred_id:
            delete_vault_credential_by_id(cred_id)
            result["credential_deleted"] = True
    finally:
        await bindings.delete_channel_binding(
            tenant_id=tenant_id, workspace_id=workspace_id,
            agent_install_id=agent_install_id, channel_key=CHANNEL_KEY_SMS,
        )
    return result
