import unittest
import asyncio
from unittest.mock import patch
import os
import importlib

from fastapi import HTTPException
from starlette.requests import Request

from server_modules import auth, runtime_common
from server_modules import db as runtime_db
from server_modules import routes_connectors


def _request(path: str = "/test", query_string: bytes = b"") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string,
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


class AuthHardeningTests(unittest.TestCase):
    def setUp(self):
        global auth
        global runtime_common
        global runtime_db
        global routes_connectors

        auth = importlib.import_module("server_modules.auth")
        runtime_common = importlib.import_module("server_modules.runtime_common")
        runtime_db = importlib.import_module("server_modules.db")
        routes_connectors = importlib.import_module("server_modules.routes_connectors")
        auth.USER_RATE_LIMIT_BUCKETS.clear()
        auth.LOGIN_RATE_LIMIT_BUCKETS.clear()
        auth.REFRESH_RATE_LIMIT_BUCKETS.clear()
        auth.CSRF_FAILURE_RATE_LIMIT_BUCKETS.clear()

    def test_get_current_user_rejects_missing_auth_when_enabled(self):
        request = _request()
        with patch.dict(os.environ, {"ORION_AUTH_REQUIRED": "1"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                auth.get_current_user(request=request)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_auth_remains_required_when_api_key_is_configured(self):
        with patch.dict(os.environ, {"ORION_API_KEY": "secret"}, clear=False):
            self.assertTrue(auth._orion_auth_required())

    def test_get_current_user_accepts_matching_api_key_when_auth_enabled(self):
        request = _request()
        with patch.dict(os.environ, {"ORION_API_KEY": "secret", "ORION_AUTH_REQUIRED": "1"}, clear=False):
            user = auth.get_current_user(request=request, x_api_key="secret")
        self.assertEqual(user["auth_type"], "api_key")
        self.assertEqual(user["user_id"], "service")

    def test_service_api_key_uses_separate_higher_rate_limit_budget(self):
        request = _request()
        with patch.object(auth, "ORION_SERVICE_RATE_LIMIT_PER_MINUTE", 2), patch("server_modules.auth._orion_api_key", return_value="secret"):
            auth.get_current_user(request=request, x_api_key="secret")
            auth.get_current_user(request=request, x_api_key="secret")
            with self.assertRaises(HTTPException) as ctx:
                auth.get_current_user(request=request, x_api_key="secret")
        self.assertEqual(ctx.exception.status_code, 429)

    def test_refresh_rate_limit_blocks_after_configured_window(self):
        request = _request(path="/auth/refresh")
        with patch.object(auth, "ORION_AUTH_REFRESH_RATE_LIMIT_PER_MINUTE", 2):
            auth.limit_refresh_requests(request=request)
            auth.limit_refresh_requests(request=request)
            with self.assertRaises(HTTPException) as ctx:
                auth.limit_refresh_requests(request=request)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_extract_request_api_key_ignores_query_string(self):
        request = _request(query_string=b"api_key=leaked")
        self.assertEqual(runtime_common._extract_request_api_key(request), "")

    def test_public_registration_disabled_dependency_blocks_when_off(self):
        with patch.dict(os.environ, {"ORION_PUBLIC_REGISTRATION_ENABLED": "0"}, clear=False):
            with patch.object(auth, "ORION_PUBLIC_REGISTRATION_ENABLED", False):
                with self.assertRaises(HTTPException) as ctx:
                    auth.ensure_public_registration_enabled()
        self.assertEqual(ctx.exception.status_code, 404)

    def test_require_admin_access_blocks_restricted_service_key_identity(self):
        request = _request()
        with patch(
            "server_modules.auth.get_current_user",
            return_value={
                "user_id": "service",
                "auth_type": "api_key",
                "role": "service",
                "is_admin": False,
                "auth_admin": False,
                "workspace_ids": [],
                "tenant_ids": [],
            },
        ):
            with self.assertRaises(HTTPException) as ctx:
                auth.require_admin_access(request=request)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_restricted_service_key_identity_is_scoped(self):
        current_user = {
            "user_id": "service",
            "auth_type": "api_key",
            "role": "service",
            "is_admin": False,
            "auth_admin": False,
            "workspace_ids": [],
            "tenant_ids": [],
        }
        self.assertEqual(auth.current_user_role(current_user), "member")
        self.assertFalse(auth.current_user_has_auth_admin_access(current_user))
        self.assertEqual(auth.allowed_workspace_ids(current_user), set())
        self.assertEqual(auth.allowed_tenant_ids(current_user), set())

    def test_restricted_service_key_cannot_access_unscoped_workspace(self):
        current_user = {
            "user_id": "service",
            "auth_type": "api_key",
            "role": "service",
            "is_admin": False,
            "auth_admin": False,
            "workspace_ids": [],
            "tenant_ids": [],
        }
        with self.assertRaises(HTTPException) as ctx:
            auth.enforce_workspace_access(current_user, "finance", minimum_role="member")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_service_key_rejects_cross_tenant_workspace_spoof(self):
        current_user = {
            "user_id": "service",
            "auth_type": "api_key",
            "role": "service",
            "is_admin": False,
            "auth_admin": False,
            "workspace_access": {
                "finance": {"workspace_id": "finance", "tenant_id": "tenant-a", "role": "owner"},
            },
        }
        with patch.object(auth, "tenant_id_for_workspace", return_value="tenant-a"):
            with self.assertRaises(HTTPException) as ctx:
                auth.enforce_workspace_access(
                    current_user,
                    "finance",
                    tenant_id="tenant-b",
                    minimum_role="member",
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_explicit_admin_api_key_identity_still_allows_admin_access(self):
        request = _request()
        with patch(
            "server_modules.auth.get_current_user",
            return_value={"user_id": "admin-service", "auth_type": "api_key", "role": "owner", "is_admin": True},
        ):
            result = auth.require_admin_access(request=request)
        self.assertTrue(result["is_admin"])
        self.assertEqual(result["role"], "owner")

    def test_require_api_key_returns_scoped_local_dev_identity_when_auth_disabled(self):
        request = _request()
        with patch.dict(os.environ, {"ORION_AUTH_REQUIRED": "0"}, clear=False):
            user = runtime_common.require_api_key(request=request)
        self.assertEqual(user["auth_type"], "local_dev")
        self.assertEqual(user["user_id"], "local-dev")
        self.assertEqual(user["role"], "member")
        self.assertFalse(user["is_admin"])
        self.assertEqual(user["workspace_ids"], ["default"])
        self.assertEqual(auth.current_user_role(user), "member")

    def test_auth_disabled_fails_closed_in_production(self):
        request = _request()
        with patch.dict(os.environ, {"ORION_AUTH_REQUIRED": "0", "ENV": "production"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                runtime_common.require_api_key(request=request)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_auth_disabled_fails_closed_when_orion_env_marks_production(self):
        request = _request()
        with patch.dict(os.environ, {"ORION_AUTH_REQUIRED": "0", "ORION_ENV": "production"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                runtime_common.require_api_key(request=request)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_database_url_does_not_backfill_from_backend_env_in_production(self):
        with patch.dict(os.environ, {"ORION_ENV": "production"}, clear=True):
            self.assertEqual(runtime_db.configured_database_url(), "")

    def test_runtime_config_only_loads_dotenv_in_explicit_local_dev(self):
        with patch.dict(os.environ, {"ORION_ENV": "production"}, clear=True):
            self.assertFalse(runtime_common.config._should_load_dotenv())
        with patch.dict(os.environ, {"ORION_ENV": "development"}, clear=True):
            self.assertTrue(runtime_common.config._should_load_dotenv())

    def test_runtime_config_rejects_missing_auth_secrets_in_production(self):
        with patch.dict(os.environ, {"ORION_ENV": "production"}, clear=True):
            with self.assertRaises(RuntimeError):
                runtime_common.config._assert_auth_secrets_safe_for_environment()

    def test_runtime_config_accepts_strong_auth_secrets_in_production(self):
        with patch.dict(
            os.environ,
            {
                "ORION_ENV": "production",
                "ORION_JWT_SECRET": "j" * 40,
                "EMPYRALIS_SECRETS_BROKER_SECRET": "s" * 40,
                "EMPYRALIS_TOOL_BROKER_SECRET": "t" * 40,
                "EMPYRALIS_MINI_APP_SHARE_SECRET": "m" * 40,
            },
            clear=True,
        ):
            runtime_common.config._assert_auth_secrets_safe_for_environment()

    def test_runtime_config_rejects_http_mcp_dev_override_in_production(self):
        with patch.dict(os.environ, {"ORION_ENV": "production", "EMPYRALIS_DEV_ALLOW_HTTP_MCP": "1"}, clear=True):
            with self.assertRaises(RuntimeError):
                runtime_common.config._assert_http_mcp_dev_override_safe_for_environment()

    def test_runtime_config_allows_http_mcp_dev_override_locally(self):
        with patch.dict(os.environ, {"ORION_ENV": "local", "EMPYRALIS_DEV_ALLOW_HTTP_MCP": "1"}, clear=True):
            runtime_common.config._assert_http_mcp_dev_override_safe_for_environment()

    def test_require_member_api_key_accepts_local_dev_member_identity(self):
        request = _request()
        with patch.dict(os.environ, {"ORION_AUTH_REQUIRED": "0"}, clear=False):
            user = runtime_common.require_member_api_key(request=request)
        self.assertEqual(user["auth_type"], "local_dev")
        self.assertEqual(user["role"], "member")
        self.assertFalse(user["is_admin"])

    def test_require_api_key_respects_local_dev_identity_overrides(self):
        request = _request()
        with patch.dict(
            os.environ,
            {
                "ORION_AUTH_REQUIRED": "0",
                "ORION_LOCAL_DEV_AUTH_ROLE": "viewer",
                "ORION_LOCAL_DEV_USER_ID": "dev-user-1",
                "ORION_LOCAL_DEV_EMAIL": "Dev.User@Example.com",
            },
            clear=False,
        ):
            user = runtime_common.require_api_key(request=request)
        self.assertEqual(user["auth_type"], "local_dev")
        self.assertEqual(user["user_id"], "dev-user-1")
        self.assertEqual(user["email"], "dev.user@example.com")
        self.assertEqual(user["role"], "viewer")
        self.assertFalse(user["is_admin"])

    def test_require_admin_api_key_blocks_local_dev_member_identity(self):
        request = _request()
        with patch.dict(os.environ, {"ORION_AUTH_REQUIRED": "0"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                runtime_common.require_admin_api_key(request=request)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_require_admin_api_key_allows_explicit_local_dev_owner_identity(self):
        request = _request()
        with patch.dict(
            os.environ,
            {"ORION_AUTH_REQUIRED": "0", "ORION_LOCAL_DEV_AUTH_ROLE": "owner", "ORION_LOCAL_DEV_WORKSPACE_IDS": "default"},
            clear=False,
        ):
            user = runtime_common.require_admin_api_key(request=request)
        self.assertEqual(user["auth_type"], "local_dev")
        self.assertEqual(user["role"], "owner")
        self.assertTrue(user["is_admin"])

    def test_require_admin_access_blocks_non_admin_bearer_identity(self):
        request = _request()
        with patch("server_modules.auth.get_current_user", return_value={"user_id": "user-1", "auth_type": "bearer", "email": "user@example.com"}):
            with self.assertRaises(HTTPException) as ctx:
                auth.require_admin_access(request=request)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_get_current_user_defaults_bearer_role_to_member(self):
        request = _request()
        token = auth.issue_token("user-1", email="user@example.com")
        user = auth.get_current_user(request=request, authorization=f"Bearer {token}")
        self.assertEqual(user["role"], "member")
        self.assertFalse(user["is_admin"])

    def test_get_current_user_respects_viewer_role_claim(self):
        request = _request()
        token = auth.issue_token("viewer-1", email="viewer@example.com", role="viewer")
        user = auth.get_current_user(request=request, authorization=f"Bearer {token}")
        self.assertEqual(user["role"], "viewer")
        self.assertFalse(user["is_admin"])

    def test_get_current_user_marks_allowlisted_admin_without_rewriting_workspace_role(self):
        request = _request()
        token = auth.issue_token("admin-1", email="admin@example.com", role="member")
        with patch.object(auth, "ORION_ADMIN_USER_IDS", {"admin-1"}), patch.object(auth, "ORION_ADMIN_EMAILS", set()):
            user = auth.get_current_user(request=request, authorization=f"Bearer {token}")
        self.assertEqual(user["role"], "member")
        self.assertTrue(user["is_admin"])

    def test_account_shell_path_does_not_consume_authenticated_api_window(self):
        request = _request(path="/api/v1/auth/account-shell")
        token = auth.issue_token("user-1", email="user@example.com", role="member")
        with patch.object(auth, "ORION_AUTHENTICATED_API_RATE_LIMIT_PER_MINUTE", 1):
            first = auth.get_current_user(request=request, authorization=f"Bearer {token}")
            second = auth.get_current_user(request=request, authorization=f"Bearer {token}")
        self.assertEqual(first["user_id"], "user-1")
        self.assertEqual(second["user_id"], "user-1")

    def test_turn_path_consumes_model_invocation_window(self):
        request = _request(path="/api/turn")
        token = auth.issue_token("user-1", email="user@example.com", role="member")
        with patch.object(auth, "ORION_MODEL_INVOCATION_RATE_LIMIT_PER_MINUTE", 1):
            first = auth.get_current_user(request=request, authorization=f"Bearer {token}")
            self.assertEqual(first["user_id"], "user-1")
            with self.assertRaises(HTTPException) as ctx:
                auth.get_current_user(request=request, authorization=f"Bearer {token}")
        self.assertEqual(ctx.exception.status_code, 429)
        detail = ctx.exception.detail if isinstance(ctx.exception.detail, dict) else {}
        self.assertEqual(detail.get("code"), "model_invocation_rate_limited")

    def test_sage_chat_path_consumes_model_invocation_window(self):
        request = _request(path="/api/sage/chat")
        token = auth.issue_token("sage-user-1", email="sage-user@example.com", role="member")
        with patch.object(auth, "ORION_MODEL_INVOCATION_RATE_LIMIT_PER_MINUTE", 1):
            first = auth.get_current_user(request=request, authorization=f"Bearer {token}")
            self.assertEqual(first["user_id"], "sage-user-1")
            with self.assertRaises(HTTPException) as ctx:
                auth.get_current_user(request=request, authorization=f"Bearer {token}")
        self.assertEqual(ctx.exception.status_code, 429)
        detail = ctx.exception.detail if isinstance(ctx.exception.detail, dict) else {}
        self.assertEqual(detail.get("code"), "model_invocation_rate_limited")


class ConnectorRouteAuthHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_credentials_vault_route_blocks_default_local_dev_member(self):
        request = _request(path="/credentials/vault", query_string=b"workspace_id=default")
        with patch.dict(os.environ, {"ORION_AUTH_REQUIRED": "0"}, clear=False):
            current_user = runtime_common.require_api_key(request=request)
            with self.assertRaises(HTTPException) as ctx:
                await routes_connectors.list_credentials_vault(request=request, current_user=current_user)
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_credentials_vault_route_allows_explicit_scoped_local_dev_owner(self):
        request = _request(path="/credentials/vault", query_string=b"workspace_id=default")
        with (
            patch.dict(
                os.environ,
                {"ORION_AUTH_REQUIRED": "0", "ORION_LOCAL_DEV_AUTH_ROLE": "owner", "ORION_LOCAL_DEV_WORKSPACE_IDS": "default"},
                clear=False,
            ),
            patch("server_modules.routes_connectors.core.list_credentials_vault", return_value={"items": []}) as list_mock,
        ):
            current_user = runtime_common.require_api_key(request=request)
            result = await routes_connectors.list_credentials_vault(request=request, current_user=current_user)
        self.assertEqual(result, {"items": []})
        list_mock.assert_called_once_with(workspace_id="default")

    async def test_credentials_vault_route_rejects_unscoped_local_dev_workspace(self):
        request = _request(path="/credentials/vault", query_string=b"workspace_id=other")
        with patch.dict(os.environ, {"ORION_AUTH_REQUIRED": "0"}, clear=False):
            current_user = runtime_common.require_api_key(request=request)
            with self.assertRaises(HTTPException) as ctx:
                await routes_connectors.list_credentials_vault(request=request, current_user=current_user)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
