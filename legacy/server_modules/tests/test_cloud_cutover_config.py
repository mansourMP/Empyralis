from __future__ import annotations

import pytest

from server_modules.cloud_cutover_config import (
    CloudCutoverConfigError,
    assert_cloud_cutover_config,
    resolve_privacy_policy_url,
)


def test_development_allows_localhost_defaults() -> None:
    report = assert_cloud_cutover_config(
        {
            "ORION_ENV": "development",
            "FRONTEND_ORIGINS": "http://127.0.0.1:3000,http://localhost:3000",
        }
    )

    assert report.public_frontend_origin == "http://127.0.0.1:3000"


def test_production_rejects_localhost_frontend_origin() -> None:
    with pytest.raises(CloudCutoverConfigError, match="localhost"):
        assert_cloud_cutover_config(
            {
                "ORION_ENV": "production",
                "FRONTEND_ORIGINS": "http://127.0.0.1:3000",
                "EMPYRALIS_PUBLIC_API_URL": "https://runtime.example.com",
            }
        )


def test_production_rejects_wildcard_frontend_origin() -> None:
    with pytest.raises(CloudCutoverConfigError, match="absolute URL"):
        assert_cloud_cutover_config(
            {
                "ORION_ENV": "production",
                "FRONTEND_ORIGINS": "*",
                "EMPYRALIS_PUBLIC_API_URL": "https://runtime.example.com",
            }
        )


def test_production_requires_https_public_api_url() -> None:
    with pytest.raises(CloudCutoverConfigError, match="scheme: https"):
        assert_cloud_cutover_config(
            {
                "ORION_ENV": "production",
                "FRONTEND_ORIGINS": "https://app.example.com",
                "EMPYRALIS_PUBLIC_API_URL": "http://runtime.example.com",
            }
        )


def test_production_rejects_invalid_websocket_scheme() -> None:
    with pytest.raises(CloudCutoverConfigError, match="gateway websocket URL"):
        assert_cloud_cutover_config(
            {
                "ORION_ENV": "production",
                "FRONTEND_ORIGINS": "https://app.example.com",
                "EMPYRALIS_PUBLIC_API_URL": "https://runtime.example.com",
                "EMPYRALIS_GATEWAY_WS_URL": "ws://runtime.example.com/gateway",
            }
        )


def test_production_privacy_url_uses_public_frontend_origin() -> None:
    assert resolve_privacy_policy_url(
        {
            "ORION_ENV": "production",
            "EMPYRALIS_PUBLIC_FRONTEND_ORIGIN": "https://app.example.com",
        }
    ) == "https://app.example.com/privacy"
