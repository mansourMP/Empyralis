from __future__ import annotations

from server_modules import vault_helpers


def test_list_vault_credentials_redacts_sensitive_metadata() -> None:
    def _load_vault():
        return {
            "credentials": [
                {
                    "id": "cred-1",
                    "label": "OpenAI Key",
                    "provider": "openai",
                    "workspace_id": "default",
                    "metadata": {
                        "api_key": "sk-live-secret",
                        "authorization": "Bearer abc.def.ghi",
                        "phone_number": "+1 415 555 1234",
                        "safe": "visible",
                    },
                }
            ]
        }

    entries = vault_helpers.list_vault_credentials(_load_vault, connector_catalog={}, workspace_id="default")
    assert len(entries) == 1
    metadata = entries[0]["metadata"]
    assert metadata["api_key"] == "[redacted]"
    assert metadata["authorization"] == "[redacted]"
    assert metadata["phone_number"] == "[redacted]"
    assert metadata["safe"] == "visible"


def test_list_vault_connectors_redacts_sensitive_metadata() -> None:
    def _load_vault():
        return {
            "credentials": [
                {
                    "id": "cred-2",
                    "label": "Telegram Connector",
                    "provider": "telegram_bot",
                    "workspace_id": "default",
                    "metadata": {
                        "session_cookie": "sid=abc123",
                        "safe": "visible",
                    },
                }
            ]
        }

    entries = vault_helpers.list_vault_connectors(
        _load_vault,
        connector_catalog={"telegram_bot": {"id": "telegram_bot"}},
        workspace_id="default",
    )
    assert len(entries) == 1
    metadata = entries[0]["metadata"]
    assert metadata["session_cookie"] == "[redacted]"
    assert metadata["safe"] == "visible"
