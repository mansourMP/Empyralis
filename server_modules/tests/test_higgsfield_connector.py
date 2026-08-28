"""Higgsfield connector wiring — official remote MCP server at
mcp.higgsfield.ai/mcp aggregating ~30 image/video generation models (Kling,
Sora, Veo, Seedream, Seedance, FLUX, and more) behind one OAuth connection.

Unlike every other provider in OAUTH_PROVIDER_CONFIGS, Higgsfield has no
developer console to pre-register a static client_id (confirmed 2026-07-18
via live discovery — see the "higgsfield" entries in connection_oauth_service
OAUTH_PROVIDER_CONFIGS / APP_MCP_SERVER_MAP for the evidence trail). These
tests prove: the catalog + OAuth config + MCP endpoint mapping resolve
correctly, the "not configured" gate behaves with env vars unset, the static
env-var override path works exactly like every other provider, and the
Dynamic Client Registration (RFC 7591) fallback self-registers once and
caches the result -- all without touching the 24 statically-configured
providers' existing behavior.
"""

from __future__ import annotations

from server_modules import connection_catalog_service
from server_modules import connection_oauth_service as service


def _clear_higgsfield_env(monkeypatch) -> None:
    for name in (
        "HIGGSFIELD_CLIENT_ID",
        "HIGGSFIELD_CLIENT_SECRET",
        "HIGGSFIELD_OAUTH_ENABLED",
        "HIGGSFIELD_MCP_ENABLED",
        "HIGGSFIELD_OAUTH_CLIENT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# 1. OAuth provider config resolves with the discovered auth shape
# ---------------------------------------------------------------------------

def test_higgsfield_oauth_config_matches_discovered_auth_shape() -> None:
    config = service.OAUTH_PROVIDER_CONFIGS["higgsfield"]

    assert config.auth_url == "https://mcp.higgsfield.ai/oauth2/authorize"
    assert config.token_url == "https://mcp.higgsfield.ai/oauth2/token"
    assert config.registration_endpoint == "https://mcp.higgsfield.ai/oauth2/register"
    # PKCE (S256), not a flat API key -- code_challenge_methods_supported
    # from mcp.higgsfield.ai/.well-known/oauth-authorization-server is ["S256"].
    assert config.auth_method == "pkce"
    # No custom exchange function needed: falls into the generic
    # _exchange_standard_oauth() branch in connect_app_via_oauth_to_mcp() /
    # complete_oauth_callback(), same as Airtable/Canva/Asana/Calendly.
    assert config.token_parser == "standard"
    assert "openid" in config.scopes
    assert "offline_access" in config.scopes
    assert config.env_vars["client_id"] == ("HIGGSFIELD_CLIENT_ID",)
    assert config.env_vars["client_secret"] == ("HIGGSFIELD_CLIENT_SECRET",)


_DCR_CAPABLE_PROVIDERS = {
    "higgsfield",
    "stripe",
    "linear",
    "notion",
    "asana",
    "canva",
    "airtable",
    "clickup",
    # 2026-07-19 connector sweep (see test_dcr_connectors.py and test_dcr_
    # connector_sweep.py for the per-provider discovery evidence trail):
    "dropbox",
    "figma",
    "todoist",
    "calendly",
    "jira",
    "confluence",
    "webflow",
    "monday",
    "gitlab",
    "miro",
    "intercom",
    "square",
    "typeform",
    "vercel",
    # Net-new connectors added in the same sweep, DCR-capable from the start:
    "zapier",
    "paypal",
    "sentry",
    "attio",
    "cloudflare",
    # 2026-07-19 Tier-1 connector directory wave (36 net-new connectors; see
    # test_dcr_connector_tier1_directory.py for the per-provider discovery
    # evidence trail). sentry/paypal/attio were directory rows too but
    # skipped as exact duplicates of the entries already listed above.
    "gusto",
    "deel",
    "remote_com",
    "ashby",
    "klaviyo",
    "customer_io",
    "netlify",
    "supabase",
    "planetscale",
    "neon",
    "railway",
    "heroku",
    "sourcegraph",
    "replit",
    "postman",
    "buildkite",
    "socket",
    "whimsical",
    "ramp",
    "brex",
    "mercury",
    "robinhood",
    "amplitude",
    "mixpanel",
    "posthog",
    "meta_ads",
    "semrush",
    "ahrefs",
    "close_crm",
    "apollo_io",
    "outreach",
    "salesloft",
    "clay",
    "fireflies",
    "fathom",
    "coda",
}


def test_only_dcr_capable_providers_declare_a_registration_endpoint() -> None:
    """The dynamic-client-registration extension must be inert for every
    provider that has a normal developer-console client_id/secret pair.
    _DCR_CAPABLE_PROVIDERS above lists the full, live-confirmed set (see
    connection_oauth_service.OAUTH_PROVIDER_CONFIGS for the discovery
    evidence on each) -- every other provider, including Zoom, Box, Docusign,
    HubSpot, and Salesforce (each confirmed to have no registration_endpoint
    in its own reachable discovery document), must stay None."""
    for provider, config in service.OAUTH_PROVIDER_CONFIGS.items():
        if provider in _DCR_CAPABLE_PROVIDERS:
            assert config.registration_endpoint is not None, f"{provider}: expected a registration_endpoint"
        else:
            assert config.registration_endpoint is None, (
                f"{provider}: registration_endpoint should stay None for "
                "statically-configured providers"
            )


def test_higgsfield_alias_resolves_to_itself() -> None:
    assert service.provider_from_connection_id("higgsfield") == "higgsfield"


# ---------------------------------------------------------------------------
# 2. Official MCP endpoint mapping
# ---------------------------------------------------------------------------

def test_higgsfield_maps_to_the_official_mcp_endpoint() -> None:
    entries = service.APP_MCP_SERVER_MAP["higgsfield"]

    assert len(entries) == 1
    assert entries[0]["endpoint"] == "https://mcp.higgsfield.ai/mcp"
    assert entries[0]["server_id"] == "higgsfield"


# ---------------------------------------------------------------------------
# 3. "Not configured" gate — behaves like every other OAuth provider by
#    default, but has two ways to become configured (static or dynamic).
# ---------------------------------------------------------------------------

def test_higgsfield_not_configured_with_no_env_and_no_flag(monkeypatch) -> None:
    _clear_higgsfield_env(monkeypatch)

    assert service.oauth_provider_configured("higgsfield") is False


def test_higgsfield_configured_with_static_client_credentials(monkeypatch) -> None:
    """The owner can still pin a static client_id/secret (e.g. obtained by
    running the RFC 7591 registration call themselves once) -- identical to
    every other provider's env-var contract."""
    _clear_higgsfield_env(monkeypatch)
    monkeypatch.setenv("HIGGSFIELD_CLIENT_ID", "static-client-id")
    monkeypatch.setenv("HIGGSFIELD_CLIENT_SECRET", "static-client-secret")

    assert service.oauth_provider_configured("higgsfield") is True


def test_higgsfield_configured_with_dynamic_registration_flag_alone(monkeypatch) -> None:
    """No static secret needed -- the owner opts in and the client
    self-registers on first real connect."""
    _clear_higgsfield_env(monkeypatch)
    monkeypatch.setenv("HIGGSFIELD_OAUTH_ENABLED", "true")

    assert service.oauth_provider_configured("higgsfield") is True


def test_other_providers_are_unaffected_by_the_dynamic_gate(monkeypatch) -> None:
    """Regression guard for the oauth_provider_configured() edit: a provider
    with no registration_endpoint must still return False on missing env
    vars, flag or no flag. Box (confirmed classic-only in the 2026-07-19
    connector sweep -- its authoritative discovery document has no
    registration_endpoint at all) stands in for "every provider that's still
    static-only" -- Dropbox and Notion can no longer be used here since both
    are DCR-capable now (see test_dcr_connectors.py / test_dcr_connector_
    sweep.py and test_notion_*_connector.py's own configured-by-default
    tests)."""
    monkeypatch.delenv("BOX_CLIENT_ID", raising=False)
    monkeypatch.delenv("BOX_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("BOX_OAUTH_ENABLED", "true")  # must have no effect

    assert service.oauth_provider_configured("box") is False


# ---------------------------------------------------------------------------
# 4. Dynamic Client Registration fallback (RFC 7591) — self-registers once,
#    caches the result, and reuses it for the same redirect_uri.
# ---------------------------------------------------------------------------

def test_dynamic_registration_self_registers_and_caches(monkeypatch) -> None:
    _clear_higgsfield_env(monkeypatch)
    monkeypatch.setenv("HIGGSFIELD_OAUTH_ENABLED", "true")
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    calls: list[tuple[str, dict]] = []

    def fake_post_json(url, payload, *, headers=None):
        calls.append((url, payload))
        return {"client_id": "dcr-client-id", "client_secret": "dcr-client-secret"}

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    redirect_uri = "https://app.example.com/api/connections/oauth/higgsfield/callback"
    client_id, client_secret = service._resolve_oauth_client("higgsfield", redirect_uri)

    assert (client_id, client_secret) == ("dcr-client-id", "dcr-client-secret")
    assert len(calls) == 1
    registered_url, registered_payload = calls[0]
    assert registered_url == "https://mcp.higgsfield.ai/oauth2/register"
    assert registered_payload["redirect_uris"] == [redirect_uri]
    assert registered_payload["token_endpoint_auth_method"] == "client_secret_post"

    # Second call for the same (provider, redirect_uri) must hit the cache,
    # not register a second OAuth client against Higgsfield's server.
    client_id_2, client_secret_2 = service._resolve_oauth_client("higgsfield", redirect_uri)
    assert (client_id_2, client_secret_2) == ("dcr-client-id", "dcr-client-secret")
    assert len(calls) == 1


def test_dynamic_registration_not_attempted_when_flag_is_off(monkeypatch) -> None:
    """Without HIGGSFIELD_OAUTH_ENABLED, _resolve_oauth_client must fail
    closed exactly like ensure_oauth_configured always has -- no surprise
    network call to a third party just because registration_endpoint exists."""
    _clear_higgsfield_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    def fail_post_json(*args, **kwargs):
        raise AssertionError("must not attempt dynamic registration when the flag is off")

    monkeypatch.setattr(service, "_post_json", fail_post_json)

    try:
        service._resolve_oauth_client("higgsfield", "https://app.example.com/callback")
    except Exception as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("expected ensure_oauth_configured's 409 to propagate")


# ---------------------------------------------------------------------------
# 5. Catalog resolution
# ---------------------------------------------------------------------------

def test_higgsfield_resolves_in_the_catalog() -> None:
    by_id = {item["id"]: item for item in connection_catalog_service.catalog_items()}

    assert "higgsfield" in by_id
    item = by_id["higgsfield"]
    assert item["lane"] == connection_catalog_service.LANE_WORK_APP_CONNECTOR
    assert item["launch_status"] == connection_catalog_service.LAUNCH_LIVE_WHEN_CONFIGURED
    assert item["setup_kind"] == "oauth"
    assert item["setup_available"] is True
    assert item["runtime_usable"] is True
    assert item["runtime_provider"] == "higgsfield"
    assert item["vault_provider"] == "higgsfield"
    assert "sage" in item["surface"]
    assert "studio" in item["surface"]
    assert "apps" in item["surface"]
    # Generates media (images/video) -- media_support.files must be true.
    assert item["media_support"]["files"] is True


def test_higgsfield_catalog_entry_carries_no_fake_support() -> None:
    """Same truth the whole catalog is held to in
    test_assistant_apps_catalog_truth.py: setup_available implies a real
    runtime_provider is documented."""
    by_id = {item["id"]: item for item in connection_catalog_service.catalog_items()}
    item = by_id["higgsfield"]

    if item["setup_available"]:
        assert (item.get("runtime_provider") or "").strip()
    if item["runtime_usable"]:
        assert (item.get("runtime_provider") or "").strip()
