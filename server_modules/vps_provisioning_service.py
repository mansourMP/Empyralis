from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from server_modules import gateway_state_repository, vault_store
from server_modules.runtime_config import EMPYRALIS_STATE_HOME


AGENT_INSTALLER_URL_ENV = "EMPYRALIS_AGENT_INSTALLER_URL"
LEGACY_AGENT_INSTALLER_URL_ENV = "EMPYRALIS_AGENT_COMPUTER_INSTALL_URL"
DEFAULT_AGENT_INSTALLER_URL = (
    "https://empyralis.ai/install/agent-computer.sh"
)
DIGITALOCEAN_OAUTH_AUTHORIZE_URL = "https://cloud.digitalocean.com/v1/oauth/authorize"
DIGITALOCEAN_OAUTH_TOKEN_URL = "https://cloud.digitalocean.com/v1/oauth/token"
DIGITALOCEAN_OAUTH_REDIRECT_URI_ENV = "EMPYRALIS_DIGITALOCEAN_OAUTH_REDIRECT_URI"
DIGITALOCEAN_CLIENT_ID_ENV = "DIGITALOCEAN_CLIENT_ID"
DIGITALOCEAN_CLIENT_SECRET_ENV = "DIGITALOCEAN_CLIENT_SECRET"
LEGACY_DIGITALOCEAN_CLIENT_ID_ENV = "DIGITALOCEAN_OAUTH_CLIENT_ID"
LEGACY_DIGITALOCEAN_CLIENT_SECRET_ENV = "DIGITALOCEAN_OAUTH_CLIENT_SECRET"
DEFAULT_DIGITALOCEAN_OAUTH_REDIRECT_URI = (
    "https://empyralis.ai/api/hardware/vps/oauth/digitalocean/callback"
)

# --- Google Cloud: "bootstrap-then-impersonate" ----------------------------
#
# Google is deliberately NOT modeled like DigitalOcean/Hetzner/Vultr's "paste
# or OAuth a token, use that token for everything forever" shape. A user's own
# Google OAuth access token expires in ~1h (7 days for a refresh token while
# our OAuth consent screen sits in "Testing" publish status) and breaks
# outright on the user's own password/2FA changes — unusable for a VM that
# needs to be manageable months later. Instead:
#
#   1. Google OAuth consent (once) — scope cloud-platform — used ONLY to run
#      steps 2 below as the user, then discarded. See create_google_oauth_start
#      / complete_google_oauth_callback / _google_setup_session_access_token.
#   2. One-time bootstrap in the user's own project (finish_google_bootstrap):
#      enable Compute Engine -> create a dedicated empyralis-provisioner
#      service account -> bind it a minimal custom VM-lifecycle-only role ->
#      grant EMPYRALIS's OWN operating identity roles/iam.serviceAccountTokenCreator
#      on that one service account. Nothing from this step is a secret worth
#      protecting on its own — what's stored afterward is just
#      {project_id, service_account_email}, not a credential.
#   3. Ongoing provisioning (_provision_google, fetch_provider_plans,
#      fetch_provider_regions, delete_recorded_vps) impersonates that service
#      account via the IAM Credentials API's generateAccessToken, keyless,
#      authenticating AS Empyralis's operator identity — never the end user's
#      token (already discarded by then) and never a downloaded
#      service-account JSON key. See _google_impersonated_access_token.
GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_REDIRECT_URI_ENV = "EMPYRALIS_GOOGLE_OAUTH_REDIRECT_URI"
GOOGLE_CLOUD_CLIENT_ID_ENV = "GOOGLE_CLOUD_CLIENT_ID"
GOOGLE_CLOUD_CLIENT_SECRET_ENV = "GOOGLE_CLOUD_CLIENT_SECRET"
DEFAULT_GOOGLE_OAUTH_REDIRECT_URI = (
    "https://empyralis.ai/api/hardware/vps/oauth/google/callback"
)
GOOGLE_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Empyralis's OWN operating GCP identity — the one every customer's bootstrap
# grants serviceAccountTokenCreator to (step 2 above) and the one every
# impersonated-token call authenticates as (step 3). Not a per-user value —
# an operator credential set once on the backend host, same posture as
# DIGITALOCEAN_CLIENT_ID/_installer_repo_token. Kept as its own long-lived
# OAuth refresh token (never a downloaded JSON key — see the module docstring
# above) so this file's existing refresh-token machinery (same shape as
# _refresh_digitalocean_credentials) covers it without a new credential type.
GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL_ENV = "GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL"
GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN_ENV = "GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN"

GOOGLE_CLOUD_RESOURCE_MANAGER_URL = "https://cloudresourcemanager.googleapis.com/v1"
GOOGLE_CLOUD_BILLING_URL = "https://cloudbilling.googleapis.com/v1"
GOOGLE_CLOUD_SERVICE_USAGE_URL = "https://serviceusage.googleapis.com/v1"
GOOGLE_CLOUD_IAM_URL = "https://iam.googleapis.com/v1"
GOOGLE_CLOUD_IAM_CREDENTIALS_URL = "https://iamcredentials.googleapis.com/v1"
GOOGLE_CLOUD_COMPUTE_URL = "https://compute.googleapis.com/compute/v1"
# Compute Engine's service id in the Cloud Billing Catalog API
# (services.skus.list) — stable, documented at
# cloud.google.com/billing/docs/how-to/catalog-api next to the "list public
# SKUs" sample, which uses this exact same id for the same service.
GOOGLE_CLOUD_BILLING_CATALOG_COMPUTE_SERVICE_ID = "6F81-5844-456A"

GOOGLE_PROVISIONER_SA_ACCOUNT_ID = "empyralis-provisioner"
GOOGLE_PROVISIONER_CUSTOM_ROLE_ID = "empyralisVmProvisioner"
# GCE's boot disk is a resource sized independently of machine type (unlike
# DO/Hetzner/Vultr, which bundle a fixed disk into each plan) — one fixed
# default, in line with Hetzner's cx22 (~40GB) and DO's 50GB s-1vcpu-2gb.
GOOGLE_DEFAULT_BOOT_DISK_GB = 40
# Google's own pricing calculator's convention for turning an hourly SKU rate
# into a monthly figure (730 = 365 * 24 / 12, i.e. the average month).
_GOOGLE_AVERAGE_HOURS_PER_MONTH = 730
# Curated general-purpose machine families for the size picker. GCE's full
# catalog also includes GPU (a2/a3/g2), bare-metal (m3), and other
# specialized families whose pricing shape (or suitability for an "Agent
# Computer") doesn't belong in this list.
_GOOGLE_SUPPORTED_MACHINE_FAMILIES = {"e2", "n2", "n2d", "n1"}

PUBLIC_API_URL = (
    os.getenv("EMPYRALIS_PUBLIC_API_URL")
    or os.getenv("EMPYRALIS_GATEWAY_API_URL")
    or os.getenv("NEXT_PUBLIC_ORION_API_URL")
    or os.getenv("NEXT_PUBLIC_API_URL")
    or "https://empyralis.ai/api"
).rstrip("/")
VPS_STATE_FILE = Path(
    os.getenv(
        "EMPYRALIS_VPS_PROVISIONING_STATE_FILE",
        str(EMPYRALIS_STATE_HOME / "gateway" / "vps-provisioning.json"),
    )
).expanduser()


@dataclass(frozen=True)
class ProviderRegion:
    id: str
    label: str


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    label: str
    auth_label: str
    create_url: str
    default_region: str
    default_size: str
    default_image: str
    token_keys: tuple[str, ...]
    regions: tuple[ProviderRegion, ...]


@dataclass(frozen=True)
class VPSResult:
    provider_resource_id: str
    public_ip: Optional[str]
    region: str
    size: str
    status: str
    provider: str


@dataclass(frozen=True)
class VPSPlan:
    id: str
    slug: str
    label: str
    vcpus: int
    memory_mb: int
    disk_gb: int
    price_monthly: float
    price_label: str
    recommended: bool = False
    # Region slugs this exact plan is actually available in. Populated for
    # DigitalOcean (whose /v2/sizes response lists it per-size); empty for
    # providers we don't thread this through yet, which the frontend treats
    # as "no restriction" rather than "available nowhere".
    regions: tuple[str, ...] = ()


class VPSProvisioningError(RuntimeError):
    pass


PROVIDER_CONFIGS: Dict[str, ProviderConfig] = {
    "digitalocean": ProviderConfig(
        provider="digitalocean",
        label="DigitalOcean",
        auth_label="DigitalOcean personal access token",
        create_url="https://api.digitalocean.com/v2/droplets",
        default_region="nyc3",
        default_size="s-1vcpu-2gb",
        default_image="ubuntu-24-04-x64",
        token_keys=("api_token", "token", "pat", "access_token"),
        regions=(
            ProviderRegion("nyc3", "New York 3"),
            ProviderRegion("sfo3", "San Francisco 3"),
            ProviderRegion("lon1", "London 1"),
            ProviderRegion("fra1", "Frankfurt 1"),
            ProviderRegion("sgp1", "Singapore 1"),
            ProviderRegion("blr1", "Bangalore 1"),
        ),
    ),
    "hetzner": ProviderConfig(
        provider="hetzner",
        label="Hetzner",
        auth_label="Hetzner Cloud API token",
        create_url="https://api.hetzner.cloud/v1/servers",
        default_region="nbg1",
        default_size="cx22",
        default_image="ubuntu-24.04",
        token_keys=("api_token", "token"),
        regions=(
            ProviderRegion("nbg1", "Nuremberg, Germany"),
            ProviderRegion("fsn1", "Falkenstein, Germany"),
            ProviderRegion("hel1", "Helsinki, Finland"),
            ProviderRegion("ash", "Ashburn, USA"),
            ProviderRegion("hil", "Hillsboro, USA"),
            ProviderRegion("sin", "Singapore"),
        ),
    ),
    "vultr": ProviderConfig(
        provider="vultr",
        label="Vultr",
        auth_label="Vultr API key",
        create_url="https://api.vultr.com/v2/instances",
        default_region="ewr",
        default_size="vc2-1c-2gb",
        default_image="2284",
        token_keys=("api_key", "api_token", "token"),
        regions=(
            ProviderRegion("ewr", "New York / New Jersey"),
            ProviderRegion("lhr", "London"),
            ProviderRegion("fra", "Frankfurt"),
            ProviderRegion("sgp", "Singapore"),
            ProviderRegion("syd", "Sydney"),
        ),
    ),
    "google": ProviderConfig(
        provider="google",
        label="Google Cloud",
        auth_label="Google Cloud OAuth connection",
        # Base URL only — the real instances.insert URL is
        # project/zone-scoped and built per-call in _provision_google, unlike
        # the other providers' account-scoped create_url.
        create_url=GOOGLE_CLOUD_COMPUTE_URL,
        default_region="us-central1",
        default_size="e2-medium",
        default_image="projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64",
        # Empty on purpose: Google is OAuth-only, never a pasted API key (see
        # the module docstring above) — store_vps_provider_token refuses this
        # provider outright rather than relying on _provider_token's generic
        # "no matching key" failure to carry that message.
        token_keys=(),
        regions=(
            ProviderRegion("us-central1", "Iowa, USA"),
            ProviderRegion("us-east1", "South Carolina, USA"),
            ProviderRegion("us-west1", "Oregon, USA"),
            ProviderRegion("europe-west1", "Belgium"),
            ProviderRegion("europe-west4", "Netherlands"),
            ProviderRegion("asia-southeast1", "Singapore"),
        ),
    ),
}

_STATE_LOCK = threading.Lock()


def provider_catalog() -> Dict[str, Any]:
    return {
        key: {
            "provider": config.provider,
            "label": config.label,
            "auth_label": config.auth_label,
            "default_region": config.default_region,
            "default_size": config.default_size,
            "regions": [asdict(region) for region in config.regions],
        }
        for key, config in PROVIDER_CONFIGS.items()
    }


def digitalocean_oauth_redirect_uri() -> str:
    return (
        os.getenv(DIGITALOCEAN_OAUTH_REDIRECT_URI_ENV)
        or DEFAULT_DIGITALOCEAN_OAUTH_REDIRECT_URI
    ).strip()


def create_digitalocean_oauth_start(
    *,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
) -> Dict[str, str]:
    client_id = _digitalocean_client_id()
    state_token = secrets.token_urlsafe(32)
    state = {
        "state": state_token,
        "provider": "digitalocean",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "user_id": str(user_id or "").strip() or "unknown-user",
        "created_at": _utc_now_iso(),
    }
    with _STATE_LOCK:
        payload = _load_state()
        payload.setdefault("oauth_states", {})[state_token] = state
        _write_state(payload)
    query = urlparse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": digitalocean_oauth_redirect_uri(),
            "response_type": "code",
            # Granular scopes, not the old "read write" alias — DO's current
            # scopes reference (docs.digitalocean.com/reference/api/scopes/)
            # replaced free-form read/write with per-resource scopes plus
            # api:read/api:write aliases for "everything this role can see".
            # This app only ever creates/deletes droplets and reads
            # regions/sizes (see provision_vps, fetch_provider_plans,
            # fetch_provider_regions, _delete_provider_resource) — request
            # exactly that instead of api:read/api:write's full-account
            # access. Each scope is documented at
            # docs.digitalocean.com/reference/api/scopes/<resource>/.
            "scope": "droplet:create droplet:delete regions:read sizes:read",
            "state": state_token,
        }
    )
    return {
        "provider": "digitalocean",
        "oauth_redirect": f"{DIGITALOCEAN_OAUTH_AUTHORIZE_URL}?{query}",
        "redirect_uri": digitalocean_oauth_redirect_uri(),
        "state": state_token,
    }


def complete_digitalocean_oauth_callback(*, code: str, state: str) -> Dict[str, str]:
    clean_code = str(code or "").strip()
    clean_state = str(state or "").strip()
    if not clean_code:
        raise VPSProvisioningError("DigitalOcean OAuth callback is missing code.")
    if not clean_state:
        raise VPSProvisioningError("DigitalOcean OAuth callback is missing state.")
    with _STATE_LOCK:
        payload = _load_state()
        state_record = dict((payload.get("oauth_states") or {}).pop(clean_state, {}) or {})
        _write_state(payload)
    if not state_record or str(state_record.get("provider") or "") != "digitalocean":
        raise VPSProvisioningError("DigitalOcean OAuth state is invalid or expired.")
    token_payload = _exchange_digitalocean_oauth_code(clean_code)
    token_id = store_vps_provider_token(
        provider="digitalocean",
        workspace_id=str(state_record.get("workspace_id") or "default"),
        tenant_id=str(state_record.get("tenant_id") or "default"),
        user_id=str(state_record.get("user_id") or "unknown-user"),
        credentials=token_payload,
        source="oauth",
    )
    return {
        "provider": "digitalocean",
        "token_id": token_id,
        "workspace_id": str(state_record.get("workspace_id") or "default"),
    }


def google_oauth_redirect_uri() -> str:
    return (
        os.getenv(GOOGLE_OAUTH_REDIRECT_URI_ENV) or DEFAULT_GOOGLE_OAUTH_REDIRECT_URI
    ).strip()


def create_google_oauth_start(
    *,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
) -> Dict[str, str]:
    client_id = _google_client_id()
    state_token = secrets.token_urlsafe(32)
    state = {
        "state": state_token,
        "provider": "google",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "user_id": str(user_id or "").strip() or "unknown-user",
        "created_at": _utc_now_iso(),
    }
    with _STATE_LOCK:
        payload = _load_state()
        payload.setdefault("oauth_states", {})[state_token] = state
        _write_state(payload)
    query = urlparse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": google_oauth_redirect_uri(),
            "response_type": "code",
            "scope": GOOGLE_CLOUD_PLATFORM_SCOPE,
            # offline+consent: the bootstrap sequence this token is used for
            # (create/select project, check billing, enable APIs, create a
            # service account, set two IAM policies) is a few real HTTP round
            # trips the user may pause partway through (e.g. to go attach a
            # billing account in another tab) — a refresh_token means that
            # pause can outlast the ~1h access token without forcing a second
            # consent screen. The token (and this refresh_token) is discarded
            # the moment finish_google_bootstrap succeeds — see
            # _google_setup_session_access_token and
            # complete_google_oauth_callback below. Never reused afterward.
            "access_type": "offline",
            "prompt": "consent",
            "state": state_token,
        }
    )
    return {
        "provider": "google",
        "oauth_redirect": f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{query}",
        "redirect_uri": google_oauth_redirect_uri(),
        "state": state_token,
    }


def complete_google_oauth_callback(*, code: str, state: str) -> Dict[str, str]:
    clean_code = str(code or "").strip()
    clean_state = str(state or "").strip()
    if not clean_code:
        raise VPSProvisioningError("Google OAuth callback is missing code.")
    if not clean_state:
        raise VPSProvisioningError("Google OAuth callback is missing state.")
    with _STATE_LOCK:
        payload = _load_state()
        state_record = dict((payload.get("oauth_states") or {}).pop(clean_state, {}) or {})
        _write_state(payload)
    if not state_record or str(state_record.get("provider") or "") != "google":
        raise VPSProvisioningError("Google OAuth state is invalid or expired.")
    token_payload = _exchange_google_oauth_code(clean_code)
    # Deliberately NOT store_vps_provider_token: Google isn't provisionable
    # yet at this point (no project chosen, bootstrap not run) — this is a
    # short-lived SETUP session the rest of the bootstrap flow (
    # list_google_projects / create_google_project / check_google_project_billing
    # / finish_google_bootstrap) consumes and then discards, never a
    # long-lived provider connection the way a DO token_id is.
    setup_id = _store_google_setup_session(
        workspace_id=str(state_record.get("workspace_id") or "default"),
        tenant_id=str(state_record.get("tenant_id") or "default"),
        user_id=str(state_record.get("user_id") or "unknown-user"),
        credentials=token_payload,
    )
    return {
        "provider": "google",
        "setup_id": setup_id,
        "workspace_id": str(state_record.get("workspace_id") or "default"),
    }


def store_vps_provider_token(
    *,
    provider: str,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    credentials: Mapping[str, Any],
    source: str = "api_token",
) -> str:
    provider_id = _normalize_provider(provider)
    if provider_id == "google":
        # Defense in depth for the frontend never offering this: Google is
        # OAuth-only (see PROVIDER_CONFIGS["google"].token_keys), so there is
        # never a pasted token to accept here — connect via
        # create_google_oauth_start / finish_google_bootstrap instead.
        raise ValueError('Google Cloud has no pasted API token — connect with "Sign in with Google" instead.')
    token = _provider_token(PROVIDER_CONFIGS[provider_id], credentials)
    return _store_provider_token_record(
        provider_id=provider_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        credentials=dict(credentials or {}, access_token=token),
        source=source,
    )


def _store_provider_token_record(
    *,
    provider_id: str,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    credentials: Mapping[str, Any],
    source: str,
) -> str:
    token_id = f"vps_token_{secrets.token_hex(16)}"
    now = _utc_now_iso()
    stored_credentials = _credentials_with_expiry(dict(credentials))
    record = {
        "token_id": token_id,
        "provider": provider_id,
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "user_id": str(user_id or "").strip() or "unknown-user",
        "source": str(source or "api_token").strip() or "api_token",
        "credentials_ciphertext": _encrypt_secret(stored_credentials),
        "created_at": now,
        "updated_at": now,
    }
    with _STATE_LOCK:
        state = _load_state()
        state.setdefault("tokens", {})[token_id] = record
        _write_state(state)
    return token_id


def _credentials_with_expiry(credentials: Mapping[str, Any]) -> Dict[str, Any]:
    """Stamp an absolute access_token_expires_at (epoch seconds) onto OAuth
    credentials that carry a relative expires_in — DigitalOcean's access
    tokens expire in 30 days (expires_in=2592000) and nothing else records
    when that clock started. A pasted API token (Hetzner/Vultr/manual DO PAT)
    never has expires_in, so this is a no-op for those — they're static
    tokens with no refresh cycle. See _digitalocean_credentials_need_refresh
    for how this gets used."""
    result = dict(credentials)
    if "access_token_expires_at" not in result:
        expires_in = _to_int(result.get("expires_in"))
        if expires_in > 0:
            result["access_token_expires_at"] = int(time.time()) + expires_in
    return result


def load_vps_provider_credentials(
    token_id: str,
    *,
    provider: str,
    workspace_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    clean_token_id = _clean_identifier(token_id, field_name="token_id")
    provider_id = _normalize_provider(provider)
    with _STATE_LOCK:
        record = dict((_load_state().get("tokens") or {}).get(clean_token_id) or {})
    if not record:
        raise KeyError(clean_token_id)
    if str(record.get("provider") or "") != provider_id:
        raise ValueError("Stored VPS credential does not match provider.")
    if str(record.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        raise KeyError(clean_token_id)
    if user_id and str(record.get("user_id") or "").strip() != str(user_id or "").strip():
        raise KeyError(clean_token_id)
    credentials = _decrypt_secret(str(record.get("credentials_ciphertext") or ""))
    # Proactive refresh: DigitalOcean's 30-day access token would otherwise
    # sit untouched in credentials_ciphertext until it 401s (the defect this
    # closes — see _digitalocean_reauth_callback for the reactive backstop
    # that still catches it if this fell through for any reason).
    if provider_id == "digitalocean" and _digitalocean_credentials_need_refresh(credentials):
        refreshed = _refresh_digitalocean_credentials(credentials)
        if refreshed is not None:
            credentials = refreshed
            _update_stored_token_credentials(clean_token_id, credentials)
    return credentials


def fetch_provider_plans(
    provider: str,
    *,
    token_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider_id = _normalize_provider(provider)
    credentials = load_vps_provider_credentials(
        token_id,
        provider=provider_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    if provider_id == "google":
        # Early return: Google's stored "credentials" are just
        # {project_id, service_account_email}, never a bearer token
        # _provider_token below could extract — see _google_active_token.
        plans = _fetch_google_plans(credentials)
        return {"provider": provider_id, "plans": [asdict(plan) for plan in plans]}
    token = _provider_token(PROVIDER_CONFIGS[provider_id], credentials)
    if provider_id == "digitalocean":
        raw = _http_json(
            "GET",
            "https://api.digitalocean.com/v2/sizes",
            token=token,
            payload=None,
            provider=provider_id,
            on_unauthorized=_digitalocean_reauth_callback(token_id, credentials),
        )
        plans = _normalize_digitalocean_plans(raw)
    elif provider_id == "hetzner":
        raw = _http_json("GET", "https://api.hetzner.cloud/v1/server_types", token=token, payload=None, provider=provider_id)
        plans = _normalize_hetzner_plans(raw)
    elif provider_id == "vultr":
        raw = _http_json("GET", "https://api.vultr.com/v2/plans?type=vc2", token=token, payload=None, provider=provider_id)
        plans = _normalize_vultr_plans(raw)
    else:  # pragma: no cover - guarded by _normalize_provider.
        raise VPSProvisioningError(f"Unsupported VPS provider: {provider_id}")
    return {
        "provider": provider_id,
        "plans": [asdict(plan) for plan in plans],
    }


def fetch_public_provider_plans(provider: str) -> Dict[str, Any]:
    """Plan catalog fetched with NO stored/connected credential — lets the
    picker show a user real prices before they've connected a provider
    account (see the frontend's pre-connect browsing step). Only Vultr
    publishes plans without authentication: verified live against the real
    APIs — GET https://api.vultr.com/v2/plans returns 200 with no
    Authorization header, while DigitalOcean's /v2/sizes and Hetzner's
    /v1/server_types both return 401 unauthenticated. Raises
    VPSProvisioningError for every other provider; callers should treat
    that as "connect an account first", not retry.
    """
    provider_id = _normalize_provider(provider)
    if provider_id != "vultr":
        raise VPSProvisioningError(
            f"{PROVIDER_CONFIGS[provider_id].label} has no public plan catalog; connect an account first."
        )
    raw = _http_json("GET", "https://api.vultr.com/v2/plans?type=vc2", token=None, payload=None, provider=provider_id)
    return {"provider": provider_id, "plans": [asdict(plan) for plan in _normalize_vultr_plans(raw)]}


def fetch_public_provider_regions(provider: str) -> Dict[str, Any]:
    """Region catalog fetched with no stored/connected credential — a safe
    drop-in for provider_catalog()[provider_id] (same field set: provider,
    label, auth_label, default_region, default_size, regions) used by the
    picker's pre-connect "provider" step and region-browsing step alike.
    For Vultr, prefers its live public /v2/regions data (same
    no-auth-required story as fetch_public_provider_plans above) over the
    static curated list; every other provider gets exactly the static entry,
    identical to what provider_catalog() has always served pre-connection.
    Never raises for a valid provider — region browsing should never
    hard-fail, mirroring fetch_provider_regions's existing contract."""
    provider_id = _normalize_provider(provider)
    config = PROVIDER_CONFIGS[provider_id]
    entry: Dict[str, Any] = {
        "provider": provider_id,
        "label": config.label,
        "auth_label": config.auth_label,
        "default_region": config.default_region,
        "default_size": config.default_size,
        "regions": [asdict(region) for region in config.regions],
    }
    if provider_id != "vultr":
        return entry
    try:
        raw = _http_json("GET", "https://api.vultr.com/v2/regions", token=None, payload=None, provider=provider_id)
        live_regions = _normalize_vultr_regions(raw)
    except VPSProvisioningError:
        live_regions = []
    if live_regions:
        entry["regions"] = live_regions
    return entry


def _normalize_vultr_regions(payload: Mapping[str, Any]) -> list[Dict[str, str]]:
    items = payload.get("regions") if isinstance(payload.get("regions"), list) else []
    regions: list[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        slug = str(item.get("id") or "").strip()
        if not slug:
            continue
        city = str(item.get("city") or "").strip()
        country = str(item.get("country") or "").strip()
        label = f"{city}, {country}" if city and country else (city or country or slug)
        regions.append({"id": slug, "label": label})
    return regions


def _static_provider_regions(provider_id: str) -> Dict[str, Any]:
    config = PROVIDER_CONFIGS[provider_id]
    return {
        "provider": provider_id,
        "default_region": config.default_region,
        "regions": [asdict(region) for region in config.regions],
    }


def _normalize_digitalocean_regions(payload: Mapping[str, Any]) -> list[Dict[str, str]]:
    items = payload.get("regions") if isinstance(payload.get("regions"), list) else []
    regions: list[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if item.get("available") is False:
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        regions.append({"id": slug, "label": str(item.get("name") or "").strip() or slug})
    return regions


# Providers we can fetch a *live* region/location list for, given a
# connected account's token. Vultr has its own path (fetch_public_provider_regions)
# since its region list is fetchable without any account at all.
_LIVE_REGION_PROVIDERS = {"digitalocean", "hetzner", "google"}


def fetch_provider_regions(
    provider: str,
    *,
    token_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Live region catalog for a connected account (DigitalOcean, Hetzner,
    Google). DigitalOcean's OAuth grant now requests regions:read explicitly
    (see create_digitalocean_oauth_start); Hetzner's pasted API token already
    carries full project access; Google's impersonated service-account token
    (see _google_active_token) carries whatever the bootstrap's custom role
    granted it (compute.regions.list — see _GOOGLE_SUPPORTED... role
    permissions). Falls back to the static curated list (same one
    provider_catalog() serves pre-connection) for providers we haven't wired
    a live call for (Vultr — see fetch_public_provider_regions, which doesn't
    need an account at all), or if the live call fails — never a hard error
    just for this.
    """
    provider_id = _normalize_provider(provider)
    if provider_id not in _LIVE_REGION_PROVIDERS:
        return _static_provider_regions(provider_id)
    try:
        credentials = load_vps_provider_credentials(
            token_id, provider=provider_id, workspace_id=workspace_id, user_id=user_id
        )
        if provider_id == "google":
            token, project_id = _google_active_token(credentials)
            regions = _fetch_live_regions("google", token, project_id=project_id)
        else:
            token = _provider_token(PROVIDER_CONFIGS[provider_id], credentials)
            on_unauthorized = (
                _digitalocean_reauth_callback(token_id, credentials) if provider_id == "digitalocean" else None
            )
            regions = _fetch_live_regions(provider_id, token, on_unauthorized=on_unauthorized)
    except (KeyError, ValueError, VPSProvisioningError):
        regions = []
    if not regions:
        return _static_provider_regions(provider_id)
    return {
        "provider": provider_id,
        "default_region": PROVIDER_CONFIGS[provider_id].default_region,
        "regions": regions,
    }


def _fetch_live_regions(
    provider_id: str,
    token: str,
    *,
    project_id: Optional[str] = None,
    on_unauthorized: Optional[Callable[[], Optional[str]]] = None,
) -> list[Dict[str, str]]:
    """Raw per-provider live region/location call, normalized to
    [{id, label}, ...]. Shared by fetch_provider_regions (token_id-based,
    full envelope + static fallback on any failure) and
    _fetch_live_region_ids (provision_vps's region-validation allow-list,
    which wants just the id set and already tolerates failure). project_id is
    Google-only — every other provider's regions/locations call is scoped by
    the bearer token alone, Google's is scoped by project."""
    if provider_id == "digitalocean":
        raw = _http_json(
            "GET",
            "https://api.digitalocean.com/v2/regions",
            token=token,
            payload=None,
            provider=provider_id,
            on_unauthorized=on_unauthorized,
        )
        return _normalize_digitalocean_regions(raw)
    if provider_id == "hetzner":
        raw = _http_json(
            "GET", "https://api.hetzner.cloud/v1/locations", token=token, payload=None, provider=provider_id
        )
        return _normalize_hetzner_locations(raw)
    if provider_id == "google":
        if not project_id:
            return []
        raw = _http_json(
            "GET",
            f"{GOOGLE_CLOUD_COMPUTE_URL}/projects/{project_id}/regions",
            token=token,
            payload=None,
            provider=provider_id,
        )
        return _normalize_google_regions(raw)
    return []


def _fetch_live_region_ids(provider_id: str, token: str, *, project_id: Optional[str] = None) -> set[str]:
    """Best-effort live region id set used as an ADDITIONAL allow-list on
    top of the static PROVIDER_CONFIGS list during provisioning (see
    provision_vps) — so a region the picker just showed (fetched live
    moments earlier) is never rejected just because it's missing from the
    hardcoded fallback tuple. Never raises: any failure here just means
    validation falls back to the static list alone, same as before live
    region data existed."""
    try:
        return {item["id"] for item in _fetch_live_regions(provider_id, token, project_id=project_id)}
    except (VPSProvisioningError, KeyError, ValueError, TypeError):
        return set()


def _normalize_hetzner_locations(payload: Mapping[str, Any]) -> list[Dict[str, str]]:
    items = payload.get("locations") if isinstance(payload.get("locations"), list) else []
    regions: list[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        slug = str(item.get("name") or "").strip()
        if not slug:
            continue
        label = str(item.get("city") or item.get("description") or "").strip() or slug
        regions.append({"id": slug, "label": label})
    return regions


def _normalize_google_regions(payload: Mapping[str, Any]) -> list[Dict[str, str]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    regions: list[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "").strip().upper() == "DOWN":
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        # compute.regions.list's "description" is typically just the region
        # name itself again (GCE doesn't return a friendly city label the
        # way DO/Hetzner do) — falls back to the id either way.
        label = str(item.get("description") or "").strip() or name
        regions.append({"id": name, "label": label})
    return regions


def resolve_provider_options(
    provider: str,
    region: Optional[str],
    size: Optional[str],
) -> Dict[str, str]:
    """Normalize provider/region/size for request handling. Defaults an
    empty region/size but does NOT reject a region merely for being absent
    from the static PROVIDER_CONFIGS list — this function has no
    credentials to check the live catalog with, and providers add regions
    over time (see fetch_provider_regions). provision_vps() is the
    authoritative region gate: it validates against static ∪ live data
    (fetching live data when it has a connected account to fetch it with)
    right before the actual provider API call, so a genuinely bad region
    still can never reach the provider — it just fails a little later than
    it used to, after resolving here.
    """
    provider_id = _normalize_provider(provider)
    config = PROVIDER_CONFIGS[provider_id]
    return {
        "provider": provider_id,
        "region": str(region or "").strip() or config.default_region,
        "size": str(size or "").strip() or config.default_size,
    }


def _installer_repo_token() -> str:
    # Interim measure while the Empyralis repo is private and no artifact
    # publish pipeline exists: install-agent-computer.sh builds the gateway
    # from a git clone rather than a prebuilt download, so it needs read
    # access to that private repo. This is an operator credential — set once
    # on the backend host, never generated or stored by this service — not
    # something an end user provides. See install-agent-computer.sh's own
    # EMPYRALIS_REPO_TOKEN handling for the client side of this.
    return (os.getenv("EMPYRALIS_REPO_TOKEN") or "").strip()


def _ensure_api_path_suffix(value: str) -> str:
    # The box registers to f"{apiBaseUrl}/gateway/registrations" (gateway's
    # normalizeBaseUrl only strips a trailing slash, it never appends /api).
    # EMPYRALIS_PUBLIC_API_URL is operator-set on the backend host — a value
    # given without /api (e.g. "https://empyralis.ai") would silently point
    # every newly provisioned box at a URL that 404s and never pairs. Append
    # /api when it's missing; leave an already-correct path (or one ending in
    # a distinct /api-suffixed segment) alone rather than doubling it up.
    normalized = str(value or "").strip().rstrip("/")
    if not normalized or normalized.endswith("/api"):
        return normalized
    return f"{normalized}/api"


def cloud_init_script(pairing_token: str, *, api_url: Optional[str] = None) -> str:
    token = str(pairing_token or "").strip()
    if not token:
        raise ValueError("pairing_token is required.")
    resolved_api_url = _ensure_api_path_suffix(str(api_url or PUBLIC_API_URL))
    if not resolved_api_url:
        raise ValueError("api_url is required.")
    repo_token = _installer_repo_token()
    repo_token_env = f" EMPYRALIS_REPO_TOKEN='{_shell_single_quote(repo_token)}'" if repo_token else ""
    return "\n".join(
        [
            "#cloud-config",
            "package_update: true",
            "runcmd:",
            "  - |",
            f"    curl -fsSL {agent_installer_url()} | EMPYRALIS_PAIRING_TOKEN='{_shell_single_quote(token)}' EMPYRALIS_API_URL='{_shell_single_quote(resolved_api_url)}'{repo_token_env} sudo -E bash",
            "",
        ]
    )


def agent_installer_url() -> str:
    return (
        os.getenv(AGENT_INSTALLER_URL_ENV)
        or os.getenv(LEGACY_AGENT_INSTALLER_URL_ENV)
        or DEFAULT_AGENT_INSTALLER_URL
    ).strip()


def provision_vps(
    provider: str,
    credentials: Mapping[str, Any],
    region: Optional[str],
    size: Optional[str],
    pairing_token: str,
    *,
    token_id: Optional[str] = None,
) -> VPSResult:
    """token_id is optional and purely additive: when the caller has one (the
    real app flow always does — see routes_gateway.provision_hardware_vps),
    it unlocks (a) validating `region` against the LIVE catalog in addition
    to the static PROVIDER_CONFIGS list, so a region the picker just showed
    can never be rejected here, and (b) a DigitalOcean reactive-401 refresh
    on the actual create-droplet call. Neither behavior triggers without a
    token_id, which keeps direct callers (tests, or any future caller that
    only has raw credentials) on the exact same static-only, no-extra-HTTP
    behavior as before this existed.
    """
    provider_id = _normalize_provider(provider)
    config = PROVIDER_CONFIGS[provider_id]
    resolved_size = str(size or "").strip() or config.default_size
    name = _server_name(provider_id)
    user_data = cloud_init_script(pairing_token)

    if provider_id == "google":
        # Early return: Google's credentials are {project_id,
        # service_account_email}, not a bearer token _provider_token below
        # could extract — the actual bearer token is a short-lived
        # impersonated one, minted via _google_impersonated_access_token,
        # never cached or persisted. Unlike DO/Hetzner/Vultr's
        # _provider_token (a free local dict lookup), minting this one costs
        # two real HTTP round trips (refresh the operator identity, then
        # generateAccessToken) — so, to preserve the same "an invalid region
        # never reaches a provider call" contract the tests below hold every
        # other provider to, it's deferred until AFTER region validation
        # rather than paid unconditionally up front. It's only minted EARLY
        # when validation itself needs it (token_id present -> live region
        # data requires an impersonated token to fetch with); the `token is
        # None` check below is what makes it exactly one mint either way,
        # never two.
        project_id = str(credentials.get("project_id") or "").strip()
        service_account_email = str(credentials.get("service_account_email") or "").strip()
        if not project_id or not service_account_email:
            raise VPSProvisioningError("Google Cloud project is not fully connected.")
        google_token: Optional[str] = None
        live_region_ids: set[str] = set()
        if token_id:
            google_token = _google_impersonated_access_token(service_account_email)
            live_region_ids = _fetch_live_region_ids("google", google_token, project_id=project_id)
        resolved_region = _validate_region(config, region, live_region_ids=live_region_ids)
        if google_token is None:
            google_token = _google_impersonated_access_token(service_account_email)
        return _provision_google(config, google_token, project_id, resolved_region, resolved_size, name, user_data)

    token = _provider_token(config, credentials)
    live_region_ids = set()
    if token_id and provider_id in _LIVE_REGION_PROVIDERS:
        live_region_ids = _fetch_live_region_ids(provider_id, token)
    resolved_region = _validate_region(config, region, live_region_ids=live_region_ids)
    if provider_id == "digitalocean":
        on_unauthorized = _digitalocean_reauth_callback(token_id, credentials) if token_id else None
        return _provision_digitalocean(
            config, token, resolved_region, resolved_size, name, user_data, on_unauthorized=on_unauthorized
        )
    if provider_id == "hetzner":
        return _provision_hetzner(config, token, resolved_region, resolved_size, name, user_data)
    if provider_id == "vultr":
        return _provision_vultr(config, token, resolved_region, resolved_size, name, user_data)
    raise VPSProvisioningError(f"Unsupported VPS provider: {provider_id}")


def record_vps_provision(
    *,
    vps_id: str,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    provider: str,
    provider_resource_id: str,
    public_ip: Optional[str],
    region: str,
    size: str,
    status: str,
    pairing_token: str,
    credentials: Mapping[str, Any],
    pairing_id: Optional[str] = None,
) -> Dict[str, Any]:
    encrypted_credentials = _encrypt_secret(dict(credentials or {}))
    encrypted_pairing_token = _encrypt_secret({"pairing_token": str(pairing_token or "").strip()})
    now = _utc_now_iso()
    record = {
        "vps_id": _clean_identifier(vps_id, field_name="vps_id"),
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "user_id": str(user_id or "").strip() or "unknown-user",
        "provider": _normalize_provider(provider),
        "provider_resource_id": str(provider_resource_id or "").strip(),
        "public_ip": str(public_ip or "").strip() or None,
        "region": str(region or "").strip(),
        "size": str(size or "").strip(),
        "status": _normalize_status(status),
        "pairing_id": str(pairing_id or "").strip() or None,
        "pairing_token_ciphertext": encrypted_pairing_token,
        "credentials_ciphertext": encrypted_credentials,
        "created_at": now,
        "updated_at": now,
    }
    with _STATE_LOCK:
        state = _load_state()
        state.setdefault("vps", {})[record["vps_id"]] = record
        _write_state(state)
    return _public_record(record)


def get_vps_provision_status(vps_id: str) -> Dict[str, Any]:
    clean_vps_id = _clean_identifier(vps_id, field_name="vps_id")
    with _STATE_LOCK:
        state = _load_state()
        record = dict((state.get("vps") or {}).get(clean_vps_id) or {})
        if not record:
            raise KeyError(clean_vps_id)
        next_status = _resolved_record_status(record)
        if next_status != record.get("status"):
            record["status"] = next_status
            record["updated_at"] = _utc_now_iso()
            state.setdefault("vps", {})[clean_vps_id] = record
            _write_state(state)
    return _public_record(record)


def delete_recorded_vps(vps_id: str) -> Dict[str, Any]:
    clean_vps_id = _clean_identifier(vps_id, field_name="vps_id")
    with _STATE_LOCK:
        state = _load_state()
        record = dict((state.get("vps") or {}).get(clean_vps_id) or {})
        if not record:
            raise KeyError(clean_vps_id)
    credentials = _decrypt_secret(str(record.get("credentials_ciphertext") or ""))
    provider_id = _normalize_provider(str(record.get("provider") or ""))
    resource_id = str(record.get("provider_resource_id") or "").strip()
    if not resource_id:
        raise VPSProvisioningError("VPS provider resource id is missing.")
    if provider_id == "google":
        # Early branch: Google's snapshot is {project_id,
        # service_account_email}, not a bearer token — see
        # _google_active_token. provider_resource_id for Google is the
        # instance NAME (see _provision_google), not a numeric id, and
        # deletion is project+zone-scoped rather than token-scoped alone.
        token, project_id = _google_active_token(credentials)
        _delete_provider_resource(
            provider_id, token, resource_id, project_id=project_id, region=str(record.get("region") or "").strip()
        )
    else:
        token = _provider_token(PROVIDER_CONFIGS[provider_id], credentials)
        # This record's credentials are a point-in-time snapshot taken at
        # provision_vps() time (see record_vps_provision), not a live pointer
        # into the token store — it can go stale on its own schedule. Give
        # DigitalOcean the same reactive-401 refresh as every other DO call, just
        # persisted back into this record instead of the (possibly long-gone,
        # disconnected) original token_id.
        on_unauthorized = None
        if provider_id == "digitalocean":
            def _reauth(_vps_id: str = clean_vps_id, _credentials: Mapping[str, Any] = credentials) -> Optional[str]:
                refreshed = _refresh_digitalocean_credentials(_credentials)
                if refreshed is None:
                    return None
                _update_vps_record_credentials(_vps_id, refreshed)
                return str(refreshed.get("access_token") or "").strip() or None

            on_unauthorized = _reauth
        _delete_provider_resource(provider_id, token, resource_id, on_unauthorized=on_unauthorized)
    with _STATE_LOCK:
        state = _load_state()
        latest = dict((state.get("vps") or {}).get(clean_vps_id) or record)
        latest["status"] = "deleted"
        latest["updated_at"] = _utc_now_iso()
        state.setdefault("vps", {})[clean_vps_id] = latest
        _write_state(state)
    return _public_record(latest)


def load_vps_record(vps_id: str) -> Dict[str, Any]:
    clean_vps_id = _clean_identifier(vps_id, field_name="vps_id")
    with _STATE_LOCK:
        record = dict((_load_state().get("vps") or {}).get(clean_vps_id) or {})
    if not record:
        raise KeyError(clean_vps_id)
    return _public_record(record)


def _provision_digitalocean(
    config: ProviderConfig,
    token: str,
    region: str,
    size: str,
    name: str,
    user_data: str,
    *,
    on_unauthorized: Optional[Callable[[], Optional[str]]] = None,
) -> VPSResult:
    payload = {
        "name": name,
        "region": region,
        "size": size,
        "image": config.default_image,
        "user_data": user_data,
        "backups": False,
        "ipv6": True,
        "monitoring": True,
        "tags": ["empyralis", "agent-computer"],
    }
    response = _http_json(
        "POST",
        config.create_url,
        token=token,
        payload=payload,
        provider=config.provider,
        on_unauthorized=on_unauthorized,
    )
    droplet = response.get("droplet") if isinstance(response.get("droplet"), dict) else {}
    resource_id = str(droplet.get("id") or "").strip()
    if not resource_id:
        raise VPSProvisioningError("DigitalOcean did not return a droplet id.")
    return VPSResult(
        provider_resource_id=resource_id,
        public_ip=_digitalocean_public_ip(droplet),
        region=region,
        size=size,
        status="provisioning",
        provider=config.provider,
    )


def _provision_hetzner(
    config: ProviderConfig,
    token: str,
    region: str,
    size: str,
    name: str,
    user_data: str,
) -> VPSResult:
    payload = {
        "name": name,
        "server_type": size,
        "image": config.default_image,
        "location": region,
        "user_data": user_data,
        "start_after_create": True,
        "labels": {"app": "empyralis", "role": "agent-computer"},
    }
    response = _http_json(
        "POST",
        config.create_url,
        token=token,
        payload=payload,
        provider=config.provider,
    )
    server = response.get("server") if isinstance(response.get("server"), dict) else {}
    resource_id = str(server.get("id") or "").strip()
    if not resource_id:
        raise VPSProvisioningError("Hetzner did not return a server id.")
    public_net = server.get("public_net") if isinstance(server.get("public_net"), dict) else {}
    ipv4 = public_net.get("ipv4") if isinstance(public_net.get("ipv4"), dict) else {}
    return VPSResult(
        provider_resource_id=resource_id,
        public_ip=str(ipv4.get("ip") or "").strip() or None,
        region=region,
        size=size,
        status="provisioning",
        provider=config.provider,
    )


def _provision_vultr(
    config: ProviderConfig,
    token: str,
    region: str,
    size: str,
    name: str,
    user_data: str,
) -> VPSResult:
    payload = {
        "region": region,
        "plan": size,
        "os_id": int(config.default_image),
        "label": name,
        "hostname": name,
        "user_data": base64.b64encode(user_data.encode("utf-8")).decode("ascii"),
        "tags": ["empyralis", "agent-computer"],
    }
    response = _http_json(
        "POST",
        config.create_url,
        token=token,
        payload=payload,
        provider=config.provider,
    )
    instance = response.get("instance") if isinstance(response.get("instance"), dict) else {}
    resource_id = str(instance.get("id") or "").strip()
    if not resource_id:
        raise VPSProvisioningError("Vultr did not return an instance id.")
    return VPSResult(
        provider_resource_id=resource_id,
        public_ip=str(instance.get("main_ip") or "").strip() or None,
        region=region,
        size=size,
        status="provisioning",
        provider=config.provider,
    )


def _http_json(
    method: str,
    url: str,
    *,
    token: Optional[str],
    payload: Optional[Mapping[str, Any]],
    provider: str,
    on_unauthorized: Optional[Callable[[], Optional[str]]] = None,
) -> Dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
    request = urlrequest.Request(
        url,
        data=body,
        method=method,
        headers={
            **({"Authorization": f"Bearer {token}"} if token else {}),
            **({"Content-Type": "application/json"} if body is not None else {}),
            "Accept": "application/json",
            "User-Agent": "Empyralis-VPS-Provisioner/1.0",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        if exc.code == 401 and on_unauthorized is not None:
            # Reactive refresh-and-retry backstop (see
            # _digitalocean_reauth_callback) for whenever the proactive
            # check in load_vps_provider_credentials didn't already catch
            # an expiring token — clock skew, an early provider-side
            # revocation, a proactive refresh that failed transiently, etc.
            # Retried once, with on_unauthorized omitted, so a second 401
            # raises normally instead of looping.
            try:
                refreshed_token = on_unauthorized()
            except Exception:
                refreshed_token = None
            if refreshed_token:
                return _http_json(method, url, token=refreshed_token, payload=payload, provider=provider)
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise VPSProvisioningError(f"{provider} provisioning failed: HTTP {exc.code} {detail}") from exc
    except urlerror.URLError as exc:
        raise VPSProvisioningError(f"{provider} provisioning failed: {exc.reason}") from exc
    try:
        parsed = json.loads(response_body) if response_body else {}
    except json.JSONDecodeError as exc:
        raise VPSProvisioningError(f"{provider} returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise VPSProvisioningError(f"{provider} returned an invalid response.")
    return parsed


def _http_form_json(
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any],
    provider: str,
) -> Dict[str, Any]:
    body = urlparse.urlencode(dict(payload)).encode("utf-8")
    request = urlrequest.Request(
        url,
        data=body,
        method=method,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "Empyralis-VPS-Provisioner/1.0",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise VPSProvisioningError(f"{provider} OAuth failed: HTTP {exc.code} {detail}") from exc
    except urlerror.URLError as exc:
        raise VPSProvisioningError(f"{provider} OAuth failed: {exc.reason}") from exc
    try:
        parsed = json.loads(response_body) if response_body else {}
    except json.JSONDecodeError as exc:
        raise VPSProvisioningError(f"{provider} returned invalid OAuth JSON.") from exc
    if not isinstance(parsed, dict):
        raise VPSProvisioningError(f"{provider} returned an invalid OAuth response.")
    return parsed


def _http_empty(
    method: str,
    url: str,
    *,
    token: str,
    provider: str,
    on_unauthorized: Optional[Callable[[], Optional[str]]] = None,
) -> None:
    request = urlrequest.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Empyralis-VPS-Provisioner/1.0",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=30) as response:
            response.read()
    except urlerror.HTTPError as exc:
        if exc.code == 401 and on_unauthorized is not None:
            try:
                refreshed_token = on_unauthorized()
            except Exception:
                refreshed_token = None
            if refreshed_token:
                _http_empty(method, url, token=refreshed_token, provider=provider)
                return
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise VPSProvisioningError(f"{provider} cleanup failed: HTTP {exc.code} {detail}") from exc
    except urlerror.URLError as exc:
        raise VPSProvisioningError(f"{provider} cleanup failed: {exc.reason}") from exc


def _delete_provider_resource(
    provider_id: str,
    token: str,
    resource_id: str,
    *,
    on_unauthorized: Optional[Callable[[], Optional[str]]] = None,
    project_id: Optional[str] = None,
    region: Optional[str] = None,
) -> None:
    if provider_id == "digitalocean":
        _http_empty(
            "DELETE",
            f"https://api.digitalocean.com/v2/droplets/{resource_id}",
            token=token,
            provider=provider_id,
            on_unauthorized=on_unauthorized,
        )
        return
    if provider_id == "hetzner":
        _http_empty(
            "DELETE",
            f"https://api.hetzner.cloud/v1/servers/{resource_id}",
            token=token,
            provider=provider_id,
        )
        return
    if provider_id == "vultr":
        _http_empty(
            "DELETE",
            f"https://api.vultr.com/v2/instances/{resource_id}",
            token=token,
            provider=provider_id,
        )
        return
    if provider_id == "google":
        if not project_id or not region:
            raise VPSProvisioningError("Google Cloud project/region is required to delete this server.")
        # Re-derives the zone from the region the same way _provision_google
        # did at creation time (see _google_resolve_zone) — the exact zone
        # used isn't separately persisted on the VPS record (only region is,
        # matching every other provider's record shape). Stable in practice
        # (zone availability essentially never changes for an already-running
        # instance's region between create and delete) but is a known
        # simplification worth a real-provisioning sanity check.
        zone = _google_resolve_zone(token, project_id, region)
        _http_empty(
            "DELETE",
            f"{GOOGLE_CLOUD_COMPUTE_URL}/projects/{project_id}/zones/{zone}/instances/{resource_id}",
            token=token,
            provider=provider_id,
        )
        return
    raise VPSProvisioningError(f"Unsupported VPS provider: {provider_id}")


def _resolved_record_status(record: Mapping[str, Any]) -> str:
    workspace_id = str(record.get("workspace_id") or "").strip()
    tenant_id = str(record.get("tenant_id") or "").strip() or None
    user_id = str(record.get("user_id") or "").strip() or None
    vps_id = str(record.get("vps_id") or "").strip()
    try:
        registrations = gateway_state_repository.list_workspace_gateway_registrations(
            workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            include_revoked=False,
        )
    except Exception:
        registrations = []
    for registration in registrations:
        metadata = registration.get("metadata") if isinstance(registration.get("metadata"), dict) else {}
        if str(metadata.get("vps_id") or "").strip() == vps_id:
            return "connected"
    try:
        pairing_token = _decrypt_pairing_token(str(record.get("pairing_token_ciphertext") or ""))
    except Exception:
        pairing_token = ""
    if pairing_token:
        pairing = gateway_state_repository.get_pairing_intent_by_token(pairing_token)
        pairing_status = str((pairing or {}).get("status") or "").strip().lower()
        if pairing_status == "consumed":
            return "registering"
        if pairing_status in {"expired", "cancelled", "failed"}:
            return "failed"
    return _normalize_status(str(record.get("status") or "provisioning"))


def _load_state() -> Dict[str, Any]:
    if not VPS_STATE_FILE.exists():
        return {"v": 1, "vps": {}, "tokens": {}, "oauth_states": {}, "google_setup_sessions": {}}
    try:
        parsed = json.loads(VPS_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"v": 1, "vps": {}}
    if not isinstance(parsed, dict):
        return {"v": 1, "vps": {}}
    if not isinstance(parsed.get("vps"), dict):
        parsed["vps"] = {}
    if not isinstance(parsed.get("tokens"), dict):
        parsed["tokens"] = {}
    if not isinstance(parsed.get("oauth_states"), dict):
        parsed["oauth_states"] = {}
    if not isinstance(parsed.get("google_setup_sessions"), dict):
        parsed["google_setup_sessions"] = {}
    parsed.setdefault("v", 1)
    return parsed


def _write_state(payload: Mapping[str, Any]) -> None:
    parent = VPS_STATE_FILE.parent if VPS_STATE_FILE.parent != Path("") else Path(".")
    parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(payload), indent=2, sort_keys=True)
    temp_path = parent / f".{VPS_STATE_FILE.name}.{secrets.token_hex(8)}.tmp"
    temp_path.write_text(serialized, encoding="utf-8")
    temp_path.replace(VPS_STATE_FILE)


def _encrypt_secret(value: Mapping[str, Any]) -> str:
    return vault_store._openssl_encrypt(json.dumps(dict(value), separators=(",", ":"), sort_keys=True))


def _decrypt_secret(ciphertext: str) -> Dict[str, Any]:
    plaintext = vault_store._openssl_decrypt(ciphertext)
    parsed = json.loads(plaintext) if plaintext else {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _decrypt_pairing_token(ciphertext: str) -> str:
    return str(_decrypt_secret(ciphertext).get("pairing_token") or "").strip()


def _public_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "vps_id": str(record.get("vps_id") or "").strip(),
        "workspace_id": str(record.get("workspace_id") or "").strip(),
        "tenant_id": str(record.get("tenant_id") or "").strip(),
        "user_id": str(record.get("user_id") or "").strip(),
        "provider": str(record.get("provider") or "").strip(),
        "provider_resource_id": str(record.get("provider_resource_id") or "").strip(),
        "public_ip": str(record.get("public_ip") or "").strip() or None,
        "region": str(record.get("region") or "").strip(),
        "size": str(record.get("size") or "").strip(),
        "status": _normalize_status(str(record.get("status") or "provisioning")),
        "pairing_id": str(record.get("pairing_id") or "").strip() or None,
        "created_at": str(record.get("created_at") or "").strip(),
        "updated_at": str(record.get("updated_at") or "").strip(),
    }


def _provider_token(config: ProviderConfig, credentials: Mapping[str, Any]) -> str:
    if not isinstance(credentials, Mapping):
        raise ValueError("credentials must be an object.")
    for key in config.token_keys:
        token = str(credentials.get(key) or "").strip()
        if token:
            return token
    raise ValueError(f"{config.auth_label} is required.")


def _digitalocean_client_id() -> str:
    client_id = (
        os.getenv(DIGITALOCEAN_CLIENT_ID_ENV)
        or os.getenv(LEGACY_DIGITALOCEAN_CLIENT_ID_ENV)
        or ""
    ).strip()
    if not client_id:
        raise VPSProvisioningError("DigitalOcean OAuth client id is not configured.")
    return client_id


def _digitalocean_client_secret() -> str:
    client_secret = (
        os.getenv(DIGITALOCEAN_CLIENT_SECRET_ENV)
        or os.getenv(LEGACY_DIGITALOCEAN_CLIENT_SECRET_ENV)
        or ""
    ).strip()
    if not client_secret:
        raise VPSProvisioningError("DigitalOcean OAuth client secret is not configured.")
    return client_secret


def _exchange_digitalocean_oauth_code(code: str) -> Dict[str, Any]:
    token_payload = _http_form_json(
        "POST",
        DIGITALOCEAN_OAUTH_TOKEN_URL,
        provider="digitalocean",
        payload={
            "grant_type": "authorization_code",
            "client_id": _digitalocean_client_id(),
            "client_secret": _digitalocean_client_secret(),
            "code": code,
            "redirect_uri": digitalocean_oauth_redirect_uri(),
        },
    )
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise VPSProvisioningError("DigitalOcean OAuth did not return an access token.")
    return token_payload


# DO's access tokens live 30 days (expires_in=2592000 on both the initial
# grant and every refresh response) — refresh a day early rather than racing
# the exact expiry instant.
_DIGITALOCEAN_TOKEN_REFRESH_BUFFER_SECONDS = 24 * 60 * 60


def _digitalocean_credentials_need_refresh(credentials: Mapping[str, Any]) -> bool:
    if not str(credentials.get("refresh_token") or "").strip():
        return False
    expires_at = _to_int(credentials.get("access_token_expires_at"))
    if expires_at <= 0:
        # Predates this fix (stored before access_token_expires_at was
        # captured), or a raw personal access token that happens to have a
        # refresh_token-shaped key some other way — nothing to judge
        # proactively either way. A genuinely expired token still gets
        # caught reactively by the on_unauthorized retry on the actual API
        # call (see _digitalocean_reauth_callback).
        return False
    return int(time.time()) >= expires_at - _DIGITALOCEAN_TOKEN_REFRESH_BUFFER_SECONDS


def _refresh_digitalocean_credentials(credentials: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """POST grant_type=refresh_token to DO's OAuth token endpoint (same
    /v1/oauth/token endpoint as the initial exchange — DO's refresh response
    is documented as "the same format as the original access token grant").
    Returns an updated credentials mapping, or None if there's no
    refresh_token to use, DO isn't configured, or the call fails — callers
    fall back to the existing (possibly already-expired) token and let the
    ordinary HTTP 401 surface, exactly as it did before this existed."""
    refresh_token = str(credentials.get("refresh_token") or "").strip()
    if not refresh_token:
        return None
    try:
        token_payload = _http_form_json(
            "POST",
            DIGITALOCEAN_OAUTH_TOKEN_URL,
            provider="digitalocean",
            payload={
                "grant_type": "refresh_token",
                "client_id": _digitalocean_client_id(),
                "client_secret": _digitalocean_client_secret(),
                "refresh_token": refresh_token,
            },
        )
    except VPSProvisioningError:
        return None
    new_access_token = str(token_payload.get("access_token") or "").strip()
    if not new_access_token:
        return None
    updated = dict(credentials)
    updated["access_token"] = new_access_token
    # DO may or may not rotate the refresh_token on use; keep the existing
    # one if the response didn't include a new one.
    new_refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if new_refresh_token:
        updated["refresh_token"] = new_refresh_token
    expires_in = _to_int(token_payload.get("expires_in"))
    if expires_in > 0:
        updated["access_token_expires_at"] = int(time.time()) + expires_in
    return updated


def _update_stored_token_credentials(token_id: str, credentials: Mapping[str, Any]) -> None:
    """Persist a refreshed credential back into the token store (state["tokens"][token_id])
    — the store that load_vps_provider_credentials / fetch_provider_plans /
    fetch_provider_regions / provision_vps all read from."""
    with _STATE_LOCK:
        state = _load_state()
        record = dict((state.get("tokens") or {}).get(token_id) or {})
        if not record:
            return
        record["credentials_ciphertext"] = _encrypt_secret(dict(credentials))
        record["updated_at"] = _utc_now_iso()
        state.setdefault("tokens", {})[token_id] = record
        _write_state(state)


def _update_vps_record_credentials(vps_id: str, credentials: Mapping[str, Any]) -> None:
    """Persist a refreshed credential back into a VPS record's own snapshot
    (state["vps"][vps_id]) — the separate, point-in-time credentials copy
    delete_recorded_vps reads from (see record_vps_provision)."""
    with _STATE_LOCK:
        state = _load_state()
        record = dict((state.get("vps") or {}).get(vps_id) or {})
        if not record:
            return
        record["credentials_ciphertext"] = _encrypt_secret(dict(credentials))
        record["updated_at"] = _utc_now_iso()
        state.setdefault("vps", {})[vps_id] = record
        _write_state(state)


def _digitalocean_reauth_callback(
    token_id: Optional[str], credentials: Mapping[str, Any]
) -> Optional[Callable[[], Optional[str]]]:
    """Build an on_unauthorized callback for _http_json/_http_empty: on a
    real HTTP 401, refresh once via DO's refresh_token grant and persist the
    result back to the token store, returning the new access token so the
    caller can retry. Returns None (no callback at all) without a token_id,
    since there'd be nowhere to persist a refreshed credential to."""
    if not token_id:
        return None

    def _reauth() -> Optional[str]:
        refreshed = _refresh_digitalocean_credentials(credentials)
        if refreshed is None:
            return None
        _update_stored_token_credentials(token_id, refreshed)
        return str(refreshed.get("access_token") or "").strip() or None

    return _reauth


# =============================================================================
# Google Cloud: bootstrap-then-impersonate
#
# Everything below is new for Google — DigitalOcean/Hetzner/Vultr above are
# untouched except for the small early-return branches added to the shared
# entry points (fetch_provider_plans, fetch_provider_regions, provision_vps,
# delete_recorded_vps, _delete_provider_resource, _fetch_live_regions,
# store_vps_provider_token, _normalize_provider). See the module docstring
# above GOOGLE_OAUTH_AUTHORIZE_URL for the three-step shape this implements.
# =============================================================================


def _google_client_id() -> str:
    client_id = (os.getenv(GOOGLE_CLOUD_CLIENT_ID_ENV) or "").strip()
    if not client_id:
        raise VPSProvisioningError("Google Cloud OAuth client id is not configured.")
    return client_id


def _google_client_secret() -> str:
    client_secret = (os.getenv(GOOGLE_CLOUD_CLIENT_SECRET_ENV) or "").strip()
    if not client_secret:
        raise VPSProvisioningError("Google Cloud OAuth client secret is not configured.")
    return client_secret


def _exchange_google_oauth_code(code: str) -> Dict[str, Any]:
    token_payload = _http_form_json(
        "POST",
        GOOGLE_OAUTH_TOKEN_URL,
        provider="google",
        payload={
            "grant_type": "authorization_code",
            "client_id": _google_client_id(),
            "client_secret": _google_client_secret(),
            "code": code,
            "redirect_uri": google_oauth_redirect_uri(),
        },
    )
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise VPSProvisioningError("Google OAuth did not return an access token.")
    return token_payload


# --- Setup-session storage: the user's OAuth token, held ONLY long enough to
# run the bootstrap sequence below, then discarded (see
# finish_google_bootstrap / _discard_google_setup_session). Never reused for
# ongoing VM management — that's what the impersonation section further down
# is for. Kept in its own state bucket (google_setup_sessions) rather than
# reusing the "tokens" bucket store_vps_provider_token writes to, since a
# setup session is not itself a usable provider connection.


def _store_google_setup_session(
    *,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    credentials: Mapping[str, Any],
) -> str:
    setup_id = f"gsetup_{secrets.token_hex(16)}"
    now = _utc_now_iso()
    record = {
        "setup_id": setup_id,
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "user_id": str(user_id or "").strip() or "unknown-user",
        "credentials_ciphertext": _encrypt_secret(_credentials_with_expiry(dict(credentials))),
        "created_at": now,
        "updated_at": now,
    }
    with _STATE_LOCK:
        state = _load_state()
        state.setdefault("google_setup_sessions", {})[setup_id] = record
        _write_state(state)
    return setup_id


def _load_google_setup_session(
    setup_id: str, *, workspace_id: str, user_id: Optional[str] = None
) -> Dict[str, Any]:
    clean_id = _clean_identifier(setup_id, field_name="setup_id")
    with _STATE_LOCK:
        record = dict((_load_state().get("google_setup_sessions") or {}).get(clean_id) or {})
    if not record:
        raise KeyError(clean_id)
    if str(record.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        raise KeyError(clean_id)
    if user_id and str(record.get("user_id") or "").strip() != str(user_id or "").strip():
        raise KeyError(clean_id)
    return record


def _update_google_setup_session_credentials(setup_id: str, credentials: Mapping[str, Any]) -> None:
    with _STATE_LOCK:
        state = _load_state()
        record = dict((state.get("google_setup_sessions") or {}).get(setup_id) or {})
        if not record:
            return
        record["credentials_ciphertext"] = _encrypt_secret(dict(credentials))
        record["updated_at"] = _utc_now_iso()
        state.setdefault("google_setup_sessions", {})[setup_id] = record
        _write_state(state)


def _discard_google_setup_session(setup_id: str) -> None:
    with _STATE_LOCK:
        state = _load_state()
        (state.get("google_setup_sessions") or {}).pop(setup_id, None)
        _write_state(state)


# Google access tokens live ~1h — refresh a few minutes early. Much shorter
# fuse than DO's 24h buffer (_DIGITALOCEAN_TOKEN_REFRESH_BUFFER_SECONDS)
# because the underlying token itself is much shorter-lived, and this session
# only needs to survive one bootstrap flow, not months of ongoing use.
_GOOGLE_USER_TOKEN_REFRESH_BUFFER_SECONDS = 5 * 60


def _google_user_credentials_need_refresh(credentials: Mapping[str, Any]) -> bool:
    if not str(credentials.get("refresh_token") or "").strip():
        return False
    expires_at = _to_int(credentials.get("access_token_expires_at"))
    if expires_at <= 0:
        return False
    return int(time.time()) >= expires_at - _GOOGLE_USER_TOKEN_REFRESH_BUFFER_SECONDS


def _refresh_google_user_credentials(credentials: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    refresh_token = str(credentials.get("refresh_token") or "").strip()
    if not refresh_token:
        return None
    try:
        token_payload = _http_form_json(
            "POST",
            GOOGLE_OAUTH_TOKEN_URL,
            provider="google",
            payload={
                "grant_type": "refresh_token",
                "client_id": _google_client_id(),
                "client_secret": _google_client_secret(),
                "refresh_token": refresh_token,
            },
        )
    except VPSProvisioningError:
        return None
    new_access_token = str(token_payload.get("access_token") or "").strip()
    if not new_access_token:
        return None
    updated = dict(credentials)
    updated["access_token"] = new_access_token
    # Google does not reliably reissue refresh_token on a refresh grant —
    # keep the existing one when it doesn't.
    new_refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if new_refresh_token:
        updated["refresh_token"] = new_refresh_token
    expires_in = _to_int(token_payload.get("expires_in"))
    if expires_in > 0:
        updated["access_token_expires_at"] = int(time.time()) + expires_in
    return updated


def _google_setup_session_access_token(
    setup_id: str, *, workspace_id: str, user_id: Optional[str] = None
) -> str:
    session = _load_google_setup_session(setup_id, workspace_id=workspace_id, user_id=user_id)
    credentials = _decrypt_secret(str(session.get("credentials_ciphertext") or ""))
    if _google_user_credentials_need_refresh(credentials):
        refreshed = _refresh_google_user_credentials(credentials)
        if refreshed is not None:
            credentials = refreshed
            _update_google_setup_session_credentials(setup_id, credentials)
    token = str(credentials.get("access_token") or "").strip()
    if not token:
        raise VPSProvisioningError("Google sign-in has expired — reconnect your Google account and try again.")
    return token


# --- Impersonation: the ONLY path ongoing (post-bootstrap) Google Cloud
# calls use. Authenticates as Empyralis's own operator identity (never the
# end user's token, which is already gone by this point) to mint a
# short-lived access token AS the customer's empyralis-provisioner service
# account — keyless, no downloaded service-account JSON ever involved.


def _google_operator_identity() -> str:
    identity = (os.getenv(GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL_ENV) or "").strip()
    if not identity:
        raise VPSProvisioningError(
            f"Empyralis's Google Cloud operator identity is not configured "
            f"({GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL_ENV} unset)."
        )
    return identity


def _google_operator_access_token() -> str:
    refresh_token = (os.getenv(GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN_ENV) or "").strip()
    if not refresh_token:
        raise VPSProvisioningError(
            f"Empyralis's Google Cloud operator identity is not configured "
            f"({GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN_ENV} unset)."
        )
    token_payload = _http_form_json(
        "POST",
        GOOGLE_OAUTH_TOKEN_URL,
        provider="google",
        payload={
            "grant_type": "refresh_token",
            "client_id": _google_client_id(),
            "client_secret": _google_client_secret(),
            "refresh_token": refresh_token,
        },
    )
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise VPSProvisioningError("Could not refresh the Google Cloud operator identity token.")
    return access_token


def _google_impersonated_access_token(service_account_email: str) -> str:
    """Mint a short-lived (default 1h) access token AS the customer's
    empyralis-provisioner service account via the IAM Credentials API's
    generateAccessToken — requires Empyralis's operator identity to already
    hold roles/iam.serviceAccountTokenCreator on this SA (granted once, in
    _google_grant_operator_impersonation during finish_google_bootstrap). The
    returned token is used for exactly one caller's worth of Compute Engine
    calls and is never persisted anywhere."""
    clean_email = str(service_account_email or "").strip()
    if not clean_email:
        raise ValueError("Google Cloud service account email is required.")
    operator_token = _google_operator_access_token()
    response = _http_json(
        "POST",
        f"{GOOGLE_CLOUD_IAM_CREDENTIALS_URL}/projects/-/serviceAccounts/{clean_email}:generateAccessToken",
        token=operator_token,
        payload={"scope": [GOOGLE_CLOUD_PLATFORM_SCOPE]},
        provider="google",
    )
    access_token = str(response.get("accessToken") or "").strip()
    if not access_token:
        raise VPSProvisioningError("Google Cloud did not return an impersonated access token.")
    return access_token


def _google_active_token(credentials: Mapping[str, Any]) -> tuple[str, str]:
    """Resolve a fresh impersonated bearer token + project_id from a stored
    Google connection ({project_id, service_account_email} — see
    finish_google_bootstrap's _store_provider_token_record call). Every
    ongoing Google call (plans, regions, provision, delete) goes through
    this — never _provider_token, which has nothing to extract from these
    credentials (see PROVIDER_CONFIGS["google"].token_keys)."""
    project_id = str(credentials.get("project_id") or "").strip()
    service_account_email = str(credentials.get("service_account_email") or "").strip()
    if not project_id or not service_account_email:
        raise VPSProvisioningError("Google Cloud project is not fully connected.")
    return _google_impersonated_access_token(service_account_email), project_id


# --- Project selection, billing check, and the bootstrap sequence itself.


def _google_project_id_slug(name: str) -> str:
    base = "".join(ch if ch.isalnum() else "-" for ch in str(name or "").lower()).strip("-")
    base = "-".join(filter(None, base.split("-")))[:22] or "empyralis"
    if not base[:1].isalpha():
        base = f"e-{base}"
    return f"{base}-{secrets.token_hex(4)}"[:30]


def list_google_projects(setup_id: str, *, workspace_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    token = _google_setup_session_access_token(setup_id, workspace_id=workspace_id, user_id=user_id)
    raw = _http_json(
        "GET",
        f"{GOOGLE_CLOUD_RESOURCE_MANAGER_URL}/projects?filter={urlparse.quote('lifecycleState:ACTIVE')}",
        token=token,
        payload=None,
        provider="google",
    )
    return {"projects": _normalize_google_projects(raw)}


def _normalize_google_projects(payload: Mapping[str, Any]) -> list[Dict[str, str]]:
    items = payload.get("projects") if isinstance(payload.get("projects"), list) else []
    projects: list[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        project_id = str(item.get("projectId") or "").strip()
        if not project_id:
            continue
        projects.append({"project_id": project_id, "name": str(item.get("name") or "").strip() or project_id})
    return projects


def create_google_project(
    setup_id: str, project_name: str, *, workspace_id: str, user_id: Optional[str] = None
) -> Dict[str, str]:
    token = _google_setup_session_access_token(setup_id, workspace_id=workspace_id, user_id=user_id)
    clean_name = str(project_name or "").strip() or "Empyralis Agent Computer"
    project_id = _google_project_id_slug(clean_name)
    _http_json(
        "POST",
        f"{GOOGLE_CLOUD_RESOURCE_MANAGER_URL}/projects",
        token=token,
        payload={"projectId": project_id, "name": clean_name},
        provider="google",
    )
    return {"project_id": project_id, "name": clean_name}


def check_google_project_billing(
    setup_id: str, project_id: str, *, workspace_id: str, user_id: Optional[str] = None
) -> Dict[str, Any]:
    clean_project_id = _clean_identifier(project_id, field_name="project_id")
    token = _google_setup_session_access_token(setup_id, workspace_id=workspace_id, user_id=user_id)
    raw = _http_json(
        "GET",
        f"{GOOGLE_CLOUD_BILLING_URL}/projects/{clean_project_id}/billingInfo",
        token=token,
        payload=None,
        provider="google",
    )
    return {
        "project_id": clean_project_id,
        "billing_enabled": bool(raw.get("billingEnabled")),
        # There is no API that attaches a billing account/card to a project —
        # Cloud Billing's projects.updateBillingInfo call only LINKS an
        # already-existing billing account, it never collects payment
        # details or creates one. The console is the only place a user can
        # actually do that; this is the fallback the frontend surfaces when
        # billing_enabled is False.
        "console_url": f"https://console.cloud.google.com/billing/linkedaccount?project={clean_project_id}",
    }


def _google_enable_compute_api(token: str, project_id: str) -> None:
    _http_json(
        "POST",
        f"{GOOGLE_CLOUD_SERVICE_USAGE_URL}/projects/{project_id}/services/compute.googleapis.com:enable",
        token=token,
        payload={},
        provider="google",
    )


def _google_create_provisioner_service_account(token: str, project_id: str) -> str:
    sa_email = f"{GOOGLE_PROVISIONER_SA_ACCOUNT_ID}@{project_id}.iam.gserviceaccount.com"
    try:
        _http_json(
            "POST",
            f"{GOOGLE_CLOUD_IAM_URL}/projects/{project_id}/serviceAccounts",
            token=token,
            payload={
                "accountId": GOOGLE_PROVISIONER_SA_ACCOUNT_ID,
                "serviceAccount": {"displayName": "Empyralis Agent Computer Provisioner"},
            },
            provider="google",
        )
    except VPSProvisioningError as exc:
        if "HTTP 409" not in str(exc):
            raise
        # Already exists — a re-run bootstrap (e.g. retried after fixing a
        # billing issue) reuses it instead of failing.
    return sa_email


# Minimal VM-lifecycle-only permission set for the empyralis-provisioner
# custom role — deliberately excludes IAM/project-level admin permissions
# (this SA can create/list/delete the VMs it needs to, nothing about the
# project itself).
_GOOGLE_PROVISIONER_ROLE_PERMISSIONS = (
    "compute.instances.create",
    "compute.instances.delete",
    "compute.instances.get",
    "compute.instances.list",
    "compute.instances.setMetadata",
    "compute.instances.setLabels",
    "compute.instances.setTags",
    "compute.disks.create",
    "compute.disks.get",
    "compute.images.useReadOnly",
    "compute.networks.get",
    "compute.networks.use",
    "compute.subnetworks.use",
    "compute.subnetworks.useExternalIp",
    "compute.firewalls.create",
    "compute.firewalls.get",
    "compute.firewalls.list",
    "compute.zones.get",
    "compute.zones.list",
    "compute.zoneOperations.get",
    "compute.regions.get",
    "compute.regions.list",
    "compute.machineTypes.get",
    "compute.machineTypes.list",
    "compute.globalOperations.get",
)


def _google_bind_custom_role(token: str, project_id: str, service_account_email: str) -> None:
    role_name = f"projects/{project_id}/roles/{GOOGLE_PROVISIONER_CUSTOM_ROLE_ID}"
    try:
        _http_json(
            "POST",
            f"{GOOGLE_CLOUD_IAM_URL}/projects/{project_id}/roles",
            token=token,
            payload={
                "roleId": GOOGLE_PROVISIONER_CUSTOM_ROLE_ID,
                "role": {
                    "title": "Empyralis VM Provisioner",
                    "description": (
                        "Minimal permissions to create, list, and delete Agent Computer VMs. "
                        "Managed by Empyralis — see empyralis.ai."
                    ),
                    "includedPermissions": list(_GOOGLE_PROVISIONER_ROLE_PERMISSIONS),
                    "stage": "GA",
                },
            },
            provider="google",
        )
    except VPSProvisioningError as exc:
        if "HTTP 409" not in str(exc):
            raise
    _google_set_project_iam_binding(
        token, project_id, role=role_name, member=f"serviceAccount:{service_account_email}"
    )


def _google_grant_operator_impersonation(token: str, project_id: str, service_account_email: str) -> None:
    _google_set_service_account_iam_binding(
        token,
        project_id,
        service_account_email,
        role="roles/iam.serviceAccountTokenCreator",
        member=f"serviceAccount:{_google_operator_identity()}",
    )


def _google_set_project_iam_binding(token: str, project_id: str, *, role: str, member: str) -> None:
    policy = _http_json(
        "POST",
        f"{GOOGLE_CLOUD_RESOURCE_MANAGER_URL}/projects/{project_id}:getIamPolicy",
        token=token,
        payload={},
        provider="google",
    )
    _google_add_binding(policy, role=role, member=member)
    _http_json(
        "POST",
        f"{GOOGLE_CLOUD_RESOURCE_MANAGER_URL}/projects/{project_id}:setIamPolicy",
        token=token,
        payload={"policy": policy},
        provider="google",
    )


def _google_set_service_account_iam_binding(
    token: str, project_id: str, service_account_email: str, *, role: str, member: str
) -> None:
    resource = f"projects/{project_id}/serviceAccounts/{service_account_email}"
    policy = _http_json(
        "POST", f"{GOOGLE_CLOUD_IAM_URL}/{resource}:getIamPolicy", token=token, payload={}, provider="google"
    )
    _google_add_binding(policy, role=role, member=member)
    _http_json(
        "POST",
        f"{GOOGLE_CLOUD_IAM_URL}/{resource}:setIamPolicy",
        token=token,
        payload={"policy": policy},
        provider="google",
    )


def _google_add_binding(policy: Dict[str, Any], *, role: str, member: str) -> None:
    """Mutates `policy` in place, adding `member` to the binding for `role`
    (creating the binding if it doesn't exist). Read-modify-write on whatever
    getIamPolicy just returned — including any bindings a human already set
    up in the console — so the follow-up setIamPolicy only ever ADDS this one
    binding rather than clobbering the rest of the policy."""
    bindings = policy.get("bindings")
    if not isinstance(bindings, list):
        bindings = []
        policy["bindings"] = bindings
    for binding in bindings:
        if isinstance(binding, dict) and binding.get("role") == role:
            members = binding.get("members")
            if not isinstance(members, list):
                members = []
                binding["members"] = members
            if member not in members:
                members.append(member)
            return
    bindings.append({"role": role, "members": [member]})


def finish_google_bootstrap(
    setup_id: str, project_id: str, *, workspace_id: str, tenant_id: str, user_id: str
) -> Dict[str, Any]:
    """The one-time bootstrap (step 2 of the module docstring above
    GOOGLE_OAUTH_AUTHORIZE_URL): verify billing, enable Compute Engine,
    create+bind a minimal-permission service account, hand Empyralis's
    operator identity impersonation rights on it, then store {project_id,
    service_account_email} as this workspace's Google Cloud connection and
    discard the user's OAuth session for good. Idempotent by design (the
    service-account-create and custom-role-create calls both tolerate 409
    Already Exists) so a user can safely retry after fixing a billing issue
    without side effects piling up.
    """
    clean_project_id = _clean_identifier(project_id, field_name="project_id")
    token = _google_setup_session_access_token(setup_id, workspace_id=workspace_id, user_id=user_id)
    billing = check_google_project_billing(setup_id, clean_project_id, workspace_id=workspace_id, user_id=user_id)
    if not billing.get("billing_enabled"):
        raise VPSProvisioningError(
            "Attach a billing account to this Google Cloud project before continuing: "
            f"{billing.get('console_url')}"
        )
    _google_enable_compute_api(token, clean_project_id)
    sa_email = _google_create_provisioner_service_account(token, clean_project_id)
    _google_bind_custom_role(token, clean_project_id, sa_email)
    _google_grant_operator_impersonation(token, clean_project_id, sa_email)
    token_id = _store_provider_token_record(
        provider_id="google",
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        user_id=user_id,
        credentials={"project_id": clean_project_id, "service_account_email": sa_email},
        source="oauth_bootstrap",
    )
    _discard_google_setup_session(setup_id)
    return {
        "provider": "google",
        "token_id": token_id,
        "project_id": clean_project_id,
        "service_account_email": sa_email,
        "workspace_id": str(workspace_id or "").strip() or "default",
    }


# --- Ongoing provisioning: zone resolution, instance create/delete.


def _google_resolve_zone(token: str, project_id: str, region: str) -> str:
    """GCE instance creation needs a ZONE, not a region — compute.regions.list
    (used for the browsable region list, the same "region" concept every
    other provider here has) groups zones; instances.insert needs one
    specific zone within it. Prefers the region's first UP zone from a live
    compute.zones.list call; falls back to the "{region}-a" naming
    convention (true of every GCE region in practice) if that call fails or
    matches nothing, so a transient zones.list error never blocks
    provisioning."""
    try:
        raw = _http_json(
            "GET",
            f"{GOOGLE_CLOUD_COMPUTE_URL}/projects/{project_id}/zones",
            token=token,
            payload=None,
            provider="google",
        )
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("status") or "").upper() != "UP":
                continue
            zone_region = str(item.get("region") or "").rstrip("/")
            if not zone_region.endswith(f"/regions/{region}"):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                return name
    except VPSProvisioningError:
        pass
    return f"{region}-a"


def _provision_google(
    config: ProviderConfig,
    token: str,
    project_id: str,
    region: str,
    size: str,
    name: str,
    user_data: str,
) -> VPSResult:
    zone = _google_resolve_zone(token, project_id, region)
    payload = {
        "name": name,
        "machineType": f"zones/{zone}/machineTypes/{size}",
        "disks": [
            {
                "boot": True,
                "autoDelete": True,
                "initializeParams": {
                    "sourceImage": config.default_image,
                    "diskSizeGb": str(GOOGLE_DEFAULT_BOOT_DISK_GB),
                },
            }
        ],
        "networkInterfaces": [
            {
                "network": "global/networks/default",
                "accessConfigs": [{"type": "ONE_TO_ONE_NAT", "name": "External NAT"}],
            }
        ],
        "metadata": {"items": [{"key": "user-data", "value": user_data}]},
        "labels": {"app": "empyralis", "role": "agent-computer"},
        "tags": {"items": ["empyralis", "agent-computer"]},
    }
    _http_json(
        "POST",
        f"{GOOGLE_CLOUD_COMPUTE_URL}/projects/{project_id}/zones/{zone}/instances",
        token=token,
        payload=payload,
        provider="google",
    )
    # instances.insert returns a zone Operation, not the Instance resource —
    # no public IP is assigned/reported until that operation completes,
    # unlike DO/Hetzner/Vultr's create calls which return some ip/id fields
    # immediately even mid-provisioning. `name` (generated by _server_name
    # before this call) is stored as provider_resource_id — GCE's
    # instances.delete addresses an instance by NAME, not the numeric id an
    # Operation's targetId carries, so storing the name (which this service
    # already knows deterministically, without needing to parse the
    # response) is what makes delete_recorded_vps work later. The
    # "provisioning" status contract (get_vps_provision_status) already
    # tolerates public_ip=None throughout, same as every other provider
    # reports before its box has actually registered.
    return VPSResult(
        provider_resource_id=name,
        public_ip=None,
        region=region,
        size=size,
        status="provisioning",
        provider=config.provider,
    )


# --- Plans: Compute Engine's machineTypes carry no price field of their own
# (unlike DO/Hetzner/Vultr's size/plan objects) — price comes from a SEPARATE
# catalog (Cloud Billing Catalog API, service 6F81-5844-456A) that prices CPU
# and RAM per-core/per-GB rather than per machine type, matched to a machine
# type only via category.resourceGroup + description text (Google publishes
# no machine-type-keyed price list). Best-effort by nature — see
# _normalize_google_plans's docstring for the two known simplifications.


def _fetch_google_plans(credentials: Mapping[str, Any]) -> list[VPSPlan]:
    token, project_id = _google_active_token(credentials)
    machine_types_raw = _http_json(
        "GET",
        f"{GOOGLE_CLOUD_COMPUTE_URL}/projects/{project_id}/aggregated/machineTypes",
        token=token,
        payload=None,
        provider="google",
    )
    sku_raw = _google_fetch_compute_skus(token)
    return _normalize_google_plans(machine_types_raw, sku_raw, region=PROVIDER_CONFIGS["google"].default_region)


def _google_fetch_compute_skus(token: str) -> Dict[str, Any]:
    skus: list[Dict[str, Any]] = []
    page_token = ""
    for _ in range(20):  # hard safety cap on pagination loops
        url = f"{GOOGLE_CLOUD_BILLING_URL}/services/{GOOGLE_CLOUD_BILLING_CATALOG_COMPUTE_SERVICE_ID}/skus"
        if page_token:
            url = f"{url}?pageToken={urlparse.quote(page_token)}"
        raw = _http_json("GET", url, token=token, payload=None, provider="google")
        items = raw.get("skus") if isinstance(raw.get("skus"), list) else []
        skus.extend(item for item in items if isinstance(item, Mapping))
        page_token = str(raw.get("nextPageToken") or "").strip()
        if not page_token:
            break
    return {"skus": skus}


def _google_region_from_zone(zone_name: str) -> str:
    # GCE zone names are always "{region}-{letter}", e.g. "us-central1-a".
    parts = str(zone_name or "").rsplit("-", 1)
    return parts[0] if len(parts) == 2 else ""


def _google_machine_type_family(name: str) -> str:
    return str(name or "").split("-")[0].strip().lower()


def _google_dedupe_machine_types(payload: Mapping[str, Any]) -> list[Dict[str, Any]]:
    """aggregatedList groups machine types by zone — the same machine type
    (identical spec everywhere it exists) shows up once per zone it's
    available in. Dedupes by name while accumulating the set of REGIONS
    (derived from each zone name) it's available in, the same
    per-plan-availability shape DO/Hetzner/Vultr's normalizers already
    populate their `regions` field with."""
    items = payload.get("items") if isinstance(payload.get("items"), Mapping) else {}
    by_name: Dict[str, Dict[str, Any]] = {}
    for zone_key, zone_value in items.items():
        if not isinstance(zone_value, Mapping):
            continue
        zone_name = str(zone_key).rsplit("/", 1)[-1].strip()
        region = _google_region_from_zone(zone_name)
        machine_types = zone_value.get("machineTypes") if isinstance(zone_value.get("machineTypes"), list) else []
        for machine_type in machine_types:
            if not isinstance(machine_type, Mapping) or machine_type.get("deprecated"):
                continue
            name = str(machine_type.get("name") or "").strip()
            family = _google_machine_type_family(name)
            if not name or family not in _GOOGLE_SUPPORTED_MACHINE_FAMILIES:
                continue
            vcpus = _to_int(machine_type.get("guestCpus"))
            memory_mb = _to_int(machine_type.get("memoryMb"))
            if vcpus < 1 or memory_mb < 1024:
                continue
            entry = by_name.setdefault(
                name,
                {
                    "name": name,
                    "vcpus": vcpus,
                    "memory_mb": memory_mb,
                    "disk_gb": GOOGLE_DEFAULT_BOOT_DISK_GB,
                    "regions": set(),
                },
            )
            if region:
                entry["regions"].add(region)
    return list(by_name.values())


def _google_sku_family_from_resource_group(resource_group: str) -> str:
    # e.g. "N2Standard" -> "n2", "N2DStandard" -> "n2d", "E2Standard" -> "e2"
    # — stripping the suffix (rather than a prefix match) avoids "n2"
    # incorrectly matching "n2dstandard".
    token = str(resource_group or "").strip().lower()
    for suffix in ("standard", "custom", "highmem", "highcpu"):
        if token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _google_sku_hourly_price(sku: Mapping[str, Any]) -> float:
    pricing_info = sku.get("pricingInfo") if isinstance(sku.get("pricingInfo"), list) else []
    if not pricing_info or not isinstance(pricing_info[0], Mapping):
        return 0.0
    expression = pricing_info[0].get("pricingExpression")
    expression = expression if isinstance(expression, Mapping) else {}
    tiers = expression.get("tieredRates") if isinstance(expression.get("tieredRates"), list) else []
    if not tiers or not isinstance(tiers[0], Mapping):
        return 0.0
    unit_price = tiers[0].get("unitPrice")
    unit_price = unit_price if isinstance(unit_price, Mapping) else {}
    return _to_float(unit_price.get("units")) + _to_float(unit_price.get("nanos")) / 1_000_000_000.0


def _google_sku_prices_by_family(payload: Mapping[str, Any], *, region: str) -> Dict[tuple[str, str], float]:
    items = payload.get("skus") if isinstance(payload.get("skus"), list) else []
    prices: Dict[tuple[str, str], float] = {}
    for sku in items:
        if not isinstance(sku, Mapping):
            continue
        category = sku.get("category") if isinstance(sku.get("category"), Mapping) else {}
        if str(category.get("usageType") or "") != "OnDemand":
            continue
        if str(category.get("resourceFamily") or "") != "Compute":
            continue
        service_regions = sku.get("serviceRegions") if isinstance(sku.get("serviceRegions"), list) else []
        if region not in service_regions:
            continue
        family = _google_sku_family_from_resource_group(str(category.get("resourceGroup") or ""))
        if not family:
            continue
        description = str(sku.get("description") or "").lower()
        if "core" in description:
            kind = "cpu"
        elif "ram" in description:
            kind = "ram"
        else:
            continue
        price = _google_sku_hourly_price(sku)
        if price > 0:
            prices[(family, kind)] = price
    return prices


def _normalize_google_plans(
    machine_types_payload: Mapping[str, Any], sku_payload: Mapping[str, Any], *, region: str
) -> list[VPSPlan]:
    """Best-effort pricing, priced against ONE reference region (the
    provider default) — like every other provider here, VPSPlan has a single
    price_monthly, not a per-region one, but Google is the one provider where
    the real price does vary by region. This is a starting/reference price,
    not a region-exact one (same convention cloud pricing calculators use for
    a headline "$X/mo" figure). Restricted to a curated set of
    general-purpose machine families (_GOOGLE_SUPPORTED_MACHINE_FAMILIES) —
    GCE's full catalog also includes GPU/bare-metal/memory-optimized
    families whose pricing shape and fit for an "Agent Computer" are both
    out of scope here.
    """
    machine_types = _google_dedupe_machine_types(machine_types_payload)
    prices = _google_sku_prices_by_family(sku_payload, region=region)
    plans: list[VPSPlan] = []
    for entry in machine_types:
        family = _google_machine_type_family(entry["name"])
        cpu_price = prices.get((family, "cpu"))
        ram_price = prices.get((family, "ram"))
        if not cpu_price or not ram_price:
            continue
        vcpus = entry["vcpus"]
        memory_mb = entry["memory_mb"]
        monthly = round((vcpus * cpu_price + (memory_mb / 1024.0) * ram_price) * _GOOGLE_AVERAGE_HOURS_PER_MONTH, 2)
        if monthly <= 0:
            continue
        plans.append(
            VPSPlan(
                id=entry["name"],
                slug=entry["name"],
                label=f"{vcpus} CPU · {_memory_label(memory_mb)} · {entry['disk_gb']}GB SSD",
                vcpus=vcpus,
                memory_mb=memory_mb,
                disk_gb=entry["disk_gb"],
                price_monthly=monthly,
                price_label=f"${monthly:g}/mo",
                regions=tuple(sorted(entry["regions"])),
            )
        )
    return _mark_recommended(plans)


def _normalize_digitalocean_plans(payload: Mapping[str, Any]) -> list[VPSPlan]:
    items = payload.get("sizes") if isinstance(payload.get("sizes"), list) else []
    plans: list[VPSPlan] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        vcpus = _to_int(item.get("vcpus"))
        memory_mb = _to_int(item.get("memory"))
        disk_gb = _to_int(item.get("disk"))
        price = _to_float(item.get("price_monthly"))
        slug = str(item.get("slug") or "").strip()
        if not slug or vcpus < 1 or memory_mb < 1024 or price <= 0:
            continue
        if item.get("available") is False:
            continue
        raw_regions = item.get("regions") if isinstance(item.get("regions"), list) else []
        regions = tuple(str(r).strip() for r in raw_regions if str(r).strip())
        plans.append(
            VPSPlan(
                id=slug,
                slug=slug,
                label=f"{vcpus} CPU · {_memory_label(memory_mb)} · {disk_gb}GB SSD",
                vcpus=vcpus,
                memory_mb=memory_mb,
                disk_gb=disk_gb,
                price_monthly=price,
                price_label=f"${price:g}/mo",
                regions=regions,
            )
        )
    return _mark_recommended(plans)


def _normalize_hetzner_plans(payload: Mapping[str, Any]) -> list[VPSPlan]:
    items = payload.get("server_types") if isinstance(payload.get("server_types"), list) else []
    plans: list[VPSPlan] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        architecture = str(item.get("architecture") or "").strip().lower()
        vcpus = _to_int(item.get("cores"))
        memory_mb = int(_to_float(item.get("memory")) * 1024)
        disk_gb = _to_int(item.get("disk"))
        slug = str(item.get("name") or item.get("id") or "").strip()
        if architecture != "x86" or not slug or vcpus < 1 or memory_mb < 1024:
            continue
        price = _hetzner_monthly_price(item)
        if price <= 0:
            continue
        plans.append(
            VPSPlan(
                id=slug,
                slug=slug,
                label=f"{vcpus} CPU · {_memory_label(memory_mb)} · {disk_gb}GB SSD",
                vcpus=vcpus,
                memory_mb=memory_mb,
                disk_gb=disk_gb,
                price_monthly=price,
                price_label=f"€{price:g}/mo",
                regions=_hetzner_plan_regions(item),
            )
        )
    return _mark_recommended(plans)


def _hetzner_plan_regions(item: Mapping[str, Any]) -> tuple[str, ...]:
    """Location slugs this server type is actually available in. Hetzner's
    GET /v1/server_types response added a per-type `locations` array (2025-
    09-24 changelog: "per-location server types") — each entry is shaped
    like a GET /v1/locations object (id/name/description/country/city/...)
    plus `available`/`recommended`/`deprecation`. Empty means the field
    wasn't present on this response at all — treated the same as
    DigitalOcean's empty regions tuple: no restriction threaded through,
    not "available nowhere"."""
    raw_locations = item.get("locations") if isinstance(item.get("locations"), list) else []
    slugs: list[str] = []
    for entry in raw_locations:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("available") is False:
            continue
        slug = str(entry.get("name") or "").strip()
        if slug:
            slugs.append(slug)
    return tuple(slugs)


def _normalize_vultr_plans(payload: Mapping[str, Any]) -> list[VPSPlan]:
    items = payload.get("plans") if isinstance(payload.get("plans"), list) else []
    plans: list[VPSPlan] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        vcpus = _to_int(item.get("vcpu_count"))
        memory_mb = _to_int(item.get("ram"))
        disk_gb = _to_int(item.get("disk"))
        price = _to_float(item.get("monthly_cost"))
        slug = str(item.get("id") or "").strip()
        if not slug or vcpus < 1 or memory_mb < 1024 or price <= 0:
            continue
        raw_locations = item.get("locations") if isinstance(item.get("locations"), list) else []
        regions = tuple(str(r).strip() for r in raw_locations if str(r).strip())
        plans.append(
            VPSPlan(
                id=slug,
                slug=slug,
                label=f"{vcpus} CPU · {_memory_label(memory_mb)} · {disk_gb}GB SSD",
                vcpus=vcpus,
                memory_mb=memory_mb,
                disk_gb=disk_gb,
                price_monthly=price,
                price_label=f"${price:g}/mo",
                regions=regions,
            )
        )
    return _mark_recommended(plans)


def _mark_recommended(plans: list[VPSPlan]) -> list[VPSPlan]:
    sorted_plans = sorted(plans, key=lambda plan: (plan.price_monthly, plan.memory_mb, plan.vcpus))
    recommended_id = ""
    for plan in sorted_plans:
        if plan.vcpus >= 2 and plan.memory_mb >= 4096:
            recommended_id = plan.id
            break
    if not recommended_id:
        for plan in sorted_plans:
            if plan.memory_mb >= 2048:
                recommended_id = plan.id
                break
    if not recommended_id and sorted_plans:
        recommended_id = sorted_plans[0].id
    return [
        VPSPlan(
            id=plan.id,
            slug=plan.slug,
            label=plan.label,
            vcpus=plan.vcpus,
            memory_mb=plan.memory_mb,
            disk_gb=plan.disk_gb,
            price_monthly=plan.price_monthly,
            price_label=plan.price_label,
            recommended=plan.id == recommended_id,
            # regions has a dataclass default of () — omitting it here (as
            # this rebuild previously did) silently wiped out every plan's
            # region-availability list on its way out of every normalizer,
            # since they all funnel through this function last. That made
            # the whole plan->region threading feature (DigitalOcean's
            # existing "regions" field on /v2/sizes, and Hetzner's/Vultr's
            # new equivalents) dead on arrival.
            regions=plan.regions,
        )
        for plan in sorted_plans
    ]


def _hetzner_monthly_price(item: Mapping[str, Any]) -> float:
    prices = item.get("prices") if isinstance(item.get("prices"), list) else []
    for entry in prices:
        if not isinstance(entry, Mapping):
            continue
        monthly = entry.get("price_monthly") if isinstance(entry.get("price_monthly"), Mapping) else {}
        price = _to_float(monthly.get("gross") or monthly.get("net"))
        if price > 0:
            return price
    return 0.0


def _memory_label(memory_mb: int) -> str:
    if memory_mb % 1024 == 0:
        return f"{memory_mb // 1024}GB"
    return f"{memory_mb}MB"


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _normalize_provider(provider: str) -> str:
    provider_id = str(provider or "").strip().lower().replace("_", "-")
    aliases = {
        "digital-ocean": "digitalocean",
        "do": "digitalocean",
        "hcloud": "hetzner",
        "gcp": "google",
        "google-cloud": "google",
        "googlecloud": "google",
    }
    provider_id = aliases.get(provider_id, provider_id)
    if provider_id not in PROVIDER_CONFIGS:
        raise ValueError(f"Unsupported VPS provider: {provider_id or 'missing'}.")
    return provider_id


def _validate_region(
    config: ProviderConfig,
    region: Optional[str],
    *,
    live_region_ids: Optional[Iterable[str]] = None,
) -> str:
    resolved = str(region or "").strip() or config.default_region
    allowed = {item.id for item in config.regions}
    if live_region_ids:
        allowed |= {str(r).strip() for r in live_region_ids if str(r).strip()}
    if resolved not in allowed:
        raise ValueError(
            f"Unsupported {config.label} region '{resolved}'. Choose one of: {', '.join(sorted(allowed))}."
        )
    return resolved


def _normalize_status(status: str) -> str:
    token = str(status or "").strip().lower()
    if token in {"provisioning", "registering", "connected", "failed", "deleted"}:
        return token
    return "provisioning"


def _clean_identifier(value: str, *, field_name: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{field_name} is required.")
    if len(token) > 160 or any(char in token for char in "/\\\x00"):
        raise ValueError(f"{field_name} is invalid.")
    return token


def _server_name(provider_id: str) -> str:
    suffix = secrets.token_hex(4)
    return f"empyralis-agent-computer-{provider_id}-{suffix}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _shell_single_quote(value: str) -> str:
    return str(value).replace("'", "'\"'\"'")


def _digitalocean_public_ip(droplet: Mapping[str, Any]) -> Optional[str]:
    networks = droplet.get("networks") if isinstance(droplet.get("networks"), dict) else {}
    for entry in networks.get("v4") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "").strip() == "public":
            ip = str(entry.get("ip_address") or "").strip()
            if ip:
                return ip
    return None
