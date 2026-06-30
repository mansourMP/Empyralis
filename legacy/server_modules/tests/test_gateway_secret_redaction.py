from __future__ import annotations

from server_modules import secret_redaction_service


class TestSecretRedactionSanitizeMapping:
    """Tests for sanitize_mapping which redacts sensitive values from dicts."""

    def test_api_key_redacted_from_flat_dict(self) -> None:
        data = {"api_key": "sk-live-12345", "name": "test"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["api_key"] == "[redacted]"
        assert result["name"] == "test"

    def test_token_in_nested_dict_redacted(self) -> None:
        data = {"config": {"token": "ghp_abc123", "user": "bob"}}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["config"]["token"] == "[redacted]"
        assert result["config"]["user"] == "bob"

    def test_apikey_camelcase_redacted(self) -> None:
        """'apikey' (the camelCase normalization) is in the sensitive key list."""
        data = {"apikey": "sk-live-xyz"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["apikey"] == "[redacted]"

    def test_password_redacted(self) -> None:
        data = {"password": "hunter2"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["password"] == "[redacted]"

    def test_non_sensitive_fields_preserved(self) -> None:
        data = {
            "name": "Alice",
            "email": "alice@example.com",
            "count": 42,
            "active": True,
            "score": 3.14,
        }
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["name"] == "Alice"
        assert result["email"] == "alice@example.com"
        assert result["count"] == 42
        assert result["active"] is True
        assert result["score"] == 3.14

    def test_original_dict_not_mutated(self) -> None:
        original = {"api_key": "secret123", "name": "test"}
        secret_redaction_service.sanitize_mapping(original)
        # Original values must be unchanged
        assert original["api_key"] == "secret123"
        assert original["name"] == "test"

    def test_secret_in_list_of_dicts_redacted(self) -> None:
        data = {
            "items": [
                {"name": "item1", "secret": "sensitive-data"},
                {"name": "item2", "secret": "also-sensitive"},
            ]
        }
        result = secret_redaction_service.sanitize_mapping(data)
        for item in result["items"]:
            assert item["secret"] == "[redacted]"
        assert result["items"][0]["name"] == "item1"
        assert result["items"][1]["name"] == "item2"

    def test_authorization_header_redacted(self) -> None:
        data = {"authorization": "Bearer my-token-example"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["authorization"] == "[redacted]"

    def test_cookie_redacted(self) -> None:
        data = {"cookie": "sessionid=abc123"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["cookie"] == "[redacted]"

    def test_deeply_nested_structure_redacted(self) -> None:
        data = {
            "outer": {
                "inner": {
                    "api_key": "deep-secret",
                    "normal": "visible",
                },
                "list": [{"token": "tok-123"}],
            }
        }
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["outer"]["inner"]["api_key"] == "[redacted]"
        assert result["outer"]["inner"]["normal"] == "visible"
        assert result["outer"]["list"][0]["token"] == "[redacted]"

    def test_access_token_redacted(self) -> None:
        data = {"access_token": "ya29.abc123"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["access_token"] == "[redacted]"

    def test_refresh_token_redacted(self) -> None:
        data = {"refresh_token": "1//abc123"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["refresh_token"] == "[redacted]"

    def test_private_key_redacted(self) -> None:
        data = {"private_key": "-----BEGIN RSA PRIVATE KEY-----"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["private_key"] == "[redacted]"

    def test_client_secret_redacted(self) -> None:
        data = {"client_secret": "my-client-secret-value"}
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["client_secret"] == "[redacted]"

    def test_multiple_sensitive_fields_all_redacted(self) -> None:
        data = {
            "api_key": "key-1",
            "token": "tok-1",
            "password": "pwd-1",
            "name": "bob",
            "email": "bob@example.com",
        }
        result = secret_redaction_service.sanitize_mapping(data)
        assert result["api_key"] == "[redacted]"
        assert result["token"] == "[redacted]"
        assert result["password"] == "[redacted]"
        assert result["name"] == "bob"
        assert result["email"] == "bob@example.com"

    def test_empty_dict_returns_empty(self) -> None:
        result = secret_redaction_service.sanitize_mapping({})
        assert result == {}

    def test_none_input_returns_empty(self) -> None:
        result = secret_redaction_service.sanitize_mapping(None)
        assert result == {}


class TestSecretRedactionSanitizeValue:
    """Tests for the lower-level sanitize_value function."""

    def test_string_value_pattern_based_redaction(self) -> None:
        """Bearer tokens in string values are pattern-redacted."""
        result = secret_redaction_service.sanitize_value(
            "Authorization: Bearer abc.def.ghi",
        )
        assert "[redacted]" in result
        assert "abc.def.ghi" not in result

    def test_api_key_pattern_in_text_redacted(self) -> None:
        """x-api-key header patterns in raw text are redacted."""
        result = secret_redaction_service.sanitize_value(
            "x-api-key: my-secret-key",
        )
        assert "x-api-key: [redacted]" in result

    def test_unknown_high_entropy_token_in_text_redacted(self) -> None:
        token = "Qx9v_Lp2R8mN4sT6uY7zA1bC3dE5fG"
        result = secret_redaction_service.sanitize_value(
            f"tool output included {token} from a B2B connector",
        )
        assert "[redacted-secret]" in result
        assert token not in result

    def test_common_raw_secret_formats_are_redacted_from_text(self) -> None:
        samples = {
            "stripe": "stripe=sk_live_51N7abcdefghijklmnopqrstuvwxyz",
            "telegram": "telegram=123456789:ABCdefGhIJKlmNoPQRstuVWxyz1234567",
            "discord": "discord=mfa.Qx9vLp2R8mN4sT6uY7zA1bC3dE5fG",
            "aws": "aws=AKIAIOSFODNN7EXAMPLE",
            "ssh": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
            "google": '{"type":"service_account","private_key":"-----BEGIN PRIVATE KEY-----\\nabc"}',
            "docker": '{"auths":{"registry.example.com":{"auth":"dXNlcjpwYXNz"}}}',
        }
        for label, text in samples.items():
            result = secret_redaction_service.sanitize_value(text)
            assert result != text, label
            assert "[redacted" in result, label

    def test_normal_text_and_url_are_not_entropy_redacted(self) -> None:
        text = "Open https://docs.example.com/product/reference for the normal integration documentation."
        result = secret_redaction_service.sanitize_value(text)
        assert result == text

    def test_bool_value_preserved(self) -> None:
        result = secret_redaction_service.sanitize_value(True)
        assert result is True

    def test_int_value_preserved(self) -> None:
        result = secret_redaction_service.sanitize_value(42)
        assert result == 42

    def test_float_value_preserved(self) -> None:
        result = secret_redaction_service.sanitize_value(3.14)
        assert result == 3.14

    def test_none_value_preserved(self) -> None:
        result = secret_redaction_service.sanitize_value(None)
        assert result is None
