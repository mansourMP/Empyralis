"""36 net-new Tier-1 connectors wired from the verified MCP connector
directory (2026-07-19), plus the generic MCP-scoped-validation routing
refactor that makes them (and every existing DCR connector) work end-to-end.

Directory provenance: every entry below was independently verified by a
research sub-agent against the vendor's OWN live /.well-known/oauth-
authorization-server (or oauth-protected-resource -> issuer chain) endpoints.
The directory's TIER 1 table listed 39 candidate rows; 3 were skipped here as
exact duplicates of already-wired connectors (sentry, paypal, attio -- see
test_dcr_connector_sweep.py's _NET_NEW_PROVIDERS) -- see test_no_duplicate_
tier1_ids_against_already_wired_connectors below for the regression guard.

This file also proves the connector_validators.py generic-routing refactor
(_connector_requires_mcp_scoped_validation / validate_generic_oauth_
connector): ANY connector with a registration_endpoint in OAUTH_PROVIDER_
CONFIGS now validates via a live MCP handshake by default -- not a hardcoded
per-provider list -- except the two connectors (airtable, dropbox) whose DCR
mints a token against the SAME OAuth app as their classic REST API (see each
one's OAUTH_PROVIDER_CONFIGS comment for the discovery evidence).
"""

from __future__ import annotations

import ast
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


# provider -> (display_name, mcp_url) -- mcp_url pinned as a regression guard
# against silently drifting from the directory's verified value.
_TIER1_PROVIDERS: dict[str, tuple[str, str]] = {
    "gusto": ("Gusto", "https://mcp.api.gusto.com"),
    "deel": ("Deel", "https://api.letsdeel.com/mcp"),
    "remote_com": ("Remote", "https://mcp.remote.com/mcp"),
    "ashby": ("Ashby", "https://mcp.ashbyhq.com/mcp/v1"),
    "klaviyo": ("Klaviyo", "https://mcp.klaviyo.com/mcp"),
    "customer_io": ("Customer.io", "https://mcp.customer.io/mcp"),
    "netlify": ("Netlify", "https://netlify-mcp.netlify.app/mcp"),
    "supabase": ("Supabase", "https://mcp.supabase.com/mcp"),
    "planetscale": ("PlanetScale", "https://mcp.pscale.dev/mcp/planetscale"),
    "neon": ("Neon", "https://mcp.neon.tech/mcp"),
    "railway": ("Railway", "https://mcp.railway.com"),
    "heroku": ("Heroku", "https://mcp.heroku.com/mcp"),
    "sourcegraph": ("Sourcegraph", "https://sourcegraph.com/.api/mcp"),
    "replit": ("Replit", "https://replit-mcp.com/server/mcp"),
    "postman": ("Postman", "https://mcp.postman.com/mcp"),
    "buildkite": ("Buildkite", "https://mcp.buildkite.com/mcp"),
    "socket": ("Socket", "https://mcp.socket.dev/"),
    "whimsical": ("Whimsical", "https://mcp.whimsical.com/mcp"),
    "ramp": ("Ramp", "https://mcp.ramp.com/mcp"),
    "brex": ("Brex", "https://api.brex.com/mcp"),
    "mercury": ("Mercury", "https://mcp.mercury.com/mcp"),
    "robinhood": ("Robinhood", "https://agent.robinhood.com/mcp/trading"),
    "amplitude": ("Amplitude", "https://mcp.amplitude.com/mcp"),
    "mixpanel": ("Mixpanel", "https://mcp.mixpanel.com/mcp"),
    "posthog": ("PostHog", "https://mcp.posthog.com/mcp"),
    "meta_ads": ("Meta Ads", "https://mcp.facebook.com/ads"),
    "semrush": ("Semrush", "https://mcp.semrush.com/v1/mcp"),
    "ahrefs": ("Ahrefs", "https://api.ahrefs.com/mcp/mcp"),
    "close_crm": ("Close", "https://mcp.close.com/mcp"),
    "apollo_io": ("Apollo.io", "https://mcp.apollo.io/mcp"),
    "outreach": ("Outreach", "https://api.outreach.io/mcp/"),
    "salesloft": ("Salesloft", "https://mcp.salesloft.com/mcp"),
    "clay": ("Clay", "https://api.clay.com/v3/mcp"),
    "fireflies": ("Fireflies.ai", "https://api.fireflies.ai/mcp"),
    "fathom": ("Fathom", "https://api.fathom.ai/mcp"),
    "coda": ("Superhuman Docs (formerly Coda)", "https://docs.superhuman.com/apis/mcp"),
}

assert len(_TIER1_PROVIDERS) == 36

# Directory rows deliberately skipped as exact duplicates of already-wired
# connectors (Task 1 dedup, 2026-07-19) -- must NOT appear in _TIER1_PROVIDERS
# and must still resolve to their pre-existing, unrelated wiring.
_SKIPPED_AS_DUPLICATE = ("sentry", "paypal", "attio")


# ---------------------------------------------------------------------------
# 1. Dedup: Task 1's explicit requirement -- no duplicate keys anywhere, and
#    the 3 skipped directory rows still resolve to their original entries.
# ---------------------------------------------------------------------------

def _dict_literal_keys(source: str, target_name: str) -> list[str]:
    """AST-level duplicate-key guard -- a literal duplicate key in a dict
    display is not a SyntaxError in Python (the later one silently wins), so
    a runtime len(dict) check can't catch it. Parses the module source and
    returns every string key literal assigned to `target_name`."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        name = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        if name == target_name and isinstance(value, ast.Dict):
            return [k.value for k in value.keys if isinstance(k, ast.Constant)]
    raise AssertionError(f"{target_name} not found as a top-level dict literal")


def test_no_duplicate_keys_in_oauth_provider_configs() -> None:
    import inspect

    source = inspect.getsource(service)
    keys = _dict_literal_keys(source, "OAUTH_PROVIDER_CONFIGS")
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"duplicate OAUTH_PROVIDER_CONFIGS keys: {dupes}"


def test_no_duplicate_keys_in_app_mcp_server_map() -> None:
    import inspect

    source = inspect.getsource(service)
    keys = _dict_literal_keys(source, "APP_MCP_SERVER_MAP")
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"duplicate APP_MCP_SERVER_MAP keys: {dupes}"


def test_no_duplicate_keys_in_connection_provider_aliases() -> None:
    import inspect

    source = inspect.getsource(service)
    keys = _dict_literal_keys(source, "_CONNECTION_PROVIDER_ALIASES")
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"duplicate _CONNECTION_PROVIDER_ALIASES keys: {dupes}"


@pytest.mark.parametrize("provider", _SKIPPED_AS_DUPLICATE)
def test_skipped_duplicates_are_not_in_the_tier1_set(provider) -> None:
    assert provider not in _TIER1_PROVIDERS


@pytest.mark.parametrize("provider", _SKIPPED_AS_DUPLICATE)
def test_skipped_duplicates_still_resolve_to_their_original_wiring(provider) -> None:
    """Regression guard: skipping these 3 directory rows must not have
    disturbed their pre-existing config (added in the prior net-new-5 pass)."""
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.registration_endpoint
    assert provider in service.APP_MCP_SERVER_MAP


def test_tier1_ids_do_not_collide_with_hardware_vps_or_channel_providers() -> None:
    """Task 1's other dedup checks: none of the 36 collide with the cloud-VPS
    hardware providers (vps_provisioning_service.py) or the channel
    providers (runtime_config.CHANNEL_REGISTRY)."""
    from server_modules.runtime_config import CHANNEL_REGISTRY

    vps_provider_ids = {"digitalocean", "hetzner", "vultr", "google", "aws"}
    channel_ids = set(CHANNEL_REGISTRY.keys())
    for provider in _TIER1_PROVIDERS:
        assert provider not in vps_provider_ids, f"{provider} collides with a VPS provider id"
        assert provider not in channel_ids, f"{provider} collides with a channel provider id"


# ---------------------------------------------------------------------------
# 2. Config shape: every Tier-1 connector is DCR-capable-by-default, matching
#    the established pattern from test_dcr_connectors.py / test_higgsfield_
#    connector.py / test_dcr_connector_sweep.py.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_registration_endpoint_set_and_opt_in_not_required(provider) -> None:
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.registration_endpoint
    assert config.registration_endpoint.startswith("https://")
    assert config.dynamic_registration_opt_in_required is False


@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_uses_pkce(provider) -> None:
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.auth_method == "pkce"


@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_auth_urls_are_https(provider) -> None:
    config = service.OAUTH_PROVIDER_CONFIGS[provider]
    assert config.auth_url.startswith("https://")
    assert config.token_url.startswith("https://")


@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_configured_by_default_with_no_env_and_no_flag(monkeypatch, provider) -> None:
    monkeypatch.delenv(f"{provider.upper()}_CLIENT_ID", raising=False)
    monkeypatch.delenv(f"{provider.upper()}_CLIENT_SECRET", raising=False)
    monkeypatch.delenv(f"{provider.upper()}_OAUTH_ENABLED", raising=False)
    monkeypatch.delenv(f"{provider.upper()}_MCP_ENABLED", raising=False)
    assert service.oauth_provider_configured(provider) is True


@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_alias_resolves_to_itself(provider) -> None:
    assert service.provider_from_connection_id(provider) == provider


# ---------------------------------------------------------------------------
# 3. APP_MCP_SERVER_MAP: exact endpoint pinned to the directory's verified
#    mcp_url.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_mcp_server_map_endpoint_matches_directory(provider) -> None:
    _label, mcp_url = _TIER1_PROVIDERS[provider]
    entries = service.APP_MCP_SERVER_MAP[provider]
    assert len(entries) >= 1
    assert entries[0]["endpoint"] == mcp_url
    assert entries[0]["server_id"]


# ---------------------------------------------------------------------------
# 4. Catalog + runtime wiring.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_in_runtime_connector_catalog(provider) -> None:
    from server_modules.runtime_config import CONNECTOR_CATALOG

    assert provider in CONNECTOR_CATALOG
    assert CONNECTOR_CATALOG[provider].get("auth")


@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_in_connection_catalog(provider) -> None:
    by_id = {item["id"]: item for item in connection_catalog_service.catalog_items()}
    assert provider in by_id
    item = by_id[provider]
    assert item["setup_kind"] == "oauth"
    assert item["setup_available"] is True
    assert item["runtime_usable"] is True
    assert item["runtime_provider"] == provider
    assert item["vault_provider"] == provider


@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_connector_metadata_generic_sets_handle_the_provider(provider) -> None:
    """_connector_public_metadata / _connector_identity_signature must both
    recognize the new id via their shared generic-OAuth-connector set."""
    from server_modules.connector_metadata import _connector_identity_signature, _connector_public_metadata

    creds = {"account_id": "acct-42", "access_token": "tok"}
    public = _connector_public_metadata(provider, creds)
    assert public.get("account_id") == "acct-42"
    signature = _connector_identity_signature(provider, creds)
    assert signature == f"{provider}:acct-42"


# ---------------------------------------------------------------------------
# 5. Generic MCP-scoped validation routing -- the Task 3 refactor. Every
#    Tier-1 connector must route through validate_mcp_scoped_oauth_connector
#    via the GENERIC decision function, not a hardcoded per-provider list.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_routes_through_generic_mcp_scoped_decision(provider) -> None:
    assert connector_validators._connector_requires_mcp_scoped_validation(provider) is True


@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_validate_generic_oauth_connector_uses_live_mcp_handshake(monkeypatch, provider) -> None:
    calls: list[dict] = []

    def fake_discover(*, transport, endpoint, server_id, credential):
        calls.append({"transport": transport, "endpoint": endpoint, "server_id": server_id})
        return [{"name": "tool_one"}]

    monkeypatch.setattr(mcp_registry_service, "discover_mcp_server_tools", fake_discover)

    def fail_classic_http(*_args, **_kwargs):
        raise AssertionError(f"{provider}: must not probe a classic REST endpoint")

    result = connector_validators.validate_generic_oauth_connector(
        provider, {"access_token": "mcp-scoped-token"}, fail_classic_http
    )

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["transport"] == "streamable_http"
    assert calls[0]["server_id"] == provider
    expected_endpoint = service.APP_MCP_SERVER_MAP[provider][0]["endpoint"]
    assert calls[0]["endpoint"] == expected_endpoint


@pytest.mark.parametrize("provider", sorted(_TIER1_PROVIDERS))
def test_validate_generic_oauth_connector_fails_loudly_on_bad_token(monkeypatch, provider) -> None:
    def fake_discover(**_kwargs):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(mcp_registry_service, "discover_mcp_server_tools", fake_discover)

    with pytest.raises(RuntimeError):
        connector_validators.validate_generic_oauth_connector(provider, {"access_token": "bad-token"}, None)


def test_classic_token_exceptions_stay_off_the_mcp_scoped_path() -> None:
    """Airtable and Dropbox are the only two connectors whose DCR mints a
    token against the SAME OAuth app as their classic REST API -- they must
    stay off the generic MCP-scoped path even though both are DCR-capable."""
    assert connector_validators._connector_requires_mcp_scoped_validation("airtable") is False
    assert connector_validators._connector_requires_mcp_scoped_validation("dropbox") is False


@pytest.mark.parametrize(
    "provider",
    ("figma", "todoist", "calendly", "jira", "confluence", "webflow", "monday", "gitlab", "miro", "intercom", "square", "typeform", "vercel"),
)
def test_previously_swept_connectors_now_route_generically_too(provider) -> None:
    """The bug this refactor fixes: these 13 (of the "14 swept") gained DCR
    against a dedicated MCP-only OAuth app in an earlier pass but were left
    on their stale classic-REST validate_oauth_bearer_connector probe, which
    rejects an MCP-scoped token. Must now resolve to True via the SAME
    generic decision every Tier-1 connector above uses -- no hardcoded list."""
    assert connector_validators._connector_requires_mcp_scoped_validation(provider) is True


@pytest.mark.parametrize("provider", ("zapier", "paypal", "sentry", "attio", "cloudflare"))
def test_previously_orphaned_net_new_connectors_route_generically(provider) -> None:
    """These 5 had no validate_<provider>_connector function at all before
    this refactor -- create_connector_vault's old hardcoded elif chain would
    400 with "Unsupported connector". Now resolved through the same generic
    decision, no per-provider code required."""
    assert connector_validators._connector_requires_mcp_scoped_validation(provider) is True


# ---------------------------------------------------------------------------
# 6. End-to-end through the real create_connector_vault / test_connector_
#    vault entry points -- proves no "Unsupported connector" hard-fail for
#    connectors that have NO bespoke validate_<provider>_connector function
#    and NO per-provider elif branch (the generic fallback added to both
#    functions' final `else` clause).
# ---------------------------------------------------------------------------

class CreateConnectorVaultGenericFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def _create(self, connector: str, *, label: str):
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

    async def test_gusto_connector_creation_does_not_hard_fail(self):
        with patch.object(mcp_registry_service, "discover_mcp_server_tools", return_value=[{"name": "list_employees"}]):
            result = await self._create("gusto", label="Gusto")
        self.assertEqual(result["connector"], "gusto")
        self.assertTrue(result["test"]["ok"])

    async def test_posthog_connector_creation_does_not_hard_fail(self):
        with patch.object(mcp_registry_service, "discover_mcp_server_tools", return_value=[{"name": "query_insight"}]):
            result = await self._create("posthog", label="PostHog")
        self.assertEqual(result["connector"], "posthog")
        self.assertTrue(result["test"]["ok"])

    async def test_bad_token_still_surfaces_as_400_not_a_silent_success(self):
        with patch.object(mcp_registry_service, "discover_mcp_server_tools", side_effect=RuntimeError("401")):
            with self.assertRaises(HTTPException) as exc_info:
                await self._create("coda", label="Coda")
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_unwired_connector_still_hard_fails(self):
        """The generic fallback only covers connectors present in
        OAUTH_PROVIDER_CONFIGS -- a genuinely unknown connector id must still
        400, proving the fallback isn't a blanket bypass."""
        with self.assertRaises(HTTPException) as exc_info:
            await self._create("totally_unknown_connector_xyz", label="Nope")
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("Unsupported connector", str(exc_info.exception.detail))


class TestConnectorVaultGenericFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_recheck_does_not_hard_fail_for_a_tier1_connector(self):
        fake_credential = {"id": "cred-1", "provider": "gusto", "workspace_id": "ws-1", "metadata": {}}

        with (
            patch(
                "server_modules.connectors_actions.resolve_vault_credential",
                return_value={"_provider": "gusto", "access_token": "mcp-scoped-token"},
            ),
            patch("server_modules.connectors_actions.get_credential", return_value=fake_credential),
            patch("server_modules.connectors_actions.update_credential_metadata", return_value=None),
            patch.object(mcp_registry_service, "discover_mcp_server_tools", return_value=[{"name": "list_employees"}]),
        ):
            result = await connectors_actions.test_connector_vault("cred-1", workspace_id="ws-1")

        self.assertTrue(result["ok"])
