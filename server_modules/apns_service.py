"""APNs: the sender, and the three facts a send can produce.

POSTURE IS workspace_invite_email_service.py's, deliberately, rather than a
second style. That module exists because an invite created a row, returned
a token and sent nothing, while the UI reported success -- and its fix was
not a better mailer, it was refusing to let three different facts share one
signal. The same three facts exist here and are the whole point of this
module:

    NOT_CONFIGURED  no APNs key is set on this deployment. This is the
                    state TODAY and it is NOT an error -- nothing is
                    broken, nobody should retry, and no operator should be
                    paged. It is simply a capability that is off.
    SENT            Apple accepted the notification (HTTP 200) and gave us
                    its apns-id.
    FAILED          we tried and Apple (or the network) said no. Carries
                    the APNs `reason` CODE, never a parsed sentence --
                    "stale string matching" is a documented failure here.

A boolean cannot express that, which is why nothing in this module returns
one. `ApnsSendResult.status` is the fact; `ok` exists only as a convenience
and is true for exactly one of the three.

410 Unregistered / BadDeviceToken DEACTIVATE THE TOKEN. Apple is telling us
the app is gone from that device; a token we keep pushing to forever is a
permanent, silent waste and eventually a reputation problem. Deactivation
is recorded on the result (`token_deactivated`) rather than hidden, because
"failed" and "failed, and that device will never be tried again" are
different things for a caller to log.

DEPENDENCIES -- what was checked rather than assumed:
  * HTTP/2 is MANDATORY for APNs (Apple's provider API is HTTP/2 only).
    httpx is already a dependency and supports HTTP/2, but only via the
    optional `h2` package, which was NOT installed here -- so `h2` is added
    to requirements.txt by this change. This is the one new dependency and
    it is unavoidable: there is no HTTP/1.1 APNs endpoint to fall back to.
  * ES256 JWT signing is done with `cryptography` (already declared in
    requirements.txt), NOT with PyJWT. PyJWT happens to be importable in
    this venv but is not declared in requirements.txt, so building on it
    would be depending on something nothing guarantees is installed. The
    signing itself is ~15 lines against a primitive we already ship.

CONFIG. Env vars, read at call time and never cached across a process, so a
deployment that sets them does not need a code change to notice:
    EMPYRALIS_APNS_KEY_P8      the .p8 private key. Accepts the PEM text
                               itself, or a filesystem path to it.
    EMPYRALIS_APNS_KEY_ID      the 10-character Key ID from Apple.
    EMPYRALIS_APNS_TEAM_ID     the 10-character Team ID.
    EMPYRALIS_APNS_BUNDLE_ID   the app's bundle id (the apns-topic).
    EMPYRALIS_APNS_ENVIRONMENT 'sandbox' | 'production' (default
                               production). A per-device value from
                               push_device_tokens.environment overrides it,
                               because the environment is a property of the
                               token, not of the deployment.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from server_modules import push_device_repository

LOGGER = logging.getLogger(__name__)

# The three honest outcomes. Collapsing "no APNs key on this deployment"
# into "the send failed" would send an operator to fix a working system;
# collapsing either into "sent" is the lie this module exists to prevent.
SEND_SENT = "sent"
SEND_NOT_CONFIGURED = "not_configured"
SEND_FAILED = "failed"

APNS_HOST_PRODUCTION = "https://api.push.apple.com"
APNS_HOST_SANDBOX = "https://api.sandbox.push.apple.com"

# Apple's own reason codes for "this token is dead, stop using it". Matched
# on the CODE, never on the human sentence beside it.
DEAD_TOKEN_REASONS = frozenset({"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"})

# APNs provider tokens are valid for 1 hour and must not be minted more
# often than once every 20 minutes, or Apple answers TooManyProviderTokenUpdates.
_JWT_TTL_SECONDS = 45 * 60


@dataclass
class ApnsSendResult:
    """One send, one fact.

    `status` is authoritative. `reason` is Apple's own code on a failure and
    is empty otherwise. `token_deactivated` says whether this result also
    retired the device row -- a caller logging a failure wants to know
    whether it will ever see this token again.
    """

    status: str
    reason: str = ""
    apns_id: str = ""
    status_code: int = 0
    token_deactivated: bool = False
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == SEND_SENT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "apns_id": self.apns_id,
            "status_code": self.status_code,
            "token_deactivated": self.token_deactivated,
            "detail": self.detail,
        }


@dataclass
class ApnsConfig:
    key_pem: str
    key_id: str
    team_id: str
    bundle_id: str
    environment: str = "production"
    # Populated lazily by _provider_jwt; kept per-config so a key rotation
    # produces a fresh token rather than reusing a signature over the old
    # key id.
    _cached: Dict[str, Any] = field(default_factory=dict)

    def host(self, environment: Optional[str] = None) -> str:
        env = (environment or self.environment or "production").strip().lower()
        return APNS_HOST_SANDBOX if env == "sandbox" else APNS_HOST_PRODUCTION


def _read_key_material(raw: str) -> str:
    """The .p8 may be supplied inline (the usual shape for an env var) or as
    a path to the file. A path that does not exist is NOT silently treated
    as key material -- it returns empty, which resolves to not_configured,
    because a garbage 'key' would produce a failed send and send someone
    hunting Apple's dashboard for a problem that is a typo in a path."""
    token = str(raw or "").strip()
    if not token:
        return ""
    if "BEGIN PRIVATE KEY" in token:
        return token
    try:
        path = Path(token).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        LOGGER.warning("apns_key_path_unreadable")
        return ""
    return ""


def load_config(env: Optional[Dict[str, str]] = None) -> Optional[ApnsConfig]:
    """The whole configured/not-configured decision, in one place.

    Returns None when ANY required piece is missing -- a half-configured
    APNs is not a degraded APNs, it is an APNs that can only ever produce
    failures, and reporting those as failures would be the exact
    "tells an owner to retry something that can never work" mistake
    workspace_invite_email_service documents.
    """
    source = env if env is not None else os.environ
    key_pem = _read_key_material(source.get("EMPYRALIS_APNS_KEY_P8", ""))
    key_id = str(source.get("EMPYRALIS_APNS_KEY_ID", "") or "").strip()
    team_id = str(source.get("EMPYRALIS_APNS_TEAM_ID", "") or "").strip()
    bundle_id = str(source.get("EMPYRALIS_APNS_BUNDLE_ID", "") or "").strip()
    environment = push_device_repository.normalize_environment(
        source.get("EMPYRALIS_APNS_ENVIRONMENT", "")
    )
    if not (key_pem and key_id and team_id and bundle_id):
        return None
    return ApnsConfig(
        key_pem=key_pem,
        key_id=key_id,
        team_id=team_id,
        bundle_id=bundle_id,
        environment=environment,
    )


def is_configured(env: Optional[Dict[str, str]] = None) -> bool:
    return load_config(env) is not None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign_es256(key_pem: str, message: bytes) -> bytes:
    """ES256 over the JWT signing input, using `cryptography` directly.

    JWS requires the RAW (r || s) 64-byte form; `cryptography` produces a
    DER-encoded signature, so it is decoded and re-emitted fixed-width.
    Getting this wrong is silent -- Apple simply answers InvalidProviderToken
    -- which is why it is one function with one job.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private_key = load_pem_private_key(key_pem.encode("utf-8"), password=None)
    der = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def build_provider_jwt(config: ApnsConfig, *, now: Optional[int] = None) -> str:
    """Mint (or reuse) the provider authentication token.

    Cached for _JWT_TTL_SECONDS on the config object because Apple refuses
    provider tokens minted more than once per 20 minutes
    (TooManyProviderTokenUpdates) -- so a naive mint-per-send breaks under
    exactly the load it is meant to serve.
    """
    issued = int(now if now is not None else time.time())
    cached = config._cached
    if cached.get("token") and issued - int(cached.get("issued_at") or 0) < _JWT_TTL_SECONDS:
        return str(cached["token"])
    header = {"alg": "ES256", "kid": config.key_id}
    payload = {"iss": config.team_id, "iat": issued}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    ).encode("ascii")
    token = signing_input.decode("ascii") + "." + _b64url(_sign_es256(config.key_pem, signing_input))
    config._cached = {"token": token, "issued_at": issued}
    return token


def build_payload(
    *,
    title: str,
    body: str,
    badge: Optional[int] = None,
    sound: Optional[str] = "default",
    custom: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    aps: Dict[str, Any] = {"alert": {"title": str(title or ""), "body": str(body or "")}}
    if sound:
        aps["sound"] = sound
    if badge is not None:
        aps["badge"] = int(badge)
    payload: Dict[str, Any] = {"aps": aps}
    for key, value in (custom or {}).items():
        if key != "aps":
            payload[key] = value
    return payload


def _extract_reason(response: httpx.Response) -> str:
    """Apple's structured `reason` code, or empty. Never the prose."""
    try:
        parsed = response.json()
    except Exception:
        return ""
    if isinstance(parsed, dict):
        return str(parsed.get("reason") or "").strip()
    return ""


async def send_push(
    *,
    device_token: str,
    payload: Dict[str, Any],
    environment: Optional[str] = None,
    collapse_id: Optional[str] = None,
    push_type: str = "alert",
    priority: int = 10,
    client: Optional[httpx.AsyncClient] = None,
    env: Optional[Dict[str, str]] = None,
) -> ApnsSendResult:
    """Send ONE notification to ONE device, and report which of the three
    things happened.

    NEVER RAISES for an ordinary outcome. A transport error is a `failed`
    result carrying the exception class, not an exception escaping into a
    caller that is usually a best-effort notification path -- the same
    "the email must never cost the invite" posture, applied to "a push must
    never cost the thing it is announcing".
    """
    token = str(device_token or "").strip()
    config = load_config(env)
    if config is None:
        # THE STATE TODAY. Not an error, nothing attempted, nothing to
        # retry, and deliberately reported before the token is even
        # validated so an unconfigured deployment can never produce a
        # `failed`.
        return ApnsSendResult(
            status=SEND_NOT_CONFIGURED,
            detail="No APNs credentials are configured on this deployment.",
        )
    if not token:
        return ApnsSendResult(
            status=SEND_FAILED,
            reason="MissingDeviceToken",
            detail="No device token was supplied.",
        )

    host = config.host(environment)
    url = f"{host}/3/device/{token}"
    headers = {
        "authorization": f"bearer {build_provider_jwt(config)}",
        "apns-topic": config.bundle_id,
        "apns-push-type": push_type,
        "apns-priority": str(priority),
        "apns-id": str(uuid.uuid4()),
    }
    if collapse_id:
        headers["apns-collapse-id"] = str(collapse_id)[:64]

    owns_client = client is None
    # http2=True is required: Apple's provider API speaks HTTP/2 only. This
    # is what makes `h2` a hard dependency rather than an optimization.
    http = client or httpx.AsyncClient(http2=True, timeout=10.0)
    try:
        response = await http.post(url, json=payload, headers=headers)
    except Exception as exc:  # transport-level, never a rejection by Apple
        LOGGER.warning("apns_send_transport_error error=%s", type(exc).__name__)
        return ApnsSendResult(
            status=SEND_FAILED,
            reason="TransportError",
            detail=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if owns_client:
            try:
                await http.aclose()
            except Exception:
                pass

    apns_id = str(response.headers.get("apns-id") or headers["apns-id"])
    if response.status_code == 200:
        return ApnsSendResult(status=SEND_SENT, apns_id=apns_id, status_code=200)

    reason = _extract_reason(response)
    deactivated = False
    if response.status_code == 410 or reason in DEAD_TOKEN_REASONS:
        # Apple says this device is gone. Retire the row so it is never
        # retried again -- across restarts, not just for this process.
        try:
            deactivated = await push_device_repository.deactivate_token_globally(
                device_token=token
            )
        except Exception as exc:
            LOGGER.warning("apns_token_deactivate_failed error=%s", type(exc).__name__)
            deactivated = False
    LOGGER.info(
        "apns_send_failed status=%s reason=%s deactivated=%s",
        response.status_code,
        reason or "unknown",
        deactivated,
    )
    return ApnsSendResult(
        status=SEND_FAILED,
        reason=reason or f"HTTP{response.status_code}",
        apns_id=apns_id,
        status_code=response.status_code,
        token_deactivated=deactivated,
    )
