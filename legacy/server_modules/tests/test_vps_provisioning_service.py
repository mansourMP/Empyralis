import base64
from unittest.mock import patch

import pytest

from server_modules import routes_gateway
from server_modules import vps_provisioning_service as vps


def test_cloud_init_script_runs_agent_computer_installer():
    script = vps.cloud_init_script("pair_test", api_url="https://api.example.com")

    assert script.startswith("#cloud-config")
    assert "curl -fsSL https://empyralis.ai/install/agent-computer.sh" in script
    assert "EMPYRALIS_PAIRING_TOKEN='pair_test'" in script
    assert "EMPYRALIS_API_URL='https://api.example.com'" in script
    assert "sudo -E bash" in script


def test_cloud_init_script_allows_installer_url_env_override(monkeypatch):
    monkeypatch.setenv(vps.AGENT_INSTALLER_URL_ENV, "https://empyralis.ai/install/agent-computer.sh")

    script = vps.cloud_init_script("pair_test", api_url="https://api.example.com")

    assert "curl -fsSL https://empyralis.ai/install/agent-computer.sh" in script


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
    assert "scope=read+write" in result["oauth_redirect"]
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

    def fake_http_json(method, url, *, token, payload, provider):
        assert method == "GET"
        assert url == "https://api.digitalocean.com/v2/sizes"
        assert token == "do_secret"
        return {
            "sizes": [
                {"slug": "tiny", "vcpus": 1, "memory": 512, "disk": 10, "price_monthly": 4, "available": True},
                {"slug": "s-1vcpu-2gb", "vcpus": 1, "memory": 2048, "disk": 50, "price_monthly": 12, "available": True},
                {"slug": "s-2vcpu-4gb", "vcpus": 2, "memory": 4096, "disk": 80, "price_monthly": 24, "available": True},
            ]
        }

    monkeypatch.setattr(vps, "_http_json", fake_http_json)

    result = vps.fetch_provider_plans("digitalocean", token_id=token_id, workspace_id="ws-1", user_id="user-1")

    assert [plan["slug"] for plan in result["plans"]] == ["s-1vcpu-2gb", "s-2vcpu-4gb"]
    assert result["plans"][1]["recommended"] is True


def test_digitalocean_provisioning_payload_uses_curated_region(monkeypatch):
    calls = []

    def fake_http_json(method, url, *, token, payload, provider):
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
    monkeypatch.setattr(vps, "_http_empty", lambda method, url, *, token, provider: deleted.append((method, url, token, provider)))

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
async def test_hardware_vps_regions_route_returns_curated_provider_list():
    response = await routes_gateway.get_hardware_vps_regions("vultr", current_user={"user_id": "user-1"})

    assert response["provider"] == "vultr"
    assert response["default_region"] == "ewr"
    assert response["default_size"] == "vc2-1c-2gb"
    assert [item["id"] for item in response["regions"]] == ["ewr", "lhr", "fra", "sgp", "syd"]


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
