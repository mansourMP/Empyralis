from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from server_modules import mini_app_token_exchange_service


def test_exchange_secret_requires_exact_exchange_secret() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(mini_app_token_exchange_service.ConfigurationError):
            mini_app_token_exchange_service.issue_access_token(
                workspace_id="ws-1",
                app_id="budget_tracker",
                user_id="user-1",
                scopes={"workspace:read"},
            )


def test_exchange_secret_rejects_legacy_secret_aliases() -> None:
    for legacy_secret_name in ("EMPYRALIS_MINI_APP_LAUNCH_SECRET", "ORION_JWT_SECRET", "ORION_SECRET_KEY"):
        with patch.dict(os.environ, {legacy_secret_name: "legacy-secret"}, clear=True):
            with pytest.raises(mini_app_token_exchange_service.ConfigurationError):
                mini_app_token_exchange_service.issue_access_token(
                    workspace_id="ws-1",
                    app_id="budget_tracker",
                    user_id="user-1",
                    scopes={"workspace:read"},
                )


def test_exchange_secret_accepts_required_exchange_secret() -> None:
    with patch.dict(os.environ, {"EMPYRALIS_MINI_APP_EXCHANGE_SECRET": "e" * 40}, clear=True):
        issued = mini_app_token_exchange_service.issue_access_token(
            workspace_id="ws-1",
            app_id="budget_tracker",
            user_id="user-1",
            scopes={"workspace:read"},
        )
        verified = mini_app_token_exchange_service.verify_access_token(issued["access_token"])

    assert verified["workspace_id"] == "ws-1"
    assert verified["app_id"] == "budget_tracker"
    assert verified["scopes"] == ["workspace:read"]
