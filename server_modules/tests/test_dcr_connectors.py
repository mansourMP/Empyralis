"""Dynamic Client Registration (RFC 7591) for 7 mainstream OAuth->MCP
connectors: Stripe, Linear, Notion, Asana, Canva, Airtable, and ClickUp.

These replicate the Higgsfield pattern from test_higgsfield_connector.py
(registration_endpoint + _resolve_oauth_client + _register_dynamic_client +
_DYNAMIC_CLIENT_CACHE) for 7 more providers, with one deliberate behavioral
difference: dynamic_registration_opt_in_required=False for all 7, so
oauth_provider_configured() reports True with NO env var and NO feature flag
set. Higgsfield still requires an explicit {PROVIDER}_OAUTH_ENABLED flag
before it reports configured (see test_higgsfield_connector.py) -- these 7
do not, since there is no developer console an operator could "finish
configuring" even if they wanted to: the platform self-registers a client
the moment one is needed, so gating that behind a flag would only ever
produce a false "not configured" reading.

Each provider's auth_url/token_url/scopes were reconciled against a live
fetch of its own /.well-known/oauth-authorization-server on 2026-07-19 --
see the comment above each entry in connection_oauth_service.OAUTH_PROVIDER_
CONFIGS for the full discovery evidence trail. Several previously pointed at
a DIFFERENT, classic per-provider OAuth app that has nothing to do with the
MCP server this connector actually calls (e.g. Stripe: this file's config
used to point at Stripe Connect's connect.stripe.com -- for authorizing a
*merchant's* account into a platform -- instead of the MCP app at
access.stripe.com/mcp, which authorizes our own agent to call Stripe's MCP
tools).

Zoom is the one provider explicitly left untouched: its own discovery
document (zoom.us/.well-known/oauth-authorization-server) advertises no
registration_endpoint at all, so it still requires a statically registered
app and ZOOM_CLIENT_ID/ZOOM_CLIENT_SECRET -- test_zoom_still_requires_a_
static_client_by_default below is a regression guard proving that stays
true.
"""

from __future__ import annotations

import pytest

from server_modules import connection_catalog_service
from server_modules import connection_oauth_service as service


_PROVIDERS = ("stripe", "linear", "notion", "asana", "canva", "airtable", "clickup")

_ENV_VAR_NAMES = {
    "stripe": ("STRIPE_CLIENT_ID", "STRIPE_CLIENT_SECRET"),
    "linear": ("LINEAR_OAUTH_CLIENT_ID", "LINEAR_OAUTH_CLIENT_SECRET"),
    "notion": ("NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET"),
    "asana": ("ASANA_CLIENT_ID", "ASANA_CLIENT_SECRET"),
    "canva": ("CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET"),
    "airtable": ("AIRTABLE_CLIENT_ID", "AIRTABLE_CLIENT_SECRET"),
    "clickup": ("CLICKUP_CLIENT_ID", "CLICKUP_CLIENT_SECRET"),
}

# Every env var name that could plausibly satisfy any of the 7 providers'
# client_id/client_secret lookup (including legacy aliases) or their
# opt-in flag -- cleared before every "no static client" test so a
# developer's local .env can't leak in and flip an assertion.
_ALL_RELATED_ENV_VARS = (
    "STRIPE_CLIENT_ID", "STRIPE_CLIENT_SECRET", "STRIPE_SECRET_KEY",
    "LINEAR_OAUTH_CLIENT_ID", "LINEAR_CLIENT_ID", "LINEAR_OAUTH_CLIENT_SECRET", "LINEAR_CLIENT_SECRET",
    "NOTION_OAUTH_CLIENT_ID", "NOTION_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET", "NOTION_CLIENT_SECRET",
    "ASANA_CLIENT_ID", "ASANA_CLIENT_SECRET",
    "CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET",
    "AIRTABLE_CLIENT_ID", "AIRTABLE_CLIENT_SECRET",
    "CLICKUP_CLIENT_ID", "CLICKUP_CLIENT_SECRET",
    "STRIPE_OAUTH_ENABLED", "STRIPE_MCP_ENABLED",
    "LINEAR_OAUTH_ENABLED", "LINEAR_MCP_ENABLED",
    "NOTION_OAUTH_ENABLED", "NOTION_MCP_ENABLED",
    "ASANA_OAUTH_ENABLED", "ASANA_MCP_ENABLED",
    "CANVA_OAUTH_ENABLED", "CANVA_MCP_ENABLED",
    "AIRTABLE_OAUTH_ENABLED", "AIRTABLE_MCP_ENABLED",
    "CLICKUP_OAUTH_ENABLED", "CLICKUP_MCP_ENABLED",
)


def _clear_env(monkeypatch) -> None:
    for name in _ALL_RELATED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# 1. OAuth provider config: registration_endpoint set, auth_url/token_url
#    reconciled to each provider's own live MCP OAuth server (not the
#    classic, unrelated per-provider app the old hardcoded config pointed
#    at) -- see per-provider comments in OAUTH_PROVIDER_CONFIGS.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", _PROVIDERS)
def test_registration_endpoint_is_set_and_opt_in_not_required(provider) -> None:
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.registration_endpoint
    assert config.registration_endpoint.startswith("https://")
    # DCR-capable-by-default -- no opt-in flag required, unlike Higgsfield.
    assert config.dynamic_registration_opt_in_required is False


@pytest.mark.parametrize(
    ("provider", "expected_auth_url", "expected_token_url", "expected_registration_endpoint"),
    [
        (
            "stripe",
            "https://access.stripe.com/mcp/oauth2/authorize",
            "https://access.stripe.com/mcp/oauth2/token",
            "https://access.stripe.com/mcp/oauth2/register",
        ),
        (
            "linear",
            "https://mcp.linear.app/authorize",
            "https://mcp.linear.app/token",
            "https://mcp.linear.app/register",
        ),
        (
            "notion",
            "https://mcp.notion.com/authorize",
            "https://mcp.notion.com/token",
            "https://mcp.notion.com/register",
        ),
        (
            "asana",
            "https://mcp.asana.com/authorize",
            "https://mcp.asana.com/token",
            "https://mcp.asana.com/register",
        ),
        (
            "canva",
            "https://mcp.canva.com/authorize",
            "https://mcp.canva.com/token",
            "https://mcp.canva.com/register",
        ),
        (
            "airtable",
            "https://airtable.com/oauth2/v1/authorize",
            "https://airtable.com/oauth2/v1/token",
            "https://airtable.com/oauth2/v1/register",
        ),
        (
            "clickup",
            "https://mcp.clickup.com/oauth/authorize",
            "https://mcp.clickup.com/oauth/token",
            "https://mcp.clickup.com/oauth/register",
        ),
    ],
)
def test_auth_and_token_urls_match_live_discovery(
    provider, expected_auth_url, expected_token_url, expected_registration_endpoint
) -> None:
    """Pins each provider's auth_url/token_url/registration_endpoint to the
    exact values fetched live from its own /.well-known/oauth-authorization-
    server on 2026-07-19 -- a regression guard against silently drifting
    back to a stale or guessed URL (Airtable's were already correct before
    this pass and are included here too, for completeness)."""
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.auth_url == expected_auth_url
    assert config.token_url == expected_token_url
    assert config.registration_endpoint == expected_registration_endpoint


def test_asana_scopes_corrected_to_the_mcp_resources_actual_scope_model() -> None:
    """Asana's classic app.asana.com OAuth used fine-grained scopes
    (tasks:read, tasks:write, ...); its MCP resource
    (mcp.asana.com/.well-known/oauth-protected-resource) declares
    scopes_supported=["default"] only -- the old fine-grained scopes don't
    exist in the new server's model at all."""
    config = service.OAUTH_PROVIDER_CONFIGS["asana"]
    assert config.scopes == ("default",)


def test_stripe_scopes_corrected_to_the_mcp_resources_actual_scope_model() -> None:
    """Stripe's classic Connect OAuth used scope "read_write"; the MCP
    server's own authorization-server discovery declares
    scopes_supported=["mcp"] only."""
    config = service.OAUTH_PROVIDER_CONFIGS["stripe"]
    assert config.scopes == ("mcp",)


def test_stripe_and_clickup_use_pkce_with_no_client_secret() -> None:
    """Both providers' token_endpoint_auth_methods_supported is ["none"]
    only -- no client secret exists, PKCE is the real security mechanism,
    so the client_secret must not be sent at token-exchange time and the
    DCR request must ask for "none" rather than the field default
    "client_secret_post"."""
    for provider in ("stripe", "clickup"):
        config = service.OAUTH_PROVIDER_CONFIGS[provider]
        assert config.auth_method == "pkce"
        assert config.include_client_secret_in_token_body is False
        assert config.dynamic_registration_token_auth_method == "none"


def test_notion_canva_airtable_request_client_secret_basic_at_registration() -> None:
    """None of these three advertise "client_secret_post" in
    token_endpoint_auth_methods_supported -- only "client_secret_basic" (and
    "none"). The DCR request must ask for the method the server actually
    supports, matching each config's existing token_auth="basic" shape
    (Notion: _exchange_notion hardcodes a Basic auth header; Canva/Airtable:
    token_auth field)."""
    for provider in ("notion", "canva", "airtable"):
        config = service.OAUTH_PROVIDER_CONFIGS[provider]
        assert config.dynamic_registration_token_auth_method == "client_secret_basic"


def test_linear_and_asana_keep_client_secret_post_at_registration() -> None:
    """Both advertise "client_secret_post" in
    token_endpoint_auth_methods_supported, matching the field default."""
    for provider in ("linear", "asana"):
        config = service.OAUTH_PROVIDER_CONFIGS[provider]
        assert config.dynamic_registration_token_auth_method == "client_secret_post"


# ---------------------------------------------------------------------------
# 2. "Not configured" gate -- true by default (no env var, no feature flag)
#    for all 7. This is the deliberate difference from Higgsfield: there is
#    no developer console an operator could "finish configuring" for any of
#    these, so gating them behind an opt-in flag would only ever produce a
#    false "not configured" reading.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", _PROVIDERS)
def test_configured_by_default_with_no_env_and_no_flag(monkeypatch, provider) -> None:
    _clear_env(monkeypatch)
    assert service.oauth_provider_configured(provider) is True


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_connection_configured_by_default_too(monkeypatch, provider) -> None:
    """oauth_connection_configured() is what connection_catalog_service
    actually calls (via _oauth_setup_unconfigured) to decide whether to show
    "not configured on this deployment" -- same alias resolution as every
    other provider, confirmed true by default here too."""
    _clear_env(monkeypatch)
    assert service.oauth_connection_configured(provider) is True


def test_zoom_still_requires_a_static_client_by_default(monkeypatch) -> None:
    """Regression guard: Zoom's own discovery document
    (zoom.us/.well-known/oauth-authorization-server) has no
    registration_endpoint at all -- confirmed live 2026-07-19 -- so it must
    stay static-only and NOT be swept up by the "configured by default"
    change made to the other 7."""
    for name in ("ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET", "ZOOM_OAUTH_ENABLED", "ZOOM_MCP_ENABLED"):
        monkeypatch.delenv(name, raising=False)

    config = service.OAUTH_PROVIDER_CONFIGS["zoom"]
    assert config.registration_endpoint is None
    assert service.oauth_provider_configured("zoom") is False


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_static_env_vars_still_configure_the_provider(monkeypatch, provider) -> None:
    """The static-env-var override keeps working exactly like every other
    provider: an operator CAN still pin a static client_id/secret."""
    _clear_env(monkeypatch)
    client_env, secret_env = _ENV_VAR_NAMES[provider]
    monkeypatch.setenv(client_env, "static-client-id")
    monkeypatch.setenv(secret_env, "static-client-secret")

    assert service.oauth_provider_configured(provider) is True


# ---------------------------------------------------------------------------
# 3. Dynamic Client Registration fallback (RFC 7591) -- self-registers, no
#    flag required, caches the result, and the static override still takes
#    priority over it when present.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", _PROVIDERS)
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

    # Second call for the same (provider, redirect_uri) must hit the cache,
    # not register a second OAuth client against the provider's server.
    client_id_2, _client_secret_2 = service._resolve_oauth_client(provider, redirect_uri)
    assert client_id_2 == client_id
    assert len(calls) == 1


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_static_client_takes_priority_over_dynamic_registration(monkeypatch, provider) -> None:
    _clear_env(monkeypatch)
    client_env, secret_env = _ENV_VAR_NAMES[provider]
    monkeypatch.setenv(client_env, "static-client-id")
    monkeypatch.setenv(secret_env, "static-client-secret")
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    def fail_post_json(*_args, **_kwargs):
        raise AssertionError(f"{provider}: must not self-register when a static client_id/secret is set")

    monkeypatch.setattr(service, "_post_json", fail_post_json)

    redirect_uri = f"https://app.example.com/api/connections/oauth/{provider}/callback"
    client_id, client_secret = service._resolve_oauth_client(provider, redirect_uri)

    assert (client_id, client_secret) == ("static-client-id", "static-client-secret")


# ---------------------------------------------------------------------------
# 4. Notion and Linear token exchange -- both have a dedicated
#    _exchange_notion/_exchange_linear function (Notion: token_parser=
#    "custom"; Linear: dispatched by provider name before the generic
#    token_parser=="standard" branch is ever checked) that previously called
#    ensure_oauth_configured() directly, bypassing _resolve_oauth_client()
#    entirely. That meant even with registration_endpoint set, the token
#    EXCHANGE step (unlike the authorize step, which already went through
#    the generic start_oauth() -> _resolve_oauth_client()) would 409 instead
#    of using the dynamically-registered client -- DCR would complete the
#    authorize redirect and then fail immediately after the user consents.
# ---------------------------------------------------------------------------

def test_exchange_notion_uses_the_dynamically_registered_client(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    registration_endpoint = service.OAUTH_PROVIDER_CONFIGS["notion"].registration_endpoint

    def fake_post_json(url, payload, **_kw):
        if url == registration_endpoint:
            return {"client_id": "dcr-notion-id", "client_secret": "dcr-notion-secret"}
        return {"access_token": "notion-token", "workspace_id": "ws-abc"}

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    credentials = service._exchange_notion(
        "auth-code",
        "https://app.example.com/api/connections/oauth/notion/callback",
    )

    assert credentials["access_token"] == "notion-token"


def test_exchange_linear_uses_the_dynamically_registered_client(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    monkeypatch.setattr(
        service,
        "_post_json",
        lambda url, payload, **_kw: {"client_id": "dcr-linear-id", "client_secret": "dcr-linear-secret"},
    )
    monkeypatch.setattr(
        service,
        "_post_form_json",
        lambda url, payload, **_kw: {"access_token": "linear-token"},
    )

    credentials = service._exchange_linear(
        "auth-code",
        "https://app.example.com/api/connections/oauth/linear/callback",
    )

    assert credentials["access_token"] == "linear-token"


def test_exchange_notion_and_linear_accept_pkce_code_verifier(monkeypatch) -> None:
    """Both configs now use auth_method="pkce" -- complete_oauth_callback
    computes a code_verifier and must be able to pass it through to these
    two custom exchange functions (they previously took no such parameter
    at all, which would have silently dropped PKCE verification)."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_ID", "static-id")
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_SECRET", "static-secret")
    monkeypatch.setenv("LINEAR_OAUTH_CLIENT_ID", "static-id")
    monkeypatch.setenv("LINEAR_OAUTH_CLIENT_SECRET", "static-secret")

    captured: dict = {}

    def fake_post_json(url, payload, **_kw):
        captured["notion_payload"] = payload
        return {"access_token": "tok"}

    def fake_post_form_json(url, payload, **_kw):
        captured["linear_payload"] = payload
        return {"access_token": "tok"}

    monkeypatch.setattr(service, "_post_json", fake_post_json)
    monkeypatch.setattr(service, "_post_form_json", fake_post_form_json)

    service._exchange_notion("code", "https://app.example.com/cb", code_verifier="notion-verifier")
    service._exchange_linear("code", "https://app.example.com/cb", code_verifier="linear-verifier")

    assert captured["notion_payload"]["code_verifier"] == "notion-verifier"
    assert captured["linear_payload"]["code_verifier"] == "linear-verifier"


# ---------------------------------------------------------------------------
# 5. Catalog resolution -- unaffected by the OAuth config changes (the
#    catalog's setup_available/runtime_usable are static descriptions, not
#    live "configured" checks), confirmed still true after this pass.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", _PROVIDERS)
def test_provider_still_resolves_in_the_catalog(provider) -> None:
    by_id = {item["id"]: item for item in connection_catalog_service.catalog_items()}
    assert provider in by_id
    item = by_id[provider]
    assert item["setup_kind"] == "oauth"
    assert item["setup_available"] is True
    assert item["runtime_provider"] == provider
