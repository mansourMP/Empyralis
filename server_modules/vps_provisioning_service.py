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
    token = _provider_token(PROVIDER_CONFIGS[provider_id], credentials)
    token_id = f"vps_token_{secrets.token_hex(16)}"
    now = _utc_now_iso()
    stored_credentials = _credentials_with_expiry(dict(credentials or {}, access_token=token))
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
_LIVE_REGION_PROVIDERS = {"digitalocean", "hetzner"}


def fetch_provider_regions(
    provider: str,
    *,
    token_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Live region catalog for a connected account (DigitalOcean, Hetzner).
    DigitalOcean's OAuth grant now requests regions:read explicitly (see
    create_digitalocean_oauth_start); Hetzner's pasted API token already
    carries full project access. Falls back to the static curated list
    (same one provider_catalog() serves pre-connection) for providers we
    haven't wired a live call for (Vultr — see fetch_public_provider_regions,
    which doesn't need an account at all), or if the live call fails — never
    a hard error just for this.
    """
    provider_id = _normalize_provider(provider)
    if provider_id not in _LIVE_REGION_PROVIDERS:
        return _static_provider_regions(provider_id)
    try:
        credentials = load_vps_provider_credentials(
            token_id, provider=provider_id, workspace_id=workspace_id, user_id=user_id
        )
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
    on_unauthorized: Optional[Callable[[], Optional[str]]] = None,
) -> list[Dict[str, str]]:
    """Raw per-provider live region/location call, normalized to
    [{id, label}, ...]. Shared by fetch_provider_regions (token_id-based,
    full envelope + static fallback on any failure) and
    _fetch_live_region_ids (provision_vps's region-validation allow-list,
    which wants just the id set and already tolerates failure)."""
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
    return []


def _fetch_live_region_ids(provider_id: str, token: str) -> set[str]:
    """Best-effort live region id set used as an ADDITIONAL allow-list on
    top of the static PROVIDER_CONFIGS list during provisioning (see
    provision_vps) — so a region the picker just showed (fetched live
    moments earlier) is never rejected just because it's missing from the
    hardcoded fallback tuple. Never raises: any failure here just means
    validation falls back to the static list alone, same as before live
    region data existed."""
    try:
        return {item["id"] for item in _fetch_live_regions(provider_id, token)}
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
    token = _provider_token(config, credentials)
    live_region_ids: set[str] = set()
    if token_id and provider_id in _LIVE_REGION_PROVIDERS:
        live_region_ids = _fetch_live_region_ids(provider_id, token)
    resolved_region = _validate_region(config, region, live_region_ids=live_region_ids)
    resolved_size = str(size or "").strip() or config.default_size
    name = _server_name(provider_id)
    user_data = cloud_init_script(pairing_token)
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
        return {"v": 1, "vps": {}, "tokens": {}, "oauth_states": {}}
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
    aliases = {"digital-ocean": "digitalocean", "do": "digitalocean", "hcloud": "hetzner"}
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
