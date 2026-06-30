from __future__ import annotations

from server_modules import agent_computer_permission_secret_model as model


def test_local_failure_permission_denied_blocks_with_safe_summary() -> None:
    classification = model.local_failure_classification(
        "Screen Recording permission denied for token=TEST_SECRET_CANARY_VALUE"
    )

    assert classification["state"] == "blocked"
    assert classification["reason"] == "local_permission_denied"
    assert "permission" in classification["summary"].lower()
    assert "TEST_SECRET_CANARY_VALUE" not in str(classification)


def test_local_failure_offline_remains_offline_and_redacted() -> None:
    classification = model.local_failure_classification(
        "Gateway is not currently connected. token=TEST_BEARER_CANARY_VALUE"
    )

    assert classification["state"] == "offline"
    assert classification["reason"] == "gateway_offline"
    assert "token=[redacted-secret]" in classification["summary"]
    assert "TEST_BEARER_CANARY_VALUE" not in classification["summary"]


def test_diagnostic_metadata_redacts_secret_like_values() -> None:
    payload = model.diagnostic_metadata(
        {
            "gateway_id": "gw-1",
            "api_key": "TEST_SECRET_CANARY_VALUE",
            "headers": ["api-key: TEST_BEARER_CANARY_VALUE"],
            "phone": "+15555551212",
        }
    )

    rendered = str(payload)
    assert payload["gateway_id"] == "gw-1"
    assert "TEST_SECRET_CANARY_VALUE" not in rendered
    assert "TEST_BEARER_CANARY_VALUE" not in rendered
    assert "+15555551212" not in rendered
    assert "[redacted" in rendered
