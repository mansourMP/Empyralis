from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from server_modules import gateway_state_repository, vault_store
from server_modules.runtime_config import EMPYRALIS_STATE_HOME

# AWS has no OAuth and no pastable API token — see PROVIDER_CONFIGS["aws"]
# and create_aws_connect_intent/confirm_aws_connection below for the
# CloudFormation cross-account IAM role pattern this uses instead. boto3 is
# an optional import (mirrors artifact_service.py / provider_profiles.py's
# BedrockAdapter) so the rest of this module — and every non-AWS provider —
# keeps working even in an environment that never installed it.
try:
    import boto3 as _boto3
except Exception:  # pragma: no cover - optional dependency at runtime
    _boto3 = None

try:
    from botocore.exceptions import BotoCoreError as _BotoCoreError
    from botocore.exceptions import ClientError as _ClientError
    from botocore.exceptions import NoCredentialsError as _NoCredentialsError
except Exception:  # pragma: no cover - optional dependency at runtime
    class _BotoCoreError(Exception):
        pass

    class _ClientError(Exception):
        pass

    class _NoCredentialsError(Exception):
        pass


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

# --- AWS cross-account IAM role (CloudFormation) — see create_aws_connect_
# intent/confirm_aws_connection. Operator-set, both read via os.getenv and
# both fail gracefully (VPSProvisioningError, not a crash) when unset,
# exactly like DIGITALOCEAN_CLIENT_ID/_SECRET above (see
# empyralis_aws_account_id / _aws_cfn_template_url).
EMPYRALIS_AWS_ACCOUNT_ID_ENV = "EMPYRALIS_AWS_ACCOUNT_ID"
EMPYRALIS_AWS_CFN_TEMPLATE_URL_ENV = "EMPYRALIS_AWS_CFN_TEMPLATE_URL"
# Fixed-role-name convention: every customer's CloudFormation stack creates
# a role with this EXACT name (see deploy/aws/empyralis-vps-role.yaml's
# `RoleName:` property, which must match byte-for-byte — see
# test_aws_cloudformation_template_role_name_matches_constant), so Empyralis
# can derive arn:aws:iam::{customerAccountId}:role/{this} from nothing but
# the 12-digit account id the customer types in — no ARN paste-back step.
AWS_CROSS_ACCOUNT_ROLE_NAME = "EmpyralisVPSProvisioner"
AWS_ROLE_SESSION_NAME = "empyralis-vps-provisioning"
# IAM/STS AssumeRole is not region-scoped; this only picks which STS/console
# endpoint to address (and where the CloudFormation stack's own metadata
# lives) — it has no bearing on which region the customer's EC2 instances
# actually run in.
AWS_STS_SIGNING_REGION = "us-east-1"
AWS_CFN_STACK_NAME = "empyralis-vps"
# How long a "connect AWS account" intent (ExternalId + derived role_arn)
# stays valid for confirm_aws_connection to redeem — long enough to walk
# through the CloudFormation console at a normal pace, short enough that an
# abandoned intent (closed tab, never ran the stack) doesn't sit around
# indefinitely in aws_pending state.
AWS_PENDING_CONNECTION_TTL_SECONDS = 60 * 60
# Public, no-AWS-credentials-needed aggregator of AWS's own published
# On-Demand pricing (see _fetch_aws_instance_pricing) — the AWS Pricing API
# itself only has endpoints in us-east-1/ap-south-1 and a notoriously
# hostile-to-parse response shape; this is the same well-known workaround
# other tooling in this space uses. Best-effort only: falls back to
# _AWS_STATIC_MONTHLY_PRICE_USD if unreachable, same resilience contract as
# every other live-data-with-static-fallback path in this file.
AWS_INSTANCES_VANTAGE_URL = "https://instances.vantage.sh/instances.json"

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

_LOGGER = logging.getLogger(__name__)

# How often (and how long) run_vps_provisioning_lifecycle's background wait
# loop polls get_vps_provision_status for the agent computer to finish
# booting/installing/connecting after its droplet/instance was created.
# VPS_CONNECT_TIMEOUT_SECONDS is a safety net above and beyond the gateway
# pairing intent's own TTL (DEFAULT_GATEWAY_PAIRING_TTL_SECONDS, 15 minutes —
# see gateway_pairing_service) which normally resolves the record to
# 'failed' first via _resolved_record_status; this only fires if that somehow
# doesn't happen (e.g. a custom/longer ttl_seconds is ever threaded through).
VPS_CONNECT_POLL_INTERVAL_SECONDS = 15
VPS_CONNECT_TIMEOUT_SECONDS = 20 * 60


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
    def __init__(self, message: str, *, workspace_id: str = ""):
        super().__init__(message)
        # Best-effort context for callers that need to redirect the user
        # somewhere on failure (e.g. the OAuth callback routes in
        # routes_gateway.py sending a blocked-popup fallback tab back to the
        # right workspace's Hardware page) — empty when unknown, callers
        # must treat that as "nowhere known to redirect to."
        self.workspace_id = str(workspace_id or "").strip()


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
    "aws": ProviderConfig(
        provider="aws",
        label="Amazon Web Services",
        auth_label="AWS cross-account IAM role (CloudFormation)",
        # Not a REST endpoint like the other three — EC2 RunInstances is a
        # signed SigV4 SDK call (see _provision_aws), which is exactly why
        # AWS needs boto3 instead of the shared _http_json. Kept as a
        # descriptive string, not a URL, purely so this field stays
        # non-empty/self-documenting like every other provider's.
        create_url="ec2:RunInstances",
        default_region="us-east-1",
        default_size="t3.small",
        # AWS AMI ids are per-region and go stale — there is no fixed slug
        # like DigitalOcean's "ubuntu-24-04-x64". Resolved live per-call via
        # ec2:DescribeImages against Canonical's official account (see
        # _resolve_aws_ami). Kept here only as a human-readable label.
        default_image="ubuntu-noble-24.04",
        # AWS credentials are never a single bearer token — see
        # _store_aws_credentials / _aws_client. role_arn is listed here only
        # so _provider_token still has a non-empty field to validate
        # presence of before any AWS branch runs (defense in depth, not the
        # real credential-loading path).
        token_keys=("role_arn",),
        regions=(
            ProviderRegion("us-east-1", "US East (N. Virginia)"),
            ProviderRegion("us-west-2", "US West (Oregon)"),
            ProviderRegion("eu-west-1", "Europe (Ireland)"),
            ProviderRegion("eu-central-1", "Europe (Frankfurt)"),
            ProviderRegion("ap-southeast-1", "Asia Pacific (Singapore)"),
            ProviderRegion("ap-south-1", "Asia Pacific (Mumbai)"),
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


def peek_oauth_state_workspace_id(state: str) -> str:
    """Best-effort, non-destructive lookup of the workspace_id an OAuth
    state token was minted for — used only to build the "come back to the
    Hardware page" redirect URL for the *error* branch of the DigitalOcean/
    Google callbacks, where the state record is never popped/consumed (the
    success path already returns workspace_id straight from its own popped
    record — see complete_digitalocean_oauth_callback / _google_oauth_callback
    below). Returns "" if the state is missing/unknown/expired; callers must
    treat that as "no workspace to redirect to", not an error.
    """
    clean_state = str(state or "").strip()
    if not clean_state:
        return ""
    with _STATE_LOCK:
        payload = _load_state()
        state_record = dict((payload.get("oauth_states") or {}).get(clean_state, {}) or {})
    return str(state_record.get("workspace_id") or "").strip()


# How long a completed OAuth callback's outcome stays available for a
# duplicate hit on the same (already-consumed) state token to replay — see
# _record_oauth_result / _cached_oauth_result. Only needs to outlive the
# handful of seconds a retry/prefetch/leftover-popup duplicate might lag
# behind the original request, not the OAuth state's own TTL.
_OAUTH_RESULT_CACHE_TTL_SECONDS = 600


def _record_oauth_result(
    state_token: str,
    provider: str,
    *,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    workspace_id: str = "",
) -> None:
    """Cache the outcome of a just-consumed OAuth state token (success or
    failure) so complete_digitalocean_oauth_callback / _google equivalent can
    answer a duplicate callback hit for the same token idempotently instead
    of raising "state is invalid or expired" once the one-time state record
    itself has already been popped. See the oauth_results bucket comment in
    _load_state.
    """
    clean_state = str(state_token or "").strip()
    if not clean_state:
        return
    now = int(time.time())
    with _STATE_LOCK:
        payload = _load_state()
        results = payload.setdefault("oauth_results", {})
        results[clean_state] = {
            "provider": provider,
            "ok": error is None,
            "result": dict(result or {}),
            "error": str(error or ""),
            "workspace_id": str(workspace_id or "").strip(),
            "expires_at": now + _OAUTH_RESULT_CACHE_TTL_SECONDS,
        }
        # Opportunistic pruning — there's no background sweep for
        # VPS_STATE_FILE, so without this the bucket would grow unbounded.
        for key in [k for k, v in results.items() if _to_int((v or {}).get("expires_at")) <= now]:
            results.pop(key, None)
        _write_state(payload)


def _cached_oauth_result(state_token: str, provider: str) -> Optional[Dict[str, Any]]:
    clean_state = str(state_token or "").strip()
    if not clean_state:
        return None
    with _STATE_LOCK:
        payload = _load_state()
        entry = dict((payload.get("oauth_results") or {}).get(clean_state) or {})
    if not entry or str(entry.get("provider") or "") != provider:
        return None
    if _to_int(entry.get("expires_at")) <= int(time.time()):
        return None
    return entry


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
        # Not a live state record — either it was never valid, or this is a
        # duplicate hit of a callback we already completed for this exact
        # state token (the OAuth `code` DO issued is single-use, so a second
        # exchange attempt would 400 at DO's end anyway). Replay the cached
        # outcome instead of erroring so a retry/prefetch/duplicate request
        # lands the user on the same result as the original, not a dead end.
        cached = _cached_oauth_result(clean_state, "digitalocean")
        if cached is not None:
            if cached.get("ok"):
                return dict(cached.get("result") or {})
            raise VPSProvisioningError(
                str(cached.get("error") or "DigitalOcean OAuth state is invalid or expired."),
                workspace_id=str(cached.get("workspace_id") or ""),
            )
        raise VPSProvisioningError("DigitalOcean OAuth state is invalid or expired.")
    state_workspace_id = str(state_record.get("workspace_id") or "default")
    try:
        token_payload = _exchange_digitalocean_oauth_code(clean_code)
        token_id = store_vps_provider_token(
            provider="digitalocean",
            workspace_id=state_workspace_id,
            tenant_id=str(state_record.get("tenant_id") or "default"),
            user_id=str(state_record.get("user_id") or "unknown-user"),
            credentials=token_payload,
            source="oauth",
        )
    except VPSProvisioningError as exc:
        # The state record (and the workspace_id in it) was already popped
        # above — reattach it here so the route layer can still send the
        # user back to the right workspace's Hardware page, and cache the
        # failure so a duplicate hit for this state token gets the same
        # answer instead of a generic "invalid or expired" (see
        # _record_oauth_result).
        _record_oauth_result(clean_state, "digitalocean", error=str(exc), workspace_id=state_workspace_id)
        raise VPSProvisioningError(str(exc), workspace_id=state_workspace_id) from exc
    result = {
        "provider": "digitalocean",
        "token_id": token_id,
        "workspace_id": state_workspace_id,
    }
    _record_oauth_result(clean_state, "digitalocean", result=result, workspace_id=state_workspace_id)
    return result


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
        # See the matching comment in complete_digitalocean_oauth_callback —
        # replay a cached outcome for a duplicate hit on an already-consumed
        # state token instead of erroring.
        cached = _cached_oauth_result(clean_state, "google")
        if cached is not None:
            if cached.get("ok"):
                return dict(cached.get("result") or {})
            raise VPSProvisioningError(
                str(cached.get("error") or "Google OAuth state is invalid or expired."),
                workspace_id=str(cached.get("workspace_id") or ""),
            )
        raise VPSProvisioningError("Google OAuth state is invalid or expired.")
    state_workspace_id = str(state_record.get("workspace_id") or "default")
    try:
        token_payload = _exchange_google_oauth_code(clean_code)
        # Deliberately NOT store_vps_provider_token: Google isn't
        # provisionable yet at this point (no project chosen, bootstrap not
        # run) — this is a short-lived SETUP session the rest of the
        # bootstrap flow (list_google_projects / create_google_project /
        # check_google_project_billing / finish_google_bootstrap) consumes
        # and then discards, never a long-lived provider connection the way
        # a DO token_id is.
        setup_id = _store_google_setup_session(
            workspace_id=state_workspace_id,
            tenant_id=str(state_record.get("tenant_id") or "default"),
            user_id=str(state_record.get("user_id") or "unknown-user"),
            credentials=token_payload,
        )
    except VPSProvisioningError as exc:
        # See the matching comment in complete_digitalocean_oauth_callback —
        # reattach the workspace_id the popped state record carried, and
        # cache the failure so a duplicate hit replays the same answer.
        _record_oauth_result(clean_state, "google", error=str(exc), workspace_id=state_workspace_id)
        raise VPSProvisioningError(str(exc), workspace_id=state_workspace_id) from exc
    result = {
        "provider": "google",
        "setup_id": setup_id,
        "workspace_id": state_workspace_id,
    }
    _record_oauth_result(clean_state, "google", result=result, workspace_id=state_workspace_id)
    return result


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
    if provider_id == "aws":
        raise VPSProvisioningError(
            "AWS connects via CloudFormation, not a pasted token — "
            "use create_aws_connect_intent / confirm_aws_connection instead."
        )
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
    if provider_id == "aws":
        # AWS has no bearer token to extract — _fetch_aws_plans assumes the
        # stored cross-account role itself (role_arn + external_id) via
        # _aws_client. See _store_aws_credentials for why this bypasses
        # _provider_token entirely instead of joining the branches below.
        plans = _fetch_aws_plans(credentials)
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
_LIVE_REGION_PROVIDERS = {"digitalocean", "hetzner", "google", "aws"}


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
        elif provider_id == "aws":
            regions = _fetch_aws_regions(credentials)
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
        live_region_ids = (
            _fetch_aws_region_ids(credentials) if provider_id == "aws" else _fetch_live_region_ids(provider_id, token)
        )
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
    if provider_id == "aws":
        return _provision_aws(credentials, resolved_region, resolved_size, name, user_data)
    raise VPSProvisioningError(f"Unsupported VPS provider: {provider_id}")


async def run_vps_provisioning_lifecycle(
    *,
    vps_id: str,
    provider: str,
    credentials: Mapping[str, Any],
    region: Optional[str],
    size: Optional[str],
    pairing_token: str,
    token_id: Optional[str],
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    pairing_id: Optional[str],
) -> None:
    """The full droplet lifecycle (create -> persist -> wait for the agent to
    connect), run off the HTTP request path as a background asyncio task —
    see routes_gateway.provision_hardware_vps, which schedules this via
    asyncio.create_task() immediately after writing a 'provisioning'
    placeholder record (record_vps_provision with an empty
    provider_resource_id) and returning 200 with vps_id + status
    'provisioning'. That placeholder is what makes GET
    /hardware/vps/{vps_id}/status pollable from the instant the POST
    response lands, well before provision_vps() below has even started —
    this function only ever *replaces* it with real data or flips it to
    'failed', it never has to create it.

    provision_vps (a blocking function: plain urllib/boto3 calls, no
    asyncio) is offloaded to a worker thread via asyncio.to_thread so it
    never blocks the event loop — see provision_hardware_vps's docstring
    reference for why that mattered: every previous call ran this
    synchronously inline in the request handler, so a slow provider call
    (AWS's create path alone makes 5 sequential API calls) could tie up the
    whole process and blow past Cloudflare's ~100s edge timeout, 502ing the
    request AFTER the droplet was already created provider-side — orphaning
    it with no vps_id ever reaching the client to poll or delete it by.

    Cleanup contract: the moment provision_vps() returns successfully, its
    provider_resource_id is persisted (record_vps_provision) before
    anything else in this function can fail — so every failure branch after
    that point has a real resource id on record for
    mark_vps_provision_failed to delete. A create-step failure
    (provision_vps itself raising) never reaches that point, so there is
    nothing to delete: every provider's create call in provision_vps is a
    single all-or-nothing operation, never a partial multi-resource create
    left dangling (AWS's prerequisite security-group/key-pair calls create
    shared, free, reusable resources — not the billed instance itself).
    """
    try:
        try:
            result = await asyncio.to_thread(
                provision_vps,
                provider,
                credentials,
                region,
                size,
                pairing_token,
                token_id=token_id,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced via the failed-status record, not a caller
            _LOGGER.warning(
                "vps provisioning create step failed vps_id=%s provider=%s error=%s",
                vps_id,
                provider,
                exc,
            )
            await asyncio.to_thread(
                mark_vps_provision_failed,
                vps_id,
                reason=str(exc) or "Provisioning failed.",
                attempt_cleanup=False,
            )
            return

        try:
            await asyncio.to_thread(
                record_vps_provision,
                vps_id=vps_id,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                user_id=user_id,
                provider=result.provider,
                provider_resource_id=result.provider_resource_id,
                public_ip=result.public_ip,
                region=result.region,
                size=result.size,
                status=result.status,
                pairing_token=pairing_token,
                credentials=credentials,
                pairing_id=pairing_id,
            )
        except Exception as exc:  # noqa: BLE001 - a real resource now exists; must not be left untracked
            _LOGGER.error(
                "vps provisioning created %s resource_id=%s for vps_id=%s but could not persist the "
                "record: %s -- attempting best-effort cleanup so the resource does not orphan",
                provider,
                result.provider_resource_id,
                vps_id,
                exc,
            )
            try:
                await asyncio.to_thread(
                    _destroy_vps_provider_resource,
                    vps_id,
                    {
                        "provider": result.provider,
                        "provider_resource_id": result.provider_resource_id,
                        "region": result.region,
                        "credentials_ciphertext": _encrypt_secret(dict(credentials or {})),
                    },
                )
            except Exception as cleanup_exc:  # noqa: BLE001 - last resort: log loudly for manual intervention
                _LOGGER.error(
                    "vps provisioning cleanup ALSO failed for orphaned resource_id=%s provider=%s "
                    "vps_id=%s: %s -- manual deletion in the provider console is required",
                    result.provider_resource_id,
                    provider,
                    vps_id,
                    cleanup_exc,
                )
            return

        deadline = time.monotonic() + VPS_CONNECT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(VPS_CONNECT_POLL_INTERVAL_SECONDS)
            try:
                status_record = await asyncio.to_thread(get_vps_provision_status, vps_id)
            except KeyError:
                # Record vanished — e.g. the user deleted it out from under
                # this task via DELETE /hardware/vps/{vps_id} while it was
                # still installing. Nothing left to watch or clean up.
                return
            except Exception as exc:  # noqa: BLE001 - transient state-file hiccup; keep polling
                _LOGGER.warning("vps provisioning status poll failed vps_id=%s error=%s", vps_id, exc)
                continue
            status = str(status_record.get("status") or "")
            if status == "connected":
                return
            if status == "deleted":
                return
            if status == "failed":
                await asyncio.to_thread(
                    mark_vps_provision_failed,
                    vps_id,
                    reason="The agent computer did not connect before the setup window expired.",
                )
                return

        await asyncio.to_thread(
            mark_vps_provision_failed,
            vps_id,
            reason=(
                f"Timed out after {VPS_CONNECT_TIMEOUT_SECONDS // 60} minutes waiting for the "
                "agent computer to connect."
            ),
        )
    except Exception:  # noqa: BLE001 - a background task has no caller to propagate to; never die silently
        _LOGGER.exception("vps provisioning background lifecycle crashed unexpectedly vps_id=%s", vps_id)


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


def _destroy_vps_provider_resource(clean_vps_id: str, record: Mapping[str, Any]) -> None:
    """Shared provider-delete dispatch: calls whichever provider's
    delete-droplet/instance API matches record['provider'], addressing
    record['provider_resource_id'] exactly the way provision_vps's own
    per-provider branches created it (see provision_vps). Used by both a
    user-initiated delete (delete_recorded_vps) and the background
    auto-cleanup a failed/timed-out provision runs (mark_vps_provision_failed)
    — one lifecycle, one delete path, so a droplet created either through
    the normal flow or left over from a failed background provision is
    always torn down the same way. Raises VPSProvisioningError (or a
    provider SDK error for AWS) on failure; callers decide whether that's
    fatal (delete_recorded_vps) or best-effort/logged (
    mark_vps_provision_failed — a background task has no caller to surface
    the error to).
    """
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
    elif provider_id == "aws":
        # No bearer token, and TerminateInstances is region-scoped (unlike
        # the other three providers' global DELETE endpoints) — the region
        # this box was actually created in is on the record itself (see
        # record_vps_provision), not something _delete_provider_resource's
        # token-based signature has anywhere to carry.
        _delete_aws_resource(credentials, str(record.get("region") or "").strip(), resource_id)
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


def delete_recorded_vps(vps_id: str) -> Dict[str, Any]:
    clean_vps_id = _clean_identifier(vps_id, field_name="vps_id")
    with _STATE_LOCK:
        state = _load_state()
        record = dict((state.get("vps") or {}).get(clean_vps_id) or {})
        if not record:
            raise KeyError(clean_vps_id)
    _destroy_vps_provider_resource(clean_vps_id, record)
    with _STATE_LOCK:
        state = _load_state()
        latest = dict((state.get("vps") or {}).get(clean_vps_id) or record)
        latest["status"] = "deleted"
        latest["updated_at"] = _utc_now_iso()
        state.setdefault("vps", {})[clean_vps_id] = latest
        _write_state(state)
    return _public_record(latest)


def mark_vps_provision_failed(vps_id: str, *, reason: str, attempt_cleanup: bool = True) -> Dict[str, Any]:
    """Terminal failure path for a background provision (see
    run_vps_provisioning_lifecycle): best-effort destroys the provider
    resource if one was ever recorded, then persists status 'failed'
    either way — this is the "cleanup-on-failure" fence the async
    provisioning contract depends on: from the moment a droplet/instance is
    created and its provider_resource_id lands on the record (via
    record_vps_provision), every failure path funnels through here, so a
    failed provision can never silently leave a running, billing box behind.

    Never raises: a background asyncio task has no request/caller to
    propagate an exception to, so a cleanup failure (e.g. the provider API
    is down) is logged and swallowed rather than left to crash the task
    unnoticed — the record still gets marked 'failed' either way, with a
    cleanup_error note, so a human has something to act on via the provider
    console and the existing DELETE /hardware/vps/{vps_id} route (which
    will simply try the same delete again).
    """
    clean_vps_id = _clean_identifier(vps_id, field_name="vps_id")
    with _STATE_LOCK:
        state = _load_state()
        record = dict((state.get("vps") or {}).get(clean_vps_id) or {})
        if not record:
            raise KeyError(clean_vps_id)
    resource_id = str(record.get("provider_resource_id") or "").strip()
    cleanup_error: Optional[str] = None
    if attempt_cleanup and resource_id and str(record.get("status") or "") not in {"deleted", "failed"}:
        try:
            _destroy_vps_provider_resource(clean_vps_id, record)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup, never fatal here
            cleanup_error = str(exc)[:500]
            _LOGGER.error(
                "vps provisioning failure cleanup could not delete resource_id=%s provider=%s vps_id=%s: %s",
                resource_id,
                record.get("provider"),
                clean_vps_id,
                exc,
            )
    with _STATE_LOCK:
        state = _load_state()
        latest = dict((state.get("vps") or {}).get(clean_vps_id) or record)
        latest["status"] = "failed"
        latest["error"] = str(reason or "Provisioning failed.")[:500]
        if cleanup_error:
            latest["cleanup_error"] = cleanup_error
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
        return {
            "v": 1,
            "vps": {},
            "tokens": {},
            "oauth_states": {},
            "oauth_results": {},
            "google_setup_sessions": {},
            "aws_pending": {},
        }
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
    if not isinstance(parsed.get("oauth_results"), dict):
        # Short-lived cache of *completed* OAuth callback outcomes, keyed by
        # the same one-time state token as oauth_states — see
        # _record_oauth_result / _cached_oauth_result below. oauth_states
        # entries are popped (single-use) the moment a callback starts
        # processing them; this bucket lets a duplicate hit for the same
        # state token (browser retry, prefetch, or — before the same-tab-only
        # OAuth fix — a leftover popup racing the same-tab fallback) be
        # answered idempotently instead of erroring on "state is invalid or
        # expired".
        parsed["oauth_results"] = {}
    if not isinstance(parsed.get("google_setup_sessions"), dict):
        parsed["google_setup_sessions"] = {}
    if not isinstance(parsed.get("aws_pending"), dict):
        # Pending "connect AWS account" intents (ExternalId + derived
        # role_arn) awaiting confirm_aws_connection — see
        # create_aws_connect_intent. Mirrors oauth_states' shape/lifecycle.
        parsed["aws_pending"] = {}
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
        "error": str(record.get("error") or "").strip() or None,
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


# --- AWS cross-account IAM role (CloudFormation) --------------------------
#
# AWS has no OAuth and nothing worth pasting as an "API token" (a long-lived
# AWS access key is exactly the kind of standing secret this whole file
# otherwise avoids storing for DO/Hetzner/Vultr's OAuth paths). Instead this
# mirrors the industry-standard cross-account pattern (Datadog, Vantage,
# etc.): the customer runs a small CloudFormation template — hosted by
# Empyralis, referenced by URL — that creates an IAM role in *their* account
# trusting *Empyralis's* account, gated on a per-customer ExternalId. From
# then on, every AWS call in this file assumes that role via STS for
# short-lived (1 hour) credentials; nothing long-lived is ever stored.
#
# Flow:
#   1. create_aws_connect_intent — customer types their 12-digit account id;
#      we generate a random ExternalId, derive the expected role ARN via the
#      fixed-role-name convention, and hand back a pre-filled CloudFormation
#      Quick-Create-Stack URL. Nothing is trusted yet — the role doesn't
#      exist in AWS until the customer runs the stack.
#   2. The customer opens that URL (frontend, new tab) and runs the stack.
#   3. confirm_aws_connection — we attempt sts:AssumeRole against the
#      derived ARN + ExternalId. Success proves the role exists with the
#      right trust policy; failure means the stack isn't done yet (or
#      failed), and the customer can just retry. On success we additionally
#      call sts:GetCallerIdentity on the assumed session and check its
#      Account matches what the customer typed — defense against a stale or
#      mistyped account id resolving somewhere unexpected.


def empyralis_aws_account_id() -> str:
    """Empyralis's own AWS account id — the only account the CloudFormation
    template's trust policy allows to assume the customer's role. Surfaced
    to the frontend so a customer can cross-check it against what the
    CloudFormation console shows before running the stack. Fails gracefully
    (VPSProvisioningError, not a crash) when unset, exactly like
    _digitalocean_client_id — this is operator setup, not end-user input."""
    account_id = (os.getenv(EMPYRALIS_AWS_ACCOUNT_ID_ENV) or "").strip()
    if not account_id:
        raise VPSProvisioningError(
            f"AWS is not configured on this backend ({EMPYRALIS_AWS_ACCOUNT_ID_ENV} is unset)."
        )
    return account_id


def _aws_cfn_template_url() -> str:
    template_url = (os.getenv(EMPYRALIS_AWS_CFN_TEMPLATE_URL_ENV) or "").strip()
    if not template_url:
        raise VPSProvisioningError(
            f"AWS is not configured on this backend ({EMPYRALIS_AWS_CFN_TEMPLATE_URL_ENV} is unset)."
        )
    return template_url


def _validate_aws_account_id(value: str) -> str:
    account_id = str(value or "").strip()
    if not account_id.isdigit() or len(account_id) != 12:
        raise ValueError("AWS account id must be exactly 12 digits.")
    return account_id


def aws_role_arn_for_account(account_id: str) -> str:
    """Fixed-role-name convention: derive the role ARN Empyralis will assume
    from nothing but the customer's account id, so the customer never pastes
    an ARN back — see AWS_CROSS_ACCOUNT_ROLE_NAME."""
    return f"arn:aws:iam::{_validate_aws_account_id(account_id)}:role/{AWS_CROSS_ACCOUNT_ROLE_NAME}"


def _aws_quick_create_url(*, external_id: str, template_url: str, empyralis_account_id: str) -> str:
    # CloudFormation's console is a hash-routed SPA — `region=` is an
    # ordinary query param, but everything past the `#` (including its own
    # nested `?`-delimited query string) is fragment state the console JS
    # reads client-side. param_ExternalId pre-fills that NoEcho parameter
    # field; param_EmpyralisAccountId pre-fills the trust-policy account id
    # too, so the operator only has to keep EMPYRALIS_AWS_ACCOUNT_ID current
    # in one place (this env var) rather than also hand-editing the hosted
    # template's Parameters.Default every time — the template's own default
    # is just a fallback for anyone who opens it directly.
    fragment_query = urlparse.urlencode(
        {
            "templateURL": template_url,
            "stackName": AWS_CFN_STACK_NAME,
            "param_ExternalId": external_id,
            "param_EmpyralisAccountId": empyralis_account_id,
        }
    )
    return (
        f"https://{AWS_STS_SIGNING_REGION}.console.aws.amazon.com/cloudformation/home"
        f"?region={AWS_STS_SIGNING_REGION}#/stacks/create/review?{fragment_query}"
    )


def create_aws_connect_intent(
    *,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    account_id: str,
) -> Dict[str, Any]:
    clean_account_id = _validate_aws_account_id(account_id)
    empyralis_account_id = empyralis_aws_account_id()
    template_url = _aws_cfn_template_url()
    external_id = str(uuid.uuid4())
    role_arn = aws_role_arn_for_account(clean_account_id)
    connection_id = f"vps_aws_pending_{secrets.token_hex(16)}"
    record = {
        "connection_id": connection_id,
        "account_id": clean_account_id,
        "external_id": external_id,
        "role_arn": role_arn,
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "user_id": str(user_id or "").strip() or "unknown-user",
        "created_at": _utc_now_iso(),
    }
    with _STATE_LOCK:
        payload = _load_state()
        payload.setdefault("aws_pending", {})[connection_id] = record
        _write_state(payload)
    return {
        "provider": "aws",
        "connection_id": connection_id,
        "account_id": clean_account_id,
        "external_id": external_id,
        "role_arn": role_arn,
        "role_name": AWS_CROSS_ACCOUNT_ROLE_NAME,
        "empyralis_account_id": empyralis_account_id,
        "quick_create_url": _aws_quick_create_url(
            external_id=external_id, template_url=template_url, empyralis_account_id=empyralis_account_id
        ),
    }


def confirm_aws_connection(
    *,
    connection_id: str,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
) -> Dict[str, Any]:
    clean_connection_id = _clean_identifier(connection_id, field_name="connection_id")
    with _STATE_LOCK:
        payload = _load_state()
        pending = dict((payload.get("aws_pending") or {}).get(clean_connection_id) or {})
    if not pending:
        raise KeyError(clean_connection_id)
    if str(pending.get("workspace_id") or "").strip() != str(workspace_id or "").strip():
        raise KeyError(clean_connection_id)
    if user_id and str(pending.get("user_id") or "").strip() != str(user_id or "").strip():
        raise KeyError(clean_connection_id)
    created_at = _parse_iso(str(pending.get("created_at") or ""))
    if (
        created_at is not None
        and (datetime.now(timezone.utc) - created_at).total_seconds() > AWS_PENDING_CONNECTION_TTL_SECONDS
    ):
        _pop_aws_pending(clean_connection_id)
        raise VPSProvisioningError("This AWS connection request expired. Start over and reconnect.")

    role_arn = str(pending.get("role_arn") or "").strip()
    external_id = str(pending.get("external_id") or "").strip()
    account_id = str(pending.get("account_id") or "").strip()
    temp_credentials = _assume_aws_role(role_arn, external_id)
    _verify_aws_caller_identity(temp_credentials, expected_account_id=account_id)

    token_id = _store_aws_credentials(
        workspace_id=str(pending.get("workspace_id") or workspace_id or "default"),
        tenant_id=str(pending.get("tenant_id") or tenant_id or "default"),
        user_id=str(pending.get("user_id") or user_id or "unknown-user"),
        credentials={"role_arn": role_arn, "external_id": external_id, "account_id": account_id},
    )
    _pop_aws_pending(clean_connection_id)
    return {"provider": "aws", "token_id": token_id, "account_id": account_id}


def _pop_aws_pending(connection_id: str) -> None:
    with _STATE_LOCK:
        payload = _load_state()
        payload.setdefault("aws_pending", {}).pop(connection_id, None)
        _write_state(payload)


def _store_aws_credentials(
    *,
    workspace_id: str,
    tenant_id: str,
    user_id: str,
    credentials: Mapping[str, Any],
) -> str:
    """AWS's own store path — deliberately NOT store_vps_provider_token,
    which normalizes a single bearer secret via _provider_token(). AWS has
    no bearer secret to normalize: the stored credential is a (role_arn,
    external_id, account_id) triple, useless on its own without a live
    sts:AssumeRole against the customer's account on every single call (see
    _aws_client) — there is no "the secret" to extract the way there is for
    the other three providers."""
    token_id = f"vps_token_{secrets.token_hex(16)}"
    now = _utc_now_iso()
    record = {
        "token_id": token_id,
        "provider": "aws",
        "workspace_id": str(workspace_id or "").strip() or "default",
        "tenant_id": str(tenant_id or "").strip() or "default",
        "user_id": str(user_id or "").strip() or "unknown-user",
        "source": "cloudformation",
        "credentials_ciphertext": _encrypt_secret(dict(credentials)),
        "created_at": now,
        "updated_at": now,
    }
    with _STATE_LOCK:
        state = _load_state()
        state.setdefault("tokens", {})[token_id] = record
        _write_state(state)
    return token_id


def _assume_aws_role(role_arn: str, external_id: str) -> Dict[str, str]:
    """The AssumeRole wrapper every AWS call in this file goes through (via
    _aws_client) — Empyralis's own ambient AWS credentials (boto3's normal
    resolution chain: environment, instance profile, shared config — never
    anything this file reads or stores itself) call sts:AssumeRole against
    the CUSTOMER's role, scoped by the ExternalId only that customer's stack
    was created with. Returns short-lived (1 hour) session credentials;
    nothing here is ever persisted."""
    if _boto3 is None:
        raise VPSProvisioningError("boto3 is not installed; AWS VPS support requires the boto3 package.")
    if not role_arn or not external_id:
        raise ValueError("AWS role_arn and external_id are required.")
    try:
        sts_client = _boto3.client("sts", region_name=AWS_STS_SIGNING_REGION)
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=AWS_ROLE_SESSION_NAME,
            ExternalId=external_id,
            DurationSeconds=3600,
        )
    except _NoCredentialsError as exc:
        raise VPSProvisioningError(
            "Empyralis's own AWS credentials are not configured on this backend — "
            "set them (environment or instance profile) before connecting customer AWS accounts."
        ) from exc
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(
            f"Could not assume the Empyralis VPS role in the customer's AWS account: {exc}"
        ) from exc
    creds = response.get("Credentials") if isinstance(response, Mapping) else None
    creds = creds if isinstance(creds, Mapping) else {}
    access_key_id = str(creds.get("AccessKeyId") or "").strip()
    secret_access_key = str(creds.get("SecretAccessKey") or "").strip()
    session_token = str(creds.get("SessionToken") or "").strip()
    if not access_key_id or not secret_access_key or not session_token:
        raise VPSProvisioningError("AWS did not return temporary credentials for the assumed role.")
    return {
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
        "aws_session_token": session_token,
    }


def _verify_aws_caller_identity(temp_credentials: Mapping[str, str], *, expected_account_id: str) -> None:
    """Defense against a stale/mistyped account id happening to still
    resolve an assumable role somewhere unexpected: after AssumeRole
    succeeds, independently confirm the assumed session's own account
    matches what the customer typed, via sts:GetCallerIdentity."""
    if _boto3 is None:
        raise VPSProvisioningError("boto3 is not installed; AWS VPS support requires the boto3 package.")
    try:
        sts_client = _boto3.client(
            "sts",
            region_name=AWS_STS_SIGNING_REGION,
            aws_access_key_id=temp_credentials["aws_access_key_id"],
            aws_secret_access_key=temp_credentials["aws_secret_access_key"],
            aws_session_token=temp_credentials["aws_session_token"],
        )
        identity = sts_client.get_caller_identity()
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"Could not verify the assumed AWS role's identity: {exc}") from exc
    actual_account = str((identity or {}).get("Account") or "").strip()
    if actual_account != str(expected_account_id or "").strip():
        raise VPSProvisioningError(
            "The assumed AWS role belongs to a different account than expected. Reconnect and try again."
        )


def _aws_client(service_name: str, credentials: Mapping[str, Any], *, region: str):
    """Every AWS service call in this file goes through here: resolve
    role_arn/external_id from the stored credential, assume the role fresh
    (see _assume_aws_role — short-lived, never cached across requests), and
    build a boto3 client scoped to `region` from the resulting session
    credentials."""
    if _boto3 is None:
        raise VPSProvisioningError("boto3 is not installed; AWS VPS support requires the boto3 package.")
    role_arn = str(credentials.get("role_arn") or "").strip()
    external_id = str(credentials.get("external_id") or "").strip()
    if not role_arn or not external_id:
        raise ValueError("AWS role_arn and external_id are required.")
    temp_credentials = _assume_aws_role(role_arn, external_id)
    try:
        return _boto3.client(
            service_name,
            region_name=region,
            aws_access_key_id=temp_credentials["aws_access_key_id"],
            aws_secret_access_key=temp_credentials["aws_secret_access_key"],
            aws_session_token=temp_credentials["aws_session_token"],
        )
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"Could not create an AWS {service_name} client: {exc}") from exc


def _parse_iso(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- AWS regions ------------------------------------------------------------

_AWS_REGION_LABELS: Dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "Europe (Ireland)",
    "eu-west-2": "Europe (London)",
    "eu-west-3": "Europe (Paris)",
    "eu-central-1": "Europe (Frankfurt)",
    "eu-north-1": "Europe (Stockholm)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "sa-east-1": "South America (São Paulo)",
    "ca-central-1": "Canada (Central)",
}


def _fetch_aws_regions(credentials: Mapping[str, Any]) -> list[Dict[str, str]]:
    ec2 = _aws_client("ec2", credentials, region=PROVIDER_CONFIGS["aws"].default_region)
    try:
        response = ec2.describe_regions(
            Filters=[{"Name": "opt-in-status", "Values": ["opt-in-not-required", "opted-in"]}]
        )
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws provisioning failed: could not list regions: {exc}") from exc
    items = response.get("Regions") if isinstance(response, Mapping) else None
    return _normalize_aws_regions(items or [])


def _normalize_aws_regions(items: Iterable[Mapping[str, Any]]) -> list[Dict[str, str]]:
    regions: list[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        region_name = str(item.get("RegionName") or "").strip()
        if not region_name:
            continue
        regions.append({"id": region_name, "label": _AWS_REGION_LABELS.get(region_name, region_name)})
    regions.sort(key=lambda entry: entry["id"])
    return regions


def _fetch_aws_region_ids(credentials: Mapping[str, Any]) -> set[str]:
    """Mirrors _fetch_live_region_ids's never-raises contract for the other
    live-region providers — provision_vps's live-region allow-list must
    never hard-fail provisioning just because this best-effort fetch did."""
    try:
        return {item["id"] for item in _fetch_aws_regions(credentials)}
    except (VPSProvisioningError, KeyError, ValueError, TypeError):
        return set()


# --- AWS plans (instance types + pricing) -----------------------------------

# Curated candidate list, mirroring the small hand-picked set every other
# provider effectively offers (DO/Hetzner/Vultr's own catalogs are much
# larger than what actually gets shown) — general-purpose burstable
# instances sized for an always-on agent-computer workload, not a
# from-scratch enumeration of EC2's hundreds of instance types.
_AWS_CANDIDATE_INSTANCE_TYPES: tuple[str, ...] = (
    "t3.micro",
    "t3.small",
    "t3.medium",
    "t3.large",
    "t3.xlarge",
    "t3.2xlarge",
)
# T3 instances are EBS-only — unlike DO/Hetzner/Vultr, AWS does not bundle a
# fixed local disk size per instance type; the root volume is provisioned
# separately (see _provision_aws's BlockDeviceMappings). This fixed size is
# both what gets displayed per plan AND what actually gets attached, so the
# two can never drift apart.
_AWS_DEFAULT_ROOT_VOLUME_GB = 40
# Static USD/month fallback (on-demand, us-east-1, Linux) for when the live
# instances.vantage.sh aggregator is unreachable — approximate published AWS
# list prices (hourly x ~730h/mo). Not a substitute for real-time billing
# data; see _fetch_aws_instance_pricing.
_AWS_STATIC_MONTHLY_PRICE_USD: Dict[str, float] = {
    "t3.micro": 7.59,
    "t3.small": 15.18,
    "t3.medium": 30.37,
    "t3.large": 60.74,
    "t3.xlarge": 121.47,
    "t3.2xlarge": 242.94,
}


def _fetch_aws_plans(credentials: Mapping[str, Any]) -> list[VPSPlan]:
    ec2 = _aws_client("ec2", credentials, region=PROVIDER_CONFIGS["aws"].default_region)
    try:
        response = ec2.describe_instance_types(InstanceTypes=list(_AWS_CANDIDATE_INSTANCE_TYPES))
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws provisioning failed: could not list instance types: {exc}") from exc
    items = response.get("InstanceTypes") if isinstance(response, Mapping) else None
    pricing = _fetch_aws_instance_pricing()
    return _normalize_aws_plans(items or [], pricing)


def _normalize_aws_plans(
    instance_types: Iterable[Mapping[str, Any]],
    pricing: Mapping[str, float],
) -> list[VPSPlan]:
    plans: list[VPSPlan] = []
    for item in instance_types:
        if not isinstance(item, Mapping):
            continue
        slug = str(item.get("InstanceType") or "").strip()
        if not slug:
            continue
        vcpu_info = item.get("VCpuInfo") if isinstance(item.get("VCpuInfo"), Mapping) else {}
        memory_info = item.get("MemoryInfo") if isinstance(item.get("MemoryInfo"), Mapping) else {}
        vcpus = _to_int(vcpu_info.get("DefaultVCpus"))
        memory_mb = _to_int(memory_info.get("SizeInMiB"))
        if vcpus < 1 or memory_mb < 1024:
            continue
        price = _to_float(pricing.get(slug))
        if price <= 0:
            continue
        plans.append(
            VPSPlan(
                id=slug,
                slug=slug,
                label=f"{vcpus} CPU · {_memory_label(memory_mb)} · {_AWS_DEFAULT_ROOT_VOLUME_GB}GB SSD",
                vcpus=vcpus,
                memory_mb=memory_mb,
                disk_gb=_AWS_DEFAULT_ROOT_VOLUME_GB,
                price_monthly=price,
                price_label=f"${price:g}/mo",
                # Specs were fetched against a single reference region
                # (see _fetch_aws_plans) rather than threaded through per-
                # region like DigitalOcean's — empty means "not threaded
                # through yet", the same convention every normalizer here
                # already uses for that state (see VPSPlan.regions).
            )
        )
    return _mark_recommended(plans)


def _fetch_aws_instance_pricing() -> Dict[str, float]:
    try:
        raw = _fetch_vantage_pricing_raw()
    except Exception:
        return dict(_AWS_STATIC_MONTHLY_PRICE_USD)
    pricing: Dict[str, float] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        slug = str(item.get("instance_type") or "").strip()
        if slug not in _AWS_CANDIDATE_INSTANCE_TYPES:
            continue
        hourly = _aws_vantage_hourly_price(item)
        if hourly > 0:
            pricing[slug] = round(hourly * 730, 2)
    return pricing or dict(_AWS_STATIC_MONTHLY_PRICE_USD)


def _aws_vantage_hourly_price(item: Mapping[str, Any]) -> float:
    pricing = item.get("pricing") if isinstance(item.get("pricing"), Mapping) else {}
    region_pricing = pricing.get("us-east-1") if isinstance(pricing.get("us-east-1"), Mapping) else {}
    linux_pricing = region_pricing.get("linux") if isinstance(region_pricing.get("linux"), Mapping) else {}
    return _to_float(linux_pricing.get("ondemand"))


def _fetch_vantage_pricing_raw() -> Any:
    """Isolated to its own function (rather than reusing _http_json, which
    asserts a top-level JSON *object*) purely because instances.vantage.sh
    returns a top-level JSON *array* — and so this is easy to monkeypatch in
    tests the same way _http_json already is elsewhere in this file."""
    request = urlrequest.Request(
        AWS_INSTANCES_VANTAGE_URL,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "Empyralis-VPS-Provisioner/1.0"},
    )
    with urlrequest.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else []


# --- AWS provisioning / deletion --------------------------------------------

_AWS_UBUNTU_OWNER_ID = "099720109477"  # Canonical's official AWS account.
_AWS_UBUNTU_NAME_FILTER = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
_AWS_SECURITY_GROUP_NAME = "empyralis-agent-computer"
_AWS_KEY_PAIR_NAME = "empyralis-agent-computer"


def _resolve_aws_ami(ec2: Any) -> tuple[str, str]:
    """Returns (image_id, root_device_name). AWS AMI ids are per-region and
    go stale — there is no fixed slug like DigitalOcean's
    "ubuntu-24-04-x64" — so this resolves the current Ubuntu 24.04 (Noble)
    AMI live via ec2:DescribeImages against Canonical's own account, picking
    the most recently published match. The root device name is read back
    from the AMI itself (not assumed to be /dev/sda1) so
    BlockDeviceMappings' volume-size override in _provision_aws actually
    lands on the AMI's real root volume instead of silently no-op'ing."""
    try:
        response = ec2.describe_images(
            Owners=[_AWS_UBUNTU_OWNER_ID],
            Filters=[
                {"Name": "name", "Values": [_AWS_UBUNTU_NAME_FILTER]},
                {"Name": "state", "Values": ["available"]},
                {"Name": "architecture", "Values": ["x86_64"]},
                {"Name": "virtualization-type", "Values": ["hvm"]},
            ],
        )
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws provisioning failed: could not resolve the Ubuntu AMI: {exc}") from exc
    images = response.get("Images") if isinstance(response, Mapping) else []
    candidates = [img for img in images if isinstance(img, Mapping) and str(img.get("ImageId") or "").strip()]
    if not candidates:
        raise VPSProvisioningError("aws provisioning failed: no Ubuntu 24.04 AMI found in this region.")
    newest = max(candidates, key=lambda img: str(img.get("CreationDate") or ""))
    image_id = str(newest["ImageId"]).strip()
    root_device_name = str(newest.get("RootDeviceName") or "/dev/sda1").strip() or "/dev/sda1"
    return image_id, root_device_name


def _resolve_aws_network(ec2: Any) -> tuple[str, str]:
    """Returns (vpc_id, subnet_id) — a fresh AWS account isn't guaranteed to
    still have its default VPC (it can be deleted), so this prefers the
    default VPC/subnet but falls back to the first available one of each
    rather than assuming either exists."""
    vpc_id = _first_resource_id(
        ec2, "describe_vpcs", "Vpcs", "VpcId", filters=[{"Name": "is-default", "Values": ["true"]}]
    ) or _first_resource_id(ec2, "describe_vpcs", "Vpcs", "VpcId")
    if not vpc_id:
        raise VPSProvisioningError(
            "aws provisioning failed: no VPC is available in this account/region. Create a VPC first."
        )
    subnet_id = _first_resource_id(
        ec2,
        "describe_subnets",
        "Subnets",
        "SubnetId",
        filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "default-for-az", "Values": ["true"]}],
    ) or _first_resource_id(ec2, "describe_subnets", "Subnets", "SubnetId", filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    if not subnet_id:
        raise VPSProvisioningError(f"aws provisioning failed: no subnet is available in VPC {vpc_id}.")
    return vpc_id, subnet_id


def _first_resource_id(
    ec2: Any,
    method_name: str,
    items_key: str,
    id_key: str,
    *,
    filters: Optional[list[Dict[str, Any]]] = None,
) -> str:
    try:
        method = getattr(ec2, method_name)
        response = method(Filters=filters) if filters else method()
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws provisioning failed: {method_name} failed: {exc}") from exc
    items = response.get(items_key) if isinstance(response, Mapping) else []
    for item in items or []:
        if isinstance(item, Mapping):
            value = str(item.get(id_key) or "").strip()
            if value:
                return value
    return ""


def _ensure_aws_security_group(ec2: Any, vpc_id: str) -> str:
    try:
        response = ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [_AWS_SECURITY_GROUP_NAME]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws provisioning failed: could not list security groups: {exc}") from exc
    groups = response.get("SecurityGroups") if isinstance(response, Mapping) else []
    for group in groups or []:
        if isinstance(group, Mapping):
            group_id = str(group.get("GroupId") or "").strip()
            if group_id:
                return group_id
    try:
        created = ec2.create_security_group(
            GroupName=_AWS_SECURITY_GROUP_NAME,
            Description=(
                "Empyralis Agent Computer - outbound install/pairing traffic; "
                "inbound SSH open for operator debugging only, no key pair is retained."
            ),
            VpcId=vpc_id,
            TagSpecifications=[{"ResourceType": "security-group", "Tags": [{"Key": "app", "Value": "empyralis"}]}],
        )
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws provisioning failed: could not create a security group: {exc}") from exc
    group_id = str(created.get("GroupId") or "").strip() if isinstance(created, Mapping) else ""
    if not group_id:
        raise VPSProvisioningError("aws provisioning failed: security group creation did not return a group id.")
    try:
        ec2.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "SSH (operator debugging)"}],
                }
            ],
        )
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws provisioning failed: could not authorize security group ingress: {exc}") from exc
    return group_id


def _ensure_aws_key_pair(ec2: Any) -> Optional[str]:
    """Ensures an EC2 key pair exists so the instance has one attached (some
    consoles/tooling expect it) — the private key material CreateKeyPair
    returns is intentionally discarded: never stored, never logged.
    Empyralis never authenticates over SSH to agent-computer boxes; pairing
    happens entirely over the cloud-init -> HTTPS callback (see
    cloud_init_script). Best-effort: returns None (no KeyName attached, and
    RunInstances works fine without one) rather than failing provisioning
    outright if key-pair management errors out."""
    try:
        ec2.describe_key_pairs(KeyNames=[_AWS_KEY_PAIR_NAME])
        return _AWS_KEY_PAIR_NAME
    except (_ClientError, _BotoCoreError):
        pass
    try:
        ec2.create_key_pair(KeyName=_AWS_KEY_PAIR_NAME, KeyType="ed25519", KeyFormat="pem")
        return _AWS_KEY_PAIR_NAME
    except (_ClientError, _BotoCoreError):
        return None


def _provision_aws(
    credentials: Mapping[str, Any],
    region: str,
    size: str,
    name: str,
    user_data: str,
) -> VPSResult:
    ec2 = _aws_client("ec2", credentials, region=region)
    image_id, root_device_name = _resolve_aws_ami(ec2)
    vpc_id, subnet_id = _resolve_aws_network(ec2)
    security_group_id = _ensure_aws_security_group(ec2, vpc_id)
    key_name = _ensure_aws_key_pair(ec2)
    run_kwargs: Dict[str, Any] = {
        "ImageId": image_id,
        "InstanceType": size,
        "MinCount": 1,
        "MaxCount": 1,
        # Plain text — botocore base64-encodes `blob`-typed params (like
        # RunInstances' UserData) itself; unlike Vultr's raw-HTTP path below,
        # this must NOT be pre-encoded or cloud-init receives double-encoded
        # garbage.
        "UserData": user_data,
        "NetworkInterfaces": [
            {
                "DeviceIndex": 0,
                "SubnetId": subnet_id,
                "AssociatePublicIpAddress": True,
                "Groups": [security_group_id],
            }
        ],
        "BlockDeviceMappings": [
            {
                "DeviceName": root_device_name,
                "Ebs": {"VolumeSize": _AWS_DEFAULT_ROOT_VOLUME_GB, "VolumeType": "gp3", "DeleteOnTermination": True},
            }
        ],
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": name},
                    {"Key": "app", "Value": "empyralis"},
                    {"Key": "role", "Value": "agent-computer"},
                ],
            }
        ],
    }
    if key_name:
        run_kwargs["KeyName"] = key_name
    try:
        response = ec2.run_instances(**run_kwargs)
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws provisioning failed: {exc}") from exc
    instances = response.get("Instances") if isinstance(response, Mapping) else []
    instance = instances[0] if instances and isinstance(instances[0], Mapping) else {}
    resource_id = str(instance.get("InstanceId") or "").strip()
    if not resource_id:
        raise VPSProvisioningError("aws did not return a created instance id.")
    return VPSResult(
        provider_resource_id=resource_id,
        public_ip=str(instance.get("PublicIpAddress") or "").strip() or None,
        region=region,
        size=size,
        status="provisioning",
        provider="aws",
    )


def _delete_aws_resource(credentials: Mapping[str, Any], region: str, resource_id: str) -> None:
    resolved_region = str(region or "").strip() or PROVIDER_CONFIGS["aws"].default_region
    ec2 = _aws_client("ec2", credentials, region=resolved_region)
    try:
        ec2.terminate_instances(InstanceIds=[resource_id])
    except (_ClientError, _BotoCoreError) as exc:
        raise VPSProvisioningError(f"aws cleanup failed: {exc}") from exc


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
        "amazon": "aws",
        "amazon-web-services": "aws",
        "ec2": "aws",
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
