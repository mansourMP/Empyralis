from __future__ import annotations

import hashlib
import hmac
import importlib
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException

import server_modules.auth as auth_module
import server_modules.channel_user_acquisition_service as channel_user_acquisition_service_module
import server_modules.control_plane_repository as control_plane_repository_module
import server_modules.db as db_module
import server_modules.jwt_secret as jwt_secret_module
import server_modules.routes_auth as routes_auth_module


def _reload_auth(monkeypatch: pytest.MonkeyPatch, tmp_path, extra_env: dict[str, str] | None = None):
    global auth_module
    global channel_user_acquisition_service_module
    global control_plane_repository_module
    global db_module
    global jwt_secret_module
    global routes_auth_module

    state_home = tmp_path / "state"
    monkeypatch.setenv("EMPYRALIS_STATE_HOME", str(state_home))
    monkeypatch.delenv("EMPYRALIS_JWT_SECRET_FILE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in ("ORION_JWT_SECRET", "JWT_SECRET", "ORION_API_KEY", "RUNTIME_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)

    db_module = importlib.import_module("server_modules.db")
    control_plane_repository_module = importlib.import_module("server_modules.control_plane_repository")
    jwt_secret_module = importlib.import_module("server_modules.jwt_secret")
    auth_module = importlib.import_module("server_modules.auth")
    channel_user_acquisition_service_module = importlib.import_module("server_modules.channel_user_acquisition_service")
    routes_auth_module = importlib.import_module("server_modules.routes_auth")

    runtime_db = importlib.reload(db_module)
    runtime_db._POOLS_BY_LOOP.clear()
    runtime_db._ENV_DSN_LOADED = True
    importlib.reload(control_plane_repository_module)
    jwt_secret = importlib.reload(jwt_secret_module)
    auth = importlib.reload(auth_module)
    auth.USER_RATE_LIMIT_BUCKETS.clear()
    auth.LOGIN_RATE_LIMIT_BUCKETS.clear()
    return auth, jwt_secret, state_home


def _expired_token(auth) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": "user-expired",
        "email": "expired@example.com",
        "workspace_ids": ["default"],
        "iat": 1,
        "exp": 2,
    }
    header_segment = auth._b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = auth._b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    signature = hmac.new(auth._jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_segment}.{payload_segment}.{auth._b64url_encode(signature)}"


class _Request:
    headers = {}
    client = None


def test_jwt_stable_across_restart(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, jwt_secret, state_home = _reload_auth(monkeypatch, tmp_path)
    first_secret = auth._jwt_secret()

    auth, jwt_secret, _ = _reload_auth(monkeypatch, tmp_path)
    second_secret = auth._jwt_secret()

    assert first_secret == second_secret
    assert jwt_secret.JWT_SECRET_FILE == state_home / "auth" / "jwt_secret"
    assert jwt_secret.JWT_SECRET_FILE.exists()


def test_google_oauth_flow(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)

    # The browser OAuth callback lives outside server_modules; this verifies the
    # persisted downstream contract it depends on: a durable user record plus a
    # valid platform JWT for the signed-in identity.
    created = auth.register_user("google.user@example.com", "password-123", name="Google User")

    payload = auth._decode_token_payload(created["token"])
    assert payload["sub"] == created["user"]["id"]
    assert payload["email"] == "google.user@example.com"
    assert auth.verify_token(created["token"]) == created["user"]["id"]


def test_google_identity_claims_fail_closed_without_runtime_audience(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    for key in (
        "GOOGLE_AUDIENCES",
        "GOOGLE_AUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_WEB_CLIENT_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        auth._validate_external_identity_claims(
            "google",
            {
                "iss": "https://accounts.google.com",
                "aud": "web-client.apps.googleusercontent.com",
                "sub": "google-user-1",
                "email": "user@example.com",
                "email_verified": True,
                "exp": 4_102_444_800,
            },
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Google authentication is not configured."


def test_google_identity_claims_reject_missing_exp(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path, {"GOOGLE_CLIENT_ID": "web-client.apps.googleusercontent.com"})

    with pytest.raises(HTTPException) as exc_info:
        auth._validate_external_identity_claims(
            "google",
            {
                "iss": "https://accounts.google.com",
                "aud": "web-client.apps.googleusercontent.com",
                "sub": "google-user-1",
                "email": "user@example.com",
                "email_verified": True,
            },
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Identity token expiration is missing."


def test_provider_jwks_fetch_retries_then_caches(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    auth.PROVIDER_JWKS_CACHE.clear()
    auth.PROVIDER_JWKS_RETRY_ATTEMPTS = 2
    calls = {"count": 0}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"keys": [{"kid": "kid-1"}]}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary")
        return Response()

    monkeypatch.setattr(auth.requests, "get", fake_get)
    payload = auth._fetch_provider_jwks("google-test", "https://example.test/jwks")

    assert payload == {"keys": [{"kid": "kid-1"}]}
    assert calls["count"] == 2
    assert "google-test" in auth.PROVIDER_JWKS_CACHE


def test_provider_jwks_fetch_uses_stale_cache_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    auth.PROVIDER_JWKS_CACHE.clear()
    auth.PROVIDER_JWKS_CACHE["google-test"] = {
        "payload": {"keys": [{"kid": "stale"}]},
        "expires_at": 0,
        "stale_until": auth.time.time() + 60,
    }
    monkeypatch.setattr(auth.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    payload = auth._fetch_provider_jwks("google-test", "https://example.test/jwks")

    assert payload == {"keys": [{"kid": "stale"}]}


def test_provider_jwks_fetch_failure_returns_503(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    auth.PROVIDER_JWKS_CACHE.clear()
    auth.PROVIDER_JWKS_RETRY_ATTEMPTS = 1
    monkeypatch.setattr(auth.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    with pytest.raises(HTTPException) as exc_info:
        auth._fetch_provider_jwks("google-test", "https://example.test/jwks")

    assert exc_info.value.status_code == 503


def test_google_identity_claims_accept_configured_runtime_audience(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(
        monkeypatch,
        tmp_path,
        {"GOOGLE_AUDIENCES": "web-client.apps.googleusercontent.com"},
    )

    subject, email, name, avatar_url = auth._validate_external_identity_claims(
        "google",
        {
            "iss": "https://accounts.google.com",
            "aud": "web-client.apps.googleusercontent.com",
            "sub": "google-user-1",
            "email": "user@example.com",
            "email_verified": True,
            "name": "Google User",
            "picture": "https://example.com/avatar.png",
            "exp": 4_102_444_800,
        },
    )

    assert subject == "google-user-1"
    assert email == "user@example.com"
    assert name == "Google User"
    assert avatar_url == "https://example.com/avatar.png"


def test_login_external_user_creates_mobile_session(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(auth.entitlements_service, "enforce_mobile_app_access", lambda **kwargs: kwargs.get("workspace"))

    logged_in = auth.login_external_user(
        provider="apple",
        subject="apple-user-123",
        email="apple.user@example.com",
        name="Apple User",
        channel="mobile",
        device_id="device-apple-1",
        device_name="iPhone",
        device_platform="ios",
    )

    assert logged_in["user"]["email"] == "apple.user@example.com"
    assert auth.verify_token(logged_in["token"]) == logged_in["user"]["id"]
    assert logged_in["auth_session"]["channel"] == "mobile"
    assert any(
        item["method_type"] == "sso" and item["provider"] == "apple"
        for item in logged_in["identity_boundary"]["auth_methods"]
    )


def test_login_external_user_reuses_subject_without_email(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(auth.entitlements_service, "enforce_mobile_app_access", lambda **kwargs: kwargs.get("workspace"))

    first = auth.login_external_user(
        provider="apple",
        subject="apple-user-repeat",
        email="repeat.apple@example.com",
        name="Repeat Apple",
        channel="mobile",
    )
    second = auth.login_external_user(
        provider="apple",
        subject="apple-user-repeat",
        channel="mobile",
    )

    assert second["user"]["id"] == first["user"]["id"]
    assert second["user"]["email"] == "repeat.apple@example.com"


def test_register_and_login_emit_security_audit(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(
        auth.security_audit_service,
        "emit_security_audit_event",
        lambda **kwargs: emitted.append(kwargs) or None,
    )

    created = auth.register_user("audit@example.com", "password-123", name="Audit User")
    logged_in = auth.login_user("audit@example.com", "password-123")

    assert created["user"]["email"] == "audit@example.com"
    assert logged_in["user"]["email"] == "audit@example.com"
    assert [item["action"] for item in emitted] == ["auth.register", "auth.login"]


def test_register_user_binds_channel_attribution_token(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    acquisition_service = channel_user_acquisition_service_module.get_channel_user_acquisition_service()
    response = acquisition_service.prepare_public_start_response(
        profile={
            "public_start": {
                "enabled": True,
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "deployed_agent_id": "dagent-1",
                "deployed_agent_name": "HealthGuide",
            }
        },
        workspace_id="ws-1",
        external_user_id="telegram-user-1",
    )

    created = auth.register_user(
        "acquisition.register@example.com",
        "password-123",
        name="Acquisition Register",
        acquisition_token=response["attribution_token"],
    )
    touch = acquisition_service.touch_from_attribution_token(response["attribution_token"])

    assert created["channel_attribution"]["converted_user_id"] == created["user"]["id"]
    assert touch["converted_user_id"] == created["user"]["id"]
    assert touch["converted_auth_flow"] == "register"


def test_login_user_binds_channel_attribution_token(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    acquisition_service = channel_user_acquisition_service_module.get_channel_user_acquisition_service()
    auth.register_user("acquisition.login@example.com", "password-123", name="Acquisition Login")
    response = acquisition_service.prepare_public_start_response(
        profile={
            "public_start": {
                "enabled": True,
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
                "deployed_agent_id": "dagent-1",
                "deployed_agent_name": "Returns Concierge",
            }
        },
        workspace_id="ws-1",
        external_user_id="telegram-user-2",
    )

    logged_in = auth.login_user(
        "acquisition.login@example.com",
        "password-123",
        acquisition_token=response["attribution_token"],
    )
    touch = acquisition_service.touch_from_attribution_token(response["attribution_token"])

    assert logged_in["channel_attribution"]["converted_user_id"] == logged_in["user"]["id"]
    assert touch["converted_user_id"] == logged_in["user"]["id"]
    assert touch["converted_auth_flow"] == "login"


def test_session_persistence(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("persist@example.com", "password-123", name="Persisted User")

    auth, _, _ = _reload_auth(monkeypatch, tmp_path)

    assert auth.verify_token(created["token"]) == created["user"]["id"]
    logged_in = auth.login_user("persist@example.com", "password-123")
    assert logged_in["user"]["id"] == created["user"]["id"]


def test_register_user_exposes_empyralis_identity_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("identity@example.com", "password-123", name="Identity User")

    boundary = created["identity_boundary"]
    auth_methods = boundary["auth_methods"]

    assert boundary["account_owner"] == "empyralis"
    assert boundary["account_id"] == created["user"]["id"]
    assert len(auth_methods) == 1
    assert auth_methods[0]["method_type"] == "password"
    assert auth_methods[0]["provider"] == "empyralis_password"
    assert auth_methods[0]["is_primary"] is True
    assert auth_methods[0]["can_recover"] is True
    assert boundary["provider_connections"] == []
    assert boundary["summary"]["linked_provider_count"] == 0
    assert boundary["summary"]["has_recovery_method"] is True


def test_register_user_assigns_real_default_workspace_identity(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("workspace.identity@example.com", "password-123", name="Workspace Identity", workspace_id="default")

    workspace_entry = created["workspace_access"][0]

    assert created["default_workspace_id"] == workspace_entry["workspace_id"]
    assert created["current_workspace_id"] == workspace_entry["workspace_id"]
    assert created["default_workspace_id"] != "default"
    assert created["current_tenant_id"] == workspace_entry["tenant_id"]
    assert created["default_tenant_id"] == workspace_entry["tenant_id"]


def test_register_user_bootstraps_ready_personal_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("workspace.ready@example.com", "password-123", name="Ready User")

    workspace = auth._control_plane_call(
        control_plane_repository_module.get_workspace_by_id(created["default_workspace_id"])
    )

    assert isinstance(workspace, dict)
    shell = dict((workspace.get("metadata") or {}).get("shell") or {})
    assert shell["preferredProfile"] == "personal_shell"
    assert shell["defaultRoute"] == f"/w/{created['default_workspace_id']}/sage"
    assert shell["setupCompleted"] is True


def test_expired_token_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc:
        auth.verify_token(_expired_token(auth))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Bearer token has expired."


def test_workspace_membership_scope_and_viewer_role(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("workspace.viewer@example.com", "password-123", name="Viewer")
    home_entry = next(
        item
        for item in created["workspace_access"]
        if isinstance(item, dict) and str(item.get("workspace_id") or "").startswith("ws_")
    )
    home_workspace_id = home_entry["workspace_id"]
    home_tenant_id = home_entry["tenant_id"]
    finance_workspace_id = f"finance-{tmp_path.name}"
    auth.ensure_workspace_tenant_binding(finance_workspace_id, home_tenant_id)
    auth.upsert_workspace_membership(created["user"]["id"], finance_workspace_id, "viewer")
    token = auth.login_user("workspace.viewer@example.com", "password-123")["token"]
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {token}")

    assert auth.workspace_role(current_user, home_workspace_id) == "owner"
    assert auth.workspace_role(current_user, finance_workspace_id) == "viewer"
    assert auth.enforce_workspace_access(current_user, finance_workspace_id, minimum_role="viewer") == finance_workspace_id
    with pytest.raises(HTTPException):
        auth.enforce_workspace_access(current_user, finance_workspace_id, minimum_role="member")
    with pytest.raises(HTTPException):
        auth.enforce_workspace_access(current_user, "secret-lab", minimum_role="viewer")


def test_default_workspace_alias_resolves_to_personal_workspace_for_bearer_user(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("workspace.alias@example.com", "password-123", name="Alias User")
    workspace_id = created["workspace_access"][0]["workspace_id"]
    tenant_id = created["workspace_access"][0]["tenant_id"]
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {created['token']}")

    assert auth.workspace_role(current_user, "default") == "owner"
    assert auth.workspace_tenant_id(current_user, "default") == tenant_id
    assert auth.enforce_workspace_access(current_user, "default", minimum_role="viewer") == workspace_id


def test_workspace_capability_policy_denies_local_capability(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("workspace.member@example.com", "password-123", name="Member")
    home_workspace_id = created["workspace_access"][0]["workspace_id"]
    auth.upsert_workspace_policy(home_workspace_id, capability_deny=["computer_control.type"])
    token = auth.login_user("workspace.member@example.com", "password-123")["token"]
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {token}")

    with pytest.raises(HTTPException) as exc:
        auth.enforce_workspace_access(
            current_user,
            home_workspace_id,
            minimum_role="member",
            capability_id="computer_control.type",
        )

    assert exc.value.status_code == 403


def test_tenant_binding_and_policy_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("tenant.member@example.com", "password-123", name="Tenant Member")
    finance_workspace_id = f"finance-{tmp_path.name}"
    auth.ensure_workspace_tenant_binding(finance_workspace_id, "tenant-acme")
    auth.upsert_workspace_membership(created["user"]["id"], finance_workspace_id, "owner")
    auth.upsert_tenant_policy(
        "tenant-acme",
        capability_allow=["computer_control.type"],
        connector_allow=["gmail"],
        machine_enrollment_scope="tenant",
    )
    auth.upsert_workspace_policy(
        finance_workspace_id,
        capability_deny=["computer_control.type"],
        connector_deny=["gmail"],
        machine_enrollment_scope="workspace",
    )
    refreshed = auth.login_user("tenant.member@example.com", "password-123")
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {refreshed['token']}")

    assert auth.workspace_tenant_id(current_user, finance_workspace_id) == "tenant-acme"
    assert auth.tenant_role(current_user, "tenant-acme") == "owner"
    assert auth.workspace_machine_enrollment_scope(current_user, finance_workspace_id) == "workspace"
    assert auth.workspace_connector_decision(current_user, finance_workspace_id, "gmail")["decision"] == "deny"
    assert auth.workspace_capability_decision(current_user, finance_workspace_id, "computer_control.type")["decision"] == "deny"

    with pytest.raises(HTTPException) as exc:
        auth.enforce_workspace_access(
            current_user,
            finance_workspace_id,
            tenant_id="tenant-other",
            minimum_role="viewer",
        )

    assert exc.value.status_code == 403


def test_workspace_tenant_resolution_prefers_authoritative_binding_over_stale_token_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("tenant.stale@example.com", "password-123", name="Tenant Stale")
    workspace_entry = created["workspace_access"][0]
    workspace_id = workspace_entry["workspace_id"]
    authoritative_tenant_id = workspace_entry["tenant_id"]
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {created['token']}")

    current_user["workspace_access"][workspace_id]["tenant_id"] = "tenant-stale"

    assert auth.workspace_tenant_id(current_user, workspace_id) == authoritative_tenant_id
    assert auth.allowed_tenant_ids(current_user) == {authoritative_tenant_id}
    assert auth.enforce_workspace_access(
        current_user,
        workspace_id,
        tenant_id=authoritative_tenant_id,
        minimum_role="viewer",
    ) == workspace_id


def test_allowed_tenant_ids_reuses_workspace_access_without_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("tenant.fastpath@example.com", "password-123", name="Tenant Fastpath")
    workspace_entry = created["workspace_access"][0]
    workspace_id = workspace_entry["workspace_id"]
    tenant_id = workspace_entry["tenant_id"]
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {created['token']}")

    def _fail_rebuild(**_: object) -> dict[str, dict[str, object]]:
        raise AssertionError("allowed_tenant_ids should not rebuild workspace access")

    monkeypatch.setattr(auth, "_effective_workspace_access", _fail_rebuild)

    assert auth.allowed_tenant_ids(current_user) == {tenant_id}
    assert auth.workspace_tenant_id(current_user, workspace_id) == tenant_id


def test_enterprise_settings_and_admin_provisioning_hooks(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    finance_workspace_id = f"finance-{tmp_path.name}"
    ops_workspace_id = f"ops-{tmp_path.name}"
    auth.upsert_tenant_enterprise_settings(
        "tenant-acme",
        sso={
            "enabled": True,
            "provider": "oidc",
            "issuer_url": "https://id.example.com",
            "domains": ["example.com"],
            "scopes": ["openid", "profile", "email"],
        },
        mfa={
            "required": True,
            "methods": ["totp", "webauthn"],
            "grace_period_hours": 24,
        },
        scim={
            "enabled": True,
            "base_url": "https://control.example.com/api/v1/auth/admin/provision/users",
            "provisioning_mode": "admin_api",
        },
    )

    enterprise = auth.load_tenant_enterprise_settings("tenant-acme")
    assert enterprise["sso"]["enabled"] is True
    assert enterprise["mfa"]["required"] is True
    assert enterprise["scim"]["enabled"] is True

    provisioned = auth.provision_user_account(
        email="provisioned@example.com",
        name="Provisioned User",
        tenant_id="tenant-acme",
        workspace_roles={finance_workspace_id: "viewer", ops_workspace_id: "member"},
        provisioning_source="scim_bridge",
        external_id="scim-user-123",
        auth_provider="oidc",
        sso_subject="oidc|user-123",
    )

    security = auth.load_user_enterprise_security(provisioned["user"]["id"])
    workspace_ids = {item["workspace_id"] for item in provisioned["workspace_access"]}
    boundary = provisioned["identity_boundary"]
    auth_methods = boundary["auth_methods"]

    assert workspace_ids == {finance_workspace_id, ops_workspace_id}
    assert security["auth_provider"] == "oidc"
    assert security["provisioning_source"] == "scim_bridge"
    assert security["external_id"] == "scim-user-123"
    assert security["sso_subject"] == "oidc|user-123"
    assert len(auth_methods) == 1
    assert auth_methods[0]["method_type"] == "sso"
    assert auth_methods[0]["provider"] == "oidc"
    assert auth_methods[0]["can_recover"] is False
    assert boundary["provider_connections"] == []
    assert boundary["summary"]["linked_provider_count"] == 0


def test_provider_connections_are_capabilities_not_identity(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("providers@example.com", "password-123", name="Provider User")
    user_id = created["user"]["id"]

    active_connection = auth.upsert_user_provider_connection(
        user_id,
        provider="openai",
        status="active",
        external_account_id="acct-openai-1",
        metadata={"capability_role": "ai_provider"},
    )
    active_boundary = auth.user_identity_boundary(user_id)

    assert active_connection["provider"] == "openai"
    assert active_boundary["summary"]["linked_provider_count"] == 1
    assert active_boundary["summary"]["has_recovery_method"] is True

    disconnected_connection = auth.upsert_user_provider_connection(
        user_id,
        provider="openai",
        status="disconnected",
        external_account_id="acct-openai-1",
        metadata={"capability_role": "ai_provider"},
    )
    disconnected_boundary = auth.user_identity_boundary(user_id)
    logged_in = auth.login_user("providers@example.com", "password-123")

    assert disconnected_connection["status"] == "disconnected"
    assert disconnected_boundary["summary"]["linked_provider_count"] == 0
    assert disconnected_boundary["provider_connections"][0]["status"] == "disconnected"
    assert logged_in["user"]["id"] == user_id
    assert logged_in["identity_boundary"]["account_owner"] == "empyralis"


def test_provider_connections_remain_workspace_scoped(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("providers.scoped@example.com", "password-123", name="Scoped Provider User")
    user_id = created["user"]["id"]
    workspace_a = created["workspace_access"][0]["workspace_id"]
    workspace_b = f"lab-{tmp_path.name}"
    if workspace_b == workspace_a:
        workspace_b = f"{workspace_b}-secondary"
    auth.ensure_workspace_tenant_binding(workspace_b, created["workspace_access"][0]["tenant_id"])
    auth.upsert_workspace_membership(user_id, workspace_b, "member")

    first = auth.upsert_user_provider_connection(
        user_id,
        provider="openai",
        workspace_id=workspace_a,
        status="active",
        external_account_id="acct-shared",
    )
    second = auth.upsert_user_provider_connection(
        user_id,
        provider="openai",
        workspace_id=workspace_b,
        status="active",
        external_account_id="acct-shared",
    )
    boundary = auth.user_identity_boundary(user_id)

    assert first["workspace_id"] == workspace_a
    assert second["workspace_id"] == workspace_b
    assert len(boundary["provider_connections"]) == 2
    assert boundary["summary"]["linked_provider_count"] == 2


def test_membership_change_invalidates_existing_bearer_token(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("membership.invalidate@example.com", "password-123", name="Membership User")
    user_id = created["user"]["id"]
    auth.ensure_workspace_tenant_binding(f"ops-{tmp_path.name}", created["workspace_access"][0]["tenant_id"])

    auth.upsert_workspace_membership(user_id, f"ops-{tmp_path.name}", "viewer")

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_Request(), authorization=f"Bearer {created['token']}")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Bearer token is stale and must be refreshed."


def test_revoked_bearer_session_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("session.revoke@example.com", "password-123", name="Session User")
    session_id = created["auth_session"]["session_id"]

    revoked = auth.revoke_auth_session(session_id, reason="manual_revoke")

    assert revoked["status"] == "revoked"
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_Request(), authorization=f"Bearer {created['token']}")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Bearer session is no longer active."


def test_revoked_device_link_blocks_bound_mobile_session(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("device.revoke@example.com", "password-123", name="Device User")
    user_id = created["user"]["id"]
    workspace_access = list(created["workspace_access"])
    workspace_id = workspace_access[0]["workspace_id"]
    device = auth.upsert_user_device_link(
        user_id,
        device_id="device-1",
        workspace_id=workspace_id,
        channel="mobile",
        platform="ios",
        trust_state="verified",
        metadata={"pairing_method": "qr"},
    )
    token = auth.issue_token(
        user_id,
        email=created["user"]["email"],
        role="owner",
        workspace_access=workspace_access,
        channel="mobile",
        device_id=device["device_id"],
        session_metadata={"auth_flow": "pairing"},
    )

    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {token}")
    assert current_user["device_id"] == "device-1"

    revoked = auth.revoke_user_device_link(user_id, "device-1", reason="device_unlinked")

    assert revoked["status"] == "revoked"
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_Request(), authorization=f"Bearer {token}")

    assert exc.value.status_code == 401
    assert exc.value.detail in {
        "Bearer session is no longer active.",
        "Bearer session device is not active.",
    }


def test_authenticated_profile_includes_identity_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("profile.identity@example.com", "password-123", name="Profile User")
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {created['token']}")

    profile = auth.get_authenticated_user_profile(current_user)

    assert profile["identity_boundary"]["account_owner"] == "empyralis"
    assert profile["identity_boundary"]["account_id"] == created["user"]["id"]
    assert profile["identity_boundary"]["auth_methods"][0]["provider"] == "empyralis_password"
    assert profile["auth_session"]["session_id"] == created["auth_session"]["session_id"]
    assert profile["identity_versions"]["membership_version"] >= 1
    assert profile["current_workspace_entitlements"]["plan_id"] == "pro"
    assert profile["current_workspace_entitlements"]["capabilities"]["mobile_app_enabled"] is True


def test_authenticated_profile_update_persists_through_control_plane_authority(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("profile.update@example.com", "password-123", name="Before Name")
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {created['token']}")

    updated = auth.update_authenticated_user_profile(
        current_user,
        name="After Name",
        avatar_url="https://example.com/avatar.png",
    )
    logged_in = auth.login_user("profile.update@example.com", "password-123")

    assert updated["user"]["name"] == "After Name"
    assert updated["user"]["avatar_url"] == "https://example.com/avatar.png"
    assert logged_in["user"]["name"] == "After Name"
    assert logged_in["user"]["avatar_url"] == "https://example.com/avatar.png"


def test_mobile_register_user_allows_default_workspace_plan(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)

    created = auth.register_user(
        "mobile.default@example.com",
        "password-123",
        name="Mobile Default",
        channel="mobile",
        device_id="iphone-default",
        workspace_id="default",
    )

    assert created["current_workspace_entitlements"]["capabilities"]["mobile_app_enabled"] is True


def test_mobile_beta_auto_signin_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)

    assert auth.mobile_beta_auto_signin_enabled() is False
    with pytest.raises(HTTPException) as exc:
        auth.login_mobile_beta_user(device_id="iphone-default")
    assert exc.value.status_code == 403


def test_mobile_register_user_issues_device_bound_session(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path, {"ORION_MOBILE_JWT_EXP_SECONDS": "7200"})

    created = auth.register_user(
        "mobile.register@example.com",
        "password-123",
        name="Mobile Register",
        channel="mobile",
        device_id="iphone-1",
        device_name="Mansur iPhone",
        device_platform="ios",
        workspace_id="default",
    )

    token_payload = auth._decode_token_payload(created["token"])

    assert token_payload["channel"] == "mobile"
    assert token_payload["device_id"] == "iphone-1"
    assert int(token_payload["exp"]) - int(token_payload["iat"]) == 7200
    assert created["auth_session"]["channel"] == "mobile"
    assert created["auth_session"]["device_id"] == "iphone-1"
    assert created["device_link"]["device_id"] == "iphone-1"
    assert created["device_link"]["platform"] == "ios"
    assert created["current_workspace_id"] == created["workspace_access"][0]["workspace_id"]
    assert created["default_workspace_id"] == created["workspace_access"][0]["workspace_id"]
    assert created["current_workspace_id"] != "default"
    assert created["session_recovery"]["refresh_token"].startswith("esr_")
    assert created["session_recovery"]["refresh_expires_at"] <= created["auth_session"]["expires_at"]
    assert created["auth_session"]["expires_at"] > token_payload["exp"]


def test_mobile_login_user_issues_persistent_workspace_scoped_session(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path, {"ORION_MOBILE_JWT_EXP_SECONDS": "5400"})
    created = auth.register_user("mobile.login@example.com", "password-123", name="Mobile Login")
    workspace_id = created["workspace_access"][0]["workspace_id"]

    logged_in = auth.login_user(
        "mobile.login@example.com",
        "password-123",
        channel="mobile",
        device_id="pixel-9",
        device_name="Pixel 9",
        device_platform="android",
        workspace_id=workspace_id,
    )
    token_payload = auth._decode_token_payload(logged_in["token"])

    assert token_payload["channel"] == "mobile"
    assert token_payload["device_id"] == "pixel-9"
    assert int(token_payload["exp"]) - int(token_payload["iat"]) == 5400
    assert logged_in["auth_session"]["channel"] == "mobile"
    assert logged_in["device_link"]["workspace_id"] == workspace_id
    assert any(item["workspace_id"] == workspace_id for item in logged_in["workspace_access"])
    assert logged_in["current_workspace_id"] == workspace_id
    assert logged_in["default_workspace_id"] == workspace_id
    assert logged_in["session_recovery"]["refresh_token"].startswith("esr_")
    assert logged_in["session_recovery"]["refresh_expires_at"] <= logged_in["auth_session"]["expires_at"]
    assert logged_in["auth_session"]["expires_at"] > token_payload["exp"]


def test_mobile_refresh_session_rotates_bearer_without_repairing(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(
        monkeypatch,
        tmp_path,
        {"ORION_MOBILE_JWT_EXP_SECONDS": "3600", "ORION_MOBILE_REFRESH_EXP_SECONDS": "86400"},
    )
    created = auth.register_user(
        "mobile.refresh@example.com",
        "password-123",
        name="Mobile Refresh",
        channel="mobile",
        device_id="iphone-refresh",
        device_name="Mansur iPhone",
        device_platform="ios",
        workspace_id="default",
    )

    refreshed = auth.refresh_authenticated_session(
        created["session_recovery"]["refresh_token"],
        device_id="iphone-refresh",
        device_name="Mansur iPhone",
        device_platform="ios",
        workspace_id=created["workspace_access"][0]["workspace_id"],
    )

    assert refreshed["token"] != created["token"]
    assert refreshed["auth_session"]["session_id"] == created["auth_session"]["session_id"]
    assert refreshed["device_link"]["device_id"] == "iphone-refresh"
    assert refreshed["session_recovery"]["refresh_token"] != created["session_recovery"]["refresh_token"]
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {refreshed['token']}")
    assert current_user["device_id"] == "iphone-refresh"


def test_web_refresh_session_survives_expired_access_ttl(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(
        monkeypatch,
        tmp_path,
        {
            "ORION_JWT_EXP_SECONDS": "60",
            "EMPYRALIS_AUTH_REFRESH_COOKIE_MAX_AGE_SECONDS": "86400",
        },
    )
    now = 1_700_000_000
    monkeypatch.setattr(auth.time, "time", lambda: now)
    created = auth.register_user(
        "web.refresh@example.com",
        "password-123",
        name="Web Refresh",
        channel="web",
        workspace_id="default",
    )
    token_payload = auth._decode_token_payload(created["token"])

    assert token_payload["channel"] == "web"
    assert int(token_payload["exp"]) - int(token_payload["iat"]) == 60
    assert created["auth_session"]["expires_at"] >= now + 86400
    assert created["session_recovery"]["refresh_expires_at"] <= created["auth_session"]["expires_at"]

    monkeypatch.setattr(auth.time, "time", lambda: now + 120)
    refreshed = auth.refresh_authenticated_session(
        created["session_recovery"]["refresh_token"],
        workspace_id=created["workspace_access"][0]["workspace_id"],
    )

    assert refreshed["token"] != created["token"]
    assert refreshed["auth_session"]["session_id"] == created["auth_session"]["session_id"]
    refreshed_payload = auth._decode_token_payload(refreshed["token"])
    assert int(refreshed_payload["exp"]) - int(refreshed_payload["iat"]) == 60


def test_stale_mobile_bearer_can_be_recovered_with_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user(
        "mobile.stale@example.com",
        "password-123",
        name="Mobile Stale",
        channel="mobile",
        device_id="iphone-stale",
        device_name="Mansur iPhone",
        device_platform="ios",
        workspace_id="default",
    )
    workspace_id = created["workspace_access"][0]["workspace_id"]
    tenant_id = created["workspace_access"][0]["tenant_id"]
    extra_workspace_id = f"ops-{tmp_path.name}"
    auth.ensure_workspace_tenant_binding(extra_workspace_id, tenant_id)
    auth.upsert_workspace_membership(created["user"]["id"], extra_workspace_id, "viewer")

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_Request(), authorization=f"Bearer {created['token']}")

    assert exc.value.detail == "Bearer token is stale and must be refreshed."

    refreshed = auth.refresh_authenticated_session(
        created["session_recovery"]["refresh_token"],
        device_id="iphone-stale",
        workspace_id=workspace_id,
    )
    current_user = auth.get_current_user(_Request(), authorization=f"Bearer {refreshed['token']}")
    assert current_user["user_id"] == created["user"]["id"]


def test_removed_workspace_membership_revokes_old_bearer_and_drops_access(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user("membership.remove@example.com", "password-123", name="Removal User")
    user_id = created["user"]["id"]
    home_workspace_id = created["workspace_access"][0]["workspace_id"]
    home_tenant_id = created["workspace_access"][0]["tenant_id"]
    extra_workspace_id = f"ops-{tmp_path.name}"
    auth.ensure_workspace_tenant_binding(extra_workspace_id, home_tenant_id)
    auth.upsert_workspace_membership(user_id, extra_workspace_id, "viewer")
    token = auth.login_user("membership.remove@example.com", "password-123")["token"]

    removed = auth.remove_workspace_membership(user_id, extra_workspace_id)

    assert removed["removed"] is True
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_Request(), authorization=f"Bearer {token}")

    assert exc.value.detail == "Bearer token is stale and must be refreshed."
    relogged = auth.login_user("membership.remove@example.com", "password-123")
    relogged_user = auth.get_current_user(_Request(), authorization=f"Bearer {relogged['token']}")
    assert auth.enforce_workspace_access(relogged_user, home_workspace_id, minimum_role="viewer") == home_workspace_id
    with pytest.raises(HTTPException):
        auth.enforce_workspace_access(relogged_user, extra_workspace_id, minimum_role="viewer")


def test_revoked_device_link_blocks_refresh_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path):
    auth, _, _ = _reload_auth(monkeypatch, tmp_path)
    created = auth.register_user(
        "mobile.revoked@example.com",
        "password-123",
        name="Mobile Revoked",
        channel="mobile",
        device_id="iphone-revoked",
        device_name="Mansur iPhone",
        device_platform="ios",
        workspace_id="default",
    )
    auth.revoke_user_device_link(created["user"]["id"], "iphone-revoked", reason="manual revoke")

    with pytest.raises(HTTPException) as exc:
        auth.refresh_authenticated_session(
            created["session_recovery"]["refresh_token"],
            device_id="iphone-revoked",
        )

    assert exc.value.status_code == 401
    assert exc.value.detail in {
        "Auth session is no longer active.",
        "Refresh token is no longer active.",
        "Refresh token device is not active.",
    }



def _build_auth_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_auth_module.router)
    app.include_router(routes_auth_module.router, prefix="/api/v1")
    return app


@pytest.mark.anyio
async def test_auth_status_returns_401_without_token():
    transport = httpx.ASGITransport(app=_build_auth_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/auth/status")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_auth_status_returns_authenticated_payload(monkeypatch: pytest.MonkeyPatch):
    app = _build_auth_test_app()
    app.dependency_overrides[routes_auth_module.get_current_user] = lambda: {"auth_type": "bearer", "user_id": "user-1"}
    monkeypatch.setattr(
        routes_auth_module,
        "get_authenticated_user_profile",
        lambda current_user: {"ok": True, "user": {"id": "user-1", "email": "user@example.com"}},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/auth/status")

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "user": {"id": "user-1", "email": "user@example.com"},
    }


@pytest.mark.anyio
async def test_mobile_beta_bootstrap_route_forwards_to_auth_helper(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_mobile_bootstrap(**kwargs):
        captured.update(kwargs)
        return {
            "token": "mobile-token",
            "refresh_token": "refresh-token",
            "user": {"id": "user-mobile", "email": "mobile@example.com"},
            "workspace": {"id": "ws-1"},
        }

    monkeypatch.setattr(routes_auth_module, "login_mobile_beta_user", fake_mobile_bootstrap)
    app = _build_auth_test_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/auth/mobile-beta-bootstrap",
            json={
                "device_id": "iphone-1",
                "device_name": "Mansur iPhone",
                "device_platform": "ios",
                "workspace_id": "ws-1",
                "session_ttl_seconds": 3600,
            },
        )

    assert response.status_code == 200
    assert response.json()["token"] == "mobile-token"
    assert captured == {
        "device_id": "iphone-1",
        "device_name": "Mansur iPhone",
        "device_platform": "ios",
        "workspace_id": "ws-1",
        "session_ttl_seconds": 3600,
    }


@pytest.mark.anyio
async def test_auth_signup_alias_forwards_to_register(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_register(body):
        captured["body"] = body
        return {"ok": True, "token": "alias-token"}

    monkeypatch.setattr(routes_auth_module, "register", fake_register)

    body = routes_auth_module.AuthRegisterRequest(
        name="Alias User",
        email="alias@example.com",
        password="password-123",
    )
    payload = await routes_auth_module.signup(body)

    assert payload == {"ok": True, "token": "alias-token"}
    assert captured["body"] == body


@pytest.mark.anyio
async def test_auth_providers_route_returns_runtime_auth_options(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        routes_auth_module,
        "auth_provider_options",
        lambda: {
            "email": {"enabled": True},
            "google": {"enabled": False},
            "apple": {"enabled": False},
        },
    )

    payload = await routes_auth_module.auth_providers()

    assert payload == {
        "email": {"enabled": True},
        "google": {"enabled": False},
        "apple": {"enabled": False},
    }


@pytest.mark.anyio
async def test_auth_refresh_route_forwards_to_refresh_handler(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_refresh(refresh_token, **kwargs):
        captured["refresh_token"] = refresh_token
        captured["kwargs"] = kwargs
        return {"ok": True, "token": "next-token"}

    monkeypatch.setattr(routes_auth_module, "refresh_authenticated_session", fake_refresh)
    app = _build_auth_test_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/auth/refresh",
            json={
                "refresh_token": "esr_session.secret",
                "device_id": "iphone-1",
                "device_name": "Mansur iPhone",
                "device_platform": "ios",
                "workspace_id": "default",
            },
        )

    assert response.status_code == 200
    assert response.json()["token"] == "next-token"
    assert captured["refresh_token"] == "esr_session.secret"
    assert captured["kwargs"] == {
        "device_id": "iphone-1",
        "device_name": "Mansur iPhone",
        "device_platform": "ios",
        "workspace_id": "default",
        "session_ttl_seconds": None,
    }


@pytest.mark.anyio
async def test_auth_devices_routes_list_and_revoke(monkeypatch: pytest.MonkeyPatch):
    app = _build_auth_test_app()
    app.dependency_overrides[routes_auth_module.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "device_id": "iphone-1",
    }
    monkeypatch.setattr(
        routes_auth_module,
        "list_authenticated_user_devices",
        lambda current_user: {
            "ok": True,
            "current_device_id": "iphone-1",
            "devices": [{"device_id": "iphone-1", "status": "active"}],
        },
    )
    monkeypatch.setattr(
        routes_auth_module,
        "revoke_authenticated_user_device",
        lambda current_user, device_id: {
            "ok": True,
            "revoked_current_device": device_id == "iphone-1",
            "device": {"device_id": device_id, "status": "revoked"},
        },
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        list_response = await client.get("/api/v1/auth/devices")
        revoke_response = await client.delete("/api/v1/auth/devices/iphone-1")

    assert list_response.status_code == 200
    assert list_response.json()["current_device_id"] == "iphone-1"
    assert revoke_response.status_code == 200
    assert revoke_response.json()["device"]["device_id"] == "iphone-1"


@pytest.mark.anyio
async def test_auth_channel_pairing_routes_forward_to_service(monkeypatch: pytest.MonkeyPatch):
    app = _build_auth_test_app()
    app.dependency_overrides[routes_auth_module.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "email": "user@example.com",
    }
    monkeypatch.setattr(
        routes_auth_module,
        "create_authenticated_channel_pairing_intent",
        lambda current_user, **kwargs: {"ok": True, "intent": {"provider": kwargs["provider"], "pairing_code": "EMP-ABCD-EFGH"}},
    )
    monkeypatch.setattr(
        routes_auth_module,
        "list_authenticated_channel_links",
        lambda current_user, **kwargs: {"ok": True, "links": [{"link_id": "chl-1", "provider": "telegram"}]},
    )
    monkeypatch.setattr(
        routes_auth_module,
        "revoke_authenticated_channel_link",
        lambda current_user, **kwargs: {"ok": True, "link": {"link_id": kwargs["link_id"], "status": "revoked"}},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create_response = await client.post(
            "/api/v1/auth/channel-pairing/intents",
            json={
                "provider": "telegram",
                "workspace_id": "default",
                "scopes": ["chat"],
            },
        )
        list_response = await client.get("/api/v1/auth/channel-pairing/links")
        revoke_response = await client.post(
            "/api/v1/auth/channel-pairing/links/chl-1/revoke",
            json={"confirm": True},
        )

    assert create_response.status_code == 200
    assert create_response.json()["intent"]["provider"] == "telegram"
    assert list_response.status_code == 200
    assert list_response.json()["links"][0]["link_id"] == "chl-1"
    assert revoke_response.status_code == 200
    assert revoke_response.json()["link"]["status"] == "revoked"


@pytest.mark.anyio
async def test_auth_enterprise_status_returns_enterprise_summary(monkeypatch: pytest.MonkeyPatch):
    app = _build_auth_test_app()
    app.dependency_overrides[routes_auth_module.get_current_user] = lambda: {
        "auth_type": "bearer",
        "user_id": "user-1",
        "workspace_access": {"default": {"tenant_id": "tenant-acme"}},
    }
    monkeypatch.setattr(
        routes_auth_module,
        "enterprise_status_for_user",
        lambda current_user: {
            "ok": True,
            "summary": {"sso_enabled": True, "mfa_required": True, "mfa_enrolled": False, "scim_enabled": True},
            "user": {"user_id": "user-1", "mfa_enrolled": False},
            "tenants": [{"tenant_id": "tenant-acme"}],
        },
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/auth/enterprise/status")

    assert response.status_code == 200
    assert response.json()["summary"]["mfa_required"] is True


@pytest.mark.anyio
async def test_admin_enterprise_config_endpoints(monkeypatch: pytest.MonkeyPatch):
    app = _build_auth_test_app()
    app.dependency_overrides[routes_auth_module.require_admin_access] = lambda: {"auth_type": "api_key", "is_admin": True, "role": "owner"}
    monkeypatch.setattr(
        routes_auth_module,
        "load_tenant_enterprise_settings",
        lambda tenant_id: {"tenant_id": tenant_id, "sso": {"enabled": False}, "mfa": {"required": False}, "scim": {"enabled": False}},
    )
    monkeypatch.setattr(
        routes_auth_module,
        "upsert_tenant_enterprise_settings",
        lambda tenant_id, sso=None, mfa=None, scim=None: {
            "tenant_id": tenant_id,
            "sso": {"enabled": bool((sso or {}).get("enabled"))},
            "mfa": {"required": bool((mfa or {}).get("required"))},
            "scim": {"enabled": bool((scim or {}).get("enabled"))},
        },
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        get_response = await client.get("/api/v1/auth/admin/enterprise-config", params={"tenant_id": "tenant-acme"})
        patch_response = await client.patch(
            "/api/v1/auth/admin/enterprise-config",
            json={
                "tenant_id": "tenant-acme",
                "sso": {"enabled": True},
                "mfa": {"required": True},
                "scim": {"enabled": True},
            },
        )

    assert get_response.status_code == 200
    assert get_response.json()["config"]["tenant_id"] == "tenant-acme"
    assert patch_response.status_code == 200
    assert patch_response.json()["config"]["sso"]["enabled"] is True
    assert patch_response.json()["config"]["mfa"]["required"] is True
    assert patch_response.json()["config"]["scim"]["enabled"] is True


@pytest.mark.anyio
async def test_admin_provision_user_endpoint(monkeypatch: pytest.MonkeyPatch):
    app = _build_auth_test_app()
    app.dependency_overrides[routes_auth_module.require_admin_access] = lambda: {"auth_type": "api_key", "is_admin": True, "role": "owner"}
    monkeypatch.setattr(
        routes_auth_module,
        "provision_user_account",
        lambda **kwargs: {"ok": True, "user": {"email": kwargs["email"]}, "workspace_access": [{"workspace_id": "finance"}]},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/auth/admin/provision/users",
            json={
                "email": "provisioned@example.com",
                "tenant_id": "tenant-acme",
                "workspace_roles": {"finance": "viewer"},
                "provisioning_source": "admin_api",
            },
        )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "provisioned@example.com"
