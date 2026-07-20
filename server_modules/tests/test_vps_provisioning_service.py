import base64
import io
import pathlib
import time
import uuid
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


# --- AWS test fixtures ------------------------------------------------------
#
# vps._boto3 is the single seam every AWS call in the service goes through
# (see _aws_client / _assume_aws_role / _verify_aws_caller_identity) — these
# fakes stand in for boto3.client(...), letting tests drive
# sts:AssumeRole/sts:GetCallerIdentity and every ec2:* call the service makes
# without touching real AWS.


class _FakeStsClient:
    def __init__(self, *, account_id: str = "123456789012", fail_assume_role: bool = False):
        self.assume_role_calls: list[dict] = []
        self.get_caller_identity_calls = 0
        self._account_id = account_id
        self._fail_assume_role = fail_assume_role

    def assume_role(self, **kwargs):
        self.assume_role_calls.append(kwargs)
        if self._fail_assume_role:
            raise vps._ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Role not found or trust policy not satisfied yet"}},
                "AssumeRole",
            )
        return {
            "Credentials": {
                "AccessKeyId": "AKIAFAKEFAKEFAKEFAKE",
                "SecretAccessKey": "fake_secret_access_key",
                "SessionToken": "fake_session_token",
            }
        }

    def get_caller_identity(self):
        self.get_caller_identity_calls += 1
        return {"Account": self._account_id}


class _FakeEc2Client:
    def __init__(
        self,
        *,
        describe_regions_response=None,
        describe_instance_types_response=None,
        describe_images_response=None,
        describe_vpcs_response=None,
        describe_subnets_response=None,
        describe_security_groups_response=None,
        create_security_group_response=None,
        describe_key_pairs_should_fail=True,
        create_key_pair_response=None,
        run_instances_response=None,
        terminate_instances_response=None,
    ):
        self._describe_regions_response = describe_regions_response or {"Regions": []}
        self._describe_instance_types_response = describe_instance_types_response or {"InstanceTypes": []}
        self._describe_images_response = describe_images_response or {"Images": []}
        self._describe_vpcs_response = describe_vpcs_response or {"Vpcs": []}
        self._describe_subnets_response = describe_subnets_response or {"Subnets": []}
        self._describe_security_groups_response = describe_security_groups_response or {"SecurityGroups": []}
        self._create_security_group_response = create_security_group_response or {"GroupId": "sg-fake"}
        self._describe_key_pairs_should_fail = describe_key_pairs_should_fail
        self._create_key_pair_response = create_key_pair_response or {"KeyName": vps._AWS_KEY_PAIR_NAME}
        self._run_instances_response = run_instances_response or {"Instances": [{"InstanceId": "i-fake"}]}
        self._terminate_instances_response = terminate_instances_response or {}

        self.describe_regions_calls = 0
        self.describe_instance_types_calls: list[dict] = []
        self.describe_images_calls: list[dict] = []
        self.describe_vpcs_calls: list[dict] = []
        self.describe_subnets_calls: list[dict] = []
        self.describe_security_groups_calls: list[dict] = []
        self.create_security_group_calls: list[dict] = []
        self.authorize_security_group_ingress_calls: list[dict] = []
        self.describe_key_pairs_calls: list[dict] = []
        self.create_key_pair_calls: list[dict] = []
        self.run_instances_calls: list[dict] = []
        self.terminate_instances_calls: list[dict] = []

    def describe_regions(self, **kwargs):
        self.describe_regions_calls += 1
        return self._describe_regions_response

    def describe_instance_types(self, **kwargs):
        self.describe_instance_types_calls.append(kwargs)
        return self._describe_instance_types_response

    def describe_images(self, **kwargs):
        self.describe_images_calls.append(kwargs)
        return self._describe_images_response

    def describe_vpcs(self, **kwargs):
        self.describe_vpcs_calls.append(kwargs)
        return self._describe_vpcs_response

    def describe_subnets(self, **kwargs):
        self.describe_subnets_calls.append(kwargs)
        return self._describe_subnets_response

    def describe_security_groups(self, **kwargs):
        self.describe_security_groups_calls.append(kwargs)
        return self._describe_security_groups_response

    def create_security_group(self, **kwargs):
        self.create_security_group_calls.append(kwargs)
        return self._create_security_group_response

    def authorize_security_group_ingress(self, **kwargs):
        self.authorize_security_group_ingress_calls.append(kwargs)
        return {}

    def describe_key_pairs(self, **kwargs):
        self.describe_key_pairs_calls.append(kwargs)
        if self._describe_key_pairs_should_fail:
            raise vps._ClientError(
                {"Error": {"Code": "InvalidKeyPair.NotFound", "Message": "not found"}}, "DescribeKeyPairs"
            )
        return {"KeyPairs": [{"KeyName": vps._AWS_KEY_PAIR_NAME}]}

    def create_key_pair(self, **kwargs):
        self.create_key_pair_calls.append(kwargs)
        return self._create_key_pair_response

    def run_instances(self, **kwargs):
        self.run_instances_calls.append(kwargs)
        return self._run_instances_response

    def terminate_instances(self, **kwargs):
        self.terminate_instances_calls.append(kwargs)
        return self._terminate_instances_response


class _FakeBoto3:
    """Stands in for the `boto3` module itself — only `.client(...)` is ever
    called on it (see vps._assume_aws_role / _verify_aws_caller_identity /
    _aws_client)."""

    def __init__(self, *, sts=None, ec2=None):
        self._clients = {}
        if sts is not None:
            self._clients["sts"] = sts
        if ec2 is not None:
            self._clients["ec2"] = ec2
        self.client_calls: list[dict] = []

    def client(self, service_name, **kwargs):
        self.client_calls.append({"service_name": service_name, **kwargs})
        return self._clients[service_name]


_AWS_ROLE_ARN = "arn:aws:iam::123456789012:role/EmpyralisVPSProvisioner"
_AWS_CREDENTIALS = {"role_arn": _AWS_ROLE_ARN, "external_id": "ext-test-id", "account_id": "123456789012"}


def _fake_aws_image(image_id="ami-fake", created="2026-01-01T00:00:00.000Z", root_device="/dev/sda1"):
    return {"ImageId": image_id, "CreationDate": created, "RootDeviceName": root_device}


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
    # read/write, far more than this app needs. tag:create/read/delete are
    # additive-only (see _tag_digitalocean_droplet_best_effort) — droplet
    # creation itself never depends on a token actually having them (see
    # test_digitalocean_provisioning_succeeds_without_tag_scope below).
    query = parse_qs(urlsplit(result["oauth_redirect"]).query)
    assert query["scope"] == [
        "droplet:create droplet:delete regions:read sizes:read tag:create tag:read tag:delete"
    ]
    assert "state=" in result["oauth_redirect"]


def _digitalocean_env(monkeypatch):
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_ID", "do_client")
    monkeypatch.setenv("DIGITALOCEAN_CLIENT_SECRET", "do_client_secret")


def test_complete_digitalocean_oauth_callback_stores_token(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _digitalocean_env(monkeypatch)
    start = vps.create_digitalocean_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")
    monkeypatch.setattr(vps, "_http_form_json", lambda *a, **kw: {
        "access_token": "do_access_token", "refresh_token": "do_refresh_token", "expires_in": 2592000,
    })

    result = vps.complete_digitalocean_oauth_callback(code="auth_code_123", state=start["state"])

    assert result["provider"] == "digitalocean"
    assert result["token_id"].startswith("vps_token_")
    assert result["workspace_id"] == "ws-1"


def test_complete_digitalocean_oauth_callback_rejects_unknown_state(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _digitalocean_env(monkeypatch)

    with pytest.raises(vps.VPSProvisioningError):
        vps.complete_digitalocean_oauth_callback(code="auth_code_123", state="not-a-real-state-token")


# --- OAuth callback idempotency (defect: popup + same-tab-fallback double
# fire) ----------------------------------------------------------------------
#
# The DigitalOcean/Google cloud-VPS OAuth "connect" flow used to open a
# popup and ALSO fall back to a same-tab navigation when the popup got
# blocked or lost its opener — in practice both paths could fire and hit
# /callback for the same authorization more than once. The state token (and
# the OAuth provider's `code`) are single-use, so only the first hit ever
# succeeds; the fix here (see cloud-vps-setup-panel.tsx's
# startDigitalOceanOAuth/startGoogleOAuth) removes the popup entirely, but
# duplicate hits can still happen (browser retry, link prefetch) — these
# tests cover the idempotency layer (_record_oauth_result /
# _cached_oauth_result) that makes a duplicate hit replay the original
# outcome instead of 400ing on "state is invalid or expired".


def test_complete_digitalocean_oauth_callback_replays_result_for_duplicate_state(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _digitalocean_env(monkeypatch)
    start = vps.create_digitalocean_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")

    exchange_calls = []

    def fake_http_form_json(method, url, *, payload, provider):
        exchange_calls.append(payload)
        return {"access_token": "do_access_token", "refresh_token": "do_refresh_token", "expires_in": 2592000}

    monkeypatch.setattr(vps, "_http_form_json", fake_http_form_json)

    first = vps.complete_digitalocean_oauth_callback(code="auth_code_123", state=start["state"])
    second = vps.complete_digitalocean_oauth_callback(code="auth_code_123", state=start["state"])

    assert first == second
    assert first["provider"] == "digitalocean"
    assert first["token_id"].startswith("vps_token_")
    assert first["workspace_id"] == "ws-1"
    # The DO code exchange only ever happened once — the duplicate hit was
    # answered from the cached result, not replayed against DO's API (whose
    # authorization code is single-use and would reject a second exchange).
    assert len(exchange_calls) == 1


def test_complete_digitalocean_oauth_callback_replays_error_for_duplicate_state(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _digitalocean_env(monkeypatch)
    start = vps.create_digitalocean_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")
    monkeypatch.setattr(
        vps, "_http_form_json", lambda *a, **kw: {"access_token": "", "expires_in": 2592000}
    )  # no access_token -> _exchange_digitalocean_oauth_code raises

    with pytest.raises(vps.VPSProvisioningError) as first_exc:
        vps.complete_digitalocean_oauth_callback(code="auth_code_123", state=start["state"])
    with pytest.raises(vps.VPSProvisioningError) as second_exc:
        vps.complete_digitalocean_oauth_callback(code="auth_code_123", state=start["state"])

    assert str(first_exc.value) == str(second_exc.value)
    # Both the original failure and the replayed duplicate know which
    # workspace to send the user back to, even though the state record
    # itself was popped (single-use) on the very first hit.
    assert first_exc.value.workspace_id == "ws-1"
    assert second_exc.value.workspace_id == "ws-1"


def test_complete_google_oauth_callback_replays_result_for_duplicate_state(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    start = vps.create_google_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")
    monkeypatch.setattr(vps, "_http_form_json", lambda *a, **kw: {
        "access_token": "user_access_token", "refresh_token": "user_refresh_token", "expires_in": 3600,
    })

    first = vps.complete_google_oauth_callback(code="auth_code_123", state=start["state"])
    second = vps.complete_google_oauth_callback(code="auth_code_123", state=start["state"])

    assert first == second
    assert first["provider"] == "google"
    assert first["setup_id"].startswith("gsetup_")
    assert first["workspace_id"] == "ws-1"


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
    # The regression this guards: `tags` must NEVER be sent on the
    # create-droplet call itself. DigitalOcean auto-creates a tag object the
    # first time it's referenced by name, and that implicit creation needs
    # the `tag:create` OAuth scope — separate from `droplet:create` — so
    # sending `tags` here 403s the ENTIRE create call ("You are missing the
    # required permission tag:create") for any token connected before
    # tag:create was added to the OAuth request. See
    # test_digitalocean_provisioning_succeeds_when_tag_scope_is_missing for
    # the end-to-end proof.
    assert "tags" not in calls[0]["payload"]


def test_digitalocean_provisioning_succeeds_when_tag_scope_is_missing(monkeypatch):
    """The actual bug this fix closes: a DigitalOcean personal access token
    connected under the old scope request (droplet:create/delete +
    regions:read/sizes:read, no tag:* scopes) must still be able to create a
    droplet. Tagging is applied AFTER creation, as a separate best-effort
    step (see _tag_digitalocean_droplet_best_effort) — every tag call here
    403s exactly like DO would for a token missing tag:create, and that must
    be swallowed rather than failing (or rolling back) the already-created
    droplet."""
    calls = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        calls.append({"method": method, "url": url, "payload": payload})
        if url == "https://api.digitalocean.com/v2/droplets":
            return {
                "droplet": {
                    "id": 12345,
                    "networks": {"v4": [{"type": "public", "ip_address": "203.0.113.10"}]},
                }
            }
        # Every tag-related call (create-tag and attach-to-resource) fails
        # exactly like DO's real 403 for a token that lacks tag:create.
        raise vps.VPSProvisioningError(
            f"{provider} provisioning failed: HTTP 403 "
            '{"id":"forbidden","message":"You are missing the required permission tag:create."}'
        )

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.provision_vps(
        "digitalocean",
        {"api_token": "do_secret"},
        "lon1",
        None,
        "pair_do",
    )

    # The droplet was created successfully despite every tag call 403ing.
    assert result.provider == "digitalocean"
    assert result.provider_resource_id == "12345"
    assert result.public_ip == "203.0.113.10"
    # First call is the create-droplet request; every subsequent call is a
    # best-effort tag attempt (create + attach, per DIGITALOCEAN_TAG_NAMES),
    # all of which 403 and are swallowed rather than raised.
    assert calls[0]["url"] == "https://api.digitalocean.com/v2/droplets"
    tag_call_urls = [c["url"] for c in calls[1:]]
    assert len(tag_call_urls) == len(vps.DIGITALOCEAN_TAG_NAMES) * 2
    assert all(vps.DIGITALOCEAN_TAGS_URL in url for url in tag_call_urls)


def test_digitalocean_provisioning_tags_droplet_when_tag_scope_is_present(monkeypatch):
    """The future-proofing half of the same fix: once a workspace reconnects
    DigitalOcean under the new scope (tag:create/read/delete — see
    create_digitalocean_oauth_start), the very same code path actually
    tags the droplet instead of silently no-op'ing forever."""
    calls = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        calls.append({"method": method, "url": url, "payload": payload})
        if url == "https://api.digitalocean.com/v2/droplets":
            return {"droplet": {"id": 12345, "networks": {"v4": []}}}
        return {}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.provision_vps("digitalocean", {"api_token": "do_secret"}, "lon1", None, "pair_do")

    assert result.provider_resource_id == "12345"
    create_tag_calls = [c for c in calls if c["url"] == vps.DIGITALOCEAN_TAGS_URL]
    attach_calls = [c for c in calls if c["url"] == f"{vps.DIGITALOCEAN_TAGS_URL}/{vps.DIGITALOCEAN_TAG_NAMES[0]}/resources"]
    assert {c["payload"]["name"] for c in create_tag_calls} == set(vps.DIGITALOCEAN_TAG_NAMES)
    assert len(attach_calls) == 1
    assert attach_calls[0]["payload"]["resources"] == [{"resource_id": "12345", "resource_type": "droplet"}]


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
        if url == "https://api.digitalocean.com/v2/droplets":
            return {"droplet": {"id": 777, "networks": {"v4": [{"type": "public", "ip_address": "203.0.113.50"}]}}}
        # Best-effort post-create tagging calls (see
        # _tag_digitalocean_droplet_best_effort) — asserted on below.
        assert vps.DIGITALOCEAN_TAGS_URL in url
        return {}

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
    assert calls[:2] == ["https://api.digitalocean.com/v2/regions", "https://api.digitalocean.com/v2/droplets"]
    assert all(vps.DIGITALOCEAN_TAGS_URL in url for url in calls[2:])


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
        if url == "https://api.digitalocean.com/v2/droplets":
            return {"droplet": {"id": 42, "networks": {"v4": []}}}
        # Best-effort post-create tagging calls (see
        # _tag_digitalocean_droplet_best_effort) — not the subject of this test.
        assert vps.DIGITALOCEAN_TAGS_URL in url
        return {}

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
        patch.object(
            routes_gateway.vps_provisioning_service,
            "get_vps_provision_status",
            return_value={"status": "connected"},
        ),
        patch.object(vps, "VPS_CONNECT_POLL_INTERVAL_SECONDS", 0),
    ):
        response = await routes_gateway.provision_hardware_vps(body, current_user=current_user)

        # provision_hardware_vps must return BEFORE the droplet lifecycle
        # (provision_vps / record_vps_provision's second, real-result call)
        # has run at all — that's the whole point of the async contract.
        # Only the synchronous pre-flight (pairing intent + placeholder
        # record) may have happened by the time this awaits.
        assert provision_mock.call_count == 0
        assert record_mock.call_count == 1

        # Drain the background task this request scheduled (still inside
        # the patch context, so the mocks it calls are still active) to
        # exercise the rest of the lifecycle deterministically instead of
        # racing the event loop.
        background_tasks = [
            task for task in routes_gateway._VPS_PROVISION_BACKGROUND_TASKS if not task.done()
        ]
        assert len(background_tasks) == 1
        await background_tasks[0]

    assert response["pairing_token"] == "pair_do"
    assert response["vps_id"].startswith("vps_")
    # The initial response can never carry the real provider_resource_id —
    # provision_vps (the call that creates it) hasn't run yet at this point.
    assert response["provider_resource_id"] == ""
    assert response["status"] == "provisioning"
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
    # record_vps_provision is called twice: once synchronously (the
    # 'provisioning' placeholder, empty provider_resource_id, so status
    # polling works before the droplet exists) and once from the background
    # task with the real result.
    assert record_mock.call_count == 2
    assert record_mock.call_args_list[0].kwargs["provider_resource_id"] == ""
    assert record_mock.call_args_list[0].kwargs["vps_id"] == response["vps_id"]
    assert record_mock.call_args_list[1].kwargs["credentials"] == {"api_token": "do_secret"}
    assert record_mock.call_args_list[1].kwargs["vps_id"] == response["vps_id"]
    assert record_mock.call_args_list[1].kwargs["provider_resource_id"] == "droplet-1"


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
        patch.object(
            routes_gateway.vps_provisioning_service,
            "get_vps_provision_status",
            return_value={"status": "connected"},
        ),
        patch.object(vps, "VPS_CONNECT_POLL_INTERVAL_SECONDS", 0),
    ):
        await routes_gateway.provision_hardware_vps(body, current_user=current_user)

        background_tasks = [
            task for task in routes_gateway._VPS_PROVISION_BACKGROUND_TASKS if not task.done()
        ]
        assert len(background_tasks) == 1
        await background_tasks[0]

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
    assert record_mock.call_args.kwargs["provider_resource_id"] == "droplet-1"


@pytest.mark.asyncio
async def test_provision_hardware_vps_route_returns_before_provision_vps_runs():
    # The core async-provisioning contract: the POST handler must return
    # fast (never wait on the create call, let alone boot/install/connect),
    # with status 'provisioning' and no provider_resource_id yet. Unlike the
    # two tests above, this one deliberately does NOT drain the scheduled
    # background task before asserting — it's checking what the caller sees
    # the instant the await on provision_hardware_vps itself returns, which
    # is exactly what an HTTP client waiting on this request would see.
    provision_started = False

    def _slow_provision_vps(*_args, **_kwargs):
        nonlocal provision_started
        provision_started = True
        return vps.VPSResult(
            provider_resource_id="droplet-9",
            public_ip="203.0.113.9",
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
        patch.object(routes_gateway.vps_provisioning_service, "provision_vps", side_effect=_slow_provision_vps),
        patch.object(routes_gateway.vps_provisioning_service, "record_vps_provision") as record_mock,
    ):
        response = await routes_gateway.provision_hardware_vps(body, current_user=current_user)

        # The response landed without provision_vps ever having been
        # invoked — asyncio.create_task only *schedules* the background
        # coroutine, it doesn't run any of it until the event loop is next
        # given a chance to (which hasn't happened yet at this point).
        assert provision_started is False
        assert record_mock.call_count == 1  # only the synchronous placeholder write

        # Clean up the still-pending task so it doesn't outlive the mocked
        # context (and so pytest-asyncio doesn't warn about a dangling
        # task); what it does isn't this test's concern.
        for task in list(routes_gateway._VPS_PROVISION_BACKGROUND_TASKS):
            task.cancel()

    assert response["status"] == "provisioning"
    assert response["provider_resource_id"] == ""
    assert response["public_ip"] is None
    assert response["vps_id"].startswith("vps_")


@pytest.mark.asyncio
async def test_run_vps_provisioning_lifecycle_persists_result_then_waits_for_connected(tmp_path, monkeypatch):
    # Full lifecycle, success path: the background task should (1) call
    # provision_vps, (2) persist the REAL result over the placeholder
    # record, then (3) keep polling get_vps_provision_status until it
    # reports 'connected', at which point it should stop -- no cleanup, no
    # failure marking.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps, "VPS_CONNECT_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    vps.record_vps_provision(
        vps_id="vps_lifecycle_1",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="digitalocean",
        provider_resource_id="",
        public_ip=None,
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        pairing_token="pair_do",
        credentials={"api_token": "do_secret"},
        pairing_id="pairing-1",
    )

    result = vps.VPSResult(
        provider_resource_id="droplet-42",
        public_ip="203.0.113.42",
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        provider="digitalocean",
    )
    status_sequence = iter(
        [
            {"status": "provisioning"},
            {"status": "registering"},
            {"status": "connected"},
        ]
    )
    delete_calls = []

    with (
        patch.object(vps, "provision_vps", return_value=result) as provision_mock,
        patch.object(vps, "get_vps_provision_status", side_effect=lambda _vps_id: next(status_sequence)),
        patch.object(vps, "_destroy_vps_provider_resource", side_effect=lambda *a: delete_calls.append(a)),
    ):
        await vps.run_vps_provisioning_lifecycle(
            vps_id="vps_lifecycle_1",
            provider="digitalocean",
            credentials={"api_token": "do_secret"},
            region="nyc3",
            size="s-1vcpu-2gb",
            pairing_token="pair_do",
            token_id=None,
            workspace_id="ws-1",
            tenant_id="tenant-1",
            user_id="user-1",
            pairing_id="pairing-1",
        )

    provision_mock.assert_called_once()
    assert delete_calls == []  # success path never touches cleanup
    record = vps.load_vps_record("vps_lifecycle_1")
    assert record["provider_resource_id"] == "droplet-42"
    assert record["public_ip"] == "203.0.113.42"


@pytest.mark.asyncio
async def test_run_vps_provisioning_lifecycle_deletes_droplet_when_connect_fails(tmp_path, monkeypatch):
    # The critical cleanup-on-failure fence: once a droplet is created
    # (provision_vps succeeds) but the agent never finishes connecting
    # (get_vps_provision_status eventually reports 'failed' -- e.g. the
    # gateway pairing intent expired), the background task must call the
    # provider's delete API for the resource it created, and persist status
    # 'failed'. A failed provision must never leave a running box.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps, "VPS_CONNECT_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    vps.record_vps_provision(
        vps_id="vps_lifecycle_2",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="digitalocean",
        provider_resource_id="",
        public_ip=None,
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        pairing_token="pair_do",
        credentials={"api_token": "do_secret"},
        pairing_id="pairing-1",
    )

    result = vps.VPSResult(
        provider_resource_id="droplet-99",
        public_ip="203.0.113.99",
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        provider="digitalocean",
    )
    status_sequence = iter(
        [
            {"status": "provisioning"},
            {"status": "registering"},
            # cloud-init never finished / the pairing intent expired first:
            {"status": "failed"},
        ]
    )
    deleted = []

    with (
        patch.object(vps, "provision_vps", return_value=result),
        patch.object(vps, "get_vps_provision_status", side_effect=lambda _vps_id: next(status_sequence)),
        patch.object(
            vps,
            "_http_empty",
            lambda method, url, *, token, provider, **_kwargs: deleted.append((method, url, token, provider)),
        ),
    ):
        await vps.run_vps_provisioning_lifecycle(
            vps_id="vps_lifecycle_2",
            provider="digitalocean",
            credentials={"api_token": "do_secret"},
            region="nyc3",
            size="s-1vcpu-2gb",
            pairing_token="pair_do",
            token_id=None,
            workspace_id="ws-1",
            tenant_id="tenant-1",
            user_id="user-1",
            pairing_id="pairing-1",
        )

    # The provider's delete-droplet API was actually called for the
    # resource that was created.
    assert deleted == [("DELETE", "https://api.digitalocean.com/v2/droplets/droplet-99", "do_secret", "digitalocean")]
    record = vps.load_vps_record("vps_lifecycle_2")
    assert record["status"] == "failed"
    assert record["provider_resource_id"] == "droplet-99"


@pytest.mark.asyncio
async def test_run_vps_provisioning_lifecycle_marks_failed_without_cleanup_when_create_fails(tmp_path, monkeypatch):
    # If provision_vps itself raises, no provider resource was ever
    # created (every provider's create call in provision_vps is a single
    # all-or-nothing operation) -- so there's nothing to delete, and the
    # background task must not attempt a delete call against an id that
    # doesn't exist. It still must mark the record 'failed' so the UI's
    # poll loop doesn't hang forever.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    vps.record_vps_provision(
        vps_id="vps_lifecycle_3",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="digitalocean",
        provider_resource_id="",
        public_ip=None,
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        pairing_token="pair_do",
        credentials={"api_token": "do_secret"},
        pairing_id="pairing-1",
    )

    delete_calls = []

    with (
        patch.object(vps, "provision_vps", side_effect=vps.VPSProvisioningError("digitalocean provisioning failed: HTTP 422")),
        patch.object(vps, "_destroy_vps_provider_resource", side_effect=lambda *a: delete_calls.append(a)),
    ):
        await vps.run_vps_provisioning_lifecycle(
            vps_id="vps_lifecycle_3",
            provider="digitalocean",
            credentials={"api_token": "do_secret"},
            region="nyc3",
            size="s-1vcpu-2gb",
            pairing_token="pair_do",
            token_id=None,
            workspace_id="ws-1",
            tenant_id="tenant-1",
            user_id="user-1",
            pairing_id="pairing-1",
        )

    assert delete_calls == []  # nothing was ever created -- nothing to delete
    record = vps.load_vps_record("vps_lifecycle_3")
    assert record["status"] == "failed"
    assert record["provider_resource_id"] == ""
    assert "422" in (record.get("error") or "")


@pytest.mark.asyncio
async def test_run_vps_provisioning_lifecycle_marks_failed_on_connect_timeout(tmp_path, monkeypatch):
    # Defensive timeout: even if the record's live-resolved status never
    # naturally flips to 'failed' (e.g. a future caller threads through a
    # longer-than-default pairing ttl_seconds), the background task's own
    # deadline must still fire, delete the droplet, and mark the record
    # failed -- it must never poll forever while a box keeps billing.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps, "VPS_CONNECT_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(vps, "VPS_CONNECT_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    vps.record_vps_provision(
        vps_id="vps_lifecycle_4",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="digitalocean",
        provider_resource_id="",
        public_ip=None,
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        pairing_token="pair_do",
        credentials={"api_token": "do_secret"},
        pairing_id="pairing-1",
    )
    result = vps.VPSResult(
        provider_resource_id="droplet-77",
        public_ip="203.0.113.77",
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        provider="digitalocean",
    )
    deleted = []

    with (
        patch.object(vps, "provision_vps", return_value=result),
        patch.object(
            vps,
            "_http_empty",
            lambda method, url, *, token, provider, **_kwargs: deleted.append((method, url, token, provider)),
        ),
    ):
        await vps.run_vps_provisioning_lifecycle(
            vps_id="vps_lifecycle_4",
            provider="digitalocean",
            credentials={"api_token": "do_secret"},
            region="nyc3",
            size="s-1vcpu-2gb",
            pairing_token="pair_do",
            token_id=None,
            workspace_id="ws-1",
            tenant_id="tenant-1",
            user_id="user-1",
            pairing_id="pairing-1",
        )

    assert deleted == [("DELETE", "https://api.digitalocean.com/v2/droplets/droplet-77", "do_secret", "digitalocean")]
    record = vps.load_vps_record("vps_lifecycle_4")
    assert record["status"] == "failed"


def test_mark_vps_provision_failed_deletes_resource_and_records_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    deleted = []
    monkeypatch.setattr(
        vps,
        "_http_empty",
        lambda method, url, *, token, provider, **_kwargs: deleted.append((method, url, token, provider)),
    )

    vps.record_vps_provision(
        vps_id="vps_mark_failed_1",
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
        credentials={"api_token": "do_secret"},
    )

    result = vps.mark_vps_provision_failed("vps_mark_failed_1", reason="boot never completed")

    assert result["status"] == "failed"
    assert result["error"] == "boot never completed"
    assert deleted == [("DELETE", "https://api.digitalocean.com/v2/droplets/12345", "do_secret", "digitalocean")]

    # Calling it again (e.g. a second failure signal racing in) must not
    # attempt a second delete against a resource that's already gone.
    vps.mark_vps_provision_failed("vps_mark_failed_1", reason="second failure signal")
    assert len(deleted) == 1


def test_mark_vps_provision_failed_skips_cleanup_when_no_resource_was_created(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("no provider resource exists yet -- delete must not be attempted")

    monkeypatch.setattr(vps, "_http_empty", _fail_if_called)

    vps.record_vps_provision(
        vps_id="vps_mark_failed_2",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="digitalocean",
        provider_resource_id="",
        public_ip=None,
        region="nyc3",
        size="s-1vcpu-2gb",
        status="provisioning",
        pairing_token="pair_do",
        credentials={"api_token": "do_secret"},
    )

    result = vps.mark_vps_provision_failed("vps_mark_failed_2", reason="create call rejected", attempt_cleanup=False)

    assert result["status"] == "failed"
    assert result["provider_resource_id"] == ""


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


# =============================================================================
# Google Cloud: "bootstrap-then-impersonate"
#
# Google is OAuth-only (no pasted API key) and never keeps using the user's
# own OAuth token for ongoing VM management — it's used once to run a
# one-time bootstrap in the user's project (create/select project -> verify
# billing -> enable Compute Engine -> create a dedicated service account ->
# bind it a minimal role -> grant Empyralis's own operator identity
# impersonation rights on it), then discarded. Every call after that
# impersonates the service account via the IAM Credentials API, keyless,
# authenticating as Empyralis's operator identity — never a downloaded
# service-account JSON key. These tests cover all three phases plus plan/
# region normalization, entirely against mocked Google APIs.
# =============================================================================


def _google_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_CLIENT_ID", "google_client")
    monkeypatch.setenv("GOOGLE_CLOUD_CLIENT_SECRET", "google_secret")
    monkeypatch.setenv("GOOGLE_CLOUD_OPERATOR_CLIENT_EMAIL", "empyralis-operator@empyralis-ops.iam.gserviceaccount.com")
    monkeypatch.setenv("GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN", "operator_refresh_token")


def _isolate_vps_state(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))


def _fake_operator_token_exchange(method, url, *, payload, provider):
    assert url == vps.GOOGLE_OAUTH_TOKEN_URL
    assert payload["grant_type"] == "refresh_token"
    assert payload["refresh_token"] == "operator_refresh_token"
    assert payload["client_id"] == "google_client"
    assert payload["client_secret"] == "google_secret"
    return {"access_token": "operator_access_token", "expires_in": 3600}


# --- Provider registration --------------------------------------------------


def test_normalize_provider_google_and_gcp_aliases():
    assert vps._normalize_provider("google") == "google"
    assert vps._normalize_provider("gcp") == "google"
    assert vps._normalize_provider("Google-Cloud") == "google"
    assert vps._normalize_provider("GoogleCloud") == "google"


def test_provider_catalog_includes_google():
    catalog = vps.provider_catalog()

    assert catalog["google"]["label"] == "Google Cloud"
    assert catalog["google"]["default_region"] == "us-central1"
    assert [r["id"] for r in catalog["google"]["regions"]][:1] == ["us-central1"]


def test_store_vps_provider_token_refuses_google(tmp_path, monkeypatch):
    # Defense in depth: the frontend never offers a token-paste form for
    # Google (OAuth only — see PROVIDER_CONFIGS["google"].token_keys), and
    # this is the backend half of that contract.
    _isolate_vps_state(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="Sign in with Google"):
        vps.store_vps_provider_token(
            provider="google",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            user_id="user-1",
            credentials={"api_token": "should-not-work"},
        )


def test_fetch_public_provider_plans_google_requires_connection():
    # Unlike Vultr, Google has no public plan catalog reachable without an
    # impersonated project token — verified against the actual shape of this
    # service's Google integration (every Google call needs a project-scoped
    # bearer token; there is no anonymous compute.machineTypes endpoint).
    with pytest.raises(vps.VPSProvisioningError):
        vps.fetch_public_provider_plans("google")


# --- Step 1: OAuth consent (once), used only to bootstrap ------------------


def test_google_oauth_start_requests_cloud_platform_scope_offline_consent(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)

    result = vps.create_google_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")

    assert result["redirect_uri"] == "https://empyralis.ai/api/hardware/vps/oauth/google/callback"
    assert result["oauth_redirect"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    query = parse_qs(urlsplit(result["oauth_redirect"]).query)
    assert query["client_id"] == ["google_client"]
    assert query["scope"] == ["https://www.googleapis.com/auth/cloud-platform"]
    # offline+consent: the bootstrap sequence can outlast a single ~1h access
    # token if the user pauses partway through (e.g. to attach billing) — see
    # _google_setup_session_access_token's proactive refresh.
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert "state" in query


def test_google_oauth_start_requires_client_id_configured(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    monkeypatch.delenv("GOOGLE_CLOUD_CLIENT_ID", raising=False)

    with pytest.raises(vps.VPSProvisioningError):
        vps.create_google_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")


def test_complete_google_oauth_callback_creates_setup_session_not_a_provider_token(tmp_path, monkeypatch):
    # The critical distinction from DigitalOcean: Google isn't provisionable
    # yet right after OAuth (no project chosen, bootstrap not run) — the
    # callback must produce a short-lived setup_id, never a vps_token_ id
    # that fetch_provider_plans/provision_vps would treat as a real
    # connection.
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    start = vps.create_google_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")

    monkeypatch.setattr(vps, "_http_form_json", lambda *a, **kw: {
        "access_token": "user_access_token", "refresh_token": "user_refresh_token", "expires_in": 3600,
    })

    result = vps.complete_google_oauth_callback(code="auth_code_123", state=start["state"])

    assert result["provider"] == "google"
    assert result["setup_id"].startswith("gsetup_")
    assert "token_id" not in result
    with pytest.raises(KeyError):
        vps.load_vps_provider_credentials(result["setup_id"], provider="google", workspace_id="ws-1")


def test_complete_google_oauth_callback_rejects_mismatched_state(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)

    with pytest.raises(vps.VPSProvisioningError):
        vps.complete_google_oauth_callback(code="auth_code_123", state="not-a-real-state-token")


# --- Step 2: bootstrap (project select/create, billing, enable API, SA,
# custom role, operator impersonation grant) --------------------------------


def _start_google_setup_session(tmp_path, monkeypatch) -> str:
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    start = vps.create_google_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")
    monkeypatch.setattr(vps, "_http_form_json", lambda *a, **kw: {
        "access_token": "user_access_token", "refresh_token": "user_refresh_token", "expires_in": 3600,
    })
    result = vps.complete_google_oauth_callback(code="auth_code_123", state=start["state"])
    return result["setup_id"]


def test_list_google_projects_normalizes_project_list(tmp_path, monkeypatch):
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        assert method == "GET"
        assert token == "user_access_token"
        assert "cloudresourcemanager.googleapis.com/v1/projects" in url
        return {"projects": [
            {"projectId": "proj-a", "name": "Project A"},
            {"projectId": "proj-b"},
            {"name": "missing project id — dropped"},
        ]}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.list_google_projects(setup_id, workspace_id="ws-1", user_id="user-1")

    assert result["projects"] == [
        {"project_id": "proj-a", "name": "Project A"},
        {"project_id": "proj-b", "name": "proj-b"},
    ]


def test_create_google_project_generates_valid_project_id_slug(tmp_path, monkeypatch):
    # Google project ids must be <=30 chars, lowercase alnum + hyphens,
    # starting with a letter.
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)
    calls = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        calls.append((method, url, payload))
        return {"name": "operations/create-op"}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.create_google_project(setup_id, "My New Agent Project!!", workspace_id="ws-1", user_id="user-1")

    project_id = result["project_id"]
    assert len(project_id) <= 30
    assert project_id[0].isalpha()
    assert all(ch.isalnum() or ch == "-" for ch in project_id)
    assert calls[0][2]["projectId"] == project_id
    assert calls[0][2]["name"] == "My New Agent Project!!"


def test_check_google_project_billing_reports_console_fallback_when_not_linked(tmp_path, monkeypatch):
    # There is no API to attach a billing account/card — Cloud Billing's API
    # only links an EXISTING one. The console URL is the only real fallback.
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)

    monkeypatch.setattr(
        vps, "_http_json",
        lambda method, url, *, token, payload, provider, **kw: {"billingEnabled": False},
    )

    result = vps.check_google_project_billing(setup_id, "my-project", workspace_id="ws-1", user_id="user-1")

    assert result["billing_enabled"] is False
    assert result["console_url"] == "https://console.cloud.google.com/billing/linkedaccount?project=my-project"


def test_check_google_project_billing_reports_enabled(tmp_path, monkeypatch):
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)

    monkeypatch.setattr(
        vps, "_http_json",
        lambda method, url, *, token, payload, provider, **kw: {"billingEnabled": True},
    )

    result = vps.check_google_project_billing(setup_id, "my-project", workspace_id="ws-1", user_id="user-1")

    assert result["billing_enabled"] is True


def test_finish_google_bootstrap_rejects_when_billing_not_linked(tmp_path, monkeypatch):
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        vps, "_http_json",
        lambda method, url, *, token, payload, provider, **kw: {"billingEnabled": False},
    )

    with pytest.raises(vps.VPSProvisioningError, match="billing"):
        vps.finish_google_bootstrap(
            setup_id, "my-project", workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
        )

    # Must not have stored a connection or torn down the setup session — the
    # user can fix billing and retry the same session.
    with pytest.raises(KeyError):
        vps.load_vps_provider_credentials("vps_token_nonexistent", provider="google", workspace_id="ws-1")
    vps._load_google_setup_session(setup_id, workspace_id="ws-1")  # still present — does not raise


def test_finish_google_bootstrap_full_sequence(tmp_path, monkeypatch):
    # Exercises the exact bootstrap sequence from the task/module docstring:
    # verify billing -> enable Compute Engine -> create empyralis-provisioner
    # SA -> create+bind a minimal custom role -> grant Empyralis's operator
    # identity serviceAccountTokenCreator on that one SA.
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)
    project_id = "my-project"
    calls = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        calls.append((method, url))
        assert token == "user_access_token"  # every bootstrap call runs AS the user, not the operator
        if "billingInfo" in url:
            return {"billingEnabled": True}
        if url.endswith(":enable"):
            return {"name": "operations/enable-op"}
        if url.endswith("/serviceAccounts") and method == "POST":
            assert payload["accountId"] == "empyralis-provisioner"
            return {"email": f"empyralis-provisioner@{project_id}.iam.gserviceaccount.com"}
        if url.endswith("/roles") and method == "POST":
            assert payload["roleId"] == "empyralisVmProvisioner"
            assert "compute.instances.create" in payload["role"]["includedPermissions"]
            assert "compute.instances.delete" in payload["role"]["includedPermissions"]
            # VM-lifecycle only — no IAM/project-admin permissions.
            assert not any(p.startswith("resourcemanager.") or p.startswith("iam.") for p in payload["role"]["includedPermissions"])
            return {"name": "role-created"}
        if url.endswith(":getIamPolicy"):
            return {"bindings": [], "etag": "abc123"}
        if url.endswith(":setIamPolicy"):
            return {"bindings": payload["policy"]["bindings"]}
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.finish_google_bootstrap(
        setup_id, project_id, workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
    )

    assert result["provider"] == "google"
    assert result["project_id"] == project_id
    assert result["service_account_email"] == f"empyralis-provisioner@{project_id}.iam.gserviceaccount.com"
    assert result["token_id"].startswith("vps_token_")

    urls_called = [url for _method, url in calls]
    assert any("billingInfo" in u for u in urls_called)
    assert any(u.endswith(":enable") for u in urls_called)
    assert any(u.endswith("/serviceAccounts") for u in urls_called)
    assert any(u.endswith("/roles") for u in urls_called)
    # The operator identity gets the binding on the SERVICE ACCOUNT (not the
    # project) — that's what makes generateAccessToken impersonation work.
    sa_resource = f"projects/{project_id}/serviceAccounts/empyralis-provisioner@{project_id}.iam.gserviceaccount.com"
    assert f"{vps.GOOGLE_CLOUD_IAM_URL}/{sa_resource}:setIamPolicy" in urls_called


def test_finish_google_bootstrap_grants_operator_identity_token_creator_role(tmp_path, monkeypatch):
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)
    project_id = "my-project"
    set_iam_policies = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "billingInfo" in url:
            return {"billingEnabled": True}
        if url.endswith(":enable"):
            return {}
        if url.endswith("/serviceAccounts") and method == "POST":
            return {}
        if url.endswith("/roles") and method == "POST":
            return {}
        if url.endswith(":getIamPolicy"):
            return {"bindings": []}
        if url.endswith(":setIamPolicy"):
            set_iam_policies.append((url, payload))
            return {}
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    vps.finish_google_bootstrap(setup_id, project_id, workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")

    sa_policy_calls = [p for (u, p) in set_iam_policies if "serviceAccounts" in u]
    assert len(sa_policy_calls) == 1
    bindings = sa_policy_calls[0]["policy"]["bindings"]
    assert {
        "role": "roles/iam.serviceAccountTokenCreator",
        "members": ["serviceAccount:empyralis-operator@empyralis-ops.iam.gserviceaccount.com"],
    } in bindings


def test_finish_google_bootstrap_tolerates_already_exists_on_retry(tmp_path, monkeypatch):
    # A user retrying bootstrap after fixing a billing issue must not fail
    # just because the service account / custom role already exist from the
    # first attempt.
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)
    project_id = "my-project"

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "billingInfo" in url:
            return {"billingEnabled": True}
        if url.endswith(":enable"):
            return {}
        if url.endswith("/serviceAccounts") and method == "POST":
            raise vps.VPSProvisioningError("google provisioning failed: HTTP 409 already exists")
        if url.endswith("/roles") and method == "POST":
            raise vps.VPSProvisioningError("google provisioning failed: HTTP 409 already exists")
        if url.endswith(":getIamPolicy"):
            return {"bindings": []}
        if url.endswith(":setIamPolicy"):
            return {}
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.finish_google_bootstrap(
        setup_id, project_id, workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
    )

    assert result["service_account_email"] == f"empyralis-provisioner@{project_id}.iam.gserviceaccount.com"


def test_finish_google_bootstrap_propagates_non_409_errors(tmp_path, monkeypatch):
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "billingInfo" in url:
            return {"billingEnabled": True}
        if url.endswith(":enable"):
            return {}
        if url.endswith("/serviceAccounts") and method == "POST":
            raise vps.VPSProvisioningError("google provisioning failed: HTTP 403 permission denied")
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    with pytest.raises(vps.VPSProvisioningError, match="403"):
        vps.finish_google_bootstrap(
            setup_id, "my-project", workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
        )


def test_finish_google_bootstrap_discards_setup_session_and_stores_connection(tmp_path, monkeypatch):
    setup_id = _start_google_setup_session(tmp_path, monkeypatch)
    project_id = "my-project"

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "billingInfo" in url:
            return {"billingEnabled": True}
        if url.endswith(":getIamPolicy"):
            return {"bindings": []}
        return {}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.finish_google_bootstrap(
        setup_id, project_id, workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
    )

    # The user's OAuth session is gone — nothing left to steal or misuse.
    with pytest.raises(KeyError):
        vps._load_google_setup_session(setup_id, workspace_id="ws-1")

    # But the connection (project_id + service_account_email — NOT a secret)
    # is usable going forward.
    stored = vps.load_vps_provider_credentials(result["token_id"], provider="google", workspace_id="ws-1")
    assert stored == {
        "project_id": project_id,
        "service_account_email": f"empyralis-provisioner@{project_id}.iam.gserviceaccount.com",
    }


# --- Step 3: ongoing use — impersonation only, never the user's token ------


def test_google_impersonated_access_token_authenticates_as_operator_not_user(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setattr(vps, "_http_form_json", _fake_operator_token_exchange)

    calls = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        calls.append((method, url, token, payload))
        assert "generateAccessToken" in url
        assert token == "operator_access_token"  # NEVER the end user's own OAuth token
        return {"accessToken": "impersonated_token_xyz", "expireTime": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps._google_impersonated_access_token("empyralis-provisioner@my-project.iam.gserviceaccount.com")

    assert result == "impersonated_token_xyz"
    assert len(calls) == 1
    assert calls[0][1] == (
        f"{vps.GOOGLE_CLOUD_IAM_CREDENTIALS_URL}/projects/-/serviceAccounts/"
        "empyralis-provisioner@my-project.iam.gserviceaccount.com:generateAccessToken"
    )
    assert calls[0][3] == {"scope": [vps.GOOGLE_CLOUD_PLATFORM_SCOPE]}


def test_google_operator_access_token_fails_gracefully_when_unconfigured(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    monkeypatch.delenv("GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN", raising=False)

    with pytest.raises(vps.VPSProvisioningError, match="GOOGLE_CLOUD_OPERATOR_REFRESH_TOKEN"):
        vps._google_operator_access_token()


def test_google_active_token_requires_fully_connected_credentials():
    with pytest.raises(vps.VPSProvisioningError):
        vps._google_active_token({"project_id": "my-project"})  # missing service_account_email
    with pytest.raises(vps.VPSProvisioningError):
        vps._google_active_token({"service_account_email": "sa@my-project.iam.gserviceaccount.com"})


# --- Plan normalization: machineTypes.aggregatedList + Cloud Billing Catalog
# SKUs, matched by family/resourceGroup and priced per-core/per-GB ----------


_GOOGLE_MACHINE_TYPES_PAYLOAD = {
    "items": {
        "zones/us-central1-a": {
            "machineTypes": [
                {"name": "e2-medium", "guestCpus": 2, "memoryMb": 4096},
                {"name": "e2-micro", "guestCpus": 2, "memoryMb": 1024},
                # GPU family — filtered out, not a curated general-purpose family.
                {"name": "a2-highgpu-1g", "guestCpus": 12, "memoryMb": 87040},
                # Deprecated — filtered out even though it's e2.
                {"name": "e2-old", "guestCpus": 2, "memoryMb": 4096, "deprecated": {"state": "OBSOLETE"}},
            ]
        },
        "zones/us-central1-b": {
            "machineTypes": [
                {"name": "e2-medium", "guestCpus": 2, "memoryMb": 4096},
            ]
        },
        "zones/europe-west1-b": {
            "machineTypes": [
                {"name": "e2-medium", "guestCpus": 2, "memoryMb": 4096},
            ]
        },
    }
}

_GOOGLE_SKUS_PAYLOAD = {
    "skus": [
        {
            "description": "E2 Instance Core running in Americas",
            "category": {"resourceFamily": "Compute", "resourceGroup": "E2Standard", "usageType": "OnDemand"},
            "serviceRegions": ["us-central1"],
            "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"units": "0", "nanos": 21811000}}]}}],
        },
        {
            "description": "E2 Instance Ram running in Americas",
            "category": {"resourceFamily": "Compute", "resourceGroup": "E2Standard", "usageType": "OnDemand"},
            "serviceRegions": ["us-central1"],
            "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"units": "0", "nanos": 2923000}}]}}],
        },
        # Preemptible SKU for the same family/region — must NOT be used
        # (usageType != OnDemand).
        {
            "description": "Preemptible E2 Instance Core running in Americas",
            "category": {"resourceFamily": "Compute", "resourceGroup": "E2Standard", "usageType": "Preemptible"},
            "serviceRegions": ["us-central1"],
            "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"units": "0", "nanos": 5000000}}]}}],
        },
    ]
}


def test_normalize_google_plans_prices_via_sku_catalog_and_filters_families():
    plans = vps._normalize_google_plans(_GOOGLE_MACHINE_TYPES_PAYLOAD, _GOOGLE_SKUS_PAYLOAD, region="us-central1")

    slugs = [p.slug for p in plans]
    assert "e2-medium" in slugs
    assert "e2-micro" in slugs
    assert "a2-highgpu-1g" not in slugs  # unsupported family (GPU)
    assert "e2-old" not in slugs  # deprecated

    medium = next(p for p in plans if p.slug == "e2-medium")
    # (2 vcpu * 0.021811) + (4 GB * 0.002923), * 730 hours/mo
    expected = round((2 * 0.021811 + 4 * 0.002923) * 730, 2)
    assert medium.price_monthly == expected
    assert medium.price_label == f"${expected:g}/mo"
    assert medium.disk_gb == vps.GOOGLE_DEFAULT_BOOT_DISK_GB
    # Present in two zones under us-central1 plus one under europe-west1 —
    # region set should dedupe to exactly the two regions.
    assert medium.regions == ("europe-west1", "us-central1")


def test_normalize_google_plans_skips_machine_types_with_no_matching_sku():
    # n2 machine type present, but no n2 SKUs in the catalog — must be
    # omitted rather than shown with a fabricated/zero price.
    machine_types = {
        "items": {"zones/us-central1-a": {"machineTypes": [{"name": "n2-standard-2", "guestCpus": 2, "memoryMb": 8192}]}}
    }
    plans = vps._normalize_google_plans(machine_types, _GOOGLE_SKUS_PAYLOAD, region="us-central1")

    assert plans == []


def test_normalize_google_plans_ignores_out_of_region_skus():
    machine_types = {
        "items": {"zones/europe-west1-b": {"machineTypes": [{"name": "e2-medium", "guestCpus": 2, "memoryMb": 4096}]}}
    }
    # SKUs above are all serviceRegions=["us-central1"] — pricing for
    # europe-west1 must not silently borrow the us-central1 rate.
    plans = vps._normalize_google_plans(machine_types, _GOOGLE_SKUS_PAYLOAD, region="europe-west1")

    assert plans == []


def test_fetch_provider_plans_google_end_to_end(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setattr(vps, "_http_form_json", _fake_operator_token_exchange)
    token_id = vps._store_provider_token_record(
        provider_id="google", workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1",
        credentials={"project_id": "my-project", "service_account_email": "empyralis-provisioner@my-project.iam.gserviceaccount.com"},
        source="oauth_bootstrap",
    )

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "generateAccessToken" in url:
            return {"accessToken": "impersonated_token_xyz"}
        if "aggregated/machineTypes" in url:
            assert "/projects/my-project/" in url
            return _GOOGLE_MACHINE_TYPES_PAYLOAD
        if "/skus" in url:
            return _GOOGLE_SKUS_PAYLOAD
        raise AssertionError(f"unexpected call: {method} {url}")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_plans("google", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert result["provider"] == "google"
    assert any(p["slug"] == "e2-medium" for p in result["plans"])


def test_fetch_provider_plans_google_paginates_sku_catalog(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setattr(vps, "_http_form_json", _fake_operator_token_exchange)

    pages = [
        {"skus": [_GOOGLE_SKUS_PAYLOAD["skus"][0]], "nextPageToken": "page-2"},
        {"skus": [_GOOGLE_SKUS_PAYLOAD["skus"][1]]},
    ]
    calls = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        calls.append(url)
        if "/skus" in url:
            return pages.pop(0)
        raise AssertionError(f"unexpected: {url}")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps._google_fetch_compute_skus("impersonated_token")

    assert len(result["skus"]) == 2
    assert any("pageToken=page-2" in u for u in calls)


# --- Region normalization: compute.regions.list -----------------------------


def test_normalize_google_regions_drops_down_regions():
    payload = {
        "items": [
            {"name": "us-central1", "description": "us-central1", "status": "UP"},
            {"name": "us-west9", "description": "us-west9", "status": "DOWN"},
        ]
    }

    regions = vps._normalize_google_regions(payload)

    assert regions == [{"id": "us-central1", "label": "us-central1"}]


def test_fetch_provider_regions_google_uses_live_compute_regions(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setattr(vps, "_http_form_json", _fake_operator_token_exchange)
    token_id = vps._store_provider_token_record(
        provider_id="google", workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1",
        credentials={"project_id": "my-project", "service_account_email": "sa@my-project.iam.gserviceaccount.com"},
        source="oauth_bootstrap",
    )

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "generateAccessToken" in url:
            return {"accessToken": "impersonated_token_xyz"}
        assert url == f"{vps.GOOGLE_CLOUD_COMPUTE_URL}/projects/my-project/regions"
        assert token == "impersonated_token_xyz"
        # "waw" (Warsaw) is entirely absent from PROVIDER_CONFIGS["google"]'s
        # static tuple — proving this is really the live call.
        return {"items": [{"name": "waw", "description": "waw", "status": "UP"}]}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_regions("google", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert [r["id"] for r in result["regions"]] == ["waw"]


def test_fetch_provider_regions_google_falls_back_to_static_on_failure(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setattr(vps, "_http_form_json", _fake_operator_token_exchange)
    token_id = vps._store_provider_token_record(
        provider_id="google", workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1",
        credentials={"project_id": "my-project", "service_account_email": "sa@my-project.iam.gserviceaccount.com"},
        source="oauth_bootstrap",
    )

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "generateAccessToken" in url:
            return {"accessToken": "impersonated_token_xyz"}
        raise vps.VPSProvisioningError("google unreachable")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_regions("google", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert [r["id"] for r in result["regions"]] == [
        "us-central1", "us-east1", "us-west1", "europe-west1", "europe-west4", "asia-southeast1",
    ]


# --- Provisioning + deletion: zone resolution, impersonated token use ------


def test_provision_vps_google_uses_impersonated_token_and_resolves_zone(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setattr(vps, "_http_form_json", _fake_operator_token_exchange)
    credentials = {"project_id": "my-project", "service_account_email": "sa@my-project.iam.gserviceaccount.com"}
    calls = []

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        calls.append((method, url, token, payload))
        if "generateAccessToken" in url:
            return {"accessToken": "impersonated_token_xyz"}
        if url.endswith("/zones") and method == "GET":
            return {"items": [{
                "name": "us-central1-a", "status": "UP",
                "region": "https://compute.googleapis.com/compute/v1/projects/my-project/regions/us-central1",
            }]}
        if "/instances" in url and method == "POST":
            return {"name": "operation-1", "targetId": "999888"}
        raise AssertionError(f"unexpected: {method} {url}")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.provision_vps("google", credentials, "us-central1", "e2-medium", "pair_google_test")

    assert result.provider == "google"
    assert result.provider_resource_id.startswith("empyralis-agent-computer-google-")
    assert result.public_ip is None
    assert result.region == "us-central1"
    assert result.size == "e2-medium"

    create_call = next(c for c in calls if "/instances" in c[1] and c[0] == "POST")
    assert create_call[2] == "impersonated_token_xyz"  # impersonated SA token, not a user token
    assert create_call[3]["machineType"] == "zones/us-central1-a/machineTypes/e2-medium"
    assert create_call[3]["disks"][0]["initializeParams"]["diskSizeGb"] == str(vps.GOOGLE_DEFAULT_BOOT_DISK_GB)
    assert create_call[3]["metadata"]["items"][0]["value"].startswith("#cloud-config")


def test_provision_vps_google_falls_back_to_a_zone_when_zones_list_fails(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setattr(vps, "_http_form_json", _fake_operator_token_exchange)
    credentials = {"project_id": "my-project", "service_account_email": "sa@my-project.iam.gserviceaccount.com"}

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "generateAccessToken" in url:
            return {"accessToken": "impersonated_token_xyz"}
        if url.endswith("/zones"):
            raise vps.VPSProvisioningError("google unreachable")
        if "/instances" in url and method == "POST":
            assert "us-central1-a" in url or payload["machineType"].startswith("zones/us-central1-a/")
            return {"name": "operation-1", "targetId": "1"}
        raise AssertionError(f"unexpected: {method} {url}")

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.provision_vps("google", credentials, "us-central1", "e2-medium", "pair_google_test")

    assert result.provider_resource_id.startswith("empyralis-agent-computer-google-")


def test_provision_vps_google_rejects_invalid_region(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    credentials = {"project_id": "my-project", "service_account_email": "sa@my-project.iam.gserviceaccount.com"}
    called = False

    def fake_http_json(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(vps, "_http_json", fake_http_json)
    monkeypatch.setattr(vps, "_http_form_json", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not need an operator token for a rejected region")))

    with pytest.raises(ValueError):
        vps.provision_vps("google", credentials, "made-up-region", "e2-medium", "pair_google_test")

    assert called is False


def test_provision_vps_google_requires_fully_connected_credentials(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)

    with pytest.raises(vps.VPSProvisioningError):
        vps.provision_vps("google", {"project_id": "my-project"}, "us-central1", "e2-medium", "pair_google_test")


def test_delete_recorded_vps_google_resolves_zone_and_deletes_by_instance_name(tmp_path, monkeypatch):
    _isolate_vps_state(tmp_path, monkeypatch)
    _google_env(monkeypatch)
    monkeypatch.setattr(vps, "_http_form_json", _fake_operator_token_exchange)

    vps.record_vps_provision(
        vps_id="vps_google_1",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="google",
        provider_resource_id="empyralis-agent-computer-google-abcd1234",
        public_ip=None,
        region="us-central1",
        size="e2-medium",
        status="provisioning",
        pairing_token="pair_google_test",
        credentials={"project_id": "my-project", "service_account_email": "sa@my-project.iam.gserviceaccount.com"},
    )

    def fake_http_json(method, url, *, token, payload, provider, **_kwargs):
        if "generateAccessToken" in url:
            return {"accessToken": "impersonated_token_xyz"}
        if url.endswith("/zones"):
            return {"items": [{
                "name": "us-central1-a", "status": "UP",
                "region": "https://compute.googleapis.com/compute/v1/projects/my-project/regions/us-central1",
            }]}
        raise AssertionError(f"unexpected json call: {method} {url}")

    deleted = []

    def fake_http_empty(method, url, *, token, provider, **_kwargs):
        deleted.append((method, url, token))

    monkeypatch.setattr(vps, "_http_json", fake_http_json)
    monkeypatch.setattr(vps, "_http_empty", fake_http_empty)

    result = vps.delete_recorded_vps("vps_google_1")

    assert result["status"] == "deleted"
    assert deleted == [(
        "DELETE",
        f"{vps.GOOGLE_CLOUD_COMPUTE_URL}/projects/my-project/zones/us-central1-a/instances/empyralis-agent-computer-google-abcd1234",
        "impersonated_token_xyz",
    )]


# --- Route-level: OAuth start/callback, project list/create, billing,
# bootstrap ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_vps_oauth_start_route_returns_authorize_url(monkeypatch):
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1") as access_mock,
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "create_google_oauth_start",
            return_value={"provider": "google", "oauth_redirect": "https://accounts.google.com/...", "state": "st1"},
        ) as start_mock,
    ):
        response = await routes_gateway.start_google_vps_oauth(
            workspace_id="ws-1", current_user={"user_id": "user-1"}
        )

    assert response["provider"] == "google"
    access_mock.assert_called_once_with({"user_id": "user-1"}, "ws-1", minimum_role="owner")
    start_mock.assert_called_once_with(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")


@pytest.mark.asyncio
async def test_google_vps_oauth_callback_route_redirects_to_hardware_on_success():
    # No popup, no window.opener/postMessage — this route now ALWAYS 303s
    # back to the Hardware page (see _vps_oauth_hardware_redirect_url), the
    # same same-tab redirect pattern every other OAuth connector already
    # uses (routes_connections.py's complete_connection_oauth_callback).
    with (
        patch.object(
            routes_gateway.vps_provisioning_service,
            "complete_google_oauth_callback",
            return_value={"provider": "google", "setup_id": "gsetup_abc", "workspace_id": "ws-1"},
        ),
        # request=None is fine here — _vps_oauth_hardware_redirect_url only
        # touches `request` via _oauth_request_origin, which is patched out
        # below (same pattern as
        # test_routes_connections_oauth_callback.py's OAuth tests).
        patch.object(routes_gateway, "_oauth_request_origin", return_value="https://app.example.com"),
    ):
        response = await routes_gateway.complete_google_vps_oauth(
            request=None, code="auth_code", state="state_token"
        )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlsplit(location)
    assert parsed.path == "/w/ws-1/hardware"
    query = parse_qs(parsed.query)
    assert query["vps_oauth_provider"] == ["google"]
    assert query["vps_oauth"] == ["google"]
    assert query["setup_id"] == ["gsetup_abc"]


@pytest.mark.asyncio
async def test_google_vps_oauth_callback_route_surfaces_cancellation_as_redirect():
    # Used to 400 with a blank/plain-text popup page — now redirects back to
    # Hardware with the error in the query string so the panel can show it,
    # never stranding the user on a dead-end response.
    with patch.object(routes_gateway, "_oauth_request_origin", return_value="https://app.example.com"):
        response = await routes_gateway.complete_google_vps_oauth(request=None, error="access_denied")

    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["vps_oauth_provider"] == ["google"]
    assert "access_denied" in query["vps_oauth_error"][0] or "cancelled" in query["vps_oauth_error"][0]


@pytest.mark.asyncio
async def test_digitalocean_vps_oauth_callback_route_redirects_to_hardware_on_success():
    with (
        patch.object(
            routes_gateway.vps_provisioning_service,
            "complete_digitalocean_oauth_callback",
            return_value={"provider": "digitalocean", "token_id": "vps_token_abc", "workspace_id": "ws-1"},
        ),
        patch.object(routes_gateway, "_oauth_request_origin", return_value="https://app.example.com"),
    ):
        response = await routes_gateway.complete_digitalocean_vps_oauth(
            request=None, code="auth_code", state="state_token"
        )

    assert response.status_code == 303
    parsed = urlsplit(response.headers["location"])
    assert parsed.path == "/w/ws-1/hardware"
    query = parse_qs(parsed.query)
    assert query["vps_oauth_provider"] == ["digitalocean"]
    assert query["vps_oauth"] == ["digitalocean"]
    assert query["token_id"] == ["vps_token_abc"]


@pytest.mark.asyncio
async def test_digitalocean_vps_oauth_callback_route_surfaces_cancellation_as_redirect():
    with patch.object(routes_gateway, "_oauth_request_origin", return_value="https://app.example.com"):
        response = await routes_gateway.complete_digitalocean_vps_oauth(request=None, error="access_denied")

    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["vps_oauth_provider"] == ["digitalocean"]
    assert "access_denied" in query["vps_oauth_error"][0] or "cancelled" in query["vps_oauth_error"][0]


@pytest.mark.asyncio
async def test_digitalocean_vps_oauth_callback_route_duplicate_hit_redirects_not_400(tmp_path, monkeypatch):
    # End-to-end regression test for the reported bug: the popup +
    # same-tab-fallback both hitting /callback for one click used to produce
    # one 200 (token stored) followed by 400s on the duplicate hits, and the
    # 400 rendered a blank popup-only page with nowhere for a same-tab
    # browser to go. Drives the REAL service function (not mocked) through
    # the route twice with the same code/state to prove the second hit now
    # redirects to the same success destination instead of erroring.
    _isolate_vps_state(tmp_path, monkeypatch)
    _digitalocean_env(monkeypatch)
    start = vps.create_digitalocean_oauth_start(workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1")
    monkeypatch.setattr(vps, "_http_form_json", lambda *a, **kw: {
        "access_token": "do_access_token", "refresh_token": "do_refresh_token", "expires_in": 2592000,
    })

    with patch.object(routes_gateway, "_oauth_request_origin", return_value="https://app.example.com"):
        first_response = await routes_gateway.complete_digitalocean_vps_oauth(
            request=None, code="auth_code_123", state=start["state"]
        )
        second_response = await routes_gateway.complete_digitalocean_vps_oauth(
            request=None, code="auth_code_123", state=start["state"]
        )

    for response in (first_response, second_response):
        assert response.status_code == 303
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["vps_oauth_provider"] == ["digitalocean"]
        assert "vps_oauth_error" not in query
        assert query["vps_oauth"] == ["digitalocean"]
    first_query = parse_qs(urlsplit(first_response.headers["location"]).query)
    second_query = parse_qs(urlsplit(second_response.headers["location"]).query)
    assert first_query["token_id"] == second_query["token_id"]


@pytest.mark.asyncio
async def test_list_google_vps_projects_route():
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "list_google_projects",
            return_value={"projects": [{"project_id": "proj-a", "name": "Project A"}]},
        ) as list_mock,
    ):
        response = await routes_gateway.list_google_vps_projects(
            setup_id="gsetup_abc", workspace_id="ws-1", current_user={"user_id": "user-1"}
        )

    assert response["projects"][0]["project_id"] == "proj-a"
    list_mock.assert_called_once_with("gsetup_abc", workspace_id="ws-1", user_id="user-1")


@pytest.mark.asyncio
async def test_list_google_vps_projects_route_returns_404_for_expired_session():
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(
            routes_gateway.vps_provisioning_service, "list_google_projects", side_effect=KeyError("gsetup_abc")
        ),
    ):
        with pytest.raises(routes_gateway.HTTPException) as exc_info:
            await routes_gateway.list_google_vps_projects(
                setup_id="gsetup_abc", workspace_id="ws-1", current_user={"user_id": "user-1"}
            )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_google_vps_project_route():
    body = routes_gateway.GoogleVPSProjectCreateRequest(
        workspace_id="ws-1", setup_id="gsetup_abc", project_name="My Project"
    )
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "create_google_project",
            return_value={"project_id": "my-project-1234", "name": "My Project"},
        ) as create_mock,
    ):
        response = await routes_gateway.create_google_vps_project(body, current_user={"user_id": "user-1"})

    assert response["project_id"] == "my-project-1234"
    create_mock.assert_called_once_with("gsetup_abc", "My Project", workspace_id="ws-1", user_id="user-1")


@pytest.mark.asyncio
async def test_google_vps_project_billing_route():
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "check_google_project_billing",
            return_value={"project_id": "my-project", "billing_enabled": False, "console_url": "https://console..."},
        ) as billing_mock,
    ):
        response = await routes_gateway.get_google_vps_project_billing(
            "my-project", setup_id="gsetup_abc", workspace_id="ws-1", current_user={"user_id": "user-1"}
        )

    assert response["billing_enabled"] is False
    billing_mock.assert_called_once_with("gsetup_abc", "my-project", workspace_id="ws-1", user_id="user-1")


@pytest.mark.asyncio
async def test_bootstrap_google_vps_route():
    body = routes_gateway.GoogleVPSBootstrapRequest(
        workspace_id="ws-1", setup_id="gsetup_abc", project_id="my-project"
    )
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1") as access_mock,
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "finish_google_bootstrap",
            return_value={
                "provider": "google", "token_id": "vps_token_abc", "project_id": "my-project",
                "service_account_email": "empyralis-provisioner@my-project.iam.gserviceaccount.com",
                "workspace_id": "ws-1",
            },
        ) as bootstrap_mock,
    ):
        response = await routes_gateway.bootstrap_google_vps_project(body, current_user={"user_id": "user-1"})

    assert response["token_id"] == "vps_token_abc"
    access_mock.assert_called_once_with({"user_id": "user-1"}, "ws-1", minimum_role="owner")
    bootstrap_mock.assert_called_once_with(
        "gsetup_abc", "my-project", workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
    )


@pytest.mark.asyncio
async def test_bootstrap_google_vps_route_surfaces_billing_not_linked_message():
    # The frontend gates the bootstrap button on a confirmed billing check
    # (see the 'google-billing' step), so this VPSProvisioningError path is a
    # backstop for a race (billing detached between the check and the click).
    # It maps to 502 like every other provider-side VPSProvisioningError on
    # this router, and — critically — the actionable "attach a billing
    # account" message reaches the user in the detail field.
    body = routes_gateway.GoogleVPSBootstrapRequest(
        workspace_id="ws-1", setup_id="gsetup_abc", project_id="my-project"
    )
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "finish_google_bootstrap",
            side_effect=routes_gateway.vps_provisioning_service.VPSProvisioningError(
                "Attach a billing account to this Google Cloud project before continuing: https://console..."
            ),
        ),
    ):
        with pytest.raises(routes_gateway.HTTPException) as exc_info:
            await routes_gateway.bootstrap_google_vps_project(body, current_user={"user_id": "user-1"})

    assert exc_info.value.status_code == 502
    assert "billing" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_create_hardware_vps_token_route_rejects_google():
    body = routes_gateway.HardwareVPSTokenRequest(
        workspace_id="ws-1", provider="google", credentials={"api_token": "should-not-work"}
    )
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
    ):
        with pytest.raises(routes_gateway.HTTPException) as exc_info:
            await routes_gateway.create_hardware_vps_token(body, current_user={"user_id": "user-1"})

    assert exc_info.value.status_code == 400
    assert "Sign in with Google" in exc_info.value.detail


@pytest.mark.asyncio
async def test_hardware_vps_plans_route_requires_connected_account_for_google_without_token():
    with pytest.raises(routes_gateway.HTTPException) as exc_info:
        await routes_gateway.get_hardware_vps_plans(
            "google", token_id=None, workspace_id=None, current_user={"user_id": "user-1"}
        )

    assert exc_info.value.status_code == 400


# =============================================================================
# AWS: CloudFormation cross-account IAM role connect flow
# =============================================================================


def test_normalize_provider_resolves_aws_aliases():
    assert vps._normalize_provider("aws") == "aws"
    assert vps._normalize_provider("AWS") == "aws"
    assert vps._normalize_provider("amazon") == "aws"
    assert vps._normalize_provider("amazon-web-services") == "aws"
    assert vps._normalize_provider("ec2") == "aws"


def test_aws_role_arn_for_account_uses_fixed_role_name_convention():
    assert (
        vps.aws_role_arn_for_account("123456789012")
        == "arn:aws:iam::123456789012:role/EmpyralisVPSProvisioner"
    )
    with pytest.raises(ValueError):
        vps.aws_role_arn_for_account("12345")
    with pytest.raises(ValueError):
        vps.aws_role_arn_for_account("12345678901a")


def test_store_vps_provider_token_rejects_aws():
    # AWS connects via create_aws_connect_intent/confirm_aws_connection, not
    # a pasted token — the generic token route must refuse it outright
    # rather than silently mis-storing a role_arn as though it were a
    # bearer secret.
    with pytest.raises(vps.VPSProvisioningError, match="CloudFormation"):
        vps.store_vps_provider_token(
            provider="aws",
            workspace_id="ws-1",
            tenant_id="tenant-1",
            user_id="user-1",
            credentials={"role_arn": _AWS_ROLE_ARN},
        )


# --- create_aws_connect_intent: ExternalId + Quick-Create URL generation ----


def test_create_aws_connect_intent_generates_external_id_and_quick_create_url(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("EMPYRALIS_AWS_ACCOUNT_ID", "999999999999")
    monkeypatch.setenv(
        "EMPYRALIS_AWS_CFN_TEMPLATE_URL",
        "https://empyralis-templates.s3.amazonaws.com/empyralis-vps-role.yaml",
    )

    result = vps.create_aws_connect_intent(
        workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
    )

    assert result["provider"] == "aws"
    assert result["account_id"] == "123456789012"
    assert result["role_arn"] == "arn:aws:iam::123456789012:role/EmpyralisVPSProvisioner"
    assert result["role_name"] == vps.AWS_CROSS_ACCOUNT_ROLE_NAME
    assert result["empyralis_account_id"] == "999999999999"
    assert result["connection_id"].startswith("vps_aws_pending_")

    # A real UUID4 — not just any random-looking string.
    assert str(uuid.UUID(result["external_id"])) == result["external_id"]

    quick_create_url = result["quick_create_url"]
    assert quick_create_url.startswith(
        "https://us-east-1.console.aws.amazon.com/cloudformation/home?region=us-east-1#"
    )
    fragment = quick_create_url.split("#", 1)[1]
    assert fragment.startswith("/stacks/create/review?")
    query = parse_qs(urlsplit(fragment).query)
    assert query["templateURL"] == ["https://empyralis-templates.s3.amazonaws.com/empyralis-vps-role.yaml"]
    assert query["stackName"] == ["empyralis-vps"]
    assert query["param_ExternalId"] == [result["external_id"]]
    assert query["param_EmpyralisAccountId"] == ["999999999999"]


def test_create_aws_connect_intent_rejects_malformed_account_id(monkeypatch):
    monkeypatch.setenv("EMPYRALIS_AWS_ACCOUNT_ID", "999999999999")
    monkeypatch.setenv("EMPYRALIS_AWS_CFN_TEMPLATE_URL", "https://example.com/template.yaml")

    with pytest.raises(ValueError):
        vps.create_aws_connect_intent(
            workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="not-an-account-id"
        )
    with pytest.raises(ValueError):
        vps.create_aws_connect_intent(
            workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="12345"
        )


def test_create_aws_connect_intent_fails_gracefully_without_account_id_env(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.delenv("EMPYRALIS_AWS_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("EMPYRALIS_AWS_CFN_TEMPLATE_URL", "https://example.com/template.yaml")

    with pytest.raises(vps.VPSProvisioningError, match="EMPYRALIS_AWS_ACCOUNT_ID"):
        vps.create_aws_connect_intent(
            workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
        )


def test_create_aws_connect_intent_fails_gracefully_without_template_url_env(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setenv("EMPYRALIS_AWS_ACCOUNT_ID", "999999999999")
    monkeypatch.delenv("EMPYRALIS_AWS_CFN_TEMPLATE_URL", raising=False)

    with pytest.raises(vps.VPSProvisioningError, match="EMPYRALIS_AWS_CFN_TEMPLATE_URL"):
        vps.create_aws_connect_intent(
            workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
        )


# --- The AssumeRole wrapper --------------------------------------------------


def test_assume_aws_role_fails_gracefully_without_boto3(monkeypatch):
    monkeypatch.setattr(vps, "_boto3", None)

    with pytest.raises(vps.VPSProvisioningError, match="boto3"):
        vps._assume_aws_role(_AWS_ROLE_ARN, "ext-1")


def test_assume_aws_role_requires_role_arn_and_external_id():
    with pytest.raises(ValueError):
        vps._assume_aws_role("", "ext-1")
    with pytest.raises(ValueError):
        vps._assume_aws_role(_AWS_ROLE_ARN, "")


def test_assume_aws_role_surfaces_missing_empyralis_credentials(monkeypatch):
    class _RaisingStsClient:
        def assume_role(self, **kwargs):
            raise vps._NoCredentialsError()

    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_RaisingStsClient()))

    with pytest.raises(vps.VPSProvisioningError, match="Empyralis's own AWS credentials"):
        vps._assume_aws_role(_AWS_ROLE_ARN, "ext-1")


def test_assume_aws_role_returns_short_lived_session_credentials(monkeypatch):
    sts_client = _FakeStsClient()
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=sts_client))

    creds = vps._assume_aws_role(_AWS_ROLE_ARN, "ext-1")

    assert creds == {
        "aws_access_key_id": "AKIAFAKEFAKEFAKEFAKE",
        "aws_secret_access_key": "fake_secret_access_key",
        "aws_session_token": "fake_session_token",
    }
    assert sts_client.assume_role_calls[0]["RoleArn"] == _AWS_ROLE_ARN
    assert sts_client.assume_role_calls[0]["ExternalId"] == "ext-1"
    assert sts_client.assume_role_calls[0]["RoleSessionName"] == vps.AWS_ROLE_SESSION_NAME


def test_aws_client_requires_role_arn_and_external_id():
    with pytest.raises(ValueError):
        vps._aws_client("ec2", {"role_arn": ""}, region="us-east-1")


# --- confirm_aws_connection: AssumeRole + GetCallerIdentity + storage -------


def test_confirm_aws_connection_assumes_role_and_stores_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("EMPYRALIS_AWS_ACCOUNT_ID", "999999999999")
    monkeypatch.setenv("EMPYRALIS_AWS_CFN_TEMPLATE_URL", "https://example.com/template.yaml")

    intent = vps.create_aws_connect_intent(
        workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
    )

    sts_client = _FakeStsClient(account_id="123456789012")
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=sts_client))

    result = vps.confirm_aws_connection(
        connection_id=intent["connection_id"], workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
    )

    assert result == {"provider": "aws", "token_id": result["token_id"], "account_id": "123456789012"}
    assert result["token_id"].startswith("vps_token_")
    assert sts_client.assume_role_calls[0]["RoleArn"] == "arn:aws:iam::123456789012:role/EmpyralisVPSProvisioner"
    assert sts_client.assume_role_calls[0]["ExternalId"] == intent["external_id"]
    assert sts_client.get_caller_identity_calls == 1

    loaded = vps.load_vps_provider_credentials(
        result["token_id"], provider="aws", workspace_id="ws-1", user_id="user-1"
    )
    assert loaded["role_arn"] == "arn:aws:iam::123456789012:role/EmpyralisVPSProvisioner"
    assert loaded["external_id"] == intent["external_id"]
    assert loaded["account_id"] == "123456789012"

    # The pending intent is consumed on success — a second confirm can't
    # replay it.
    with pytest.raises(KeyError):
        vps.confirm_aws_connection(
            connection_id=intent["connection_id"], workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
        )


def test_confirm_aws_connection_is_retryable_when_role_not_ready_yet(tmp_path, monkeypatch):
    # The customer clicking "Confirm" before actually finishing the
    # CloudFormation stack must be a normal, retryable failure — not one
    # that burns the connection_id.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("EMPYRALIS_AWS_ACCOUNT_ID", "999999999999")
    monkeypatch.setenv("EMPYRALIS_AWS_CFN_TEMPLATE_URL", "https://example.com/template.yaml")

    intent = vps.create_aws_connect_intent(
        workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
    )

    monkeypatch.setattr(
        vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(account_id="123456789012", fail_assume_role=True))
    )
    with pytest.raises(vps.VPSProvisioningError):
        vps.confirm_aws_connection(
            connection_id=intent["connection_id"], workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
        )

    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(account_id="123456789012")))
    result = vps.confirm_aws_connection(
        connection_id=intent["connection_id"], workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
    )
    assert result["provider"] == "aws"


def test_confirm_aws_connection_rejects_caller_identity_account_mismatch(tmp_path, monkeypatch):
    # AssumeRole itself can succeed (role exists, ExternalId matches) while
    # still resolving to the wrong account if the customer mistyped their
    # account id in a way that happens to be assumable — GetCallerIdentity
    # is the independent check that catches this.
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("EMPYRALIS_AWS_ACCOUNT_ID", "999999999999")
    monkeypatch.setenv("EMPYRALIS_AWS_CFN_TEMPLATE_URL", "https://example.com/template.yaml")

    intent = vps.create_aws_connect_intent(
        workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
    )

    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(account_id="000000000099")))

    with pytest.raises(vps.VPSProvisioningError, match="different account"):
        vps.confirm_aws_connection(
            connection_id=intent["connection_id"], workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
        )


def test_confirm_aws_connection_enforces_workspace_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("EMPYRALIS_AWS_ACCOUNT_ID", "999999999999")
    monkeypatch.setenv("EMPYRALIS_AWS_CFN_TEMPLATE_URL", "https://example.com/template.yaml")

    intent = vps.create_aws_connect_intent(
        workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
    )

    with pytest.raises(KeyError):
        vps.confirm_aws_connection(
            connection_id=intent["connection_id"], workspace_id="ws-OTHER", tenant_id="tenant-1", user_id="user-1"
        )


def test_confirm_aws_connection_expires_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))
    monkeypatch.setenv("EMPYRALIS_AWS_ACCOUNT_ID", "999999999999")
    monkeypatch.setenv("EMPYRALIS_AWS_CFN_TEMPLATE_URL", "https://example.com/template.yaml")

    intent = vps.create_aws_connect_intent(
        workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
    )
    with vps._STATE_LOCK:
        state = vps._load_state()
        state["aws_pending"][intent["connection_id"]]["created_at"] = "2000-01-01T00:00:00Z"
        vps._write_state(state)

    with pytest.raises(vps.VPSProvisioningError, match="expired"):
        vps.confirm_aws_connection(
            connection_id=intent["connection_id"], workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
        )


# --- Plan normalization (pure functions) ------------------------------------


def test_normalize_aws_plans_merges_specs_and_pricing():
    instance_types = [
        {"InstanceType": "t3.micro", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 1024}},
        {"InstanceType": "t3.small", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 2048}},
        # No pricing entry below for this one — must be dropped, never
        # returned with a bogus $0/mo price.
        {"InstanceType": "t3.nano", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 1024}},
    ]
    pricing = {"t3.micro": 7.59, "t3.small": 15.18}

    plans = vps._normalize_aws_plans(instance_types, pricing)

    assert [p.slug for p in plans] == ["t3.micro", "t3.small"]
    assert plans[0].price_label == "$7.59/mo"
    assert plans[0].disk_gb == vps._AWS_DEFAULT_ROOT_VOLUME_GB
    assert plans[0].vcpus == 2
    assert plans[0].memory_mb == 1024
    # First plan meeting _mark_recommended's >=2GB fallback threshold.
    assert plans[1].recommended is True
    assert plans[0].recommended is False


def test_normalize_aws_plans_drops_types_below_memory_floor():
    instance_types = [{"InstanceType": "t3.nano", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 512}}]
    pricing = {"t3.nano": 3.8}

    assert vps._normalize_aws_plans(instance_types, pricing) == []


def test_normalize_aws_regions_sorts_and_labels_known_regions():
    items = [{"RegionName": "us-west-2"}, {"RegionName": "us-east-1"}, {"RegionName": "xx-made-up-1"}]

    regions = vps._normalize_aws_regions(items)

    assert [r["id"] for r in regions] == ["us-east-1", "us-west-2", "xx-made-up-1"]
    assert regions[0]["label"] == "US East (N. Virginia)"
    # Unknown region falls back to its own code as the label instead of
    # being dropped — never "available nowhere" just for lacking a
    # human-friendly name in the curated map.
    assert regions[2]["label"] == "xx-made-up-1"


def test_fetch_aws_instance_pricing_falls_back_to_static_table_on_failure(monkeypatch):
    def _boom():
        raise vps.urlerror.URLError("unreachable")

    monkeypatch.setattr(vps, "_fetch_vantage_pricing_raw", _boom)

    assert vps._fetch_aws_instance_pricing() == vps._AWS_STATIC_MONTHLY_PRICE_USD


def test_fetch_aws_instance_pricing_prefers_live_vantage_data(monkeypatch):
    monkeypatch.setattr(
        vps,
        "_fetch_vantage_pricing_raw",
        lambda: [
            {"instance_type": "t3.small", "pricing": {"us-east-1": {"linux": {"ondemand": "0.0208"}}}},
            # Not one of the curated candidate types — must be ignored.
            {"instance_type": "m5.24xlarge", "pricing": {"us-east-1": {"linux": {"ondemand": "4.608"}}}},
        ],
    )

    pricing = vps._fetch_aws_instance_pricing()

    assert pricing == {"t3.small": round(0.0208 * 730, 2)}


# --- Live boto3/STS/EC2-backed fetches --------------------------------------


def test_fetch_aws_regions_uses_assumed_role_ec2_client(monkeypatch):
    sts_client = _FakeStsClient(account_id="123456789012")
    ec2_client = _FakeEc2Client(
        describe_regions_response={"Regions": [{"RegionName": "us-west-2"}, {"RegionName": "eu-west-1"}]}
    )
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=sts_client, ec2=ec2_client))

    regions = vps._fetch_aws_regions(_AWS_CREDENTIALS)

    assert [r["id"] for r in regions] == ["eu-west-1", "us-west-2"]
    assert sts_client.assume_role_calls[0]["ExternalId"] == "ext-test-id"
    assert ec2_client.describe_regions_calls == 1


def test_fetch_aws_region_ids_never_raises(monkeypatch):
    class _FailingEc2(_FakeEc2Client):
        def describe_regions(self, **kwargs):
            raise vps._ClientError({"Error": {"Code": "Throttling", "Message": "slow down"}}, "DescribeRegions")

    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(), ec2=_FailingEc2()))

    assert vps._fetch_aws_region_ids(_AWS_CREDENTIALS) == set()


def test_fetch_aws_plans_end_to_end_with_live_pricing(monkeypatch):
    sts_client = _FakeStsClient(account_id="123456789012")
    ec2_client = _FakeEc2Client(
        describe_instance_types_response={
            "InstanceTypes": [{"InstanceType": "t3.small", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 2048}}]
        }
    )
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=sts_client, ec2=ec2_client))
    monkeypatch.setattr(
        vps,
        "_fetch_vantage_pricing_raw",
        lambda: [{"instance_type": "t3.small", "pricing": {"us-east-1": {"linux": {"ondemand": "0.0208"}}}}],
    )

    plans = vps._fetch_aws_plans(_AWS_CREDENTIALS)

    assert len(plans) == 1
    assert plans[0].slug == "t3.small"
    assert plans[0].price_monthly == round(0.0208 * 730, 2)
    # DescribeInstanceTypes was scoped to the curated candidate list, not a
    # full, unbounded enumeration of every EC2 instance type.
    assert set(ec2_client.describe_instance_types_calls[0]["InstanceTypes"]) == set(vps._AWS_CANDIDATE_INSTANCE_TYPES)


# --- Provisioning / deletion -------------------------------------------------


def test_provision_aws_creates_instance_with_resolved_ami_network_and_disk(monkeypatch):
    sts_client = _FakeStsClient(account_id="123456789012")
    ec2_client = _FakeEc2Client(
        describe_images_response={
            "Images": [
                _fake_aws_image("ami-old", "2025-01-01T00:00:00.000Z", "/dev/sda1"),
                _fake_aws_image("ami-new", "2026-06-01T00:00:00.000Z", "/dev/xvda"),
            ]
        },
        describe_vpcs_response={"Vpcs": [{"VpcId": "vpc-1", "IsDefault": True}]},
        describe_subnets_response={"Subnets": [{"SubnetId": "subnet-1"}]},
        describe_security_groups_response={"SecurityGroups": []},
        create_security_group_response={"GroupId": "sg-1"},
        run_instances_response={"Instances": [{"InstanceId": "i-0123456789abcdef0", "PublicIpAddress": "198.51.100.5"}]},
    )
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=sts_client, ec2=ec2_client))

    result = vps._provision_aws(
        _AWS_CREDENTIALS, "us-west-2", "t3.small", "empyralis-agent-computer-aws-abcd", "#cloud-config\nfoo"
    )

    assert result.provider == "aws"
    assert result.provider_resource_id == "i-0123456789abcdef0"
    assert result.public_ip == "198.51.100.5"
    assert result.region == "us-west-2"
    assert result.size == "t3.small"

    run_call = ec2_client.run_instances_calls[0]
    assert run_call["ImageId"] == "ami-new"  # the NEWEST match, not just the first returned
    assert run_call["InstanceType"] == "t3.small"
    assert run_call["UserData"] == "#cloud-config\nfoo"  # plain text — botocore base64-encodes it, not us
    assert run_call["BlockDeviceMappings"][0]["DeviceName"] == "/dev/xvda"  # from the AMI itself, not hardcoded
    assert run_call["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == vps._AWS_DEFAULT_ROOT_VOLUME_GB
    assert run_call["NetworkInterfaces"][0]["SubnetId"] == "subnet-1"
    assert run_call["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is True
    assert run_call["NetworkInterfaces"][0]["Groups"] == ["sg-1"]
    assert run_call["KeyName"] == vps._AWS_KEY_PAIR_NAME
    # A security group had to be created (none existed) — and given an
    # ingress rule, since create_security_group_response was reached at all.
    assert ec2_client.create_security_group_calls
    assert ec2_client.authorize_security_group_ingress_calls[0]["GroupId"] == "sg-1"

    # boto3.client("ec2", ...) was built with the *box's* region, not aws's
    # module-level default_region — us-east-1 in PROVIDER_CONFIGS.
    fake_boto3 = vps._boto3
    ec2_client_call = next(c for c in fake_boto3.client_calls if c["service_name"] == "ec2")
    assert ec2_client_call["region_name"] == "us-west-2"


def test_provision_aws_omits_key_name_when_key_pair_management_fails(monkeypatch):
    class _NoKeyPairEc2(_FakeEc2Client):
        def create_key_pair(self, **kwargs):
            raise vps._ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}}, "CreateKeyPair")

    ec2_client = _NoKeyPairEc2(
        describe_images_response={"Images": [_fake_aws_image()]},
        describe_vpcs_response={"Vpcs": [{"VpcId": "vpc-1"}]},
        describe_subnets_response={"Subnets": [{"SubnetId": "subnet-1"}]},
        describe_security_groups_response={"SecurityGroups": [{"GroupId": "sg-1"}]},
        run_instances_response={"Instances": [{"InstanceId": "i-nokeypair"}]},
    )
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(), ec2=ec2_client))

    result = vps._provision_aws(_AWS_CREDENTIALS, "us-east-1", "t3.micro", "name", "#cloud-config\n")

    assert result.provider_resource_id == "i-nokeypair"
    assert "KeyName" not in ec2_client.run_instances_calls[0]


def test_provision_aws_raises_when_no_vpc_available(monkeypatch):
    ec2_client = _FakeEc2Client(
        describe_images_response={"Images": [_fake_aws_image()]},
        describe_vpcs_response={"Vpcs": []},
    )
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(), ec2=ec2_client))

    with pytest.raises(vps.VPSProvisioningError, match="VPC"):
        vps._provision_aws(_AWS_CREDENTIALS, "us-east-1", "t3.micro", "name", "#cloud-config\n")

    assert not ec2_client.run_instances_calls


def test_delete_aws_resource_terminates_instance_in_recorded_region(monkeypatch):
    ec2_client = _FakeEc2Client()
    fake_boto3 = _FakeBoto3(sts=_FakeStsClient(), ec2=ec2_client)
    monkeypatch.setattr(vps, "_boto3", fake_boto3)

    vps._delete_aws_resource(_AWS_CREDENTIALS, "eu-west-1", "i-abc")

    assert ec2_client.terminate_instances_calls == [{"InstanceIds": ["i-abc"]}]
    ec2_client_call = next(c for c in fake_boto3.client_calls if c["service_name"] == "ec2")
    assert ec2_client_call["region_name"] == "eu-west-1"


# --- End to end through the shared provision_vps / delete_recorded_vps -----


def test_provision_vps_aws_end_to_end(monkeypatch):
    ec2_client = _FakeEc2Client(
        describe_images_response={"Images": [_fake_aws_image()]},
        describe_vpcs_response={"Vpcs": [{"VpcId": "vpc-1"}]},
        describe_subnets_response={"Subnets": [{"SubnetId": "subnet-1"}]},
        describe_security_groups_response={"SecurityGroups": [{"GroupId": "sg-1"}]},
        run_instances_response={"Instances": [{"InstanceId": "i-abc123", "PublicIpAddress": "203.0.113.9"}]},
    )
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(), ec2=ec2_client))

    result = vps.provision_vps("aws", _AWS_CREDENTIALS, "us-west-2", "t3.medium", "pair_aws")

    assert result.provider == "aws"
    assert result.provider_resource_id == "i-abc123"
    assert result.public_ip == "203.0.113.9"
    run_call = ec2_client.run_instances_calls[0]
    assert run_call["InstanceType"] == "t3.medium"
    assert "#cloud-config" in run_call["UserData"]
    assert "pair_aws" in run_call["UserData"]


def test_provision_vps_aws_invalid_region_rejected_before_any_aws_call():
    with pytest.raises(ValueError):
        vps.provision_vps("aws", _AWS_CREDENTIALS, "not-a-real-region", None, "pair_aws")


def test_provision_vps_accepts_live_only_aws_region(monkeypatch):
    # "af-south-1" is not in aws's static PROVIDER_CONFIGS regions tuple, but
    # IS returned by the live DescribeRegions call — same defect-#3-shaped
    # guarantee the DigitalOcean live-region tests already pin.
    ec2_client = _FakeEc2Client(
        describe_regions_response={"Regions": [{"RegionName": "af-south-1"}]},
        describe_images_response={"Images": [_fake_aws_image()]},
        describe_vpcs_response={"Vpcs": [{"VpcId": "vpc-1"}]},
        describe_subnets_response={"Subnets": [{"SubnetId": "subnet-1"}]},
        describe_security_groups_response={"SecurityGroups": [{"GroupId": "sg-1"}]},
        run_instances_response={"Instances": [{"InstanceId": "i-live-region"}]},
    )
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(), ec2=ec2_client))

    result = vps.provision_vps(
        "aws", _AWS_CREDENTIALS, "af-south-1", None, "pair_aws", token_id="vps_token_live_aws"
    )

    assert result.provider_resource_id == "i-live-region"


def test_provision_vps_aws_still_accepts_static_region_when_live_fetch_fails(monkeypatch):
    class _FailingRegionsEc2(_FakeEc2Client):
        def describe_regions(self, **kwargs):
            raise vps._ClientError({"Error": {"Code": "Throttling", "Message": "slow down"}}, "DescribeRegions")

    ec2_client = _FailingRegionsEc2(
        describe_images_response={"Images": [_fake_aws_image()]},
        describe_vpcs_response={"Vpcs": [{"VpcId": "vpc-1"}]},
        describe_subnets_response={"Subnets": [{"SubnetId": "subnet-1"}]},
        describe_security_groups_response={"SecurityGroups": [{"GroupId": "sg-1"}]},
        run_instances_response={"Instances": [{"InstanceId": "i-static-region"}]},
    )
    monkeypatch.setattr(vps, "_boto3", _FakeBoto3(sts=_FakeStsClient(), ec2=ec2_client))

    result = vps.provision_vps(
        "aws", _AWS_CREDENTIALS, "us-east-1", None, "pair_aws", token_id="vps_token_live_aws"
    )

    assert result.provider_resource_id == "i-static-region"


def test_delete_recorded_vps_terminates_aws_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(vps, "VPS_STATE_FILE", tmp_path / "vps.json")
    monkeypatch.setattr(vps.vault_store, "_openssl_encrypt", lambda text: f"enc:{text}")
    monkeypatch.setattr(vps.vault_store, "_openssl_decrypt", lambda text: text.removeprefix("enc:"))

    vps.record_vps_provision(
        vps_id="vps_aws_1",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="aws",
        provider_resource_id="i-0123456789abcdef0",
        public_ip="198.51.100.5",
        region="eu-west-1",
        size="t3.small",
        status="provisioning",
        pairing_token="pair_aws",
        credentials=_AWS_CREDENTIALS,
    )

    ec2_client = _FakeEc2Client()
    fake_boto3 = _FakeBoto3(sts=_FakeStsClient(), ec2=ec2_client)
    monkeypatch.setattr(vps, "_boto3", fake_boto3)

    result = vps.delete_recorded_vps("vps_aws_1")

    assert result["status"] == "deleted"
    assert ec2_client.terminate_instances_calls == [{"InstanceIds": ["i-0123456789abcdef0"]}]
    ec2_client_call = next(c for c in fake_boto3.client_calls if c["service_name"] == "ec2")
    assert ec2_client_call["region_name"] == "eu-west-1"


@pytest.mark.asyncio
async def test_hardware_vps_plans_route_requires_connected_account_for_aws_without_token():
    # Unlike Vultr, AWS has no pre-connect public plan catalog (first pass —
    # DescribeInstanceTypes needs an assumed role) — no token_id must behave
    # exactly like DigitalOcean/Hetzner's equivalent guard.
    with pytest.raises(routes_gateway.HTTPException) as exc_info:
        await routes_gateway.get_hardware_vps_plans(
            "aws", token_id=None, workspace_id=None, current_user={"user_id": "user-1"}
        )

    assert exc_info.value.status_code == 400


# --- Routes: /hardware/vps/aws/connect and /hardware/vps/aws/confirm -------


@pytest.mark.asyncio
async def test_start_aws_vps_connect_route_enforces_owner_access_and_returns_intent():
    current_user = {"user_id": "user-1"}
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1") as access_mock,
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "create_aws_connect_intent",
            return_value={
                "provider": "aws",
                "connection_id": "vps_aws_pending_1",
                "quick_create_url": "https://example.com",
            },
        ) as intent_mock,
    ):
        body = routes_gateway.HardwareVPSAwsConnectRequest(workspace_id="ws-1", account_id="123456789012")
        response = await routes_gateway.start_aws_vps_connect(body, current_user=current_user)

    assert response["connection_id"] == "vps_aws_pending_1"
    access_mock.assert_called_once_with(current_user, "ws-1", minimum_role="owner")
    intent_mock.assert_called_once_with(
        workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1", account_id="123456789012"
    )


@pytest.mark.asyncio
async def test_start_aws_vps_connect_route_maps_invalid_account_id_to_400():
    current_user = {"user_id": "user-1"}
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "create_aws_connect_intent",
            side_effect=ValueError("AWS account id must be exactly 12 digits."),
        ),
    ):
        body = routes_gateway.HardwareVPSAwsConnectRequest(workspace_id="ws-1", account_id="not-valid")
        with pytest.raises(routes_gateway.HTTPException) as exc_info:
            await routes_gateway.start_aws_vps_connect(body, current_user=current_user)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_start_aws_vps_connect_route_maps_unconfigured_backend_to_500():
    current_user = {"user_id": "user-1"}
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "create_aws_connect_intent",
            side_effect=vps.VPSProvisioningError("AWS is not configured on this backend."),
        ),
    ):
        body = routes_gateway.HardwareVPSAwsConnectRequest(workspace_id="ws-1", account_id="123456789012")
        with pytest.raises(routes_gateway.HTTPException) as exc_info:
            await routes_gateway.start_aws_vps_connect(body, current_user=current_user)

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_confirm_aws_vps_connect_route_maps_expired_intent_to_404():
    current_user = {"user_id": "user-1"}
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "confirm_aws_connection",
            side_effect=KeyError("vps_aws_pending_missing"),
        ),
    ):
        body = routes_gateway.HardwareVPSAwsConfirmRequest(workspace_id="ws-1", connection_id="vps_aws_pending_missing")
        with pytest.raises(routes_gateway.HTTPException) as exc_info:
            await routes_gateway.confirm_aws_vps_connect(body, current_user=current_user)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_confirm_aws_vps_connect_route_maps_role_not_ready_to_502():
    current_user = {"user_id": "user-1"}
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1"),
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "confirm_aws_connection",
            side_effect=vps.VPSProvisioningError("Could not assume the Empyralis VPS role."),
        ),
    ):
        body = routes_gateway.HardwareVPSAwsConfirmRequest(workspace_id="ws-1", connection_id="vps_aws_pending_1")
        with pytest.raises(routes_gateway.HTTPException) as exc_info:
            await routes_gateway.confirm_aws_vps_connect(body, current_user=current_user)

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_confirm_aws_vps_connect_route_returns_token_on_success():
    current_user = {"user_id": "user-1"}
    with (
        patch.object(routes_gateway, "enforce_workspace_access", return_value="ws-1") as access_mock,
        patch.object(routes_gateway, "workspace_tenant_id", return_value="tenant-1"),
        patch.object(
            routes_gateway.vps_provisioning_service,
            "confirm_aws_connection",
            return_value={"provider": "aws", "token_id": "vps_token_abc", "account_id": "123456789012"},
        ) as confirm_mock,
    ):
        body = routes_gateway.HardwareVPSAwsConfirmRequest(workspace_id="ws-1", connection_id="vps_aws_pending_1")
        response = await routes_gateway.confirm_aws_vps_connect(body, current_user=current_user)

    assert response["token_id"] == "vps_token_abc"
    access_mock.assert_called_once_with(current_user, "ws-1", minimum_role="owner")
    confirm_mock.assert_called_once_with(
        connection_id="vps_aws_pending_1", workspace_id="ws-1", tenant_id="tenant-1", user_id="user-1"
    )


# --- deploy/aws/empyralis-vps-role.yaml <-> Python constant consistency ----


def test_aws_cloudformation_template_role_name_matches_constant():
    import yaml

    template_path = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "aws" / "empyralis-vps-role.yaml"

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_multi_constructor("!", lambda loader, tag_suffix, node: None)

    with template_path.open("r", encoding="utf-8") as handle:
        template = yaml.load(handle, Loader=_Loader)

    role_properties = template["Resources"]["EmpyralisVPSProvisionerRole"]["Properties"]
    assert role_properties["RoleName"] == vps.AWS_CROSS_ACCOUNT_ROLE_NAME

    # Every IAM action _provision_aws/_fetch_aws_plans/_fetch_aws_regions/
    # _delete_aws_resource actually call must be covered by the template's
    # inline policy — catches the policy and the implementation silently
    # drifting apart from each other.
    statements = role_properties["Policies"][0]["PolicyDocument"]["Statement"]
    granted_actions = {action for statement in statements for action in statement["Action"]}
    required_actions = {
        "ec2:RunInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstanceTypes",
        "ec2:DescribeRegions",
        "ec2:DescribeImages",
        "ec2:CreateTags",
        "ec2:DescribeKeyPairs",
        "ec2:CreateKeyPair",
        "ec2:ImportKeyPair",
        "ec2:DescribeSecurityGroups",
        "ec2:CreateSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
    }
    assert required_actions.issubset(granted_actions)
