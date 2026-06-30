from __future__ import annotations

import unittest
from unittest.mock import patch
import importlib

from fastapi import HTTPException
from starlette.requests import Request

from server_modules import auth


def _request(path: str = "/auth/admin") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


class AuthRoleBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        global auth

        auth = importlib.import_module("server_modules.auth")

    def test_current_user_role_does_not_promote_auth_admin_to_owner(self) -> None:
        role = auth.current_user_role(
            {
                "auth_type": "bearer",
                "role": "member",
                "is_admin": True,
                "auth_admin": True,
            }
        )
        self.assertEqual(role, "member")

    def test_require_admin_access_blocks_workspace_owner_without_auth_admin_identity(self) -> None:
        request = _request()
        with patch(
            "server_modules.auth.get_current_user",
            return_value={
                "user_id": "user-1",
                "auth_type": "bearer",
                "role": "owner",
                "email": "owner@example.com",
                "workspace_ids": ["ws-1"],
            },
        ):
            with self.assertRaises(HTTPException) as ctx:
                auth.require_admin_access(request=request)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_allowlisted_auth_admin_keeps_workspace_role_but_passes_admin_gate(self) -> None:
        request = _request()
        token = auth.issue_token("admin-1", email="admin@example.com", role="member")
        with patch.object(auth, "ORION_ADMIN_USER_IDS", {"admin-1"}), patch.object(auth, "ORION_ADMIN_EMAILS", set()):
            current_user = auth.get_current_user(request=request, authorization=f"Bearer {token}")
            self.assertEqual(current_user["role"], "member")
            self.assertTrue(current_user["is_admin"])
            self.assertTrue(current_user["auth_admin"])

            result = auth.require_admin_access(request=request, authorization=f"Bearer {token}")

        self.assertEqual(result["role"], "member")
        self.assertTrue(result["is_admin"])
        self.assertTrue(result["auth_admin"])
