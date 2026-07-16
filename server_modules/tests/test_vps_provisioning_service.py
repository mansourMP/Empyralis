import base64
import io
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest

from server_modules import routes_gateway
from server_modules import vps_provisioning_service as vps


class _FakeUrlopenResponse:
    """Minimal stand-in for the context-manager `http.client.HTTPResponse`
    urlrequest.urlopen() normally returns, for tests that need _http_json's
    REAL retry-on-401 logic to run (as opposed to monkeypatching _http_json
    itself, which would bypass the exact thing under test)."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


def test_cloud_init_script_runs_agent_computer_installer():
    script = vps.cloud_init_script("pair_test", api_url="https://api.example.com/api")

    assert script.startswith("#cloud-config")
    assert "curl -fsSL https://empyralis.ai/install/agent-computer.sh" in script
    assert "EMPYRALIS_PAIRING_TOKEN='pair_test'" in script
    assert "EMPYRALIS_API_URL='https://api.example.com/api'" in script
    assert "sudo -E bash" in script


def test_cloud_init_script_appends_missing_api_suffix():
    # docs/DEPLOY-RUNBOOK.md previously told operators to set
    # EMPYRALIS_PUBLIC_API_URL without /api — the box would then register to
    # {url}/gateway/registrations with no /api prefix and 404 forever.
    script = vps.cloud_init_script("pair_test", api_url="https://empyralis.ai")

    assert "EMPYRALIS_API_URL='https://empyralis.ai/api'" in script


def test_cloud_init_script_does_not_double_append_api_suffix():
    script = vps.cloud_init_script("pair_test", api_url="https://empyralis.ai/api")

    assert "EMPYRALIS_API_URL='https://empyralis.ai/api'" in script
    assert "/api/api" not in script


def test_cloud_init_script_strips_trailing_slash_before_checking_api_suffix():
    script = vps.cloud_init_script("pair_test", api_url="https://empyralis.ai/api/")

    assert "EMPYRALIS_API_URL='https://empyralis.ai/api'" in script
    assert "/api/api" not in script


def test_cloud_init_script_allows_installer_url_env_override(monkeypatch):
    monkeypatch.setenv(vps.AGENT_INSTALLER_URL_ENV, "https://empyralis.ai/install/agent-computer.sh")

    script = vps.cloud_init_script("pair_test", api_url="https://api.example.com")

    assert "curl -fsSL https://empyralis.ai/install/agent-computer.sh" in script


def test_cloud_init_script_omits_repo_token_when_unset(monkeypatch):
    monkeypatch.delenv("EMPYRALIS_REPO_TOKEN", raising=False)

    script = vps.cloud_init_script("pair_test", api_url="https://api.example.com")

    assert "EMPYRALIS_REPO_TOKEN" not in script


def test_cloud_init_script_threads_repo_token_when_backend_has_one(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_REPO_TOKEN", "ghp_test_token")

    script = vps.cloud_init_script("pair_test", api_url="https://api.example.com")

    assert "EMPYRALIS_REPO_TOKEN='ghp_test_token'" in script
    # Still precedes sudo -E so it lands in the installer's environment,
    # same mechanism as the pairing token and API URL.
    assert script.index("EMPYRALIS_REPO_TOKEN") < script.index("sudo -E bash")


def test_digitalocean_oauth_start_stores_state_and_uses_registered_redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_ID", "do_client")

    result = vps.create_digitalocean_oauth_start(
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert result["redirect_uri"] == "https://empyralis.ai/api/hardware/vps/oauth/digitalocean/callback"
    assert result["oauth_redirect"].startswith("https://cloud.digitalocean.com/v1/oauth/authorize?")
    assert "client_id=do_client" in result["oauth_redirect"]
    # Granular scopes matching exactly what this service calls (droplet
    # create/delete + region/size reads) — not the old "read write" alias,
    # which (per DO's current scopes reference) maps to full-account
    # read/write, far more than this app needs.
    query = parse_qs(urlsplit(result["oauth_redirect"]).query)
    assert query["scope"] == ["droplet:create droplet:delete regions:read sizes:read"]
    assert "state=" in result["oauth_redirect"]


def test_provider_token_store_encrypts_and_loads_by_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    token_id = vps.store_vps_provider_token(
        provider="hetzner",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"api_token": "secret"},
    )
    loaded = vps.load_vps_provider_credentials(token_id, provider="hetzner", workspace_id="ws-1", user_id="user-1")

    assert token_id.startswith("vps_token_")
    assert loaded["api_token"] == "secret"
    with pytest.raises(KeyError):
        vps.load_vps_provider_credentials(token_id, provider="hetzner", workspace_id="ws-2", user_id="user-1")


# --- DigitalOcean refresh-token renewal (defect #1) -------------------------
#
# DO access tokens expire in 30 days (expires_in=2592000). Before this fix,
# the refresh_token DO's OAuth callback returns was persisted into
# credentials_ciphertext and then never used again — nothing ever called DO's
# token endpoint with grant_type=refresh_token, so any droplet managed more
# than 30 days after connecting failed with HTTP 401. These tests cover both
# halves of the fix: proactive refresh (before expiry, in
# load_vps_provider_credentials) and reactive refresh (on an actual 401, via
# the on_unauthorized callback threaded into _http_json/_http_empty).


def test_store_vps_provider_token_stamps_expiry_from_oauth_expires_in(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    before = int(time.time())

    token_id = vps.store_vps_provider_token(
        provider="digitalocean",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"access_token": "do_secret", "refresh_token": "do_refresh", "expires_in": 2592000},
        source="oauth",
    )
    loaded = vps.load_vps_provider_credentials(
        token_id, provider="digitalocean", workspace_id="ws-1", user_id="user-1"
    )

    assert before + 2592000 <= loaded["access_token_expires_at"] <= before + 2592000 + 5


def test_store_vps_provider_token_does_not_stamp_expiry_for_pasted_token(tmp_path, monkeypatch):
    # Hetzner/Vultr tokens (and a manually pasted DO personal access token)
    # never carry expires_in — there's nothing to proactively judge, so no
    # access_token_expires_at should be synthesized for them.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    token_id = vps.store_vps_provider_token(
        provider="hetzner",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"api_token": "hetzner_secret"},
    )
    loaded = vps.load_vps_provider_credentials(token_id, provider="hetzner", workspace_id="ws-1", user_id="user-1")

    assert "access_token_expires_at" not in loaded


def test_load_vps_provider_credentials_proactively_refreshes_expiring_digitalocean_token(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_ID", "do_client")
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_SECRET", "do_client_secret")

    # expires_in=60 puts access_token_expires_at well inside the 24h refresh
    # buffer (_DIGITALOCEAN_TOKEN_REFRESH_BUFFER_SECONDS) as soon as it's
    # stored, so the very next load must refresh it proactively.
    token_id = vps.store_vps_provider_token(
        provider="digitalocean",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"access_token": "stale_token", "refresh_token": "refresh_abc", "expires_in": 60},
        source="oauth",
    )

    refresh_calls = []

    def fake_http_form_json(method, url, *, payload, provider):
        refresh_calls.append(payload)
        assert method == "POST"
        assert url == vps.DIGITALOCEAN_OAUTH_TOKEN_URL
        assert payload["grant_type"] == "refresh_token"
        assert payload["refresh_token"] == "refresh_abc"
        assert payload["client_id"] == "do_client"
        assert payload["client_secret"] == "do_client_secret"
        return {
            "access_token": "fresh_token",
            "refresh_token": "refresh_xyz",
            "expires_in": 2592000,
            "token_type": "bearer",
        }

    monkeypatch.setattr(vps, "_http_form_json", fake_http_form_json)

    loaded = vps.load_vps_provider_credentials(
        token_id, provider="digitalocean", workspace_id="ws-1", user_id="user-1"
    )

    assert len(refresh_calls) == 1
    assert loaded["access_token"] == "fresh_token"
    assert loaded["refresh_token"] == "refresh_xyz"

    # Persisted, not just returned in-memory — a second load must not
    # refresh again (if it tried, the assertions inside fake_http_form_json
    # would still pass, but refresh_calls would grow past 1).
    loaded_again = vps.load_vps_provider_credentials(
        token_id, provider="digitalocean", workspace_id="ws-1", user_id="user-1"
    )
    assert loaded_again["access_token"] == "fresh_token"
    assert len(refresh_calls) == 1


def test_load_vps_provider_credentials_does_not_refresh_fresh_digitalocean_token(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    token_id = vps.store_vps_provider_token(
        provider="digitalocean",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"access_token": "still_fresh", "refresh_token": "refresh_abc", "expires_in": 2592000},
        source="oauth",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("refresh should not be attempted for a token nowhere near expiry")

    monkeypatch.setattr(vps, "_http_form_json", fail_if_called)

    loaded = vps.load_vps_provider_credentials(
        token_id, provider="digitalocean", workspace_id="ws-1", user_id="user-1"
    )

    assert loaded["access_token"] == "still_fresh"


def test_load_vps_provider_credentials_skips_refresh_without_refresh_token(tmp_path, monkeypatch):
    # A manually pasted DigitalOcean personal access token has no OAuth
    # refresh_token, even though the provider is "digitalocean" — must never
    # attempt a refresh call for it.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    token_id = vps.store_vps_provider_token(
        provider="digitalocean",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"api_token": "manual_pat"},
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("refresh should not be attempted without a refresh_token")

    monkeypatch.setattr(vps, "_http_form_json", fail_if_called)

    loaded = vps.load_vps_provider_credentials(
        token_id, provider="digitalocean", workspace_id="ws-1", user_id="user-1"
    )

    assert loaded["api_token"] == "manual_pat"


def test_http_json_retries_once_on_401_via_on_unauthorized_callback(monkeypatch):
    auth_headers_seen = []

    def fake_urlopen(request, timeout=30):
        auth = request.headers.get("Authorization")
        auth_headers_seen.append(auth)
        if auth == "Bearer stale_token":
            raise vps.urlerror.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        assert auth == "Bearer fresh_token"
        return _FakeUrlopenResponse(b'{"droplet": {"id": 999}}')

    monkeypatch.setattr(vps.urlrequest, "urlopen", fake_urlopen)

    refresh_calls = []

    def on_unauthorized():
        refresh_calls.append(1)
        return "fresh_token"

    result = vps._http_json(
        "POST",
        "https://api.digitalocean.com/v2/droplets",
        token="stale_token",
        payload={"name": "x"},
        provider="digitalocean",
        on_unauthorized=on_unauthorized,
    )

    assert result == {"droplet": {"id": 999}}
    assert auth_headers_seen == ["Bearer stale_token", "Bearer fresh_token"]
    assert len(refresh_calls) == 1


def test_http_json_raises_original_401_when_refresh_cannot_help(monkeypatch):
    def fake_urlopen(request, timeout=30):
        raise vps.urlerror.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(vps.urlrequest, "urlopen", fake_urlopen)

    # A callback that can't actually refresh (returns None, e.g. because
    # there was no refresh_token) must not mask the original 401 — no
    # infinite retry loop, just the ordinary provisioning error.
    with pytest.raises(vps.VPSProvisioningError, match="HTTP 401"):
        vps._http_json(
            "GET",
            "https://api.digitalocean.com/v2/sizes",
            token="stale_token",
            payload=None,
            provider="digitalocean",
            on_unauthorized=lambda: None,
        )


def test_fetch_provider_plans_reactive_refresh_persists_new_token_on_401(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_ID", "do_client")
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_SECRET", "do_client_secret")

    # expires_in is a full 30 days out, so the PROACTIVE check in
    # load_vps_provider_credentials does not fire — this test is specifically
    # about the REACTIVE backstop firing when DO rejects the token anyway
    # (clock skew, an early server-side revocation, a transient proactive
    # refresh failure, etc).
    token_id = vps.store_vps_provider_token(
        provider="digitalocean",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"access_token": "actually_revoked", "refresh_token": "refresh_abc", "expires_in": 2592000},
        source="oauth",
    )

    def fake_http_form_json(method, url, *, payload, provider):
        assert payload["grant_type"] == "refresh_token"
        assert payload["refresh_token"] == "refresh_abc"
        return {"access_token": "reissued_token", "expires_in": 2592000}

    monkeypatch.setattr(vps, "_http_form_json", fake_http_form_json)

    auth_headers_seen = []

    def fake_urlopen(request, timeout=30):
        auth = request.headers.get("Authorization")
        auth_headers_seen.append(auth)
        if auth == "Bearer actually_revoked":
            raise vps.urlerror.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        assert auth == "Bearer reissued_token"
        return _FakeUrlopenResponse(b'{"sizes": []}')

    monkeypatch.setattr(vps.urlrequest, "urlopen", fake_urlopen)

    result = vps.fetch_provider_plans("digitalocean", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert result["plans"] == []
    assert auth_headers_seen == ["Bearer actually_revoked", "Bearer reissued_token"]

    # And the refreshed token got persisted, not just used for this one call.
    reloaded = vps.load_vps_provider_credentials(
        token_id, provider="digitalocean", workspace_id="ws-1", user_id="user-1"
    )
    assert reloaded["access_token"] == "reissued_token"


def test_delete_recorded_vps_reactively_refreshes_digitalocean_token_on_401(tmp_path, monkeypatch):
    # delete_recorded_vps reads its OWN point-in-time credentials snapshot
    # (record_vps_provision), not the token store — it needs its own
    # reactive-refresh wiring, persisted back into that snapshot.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_ID", "do_client")
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_SECRET", "do_client_secret")

    vps.record_vps_provision(
        vps_id="vps_3",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="digitalocean",
        provider_resource_id="12345",
        public_ip=None,
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        pairing_token="pair_do",
        credentials={"access_token": "expired_snapshot", "refresh_token": "refresh_snapshot"},
    )

    def fake_http_form_json(method, url, *, payload, provider):
        assert payload["refresh_token"] == "refresh_snapshot"
        return {"access_token": "renewed_snapshot", "expires_in": 2592000}

    monkeypatch.setattr(vps, "_http_form_json", fake_http_form_json)

    auth_headers_seen = []

    def fake_urlopen(request, timeout=30):
        auth = request.headers.get("Authorization")
        auth_headers_seen.append(auth)
        if auth == "Bearer expired_snapshot":
            raise vps.urlerror.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        assert auth == "Bearer renewed_snapshot"
        return _FakeUrlopenResponse(b"")

    monkeypatch.setattr(vps.urlrequest, "urlopen", fake_urlopen)

    result = vps.delete_recorded_vps("vps_3")

    assert result["status"] == "deleted"
    assert auth_headers_seen == ["Bearer expired_snapshot", "Bearer renewed_snapshot"]


def test_fetch_provider_plans_normalizes_digitalocean_sizes(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    token_id = vps.store_vps_provider_token(
        provider="digitalocean",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"access_token": "do_secret"},
        source="oauth",
    )

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        assert method == "GET"
        assert url == "https://api.digitalocean.com/v2/sizes"
        assert token == "do_secret"
        return {
            "sizes": [
                {"slug": "tiny", "vcpus": 1, "memory": 512, "disk": 10, "price_monthly": 4, "available": True},
                {
                    "slug": "s-1vcpu-2gb",
                    "vcpus": 1,
                    "memory": 2048,
                    "disk": 50,
                    "price_monthly": 12,
                    "available": True,
                    "regions": ["nyc3", "sfo3"],
                },
                {"slug": "s-2vcpu-4gb", "vcpus": 2, "memory": 4096, "disk": 80, "price_monthly": 24, "available": True},
            ]
        }

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_plans("digitalocean", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert [plan["slug"] for plan in result["plans"]] == ["s-1vcpu-2gb", "s-2vcpu-4gb"]
    assert result["plans"][1]["recommended"] is True
    # Regression guard: _mark_recommended used to rebuild every VPSPlan
    # without copying `regions`, silently discarding this per-size
    # availability list on its way out of every normalizer. asdict()
    # preserves the dataclass field's tuple type (JSON serialization at the
    # route layer turns it into a plain array for the frontend).
    assert result["plans"][0]["regions"] == ("nyc3", "sfo3")
    assert result["plans"][1]["regions"] == ()


def test_digitalocean_provisioning_payload_uses_curated_region(monkeypatch):
    calls = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        calls.append(
            {
                "method": method,
                "url": url,
                "token": token,
                "payload": payload,
                "provider": provider,
            }
        )
        return {
            "droplet": {
                "id": 12345,
                "networks": {"v4": [{"type": "public", "ip_address": "203.0.113.10"}]},
            }
        }

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.provision_vps(
        "digitalocean",
        {"api_token": "do_secret"},
        "lon1",
        None,
        "pair_do",
    )

    assert result.provider == "digitalocean"
    assert result.provider_resource_id == "12345"
    assert result.public_ip == "203.0.113.10"
    assert result.size == "s-1vcpu-2gb"
    assert calls[0]["url"] == "https://api.digitalocean.com/v2/droplets"
    assert calls[0]["token"] == "do_secret"
    assert calls[0]["payload"]["region"] == "lon1"
    assert calls[0]["payload"]["image"] == "ubuntu-24-04-x64"
    assert calls[0]["payload"]["user_data"].startswith("#cloud-config")


def test_vultr_provisioning_uses_current_ubuntu_2404_id_and_base64_user_data(monkeypatch):
    calls = []

    def fake_http_json(method, url, *, token, payload, provider):
        calls.append(payload)
        return {"instance": {"id": "vultr-1", "main_ip": "198.51.100.20"}}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.provision_vps("vultr", {"api_key": "vultr_secret"}, "syd", None, "pair_vultr")

    payload = calls[0]
    decoded_user_data = base64.b64decode(payload["user_data"]).decode("utf-8")
    assert result.provider == "vultr"
    assert result.provider_resource_id == "vultr-1"
    assert payload["region"] == "syd"
    assert payload["plan"] == "vc2-1c-2gb"
    assert payload["os_id"] == 2284
    assert decoded_user_data.startswith("#cloud-config")
    assert "EMPYRALIS_PAIRING_TOKEN='pair_vultr'" in decoded_user_data


def test_invalid_region_is_rejected_before_provider_call(monkeypatch):
    called = False

    def fake_http_json(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    with pytest.raises(ValueError):
        vps.provision_vps("hetzner", {"api_token": "secret"}, "free-text", None, "pair_hz")

    assert called is False


def test_provision_vps_without_token_id_makes_no_live_region_call(monkeypatch):
    # No token_id (e.g. a caller working from raw credentials with no
    # connected-account record) => no live-region fetch attempted at all,
    # even for a provider that supports one — identical behavior to before
    # provision_vps could do this, and it still must not call the provider
    # for an invalid region.
    called = False

    def fake_http_json(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    with pytest.raises(ValueError):
        vps.provision_vps("digitalocean", {"api_token": "do_secret"}, "sfo2", None, "pair_do")

    assert called is False


# --- Live-region validation trusts the live catalog, not just the static
# curated list (defect #3) -----------------------------------------------


def test_provision_vps_accepts_live_only_digitalocean_region(monkeypatch):
    # "sfo2" isn't in DO's static PROVIDER_CONFIGS regions tuple, but IS
    # returned by the live /v2/regions call — this is the actual bug: the
    # picker shows a live-fetched region, then the backend used to reject it
    # at provision time because _validate_region only ever checked the
    # static list. provision_vps must accept it once it has a token_id to
    # fetch live data with.
    calls = []

    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        calls.append(url)
        if url == "https://api.digitalocean.com/v2/regions":
            return {"regions": [{"slug": "sfo2", "name": "San Francisco 2", "available": True}]}
        assert url == "https://api.digitalocean.com/v2/droplets"
        return {"droplet": {"id": 777, "networks": {"v4": [{"type": "public", "ip_address": "203.0.113.50"}]}}}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.provision_vps(
        "digitalocean",
        {"api_token": "do_secret"},
        "sfo2",
        None,
        "pair_do",
        token_id="vps_token_live",
    )

    assert result.provider_resource_id == "777"
    assert calls == ["https://api.digitalocean.com/v2/regions", "https://api.digitalocean.com/v2/droplets"]


def test_provision_vps_still_rejects_region_absent_from_static_and_live(monkeypatch):
    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        if url == "https://api.digitalocean.com/v2/regions":
            return {"regions": [{"slug": "sfo2", "name": "San Francisco 2", "available": True}]}
        raise AssertionError("must not reach the create-droplet call for a genuinely invalid region")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    with pytest.raises(ValueError):
        vps.provision_vps(
            "digitalocean",
            {"api_token": "do_secret"},
            "made-up-region",
            None,
            "pair_do",
            token_id="vps_token_live",
        )


def test_provision_vps_still_accepts_static_digitalocean_region_when_live_fetch_fails(monkeypatch):
    # The live-region fetch is best-effort — if it errors out, provision_vps
    # must still work for any region already in the static curated list
    # (same resilience contract as fetch_provider_regions).
    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        if url == "https://api.digitalocean.com/v2/regions":
            raise vps.VPSProvisioningError("digitalocean unreachable")
        assert url == "https://api.digitalocean.com/v2/droplets"
        return {"droplet": {"id": 42, "networks": {"v4": []}}}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.provision_vps(
        "digitalocean",
        {"api_token": "do_secret"},
        "nyc3",
        None,
        "pair_do",
        token_id="vps_token_live",
    )

    assert result.provider_resource_id == "42"


def test_fetch_provider_regions_hetzner_uses_live_locations(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    token_id = vps.store_vps_provider_token(
        provider="hetzner",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"api_token": "hetzner_secret"},
    )

    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        assert method == "GET"
        assert url == "https://api.hetzner.cloud/v1/locations"
        assert token == "hetzner_secret"
        return {
            "locations": [
                {"id": 1, "name": "fsn1", "description": "Falkenstein DC Park 1", "city": "Falkenstein"},
                {"id": 6, "name": "sin", "description": "Singapore", "city": "Singapore"},
            ]
        }

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_regions("hetzner", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    # Singapore ("sin") is entirely missing from the static PROVIDER_CONFIGS
    # tuple this fetch would otherwise fall back to — proving this is really
    # the live call, not a static-list coincidence.
    assert [r["id"] for r in result["regions"]] == ["fsn1", "sin"]


def test_fetch_provider_regions_hetzner_falls_back_to_static_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    token_id = vps.store_vps_provider_token(
        provider="hetzner",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"api_token": "hetzner_secret"},
    )

    def fake_http_json(*args, **kwargs):
        raise vps.VPSProvisioningError("hetzner unreachable")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_regions("hetzner", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert [r["id"] for r in result["regions"]] == ["nbg1", "fsn1", "hel1", "ash", "hil", "sin"]


# --- Plan -> region availability threaded for Hetzner/Vultr (defect #4) ----


def test_fetch_provider_plans_threads_hetzner_region_availability(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    token_id = vps.store_vps_provider_token(
        provider="hetzner",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"api_token": "hetzner_secret"},
    )

    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        return {
            "server_types": [
                {
                    "name": "cx22",
                    "cores": 2,
                    "memory": 4,
                    "disk": 40,
                    "architecture": "x86",
                    "prices": [{"price_monthly": {"gross": "5.99"}}],
                    # Hetzner's 2025-09-24 "per-location server types" shape:
                    # a `locations` array per server_type, not a flat top-
                    # level `locations` field like Vultr's plans have.
                    "locations": [
                        {"name": "fsn1", "available": True},
                        {"name": "sin", "available": True},
                        {"name": "hil", "available": False},
                    ],
                },
            ]
        }

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_plans("hetzner", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert result["plans"][0]["regions"] == ("fsn1", "sin")


def test_fetch_provider_plans_threads_vultr_region_availability(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    token_id = vps.store_vps_provider_token(
        provider="vultr",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        credentials={"api_key": "vultr_secret"},
    )

    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        return {
            "plans": [
                {
                    "id": "vc2-1c-2gb",
                    "vcpu_count": 1,
                    "ram": 2048,
                    "disk": 55,
                    "monthly_cost": 12,
                    "locations": ["ewr", "lhr"],
                },
            ]
        }

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_plans("vultr", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert result["plans"][0]["regions"] == ("ewr", "lhr")


# --- Pre-connect browsing: Vultr's plans/regions are public (defect #5) ----


def test_fetch_public_provider_plans_vultr_succeeds(monkeypatch):
    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        assert method == "GET"
        assert url == "https://api.vultr.com/v2/plans?type=vc2"
        assert token is None
        return {
            "plans": [
                {
                    "id": "vc2-1c-2gb",
                    "vcpu_count": 1,
                    "ram": 2048,
                    "disk": 55,
                    "monthly_cost": 12,
                    "locations": ["ewr"],
                },
            ]
        }

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_public_provider_plans("vultr")

    assert result["provider"] == "vultr"
    assert result["plans"][0]["slug"] == "vc2-1c-2gb"


def test_fetch_public_provider_plans_raises_for_providers_without_a_public_catalog():
    # Verified live against the real APIs: DigitalOcean's /v2/sizes and
    # Hetzner's /v1/server_types both 401 unauthenticated — only Vultr's
    # plans are reachable with no connected account.
    with pytest.raises(vps.VPSProvisioningError):
        vps.fetch_public_provider_plans("digitalocean")
    with pytest.raises(vps.VPSProvisioningError):
        vps.fetch_public_provider_plans("hetzner")


def test_fetch_public_provider_regions_vultr_prefers_live_data(monkeypatch):
    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        assert url == "https://api.vultr.com/v2/regions"
        assert token is None
        return {"regions": [{"id": "waw", "city": "Warsaw", "country": "PL"}]}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_public_provider_regions("vultr")

    assert result["default_size"] == "vc2-1c-2gb"
    assert [r["id"] for r in result["regions"]] == ["waw"]


def test_fetch_public_provider_regions_non_vultr_returns_static_catalog_shape():
    # Same field set provider_catalog() has always served for these
    # providers pre-connection (provider/label/auth_label/default_region/
    # default_size/regions) — fetch_public_provider_regions is a drop-in.
    result = vps.fetch_public_provider_regions("digitalocean")

    assert result["provider"] == "digitalocean"
    assert result["label"] == "DigitalOcean"
    assert result["default_size"] == "s-1vcpu-2gb"
    assert [r["id"] for r in result["regions"]] == ["nyc3", "sfo3", "lon1", "fra1", "sgp1", "blr1"]


@pytest.mark.asyncio
async def test_hardware_vps_plans_route_serves_public_vultr_catalog_without_connecting():
    def fake_http_json(method, url, *, token, payload, provider, on_unauthorized=None):
        assert token is None
        return {
            "plans": [
                {"id": "vc2-1c-2gb", "vcpu_count": 1, "ram": 2048, "disk": 55, "monthly_cost": 12},
            ]
        }

    with patch.object(routes_gateway.vps_provisioning_service, "_http_json", fake_http_json):
        response = await routes_gateway.get_hardware_vps_plans(
            "vultr", token_id=None, workspace_id=None, current_user={"user_id": "user-1"}
        )

    assert response["provider"] == "vultr"
    assert response["plans"][0]["slug"] == "vc2-1c-2gb"


@pytest.mark.asyncio
async def test_hardware_vps_plans_route_requires_connected_account_for_digitalocean_without_token():
    with pytest.raises(routes_gateway.HTTPException) as exc_info:
        await routes_gateway.get_hardware_vps_plans(
            "digitalocean", token_id=None, workspace_id=None, current_user={"user_id": "user-1"}
        )

    assert exc_info.value.status_code == 400


def test_vps_status_becomes_connected_when_gateway_registration_has_vps_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setattr(
        vps.gateway_state_repository,
        "list_workspace_gateway_registrations",
        lambda *args, **kwargs: [{"gateway_id": "gw-1", "metadata": {"vps_id": "vps_1"}}],
    )

    vps.record_vps_provision(
        vps_id="vps_1",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="hetzner",
        provider_resource_id="server-1",
        public_ip=None,
        region="fsn1",
        size="cx22",
        status="provisioning",
        pairing_token="pair_hz",
        credentials={"api_token": "secret"},
    )

    status = vps.get_vps_provision_status("vps_1")

    assert status["status"] == "connected"
    assert "credentials_ciphertext" not in status
    assert "pairing_token_ciphertext" not in status


def test_delete_recorded_vps_calls_provider_cleanup(tmp_path, monkeypatch):
    deleted = []
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setattr(
        vps,
        "_http_empty",
        lambda method, url, *, token, provider, **_kwargs: deleted.append((method, url, token, provider)),
    )

    vps.record_vps_provision(
        vps_id="vps_2",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="digitalocean",
        provider_resource_id="12345",
        public_ip=None,
        region="nyc3",
        size="s-1vcpu-2gb",
        status="failed",
        pairing_token="pair_do",
        credentials={"api_token": "do_secret"},
    )

    result = vps.delete_recorded_vps("vps_2")

    assert result["status"] == "deleted"
    assert deleted == [("DELETE", "https://api.digitalocean.com/v2/droplets/12345", "do_secret", "digitalocean")]


@pytest.mark.asyncio
async def test_hardware_vps_regions_route_returns_curated_provider_list(monkeypatch):
    # Pre-connect Vultr browsing prefers Vultr's live public /v2/regions
    # (see fetch_public_provider_regions) — simulate that call failing so
    # this test exercises (and stays pinned to) the curated static fallback
    # without making a real network call.
    monkeypatch.setattr(
        vps,
        "_http_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(vps.VPSProvisioningError("vultr unreachable")),
    )

    response = await routes_gateway.get_hardware_vps_regions("vultr", current_user={"user_id": "user-1"})

    assert response["provider"] == "vultr"
    assert response["default_region"] == "ewr"
    assert response["default_size"] == "vc2-1c-2gb"
    assert [item["id"] for item in response["regions"]] == ["ewr", "lhr", "fra", "sgp", "syd"]


@pytest.mark.asyncio
async def test_hardware_vps_regions_route_prefers_live_vultr_regions_when_available(monkeypatch):
    # When Vultr's public regions call succeeds, the route should serve that
    # live list (a newer/different set proves it's not just the static one)
    # instead of the curated fallback — this is what makes pre-connect
    # region browsing (item 5) actually show real, current data.
    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        assert method == "GET"
        assert url == "https://api.vultr.com/v2/regions"
        assert token is None
        return {
            "regions": [
                {"id": "ewr", "city": "Newark", "country": "US"},
                {"id": "waw", "city": "Warsaw", "country": "PL"},
            ]
        }

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    response = await routes_gateway.get_hardware_vps_regions("vultr", current_user={"user_id": "user-1"})

    assert response["provider"] == "vultr"
    assert response["default_size"] == "vc2-1c-2gb"
    assert [item["id"] for item in response["regions"]] == ["ewr", "waw"]


@pytest.mark.asyncio
async def test_hardware_vps_regions_route_unaffected_for_non_vultr_without_token():
    # DigitalOcean/Hetzner have no public regions endpoint (verified live:
    # both 401 unauthenticated) — the no-token branch must keep serving
    # exactly the static curated list for them, unchanged.
    response = await routes_gateway.get_hardware_vps_regions("digitalocean", current_user={"user_id": "user-1"})

    assert response["provider"] == "digitalocean"
    assert response["default_size"] == "s-1vcpu-2gb"
    assert [item["id"] for item in response["regions"]] == ["nyc3", "sfo3", "lon1", "fra1", "sgp1", "blr1"]


@pytest.mark.asyncio
async def test_provision_hardware_vps_route_creates_pairing_then_records_vps():
    result = vps.VPSResult(
        provider_resource_id="droplet-1",
        public_ip="203.0.113.30",
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        provider="digitalocean",
    )
    body = routes_gateway.HardwareVPSProvisionRequest(
        workspace_id="ws-1",
        provider="digitalocean",
        credentials={"api_token": "do_secret"},
        region="nyc3",
        size=None,
        runtime_access_mode="full_access",
        autonomous_agent_setup_warning_acknowledged=True,
        metadata={"autonomous_agent_setup_warning_version": "2026-06-06"},
    )
    current_user = {"user_id": "user-1"}

    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1") as access_mock,
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.gateway_pairing_service,
            "create_gateway_pairing_intent",
            return_value={"pairing_token": "pair_do", "pairing_id": "pairing-1"},
        ) as pairing_mock,
        patch.object(routes_gateway.vps_provisioning_service, "provision_vps", return_value=result) as provision_mock,
        patch.object(routes_gateway.vps_provisioning_service, "record_vps_provision") as record_mock,
    ):
        response = await routes_gateway.provision_hardware_vps(body, current_user=current_user)

    assert response["pairing_token"] == "pair_do"
    assert response["vps_id"].startswith("vps_")
    assert response["provider_resource_id"] == "droplet-1"
    access_mock.assert_called_once_with(current_user, "ws-1", minimum_role="owner")
    assert pairing_mock.call_args.kwargs["metadata"]["setup_source"] == "vps"
    assert pairing_mock.call_args.kwargs["metadata"]["vps_id"] == response["vps_id"]
    assert pairing_mock.call_args.kwargs["metadata"]["autonomous_agent_setup_warning_version"] == "2026-06-06"
    assert pairing_mock.call_args.kwargs["runtime_access_mode"] == "full_access"
    assert pairing_mock.call_args.kwargs["autonomous_agent_setup_warning_acknowledged"] is True
    assert provision_mock.call_args.args == (
        "digitalocean",
        {"api_token": "do_secret"},
        "nyc3",
        "s-1vcpu-2gb",
        "pair_do",
    )
    assert record_mock.call_args.kwargs["credentials"] == {"api_token": "do_secret"}
    assert record_mock.call_args.kwargs["vps_id"] == response["vps_id"]


@pytest.mark.asyncio
async def test_provision_hardware_vps_route_uses_stored_provider_token():
    result = vps.VPSResult(
        provider_resource_id="droplet-1",
        public_ip="203.0.113.30",
        region="nyc3",
        size="s-2vcpu-4gb",
        status="provisioning",
        provider="digitalocean",
    )
    body = routes_gateway.HardwareVPSProvisionRequest(
        workspace_id="ws-1",
        provider="digitalocean",
        token_id="vps_token_1",
        region="nyc3",
        size="s-2vcpu-4gb",
        runtime_access_mode="full_access",
        autonomous_agent_setup_warning_acknowledged=True,
    )
    current_user = {"user_id": "user-1"}

    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.gateway_pairing_service,
            "create_gateway_pairing_intent",
            return_value={"pairing_token": "pair_do", "pairing_id": "pairing-1"},
        ),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "load_vps_provider_credentials",
            return_value={"access_token": "do_secret"},
        ) as load_credentials_mock,
        patch.object(routes_gateway.vps_provisioning_service, "provision_vps", return_value=result) as provision_mock,
        patch.object(routes_gateway.vps_provisioning_service, "record_vps_provision") as record_mock,
    ):
        await routes_gateway.provision_hardware_vps(body, current_user=current_user)

    load_credentials_mock.assert_called_once_with(
        "vps_token_1",
        provider="digitalocean",
        workspace_id="ws-1",
        user_id="user-1",
    )
    assert provision_mock.call_args.args == (
        "digitalocean",
        {"access_token": "do_secret"},
        "nyc3",
        "s-2vcpu-4gb",
        "pair_do",
    )
    assert record_mock.call_args.kwargs["credentials"] == {"access_token": "do_secret"}


@pytest.mark.asyncio
async def test_hardware_vps_status_route_enforces_workspace_access():
    with (
        patch.object(
            routes_gateway.vps_provisioning_service,
            "load_vps_record",
            return_value={
                "vps_id": "vps_1",
                "workspace_id": "ws-1",
                "provider": "hetzner",
                "provider_resource_id": "server-1",
                "public_ip": "203.0.113.40",
                "region": "fsn1",
                "size": "cx22",
                "status": "provisioning",
            },
        ),
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1") as access_mock,
        patch.object(
            routes_gateway.vps_provisioning_service,
            "get_vps_provision_status",
            return_value={
                "vps_id": "vps_1",
                "provider": "hetzner",
                "provider_resource_id": "server-1",
                "public_ip": "203.0.113.40",
                "region": "fsn1",
                "size": "cx22",
                "status": "connected",
            },
        ),
    ):
        response = await routes_gateway.get_hardware_vps_status("vps_1", current_user={"user_id": "user-1"})

    assert response["status"] == "connected"
    access_mock.assert_called_once_with({"user_id": "user-1"}, "ws-1", minimum_role="viewer")


@pytest.mark.asyncio
async def test_hardware_vps_delete_route_enforces_owner_access():
    with (
        patch.object(
            routes_gateway.vps_provisioning_service,
            "load_vps_record",
            return_value={
                "vps_id": "vps_1",
                "workspace_id": "ws-1",
                "provider": "vultr",
                "provider_resource_id": "instance-1",
                "status": "failed",
            },
        ),
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1") as access_mock,
        patch.object(
            routes_gateway.vps_provisioning_service,
            "delete_recorded_vps",
            return_value={
                "vps_id": "vps_1",
                "provider": "vultr",
                "provider_resource_id": "instance-1",
                "status": "deleted",
            },
        ) as delete_mock,
    ):
        response = await routes_gateway.delete_hardware_vps("vps_1", current_user={"user_id": "user-1"})

    assert response["status"] == "deleted"
    access_mock.assert_called_once_with({"user_id": "user-1"}, "ws-1", minimum_role="owner")
    delete_mock.assert_called_once_with("vps_1")
