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

import time
import unittest
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from server_modules import connection_catalog_service
from server_modules import connection_oauth_service as service
from server_modules import connector_validators
from server_modules import connectors_actions
from server_modules import mcp_registry_service
from server_modules import runtime_models
from server_modules.schemas import ConnectorCreate


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


# ---------------------------------------------------------------------------
# 6. Gap 1 -- post-auth validation must prove the token against the
#    connector's MCP resource (a live initialize + tools/list handshake via
#    mcp_registry_service), NOT the classic REST API a DCR/MCP-scoped token
#    was never issued for. Before this fix, create_connector_vault("stripe")
#    ran validate_stripe_connector's classic api.stripe.com/v1/account probe
#    with an MCP-scoped token and 400'd immediately after the user connected
#    -- the connection looked broken the moment it was made. validate_stripe_
#    connector / validate_linear_connector / validate_notion_connector /
#    validate_asana_connector / validate_canva_connector / validate_clickup_
#    connector / validate_higgsfield_connector (connector_validators.py) now
#    all route through validate_mcp_scoped_oauth_connector. Airtable is the
#    deliberate exception (its MCP resource shares its classic auth server)
#    and keeps validate_oauth_bearer_connector's classic REST profile_probe.
# ---------------------------------------------------------------------------

_MCP_SCOPED_PROVIDERS = ("stripe", "linear", "notion", "asana", "canva", "clickup", "higgsfield")


@pytest.mark.parametrize("provider", _MCP_SCOPED_PROVIDERS)
def test_mcp_scoped_connectors_validate_via_live_mcp_call_not_classic_api(monkeypatch, provider) -> None:
    calls: list[dict] = []

    def fake_discover(*, transport, endpoint, server_id, credential):
        calls.append(
            {"transport": transport, "endpoint": endpoint, "server_id": server_id, "credential": credential}
        )
        return [{"name": "tool_one"}, {"name": "tool_two"}]

    monkeypatch.setattr(mcp_registry_service, "discover_mcp_server_tools", fake_discover)

    def fail_classic_http(*_args, **_kwargs):
        raise AssertionError(f"{provider}: must not probe a classic REST endpoint for an MCP-scoped token")

    validator = getattr(connector_validators, f"validate_{provider}_connector")
    result = validator({"access_token": "mcp-scoped-token"}, fail_classic_http)

    assert result["ok"] is True
    assert result["mcp_tool_count"] == 2
    assert len(calls) == 1
    assert calls[0]["transport"] == "streamable_http"
    assert calls[0]["server_id"] == provider
    assert calls[0]["credential"] == {"access_token": "mcp-scoped-token"}
    expected_endpoint = service.APP_MCP_SERVER_MAP[provider][0]["endpoint"]
    assert calls[0]["endpoint"] == expected_endpoint


@pytest.mark.parametrize("provider", _MCP_SCOPED_PROVIDERS)
def test_mcp_scoped_connector_validation_fails_when_mcp_rejects_token(monkeypatch, provider) -> None:
    def fake_discover(**_kwargs):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(mcp_registry_service, "discover_mcp_server_tools", fake_discover)

    validator = getattr(connector_validators, f"validate_{provider}_connector")
    with pytest.raises(RuntimeError):
        validator({"access_token": "bad-token"}, None)


@pytest.mark.parametrize("provider", _MCP_SCOPED_PROVIDERS)
def test_mcp_scoped_connector_validation_requires_access_token(provider) -> None:
    validator = getattr(connector_validators, f"validate_{provider}_connector")
    with pytest.raises(RuntimeError):
        validator({}, None)


def test_airtable_keeps_the_classic_profile_probe() -> None:
    """Regression guard: Airtable's MCP resource shares its classic auth
    server (see OAUTH_PROVIDER_CONFIGS['airtable']'s discovery notes), so it
    must keep validate_oauth_bearer_connector's classic REST probe -- not
    the MCP-scoped path the other 6 + Higgsfield now use."""
    import inspect

    source = inspect.getsource(connector_validators.validate_airtable_connector)
    assert "validate_oauth_bearer_connector" in source
    assert "validate_mcp_scoped_oauth_connector" not in source


# ---------------------------------------------------------------------------
# 7. Gap 1 + Gap 2, through the real create_connector_vault entry point (the
#    function that runs right after OAuth succeeds). Before this fix:
#    create_connector_vault("stripe", ...) hit classic api.stripe.com with
#    an MCP-scoped token and raised HTTPException(400); create_connector_
#    vault had NO "higgsfield" branch at all, so a fresh Higgsfield
#    connection 400'd with "Unsupported connector 'higgsfield'" regardless
#    of token validity.
# ---------------------------------------------------------------------------

class CreateConnectorVaultMcpValidationTests(unittest.IsolatedAsyncioTestCase):
    async def _create(self, connector: str, *, label: str):
        # _find_duplicate_connector_entry (which reaches Postgres via
        # server.load_vault) and upsert_credential (the single-row Postgres
        # write) are the two control-plane-DB touches create_connector_vault
        # makes around the validation branch -- both stubbed so the test
        # exercises the Gap 1/Gap 2 validation path without a live DB (the
        # same Postgres dependency that env-skips the existing create_
        # connector_vault tests in this harness). _find_duplicate is stubbed
        # to "no duplicate" rather than via load_vault so it can't reach the
        # real server.load_vault regardless of module-init ordering.
        with (
            patch("server_modules.connectors_actions._find_duplicate_connector_entry", return_value=None),
            patch("server_modules.connectors_actions.upsert_credential", side_effect=lambda entry: entry),
            patch.dict(
                runtime_models._CONNECTOR_CATALOG,
                {connector: {"label": label, "auth": ["access_token"]}},
                clear=False,
            ),
        ):
            return await connectors_actions.create_connector_vault(
                ConnectorCreate(
                    label=label,
                    connector=connector,
                    workspace_id="ws-1",
                    credentials={"access_token": "mcp-scoped-token"},
                )
            )

    async def test_stripe_connector_creation_succeeds_with_mcp_scoped_token(self):
        calls: list[str] = []

        def fake_discover(*, transport, endpoint, server_id, credential):
            calls.append(endpoint)
            return [{"name": "create_payment_link"}]

        with patch.object(mcp_registry_service, "discover_mcp_server_tools", fake_discover):
            result = await self._create("stripe", label="Stripe")

        self.assertEqual(result["connector"], "stripe")
        self.assertTrue(result["test"]["ok"])
        # Proves the probe went to the MCP endpoint, not classic api.stripe.com.
        self.assertEqual(calls, ["https://mcp.stripe.com"])

    async def test_stripe_connector_creation_still_fails_loudly_on_a_bad_token(self):
        """The fix must not turn every Stripe connection into an
        unconditional success -- a token the MCP server itself rejects must
        still surface as a 400, just from the right endpoint."""
        with patch.object(mcp_registry_service, "discover_mcp_server_tools", side_effect=RuntimeError("401")):
            with self.assertRaises(HTTPException) as exc_info:
                await self._create("stripe", label="Stripe")
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_higgsfield_connector_creation_no_longer_400s(self):
        with patch.object(mcp_registry_service, "discover_mcp_server_tools", return_value=[{"name": "generate_image"}]):
            result = await self._create("higgsfield", label="Higgsfield")

        self.assertEqual(result["connector"], "higgsfield")
        self.assertTrue(result["test"]["ok"])

    async def test_create_connector_vault_response_never_carries_raw_credentials(self):
        """Security regression guard (MAN-109): validate_mcp_scoped_oauth_
        connector (connector_validators.py) always echoes the normalized
        credentials dict back under result["credentials"] so create_connector_
        vault can persist the refreshed value -- but create_connector_vault's
        own return value must never forward that key to the HTTP caller. Before
        the fix, POST /connectors/vault handed the just-exchanged access_token
        straight back to the browser in the response body for every OAuth-
        connected provider (Slack, GitHub, Stripe, Linear, Notion, ... every
        provider whose validator nests its result under "credentials")."""
        with patch.object(mcp_registry_service, "discover_mcp_server_tools", return_value=[{"name": "create_payment_link"}]):
            result = await self._create("stripe", label="Stripe")

        self.assertNotIn("credentials", result["test"])
        self.assertNotIn("mcp-scoped-token", str(result))


# ---------------------------------------------------------------------------
# 8. Gap 3 -- a cached DCR client's client_secret can itself expire (RFC
#    7591 client_secret_expires_at). _DYNAMIC_CLIENT_CACHE must track it and
#    transparently re-register once a cached entry is expired or expiring
#    within the safety margin, instead of caching a client for the life of
#    the process and letting every subsequent token exchange/refresh 401
#    once the real provider-side secret has expired (a live registration
#    against Linear returned a real 24-hour client_secret_expires_at).
# ---------------------------------------------------------------------------

def test_expired_client_secret_triggers_reregistration(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    redirect_uri = "https://app.example.com/api/connections/oauth/linear/callback"
    cache_key = ("linear", redirect_uri)
    service._DYNAMIC_CLIENT_CACHE[cache_key] = service._DynamicClientRegistration(
        client_id="stale-client-id",
        client_secret="stale-client-secret",
        client_secret_expires_at=int(time.time()) - 3600,  # expired an hour ago
    )

    calls: list[tuple[str, dict]] = []

    def fake_post_json(url, payload, *, headers=None):
        calls.append((url, payload))
        return {
            "client_id": "fresh-client-id",
            "client_secret": "fresh-client-secret",
            "client_secret_expires_at": int(time.time()) + 86400,
        }

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    client_id, client_secret = service._resolve_oauth_client("linear", redirect_uri)

    assert (client_id, client_secret) == ("fresh-client-id", "fresh-client-secret")
    assert len(calls) == 1
    cached = service._DYNAMIC_CLIENT_CACHE[cache_key]
    assert cached.client_id == "fresh-client-id"
    assert cached.client_secret_expires_at > int(time.time())


def test_client_secret_expiring_within_safety_margin_triggers_reregistration(monkeypatch) -> None:
    """Even before the literal expiry instant, an entry within the 5-minute
    safety margin must be treated as expired -- a token exchange racing the
    real expiry moment is exactly the failure mode the margin exists to
    avoid."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    redirect_uri = "https://app.example.com/api/connections/oauth/notion/callback"
    cache_key = ("notion", redirect_uri)
    service._DYNAMIC_CLIENT_CACHE[cache_key] = service._DynamicClientRegistration(
        client_id="soon-to-expire-id",
        client_secret="soon-to-expire-secret",
        client_secret_expires_at=int(time.time()) + 60,  # 1 minute left; margin is 5
    )

    calls: list[tuple[str, dict]] = []

    def fake_post_json(url, payload, *, headers=None):
        calls.append((url, payload))
        return {"client_id": "new-id", "client_secret": "new-secret", "client_secret_expires_at": 0}

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    client_id, _client_secret = service._resolve_oauth_client("notion", redirect_uri)
    assert client_id == "new-id"
    assert len(calls) == 1


def test_client_secret_expires_at_zero_never_expires() -> None:
    """RFC 7591 sec 3.2.1: client_secret_expires_at == 0 means the secret
    never expires -- Higgsfield returns exactly this. Must not be treated
    as already-expired (a naive `now >= expires_at` check would trip on 0
    immediately)."""
    entry = service._DynamicClientRegistration(
        client_id="cid", client_secret="csecret", client_secret_expires_at=0,
    )
    assert entry.is_expired() is False


def test_client_secret_expires_at_absent_is_treated_as_never_expiring(monkeypatch) -> None:
    """A registration response with no client_secret_expires_at field at all
    (coerced to 0 by _coerce_epoch_seconds) must behave identically to an
    explicit 0: not expired, and a second resolve call must hit the cache
    rather than re-register."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})
    monkeypatch.setenv("HIGGSFIELD_OAUTH_ENABLED", "true")

    calls: list[tuple[str, dict]] = []

    def fake_post_json(url, payload, *, headers=None):
        calls.append((url, payload))
        return {"client_id": "cid", "client_secret": "csecret"}  # no expiry fields at all

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    redirect_uri = "https://app.example.com/api/connections/oauth/higgsfield/callback"
    client_id, client_secret = service._resolve_oauth_client("higgsfield", redirect_uri)
    assert (client_id, client_secret) == ("cid", "csecret")

    cached = service._DYNAMIC_CLIENT_CACHE[("higgsfield", redirect_uri)]
    assert cached.client_secret_expires_at == 0
    assert cached.is_expired() is False

    # Second resolve for the same (provider, redirect_uri) must hit the
    # cache -- a 0/absent expiry never goes stale.
    client_id_2, client_secret_2 = service._resolve_oauth_client("higgsfield", redirect_uri)
    assert (client_id_2, client_secret_2) == ("cid", "csecret")
    assert len(calls) == 1


def test_client_id_issued_at_is_captured(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    issued_at = int(time.time())
    monkeypatch.setattr(
        service,
        "_post_json",
        lambda url, payload, **_kw: {
            "client_id": "cid",
            "client_secret": "csecret",
            "client_id_issued_at": issued_at,
            "client_secret_expires_at": issued_at + 86400,
        },
    )

    redirect_uri = "https://app.example.com/api/connections/oauth/asana/callback"
    service._resolve_oauth_client("asana", redirect_uri)

    cached = service._DYNAMIC_CLIENT_CACHE[("asana", redirect_uri)]
    assert cached.client_id_issued_at == issued_at


def test_garbage_expiry_values_degrade_to_never_expiring(monkeypatch) -> None:
    """_coerce_epoch_seconds must turn a non-numeric / null client_secret_
    expires_at into 0 (never-expires) rather than raise out of registration
    bookkeeping -- a provider returning a malformed field must not brick the
    whole connect."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    monkeypatch.setattr(
        service,
        "_post_json",
        lambda url, payload, **_kw: {
            "client_id": "cid",
            "client_secret": "csecret",
            "client_secret_expires_at": "not-a-number",
            "client_id_issued_at": None,
        },
    )

    redirect_uri = "https://app.example.com/api/connections/oauth/canva/callback"
    client_id, _secret = service._resolve_oauth_client("canva", redirect_uri)
    assert client_id == "cid"
    cached = service._DYNAMIC_CLIENT_CACHE[("canva", redirect_uri)]
    assert cached.client_secret_expires_at == 0
    assert cached.client_id_issued_at == 0
    assert cached.is_expired() is False


def test_resolve_oauth_client_for_refresh_reregisters_an_expired_entry(monkeypatch) -> None:
    """An expired client_secret must NEVER be handed back to the token
    endpoint (it is guaranteed to be rejected). It used to fall through to a
    hard 'not configured' failure, which permanently killed every Linear
    connection every 24 hours -- Linear's DCR client_secret expires that
    fast. MAN-124: the registration's redirect_uri is now stored alongside
    it, so this path re-registers instead of giving up."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    redirect_uri = "https://app.example.com/api/connections/oauth/linear/callback"
    service._DYNAMIC_CLIENT_CACHE[("linear", redirect_uri)] = service._DynamicClientRegistration(
        client_id="stale-id",
        client_secret="stale-secret",
        client_secret_expires_at=int(time.time()) - 10,
    )

    calls: list = []

    def fake_post_json(url, payload, *, headers=None):
        calls.append((url, payload))
        return {"client_id": "reregistered-id", "client_secret": "reregistered-secret"}

    monkeypatch.setattr(service, "_post_json", fake_post_json)

    client_id, client_secret = service._resolve_oauth_client_for_refresh("linear")

    assert (client_id, client_secret) == ("reregistered-id", "reregistered-secret")
    assert len(calls) == 1
    # Re-registered against the SAME redirect_uri the stale client used.
    assert calls[0][1]["redirect_uris"] == [redirect_uri]
    # The stale entry never reaches a token endpoint.
    assert "stale-secret" not in (client_secret,)


def test_resolve_oauth_client_for_refresh_still_fails_when_nothing_is_known(monkeypatch) -> None:
    """With no cached and no stored registration there is no redirect_uri to
    re-register against, so the original 'not configured' failure must still
    propagate rather than the code inventing one."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    def fail_post_json(*_args, **_kwargs):
        raise AssertionError("must not register without a known redirect_uri")

    monkeypatch.setattr(service, "_post_json", fail_post_json)

    with pytest.raises(HTTPException):
        service._resolve_oauth_client_for_refresh("linear")


def test_resolve_oauth_client_for_refresh_reuses_non_expired_cached_entry(monkeypatch) -> None:
    """Regression guard for the same function: a cache entry that is NOT
    expired must still be reused for background refresh, exactly as before
    this fix."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(service, "_DYNAMIC_CLIENT_CACHE", {})

    redirect_uri = "https://app.example.com/api/connections/oauth/linear/callback"
    service._DYNAMIC_CLIENT_CACHE[("linear", redirect_uri)] = service._DynamicClientRegistration(
        client_id="fresh-id",
        client_secret="fresh-secret",
        client_secret_expires_at=int(time.time()) + 86400,
    )

    client_id, client_secret = service._resolve_oauth_client_for_refresh("linear")
    assert (client_id, client_secret) == ("fresh-id", "fresh-secret")
