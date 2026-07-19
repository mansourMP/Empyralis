"""Dynamic Client Registration (RFC 7591) sweep of the remaining existing
OAuth->MCP connectors, plus the net-new connectors added in the same pass.

This is the 2026-07-19 follow-on to test_dcr_connectors.py (which covered the
first 7: Stripe, Linear, Notion, Asana, Canva, Airtable, ClickUp) and
test_higgsfield_connector.py (Higgsfield). Every provider asserted DCR-capable
here had its own /.well-known/oauth-authorization-server (or oauth-protected-
resource -> issuer) fetched live and its registration_endpoint confirmed
present; see the comment above each entry in connection_oauth_service.
OAUTH_PROVIDER_CONFIGS for the full per-provider discovery evidence trail.

Part 1 -- existing connectors that GAINED DCR in this sweep:
    dropbox, figma, todoist, calendly, jira, confluence, webflow, monday,
    gitlab, miro, intercom, square, typeform, vercel

Part 1 -- existing connectors confirmed CLASSIC-ONLY (no reachable
registration_endpoint in their authoritative discovery document), guarded here
so they don't silently drift into "configured by default":
    google_workspace, github, microsoft_365, slack, hubspot, salesforce, box,
    docusign  (plus zoom, already guarded in test_dcr_connectors.py)

Part 2 -- net-new connectors added in this pass, DCR-capable from the start:
    zapier, paypal, sentry, attio, cloudflare
"""

from __future__ import annotations

import pytest

from server_modules import connection_catalog_service
from server_modules import connection_oauth_service as service


# ---------------------------------------------------------------------------
# Part 1: existing connectors that GAINED DCR
# ---------------------------------------------------------------------------

# provider -> (env_client_var, env_secret_var) for the "static override still
# works" test. Only the primary (first-listed) env var names are needed.
_SWEPT_PROVIDERS = {
    "dropbox": ("DROPBOX_OAUTH_CLIENT_ID", "DROPBOX_OAUTH_CLIENT_SECRET"),
    "figma": ("FIGMA_CLIENT_ID", "FIGMA_CLIENT_SECRET"),
    "todoist": ("TODOIST_CLIENT_ID", "TODOIST_CLIENT_SECRET"),
    "calendly": ("CALENDLY_CLIENT_ID", "CALENDLY_CLIENT_SECRET"),
    "jira": ("ATLASSIAN_CLIENT_ID", "ATLASSIAN_CLIENT_SECRET"),
    "confluence": ("ATLASSIAN_CLIENT_ID", "ATLASSIAN_CLIENT_SECRET"),
    "webflow": ("WEBFLOW_CLIENT_ID", "WEBFLOW_CLIENT_SECRET"),
    "monday": ("MONDAY_CLIENT_ID", "MONDAY_CLIENT_SECRET"),
    "gitlab": ("GITLAB_CLIENT_ID", "GITLAB_CLIENT_SECRET"),
    "miro": ("MIRO_CLIENT_ID", "MIRO_CLIENT_SECRET"),
    "intercom": ("INTERCOM_CLIENT_ID", "INTERCOM_CLIENT_SECRET"),
    "square": ("SQUARE_APPLICATION_ID", "SQUARE_APPLICATION_SECRET"),
    "typeform": ("TYPEFORM_CLIENT_ID", "TYPEFORM_CLIENT_SECRET"),
    "vercel": ("VERCEL_CLIENT_ID", "VERCEL_CLIENT_SECRET"),
}

# Net-new connectors (Part 2) -- same DCR contract, added fresh.
_NET_NEW_PROVIDERS = {
    "zapier": ("ZAPIER_CLIENT_ID", "ZAPIER_CLIENT_SECRET"),
    "paypal": ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"),
    "sentry": ("SENTRY_CLIENT_ID", "SENTRY_CLIENT_SECRET"),
    "attio": ("ATTIO_CLIENT_ID", "ATTIO_CLIENT_SECRET"),
    "cloudflare": ("CLOUDFLARE_CLIENT_ID", "CLOUDFLARE_CLIENT_SECRET"),
}

_ALL_DCR_PROVIDERS = {**_SWEPT_PROVIDERS, **_NET_NEW_PROVIDERS}

# Exact live-discovered endpoints (2026-07-19). Pinned as a regression guard
# against silently drifting back to a stale/guessed URL. Matches the value in
# each OAUTH_PROVIDER_CONFIGS entry's evidence comment.
_EXPECTED_REGISTRATION_ENDPOINT = {
    "dropbox": "https://www.dropbox.com/oauth2/register",
    "figma": "https://api.figma.com/v1/oauth/mcp/register",
    "todoist": "https://todoist.com/oauth/register",
    "calendly": "https://calendly.com/oauth/register",
    "jira": "https://cf.mcp.atlassian.com/v1/register",
    "confluence": "https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3/dcr/register",
    "webflow": "https://mcp.webflow.com/oauth/register",
    "monday": "https://mcp.monday.com/register",
    "gitlab": "https://gitlab.com/oauth/register",
    "miro": "https://mcp.miro.com/register",
    "intercom": "https://mcp.intercom.com/register",
    "square": "https://mcp.squareup.com/register",
    "typeform": "https://api.typeform.com/oauth/register",
    "vercel": "https://vercel.com/api/login/oauth/register",
    "zapier": "https://mcp.zapier.com/api/v1/oauth/register",
    "paypal": "https://mcp.paypal.com/register",
    "sentry": "https://mcp.sentry.dev/oauth/register",
    "attio": "https://app.attio.com/oauth/register",
    "cloudflare": "https://mcp.cloudflare.com/register",
}

# Every env var name that could plausibly satisfy any DCR provider's
# client_id/client_secret lookup (including legacy aliases) or its opt-in
# flag -- cleared before every "no static client" test so a developer's local
# .env can't leak in and flip an assertion.
_ALL_RELATED_ENV_VARS = (
    "DROPBOX_OAUTH_CLIENT_ID", "DROPBOX_CLIENT_ID", "DROPBOX_APP_KEY",
    "DROPBOX_OAUTH_CLIENT_SECRET", "DROPBOX_CLIENT_SECRET", "DROPBOX_APP_SECRET",
    "FIGMA_CLIENT_ID", "FIGMA_CLIENT_SECRET",
    "TODOIST_CLIENT_ID", "TODOIST_CLIENT_SECRET",
    "CALENDLY_CLIENT_ID", "CALENDLY_CLIENT_SECRET",
    "ATLASSIAN_CLIENT_ID", "JIRA_CLIENT_ID", "CONFLUENCE_CLIENT_ID",
    "ATLASSIAN_CLIENT_SECRET", "JIRA_CLIENT_SECRET", "CONFLUENCE_CLIENT_SECRET",
    "WEBFLOW_CLIENT_ID", "WEBFLOW_CLIENT_SECRET",
    "MONDAY_CLIENT_ID", "MONDAY_CLIENT_SECRET",
    "GITLAB_CLIENT_ID", "GITLAB_CLIENT_SECRET",
    "MIRO_CLIENT_ID", "MIRO_CLIENT_SECRET",
    "INTERCOM_CLIENT_ID", "INTERCOM_CLIENT_SECRET",
    "SQUARE_APPLICATION_ID", "SQUARE_CLIENT_ID",
    "SQUARE_APPLICATION_SECRET", "SQUARE_CLIENT_SECRET",
    "TYPEFORM_CLIENT_ID", "TYPEFORM_CLIENT_SECRET",
    "VERCEL_CLIENT_ID", "VERCEL_CLIENT_SECRET",
    "ZAPIER_CLIENT_ID", "ZAPIER_CLIENT_SECRET",
    "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET",
    "SENTRY_CLIENT_ID", "SENTRY_CLIENT_SECRET",
    "ATTIO_CLIENT_ID", "ATTIO_CLIENT_SECRET",
    "CLOUDFLARE_CLIENT_ID", "CLOUDFLARE_CLIENT_SECRET",
)


def _clear_env(monkeypatch) -> None:
    for name in _ALL_RELATED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Also clear every {PROVIDER}_OAUTH_ENABLED / _MCP_ENABLED flag so an
    # opt-in flag can't accidentally satisfy the gate for a DCR provider that
    # is supposed to be configured WITHOUT one.
    for provider in _ALL_DCR_PROVIDERS:
        monkeypatch.delenv(f"{provider.upper()}_OAUTH_ENABLED", raising=False)
        monkeypatch.delenv(f"{provider.upper()}_MCP_ENABLED", raising=False)


# --- config shape ---------------------------------------------------------

@pytest.mark.parametrize("provider", sorted(_ALL_DCR_PROVIDERS))
def test_registration_endpoint_set_and_opt_in_not_required(provider) -> None:
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.registration_endpoint
    assert config.registration_endpoint.startswith("https://")
    # DCR-capable-by-default -- no opt-in flag required, unlike Higgsfield.
    assert config.dynamic_registration_opt_in_required is False


@pytest.mark.parametrize("provider", sorted(_ALL_DCR_PROVIDERS))
def test_registration_endpoint_matches_live_discovery(provider) -> None:
    """Pins each provider's registration_endpoint to the exact value fetched
    live from its own discovery document on 2026-07-19."""
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.registration_endpoint == _EXPECTED_REGISTRATION_ENDPOINT[provider]


@pytest.mark.parametrize("provider", sorted(_ALL_DCR_PROVIDERS))
def test_dcr_providers_use_pkce(provider) -> None:
    """Every provider swept in / added here advertised
    code_challenge_methods_supported=["S256"] in its live discovery, so all
    are PKCE. (PKCE is what actually secures the "none"-auth public clients
    like Calendly/Vercel, and is advertised alongside a secret for the rest.)"""
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.auth_method == "pkce"


def test_dcr_token_auth_method_matches_secret_body_flag() -> None:
    """A "none" DCR token_endpoint_auth_method means the provider issues no
    client secret, so include_client_secret_in_token_body must be False (and
    vice versa for the secret-based methods) -- otherwise the token exchange
    would send credentials the server rejects, or omit ones it requires.
    Guards the Calendly/Vercel ("none") vs. the rest ("client_secret_*")
    split specifically."""
    for provider in _ALL_DCR_PROVIDERS:
        config = service.OAUTH_PROVIDER_CONFIGS[provider]
        if config.dynamic_registration_token_auth_method == "none":
            assert config.include_client_secret_in_token_body is False, (
                f"{provider}: token_endpoint_auth_method 'none' but still sends client_secret"
            )
        else:
            assert config.dynamic_registration_token_auth_method in (
                "client_secret_post",
                "client_secret_basic",
            ), f"{provider}: unexpected DCR token_endpoint_auth_method"


# --- "configured by default" gate -----------------------------------------

@pytest.mark.parametrize("provider", sorted(_ALL_DCR_PROVIDERS))
def test_configured_by_default_with_no_env_and_no_flag(monkeypatch, provider) -> None:
    _clear_env(monkeypatch)
    assert service.oauth_provider_configured(provider) is True


@pytest.mark.parametrize("provider", sorted(_ALL_DCR_PROVIDERS))
def test_connection_configured_by_default_too(monkeypatch, provider) -> None:
    """oauth_connection_configured() (alias resolution -> oauth_provider_
    configured) is what connection_catalog_service actually calls to decide
    whether to show "not configured on this deployment"."""
    _clear_env(monkeypatch)
    assert service.oauth_connection_configured(provider) is True


@pytest.mark.parametrize("provider", sorted(_ALL_DCR_PROVIDERS))
def test_static_env_vars_still_configure_the_provider(monkeypatch, provider) -> None:
    """The static-env-var override keeps working: an operator CAN still pin a
    static client_id/secret."""
    _clear_env(monkeypatch)
    client_env, secret_env = _ALL_DCR_PROVIDERS[provider]
    monkeypatch.setenv(client_env, "static-client-id")
    monkeypatch.setenv(secret_env, "static-client-secret")
    assert service.oauth_provider_configured(provider) is True


# --- DCR self-registration behavior ---------------------------------------

@pytest.mark.parametrize("provider", sorted(_ALL_DCR_PROVIDERS))
def test_dynamic_registration_self_registers_with_no_flag_needed(monkeypatch, provider) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    calls: list[tuple[str, dict]] = []

    def fake_post_json(url, payload, *, headers=None):
        calls.append((url, payload))
        return {"client_id": f"dcr-{provider}-client-id", "client_secret": "dcr-client-secret"}

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    redirect_uri = f"https://app.example.com/api/connections/oauth/{provider}/callback"
    client_id, _client_secret = service._resolve_oauth_client(provider, redirect_uri)

    assert client_id == f"dcr-{provider}-client-id"
    assert len(calls) == 1
    registered_url, registered_payload = calls[0]
    expected_config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert registered_url == expected_config.registration_endpoint
    assert registered_payload["redirect_uris"] == [redirect_uri]
    assert registered_payload["token_endpoint_auth_method"] == expected_config.dynamic_registration_token_auth_method

    # Second call for the same (provider, redirect_uri) must hit the cache.
    client_id_2, _client_secret_2 = service._resolve_oauth_client(provider, redirect_uri)
    assert client_id_2 == client_id
    assert len(calls) == 1


@pytest.mark.parametrize("provider", sorted(_ALL_DCR_PROVIDERS))
def test_static_client_takes_priority_over_dynamic_registration(monkeypatch, provider) -> None:
    _clear_env(monkeypatch)
    client_env, secret_env = _ALL_DCR_PROVIDERS[provider]
    monkeypatch.setenv(client_env, "static-client-id")
    monkeypatch.setenv(secret_env, "static-client-secret")
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    def fail_post_json(*_args, **_kwargs):
        raise AssertionError(f"{provider}: must not self-register when a static client_id/secret is set")

    monkeypatch.setattr(service, "_post_json", fail_post_json)

    redirect_uri = f"https://app.example.com/api/connections/oauth/{provider}/callback"
    client_id, client_secret = service._resolve_oauth_client(provider, redirect_uri)
    assert (client_id, client_secret) == ("static-client-id", "static-client-secret")


# --- Dropbox's dedicated exchange function was PKCE-fixed in this pass -----

def test_exchange_dropbox_uses_dynamically_registered_client_and_forwards_pkce(monkeypatch) -> None:
    """Dropbox has a dedicated _exchange_dropbox (kept for its account_id
    capture). Switching Dropbox to auth_method="pkce" + DCR required the same
    fix Notion/Linear needed: it must resolve its client via
    _resolve_oauth_client (so the dynamically-registered client is used, not a
    409 from ensure_oauth_configured) AND accept + forward a code_verifier
    (else PKCE verification silently drops)."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    registration_endpoint = service.OAUTH_PROVIDER_CONFIGS["dropbox"].registration_endpoint

    captured: dict = {}

    def fake_post_json(url, payload, **_kw):
        # DCR registration call
        return {"client_id": "dcr-dropbox-id", "client_secret": "dcr-dropbox-secret"}

    def fake_post_form_json(url, payload, **_kw):
        captured["payload"] = payload
        return {"access_token": "dropbox-token", "account_id": "acct-123"}

    monkeypatch.setattr(service, "_post_json", fake_post_json)
    monkeypatch.setattr(service, "_post_form_json", fake_post_form_json)

    credentials = service._exchange_dropbox(
        "auth-code",
        "https://app.example.com/api/connections/oauth/dropbox/callback",
        code_verifier="dropbox-verifier",
    )

    assert credentials["access_token"] == "dropbox-token"
    assert credentials["account_id"] == "acct-123"
    assert captured["payload"]["code_verifier"] == "dropbox-verifier"
    assert captured["payload"]["client_id"] == "dcr-dropbox-id"


# ---------------------------------------------------------------------------
# Part 1: CLASSIC-ONLY connectors -- regression guard that the sweep did NOT
# sweep these up. Each was live-checked 2026-07-19 and its authoritative
# discovery document has no reachable registration_endpoint.
# ---------------------------------------------------------------------------

_CLASSIC_ONLY_PROVIDERS = (
    "google_workspace",
    "github",
    "microsoft_365",
    "slack",
    "hubspot",
    "salesforce",
    "box",
    "docusign",
)


@pytest.mark.parametrize("provider", _CLASSIC_ONLY_PROVIDERS)
def test_classic_only_providers_have_no_registration_endpoint(provider) -> None:
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.registration_endpoint is None


@pytest.mark.parametrize("provider", _CLASSIC_ONLY_PROVIDERS)
def test_classic_only_providers_not_configured_without_env(monkeypatch, provider) -> None:
    """No registration_endpoint => oauth_provider_configured must stay False
    on a clean env, even with an opt-in flag set (which has no meaning here)."""
    # Clear the widest plausible set of client/secret env vars for these.
    for name in (
        "GOOGLE_WORKSPACE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_CLIENT_ID",
        "GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET",
        "GITHUB_OAUTH_CLIENT_ID", "GITHUB_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET", "GITHUB_CLIENT_SECRET",
        "MICROSOFT_365_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_CLIENT_ID",
        "MICROSOFT_365_OAUTH_CLIENT_SECRET", "MICROSOFT_OAUTH_CLIENT_SECRET", "MICROSOFT_CLIENT_SECRET",
        "SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET",
        "HUBSPOT_CLIENT_ID", "HUBSPOT_CLIENT_SECRET",
        "SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET",
        "BOX_CLIENT_ID", "BOX_CLIENT_SECRET",
        "DOCUSIGN_CLIENT_ID", "DOCUSIGN_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(f"{provider.upper()}_OAUTH_ENABLED", "true")  # must have no effect

    assert service.oauth_provider_configured(provider) is False


# ---------------------------------------------------------------------------
# Part 2: net-new connectors -- present in the OAuth configs, the MCP server
# map, the connection alias table, the runtime CONNECTOR_CATALOG, and the
# connection catalog service's user-facing item list.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", sorted(_NET_NEW_PROVIDERS))
def test_net_new_provider_present_in_oauth_config_and_mcp_map(provider) -> None:
    assert provider in service.OAUTH_PROVIDER_CONFIGS
    assert provider in service.APP_MCP_SERVER_MAP
    entries = service.APP_MCP_SERVER_MAP[provider]
    assert len(entries) >= 1
    for entry in entries:
        assert str(entry.get("endpoint") or "").startswith("https://")
        assert str(entry.get("server_id") or "").strip()


@pytest.mark.parametrize("provider", sorted(_NET_NEW_PROVIDERS))
def test_net_new_provider_alias_resolves_to_itself(provider) -> None:
    assert service.provider_from_connection_id(provider) == provider


@pytest.mark.parametrize("provider", sorted(_NET_NEW_PROVIDERS))
def test_net_new_provider_in_runtime_connector_catalog(provider) -> None:
    from server_modules.runtime_config import CONNECTOR_CATALOG
    assert provider in CONNECTOR_CATALOG
    assert CONNECTOR_CATALOG[provider].get("auth")


@pytest.mark.parametrize("provider", sorted(_NET_NEW_PROVIDERS))
def test_net_new_provider_in_connection_catalog(provider) -> None:
    by_id = {item["id"]: item for item in connection_catalog_service.catalog_items()}
    assert provider in by_id
    item = by_id[provider]
    assert item["setup_kind"] == "oauth"
    assert item["setup_available"] is True
    assert item["runtime_provider"] == provider


def test_zapier_maps_to_the_official_single_mcp_endpoint() -> None:
    """Zapier is the one-URL-reaches-everything connector: a single hosted MCP
    server, authed with the customer's own Zapier account, exposing whatever
    apps they've already wired into Zapier."""
    entries = service.APP_MCP_SERVER_MAP["zapier"]
    assert len(entries) == 1
    assert entries[0]["endpoint"] == "https://mcp.zapier.com/api/v1/connect"
