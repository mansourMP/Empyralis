"""Raw Polar API primitives — auth, checkout/customer-portal session
creation, and Standard Webhooks signature verification.

Empyralis is billed through Polar, not Stripe. This is a deliberate,
geography-forced choice, not a preference — see CLAUDE.md, "Payment
processor is Polar, not Stripe" (2026-08-20): Stripe has no standalone
merchant account in Uzbekistan, where the founder is based. Polar is a
Merchant of Record (a US entity that owns the Stripe relationship
underneath, via Stripe Connect Express) and is the only processor that
lets this business receive money at all.

This module is intentionally the ONLY place that speaks Polar's wire
format. ``billing_service.py`` owns what a checkout/webhook MEANS for a
workspace's plan and credit balance (unchanged, provider-agnostic); this
module owns HOW to ask Polar for a checkout URL and HOW to prove a
webhook really came from Polar. Nothing here touches control_plane_repository.

── API shape, verified against Polar's own Python SDK source (not
   guessed) ─────────────────────────────────────────────────────────────

Verified by downloading and reading ``polar-sdk`` (0.32.0, PyPI,
Speakeasy-generated) and ``standardwebhooks`` (1.1.0, PyPI) — the actual
shipped implementations, not documentation prose, which was frequently
incomplete or stale when checked directly against polar.sh/docs.

  Base URL     https://api.polar.sh              (production)
               https://sandbox-api.polar.sh       (sandbox)
  Auth         Authorization: Bearer <access_token>   (an Organization
               Access Token minted in the Polar dashboard)
  Checkout     POST /v1/checkouts/
               body (snake_case JSON): {"products": ["<product_id>"],
                 "success_url", "return_url", "customer_email",
                 "external_customer_id", "metadata": {...},
                 "amount": <cents>}   (``amount`` only applies to a
                 product configured with Polar's "pay what you want"
                 price type — see polar_credit_product_id() below)
               response: {"id", "url", "status", "customer_id",
                 "product_id", "metadata", "subscription_id", ...}
  Customer     POST /v1/customer-sessions/
  portal       body: {"customer_id": "..."} OR {"external_customer_id": "..."}
               response: {"customer_portal_url": "...", "customer_id", ...}
  Webhook      3 headers: webhook-id, webhook-timestamp, webhook-signature
  signature    (Standard Webhooks spec — https://www.standardwebhooks.com/).
               webhook-signature is a space-separated list of "v1,<sig>"
               tokens; a request is valid if ANY token's signature matches.
               signed message = f"{webhook_id}.{webhook_timestamp}.{raw_body}"
               HMAC-SHA256, key = the configured webhook secret's raw UTF-8
               bytes, signature is base64-encoded. Polar's own SDK
               (polar_sdk._webhooks.validate_event) round-trips the secret
               through base64.b64encode() then the standardwebhooks
               library's base64.b64decode() before HMAC'ing — those two
               steps cancel out to plain UTF-8 bytes for a secret with no
               "whsec_" prefix, which is what this module implements
               directly (stdlib hmac/hashlib/base64 only, no new
               dependency, matching how the pre-existing Stripe verifier
               in billing_service.py was written before it was replaced).
               Tolerance: +/- 5 minutes (their own library's constant).
  Event shape  {"type": "order.paid" | "subscription.created" | ...,
                "timestamp": "...", "data": {<the resource object itself,
                NOT nested under "object" the way Stripe nests it>}}
  Statuses     CheckoutStatus: open | expired | confirmed | succeeded | failed
               SubscriptionStatus: incomplete | incomplete_expired |
                 trialing | active | past_due | canceled | unpaid | paused
               (byte-identical vocabulary to Stripe's, which is why
               billing_service.py's TERMINAL_SUBSCRIPTION_STATUSES /
               ACTIVE_SUBSCRIPTION_STATUSES needed no changes.)

Linking a webhook event back to a workspace: every checkout call below
passes ``external_customer_id=workspace_id`` — Polar's own documented
mechanism for "the ID of the customer in your system" (creates-or-reuses
a Polar Customer keyed on that external id). That external id survives
onto the resulting Order/Subscription as ``data.customer.external_id``,
which is the PRIMARY resolution path in billing_service.py — more
robust than relying on ``metadata`` propagation, which the raw SDK
models support structurally (Order/Subscription both carry a
``metadata`` field) but Polar's own docs never explicitly confirm
propagates from checkout time. ``metadata`` is still sent and still read
as a fallback since it costs nothing and several public Polar
integration guides describe it working.

Not verifiable from here: this has never been called against a real
Polar account (the founder's own onboarding is unfinished, and this repo
must never touch his live credentials — see CLAUDE.md's testing rules).
Every request/response shape below is taken from Polar's shipped Python
SDK source, not from a live call. Confirming it end-to-end is the
founder's own final step once he has a real access token and product ids.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Mapping, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import HTTPException


POLAR_PROVIDER = "polar"

POLAR_SERVER_PRODUCTION = "production"
POLAR_SERVER_SANDBOX = "sandbox"
POLAR_API_BASES: Dict[str, str] = {
    POLAR_SERVER_PRODUCTION: "https://api.polar.sh",
    POLAR_SERVER_SANDBOX: "https://sandbox-api.polar.sh",
}

# Standard Webhooks tolerance — matches the `standardwebhooks` Python
# library's own hardcoded 5-minute window (webhooks.py: `timedelta(minutes=5)`).
POLAR_WEBHOOK_TOLERANCE_SECONDS = 300


def polar_access_token() -> str:
    return str(os.getenv("EMPYRALIS_POLAR_ACCESS_TOKEN") or "").strip()


def polar_server() -> str:
    # Defaults to sandbox deliberately: an access token only works against
    # the environment it was minted for, so a wrong guess here fails loudly
    # (401) rather than risking a real charge — the safer default when
    # nobody has explicitly said "production" yet.
    raw = str(os.getenv("EMPYRALIS_POLAR_SERVER") or "").strip().lower()
    return raw if raw in POLAR_API_BASES else POLAR_SERVER_SANDBOX


def polar_api_base() -> str:
    return POLAR_API_BASES[polar_server()]


def polar_webhook_secret() -> str:
    return str(os.getenv("EMPYRALIS_POLAR_WEBHOOK_SECRET") or "").strip()


def polar_product_map() -> Dict[str, str]:
    """plan_id -> Polar product id, for recurring-subscription plans."""
    mapping: Dict[str, str] = {}
    raw_json = str(os.getenv("EMPYRALIS_POLAR_PRODUCT_IDS") or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            for raw_plan, raw_product_id in parsed.items():
                product_id = str(raw_product_id or "").strip()
                plan_id = str(raw_plan or "").strip().lower()
                if product_id and plan_id:
                    mapping[plan_id] = product_id
    # Per-plan override, e.g. EMPYRALIS_POLAR_PRODUCT_PRO — mirrors the
    # shape the (now-removed) Stripe price map used, so a deploy can set
    # one env var per plan instead of hand-writing JSON.
    for env_suffix in ("FREE", "PILOT", "PRO"):
        product_id = str(os.getenv(f"EMPYRALIS_POLAR_PRODUCT_{env_suffix}") or "").strip()
        if product_id:
            mapping[env_suffix.lower()] = product_id
    return mapping


def polar_credit_product_id() -> str:
    """The single 'pay what you want' Polar product used for hosted-AI
    credit top-ups of an arbitrary dollar amount. Polar's checkout API has
    no equivalent of Stripe's inline `price_data` — an arbitrary amount can
    only be charged against a product whose PRICE TYPE is configured as
    "Pay what you want" in the Polar dashboard, and the checkout call then
    passes `amount` to select the amount within that product's configured
    min/max bounds. This product must exist in Polar before this works —
    see the founder-facing setup notes in .env.example.
    """
    return str(os.getenv("EMPYRALIS_POLAR_CREDIT_PRODUCT_ID") or "").strip()


def polar_configured() -> bool:
    return bool(polar_access_token())


def _polar_request(method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    access_token = polar_access_token()
    if not access_token:
        raise HTTPException(status_code=503, detail="Polar billing is not configured.")
    body_bytes = json.dumps(json_body or {}).encode("utf-8")
    http_request = urlrequest.Request(
        f"{polar_api_base()}{path}",
        data=body_bytes if method != "GET" else None,
        method=method,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(http_request, timeout=15) as response:
            payload = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Polar request failed: {detail or exc.reason}") from exc
    except urlerror.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Polar request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(payload) if payload else {}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Polar returned an invalid response.") from exc
    return dict(parsed) if isinstance(parsed, dict) else {}


def create_checkout_session(
    *,
    product_id: str,
    success_url: str,
    return_url: Optional[str] = None,
    customer_email: Optional[str] = None,
    external_customer_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    amount_cents: Optional[int] = None,
) -> Dict[str, Any]:
    """POST /v1/checkouts/ — creates a hosted checkout session.

    Polar has no `cancel_url`; `return_url` is the closest equivalent
    (shows a back button on the checkout page). There is no separate
    "cancelled" landing page the way Stripe's checkout has one — nothing
    is charged if the customer abandons the checkout, so there is nothing
    to reconcile on that path.
    """
    body: Dict[str, Any] = {
        "products": [product_id],
        "success_url": success_url,
    }
    if return_url:
        body["return_url"] = return_url
    if customer_email:
        body["customer_email"] = customer_email
    if external_customer_id:
        body["external_customer_id"] = external_customer_id
    if metadata:
        body["metadata"] = {str(k): str(v) for k, v in metadata.items()}
    if amount_cents is not None:
        body["amount"] = int(amount_cents)
    return _polar_request("POST", "/v1/checkouts/", json_body=body)


def create_customer_portal_session(
    *,
    customer_id: Optional[str] = None,
    external_customer_id: Optional[str] = None,
    return_url: Optional[str] = None,
) -> Dict[str, Any]:
    """POST /v1/customer-sessions/ — mints a magic-link session into
    Polar's hosted Customer Portal, where a customer manages their own
    subscription/payment method. Exactly one of customer_id /
    external_customer_id must be supplied.
    """
    if not customer_id and not external_customer_id:
        raise HTTPException(status_code=400, detail="Polar customer portal session needs a customer id.")
    body: Dict[str, Any] = {}
    if customer_id:
        body["customer_id"] = customer_id
    else:
        body["external_customer_id"] = external_customer_id
    if return_url:
        body["return_url"] = return_url
    return _polar_request("POST", "/v1/customer-sessions/", json_body=body)


def _polar_webhook_hmac_key(secret: str) -> bytes:
    """Reproduces polar_sdk._webhooks.validate_event's secret handling
    exactly: base64-encode the raw secret, then run it through the same
    "strip an optional whsec_ prefix, then base64-decode with padding
    tolerance" logic standardwebhooks.Webhook.__init__ applies. For a
    secret with no whsec_ prefix (the common case), these two steps
    cancel out to the secret's own raw UTF-8 bytes — this function does
    the round trip explicitly anyway so the behavior stays byte-identical
    to Polar's own SDK for a secret that DOES carry the prefix.
    """
    base64_secret = base64.b64encode(secret.encode("utf-8")).decode("ascii")
    if base64_secret.startswith("whsec_"):
        base64_secret = base64_secret[len("whsec_") :]
    # Extra "=" padding is harmless to Python's b64decode as long as the
    # string is otherwise valid base64 — matches standardwebhooks' own
    # "add padding in case whsecret is unpadded base64" comment.
    return base64.b64decode(base64_secret + "==")


def verify_webhook_signature(body: bytes, headers: Mapping[str, str]) -> None:
    """Raises HTTPException(400) unless `body` carries a valid Standard
    Webhooks signature for one of our configured Polar webhook secret.
    Header lookup is case-insensitive, matching how Starlette's own
    Headers mapping (and the standardwebhooks library) treat them.
    """
    secret = polar_webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Polar webhook secret is not configured.")
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    webhook_id = lowered.get("webhook-id")
    webhook_timestamp = lowered.get("webhook-timestamp")
    webhook_signature = lowered.get("webhook-signature")
    if not webhook_id or not webhook_timestamp or not webhook_signature:
        raise HTTPException(status_code=400, detail="Polar webhook signature headers are missing.")
    try:
        timestamp_value = int(float(webhook_timestamp))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Polar webhook timestamp is invalid.") from exc
    if abs(int(time.time()) - timestamp_value) > POLAR_WEBHOOK_TOLERANCE_SECONDS:
        raise HTTPException(status_code=400, detail="Polar webhook timestamp is outside the tolerance window.")

    signed_payload = f"{webhook_id}.{webhook_timestamp}.{body.decode('utf-8')}".encode("utf-8")
    key = _polar_webhook_hmac_key(secret)
    expected_signature = base64.b64encode(hmac.new(key, signed_payload, hashlib.sha256).digest()).decode("ascii")

    for candidate in webhook_signature.split(" "):
        if "," not in candidate:
            continue
        version, _, provided_signature = candidate.partition(",")
        if version.strip() != "v1":
            continue
        if hmac.compare_digest(expected_signature, provided_signature.strip()):
            return
    raise HTTPException(status_code=400, detail="Polar webhook signature verification failed.")
