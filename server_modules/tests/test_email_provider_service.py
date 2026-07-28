from __future__ import annotations

import httpx
import pytest

from server_modules import email_provider_service as service


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.text = text

    def json(self):
        return self._json_body


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient so no real network call is made."""

    last_request: dict | None = None
    response: _FakeResponse = _FakeResponse(200, {"id": "email_123"})
    raise_error: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, headers=None, json=None):
        type(self).last_request = {"url": url, "headers": headers, "json": json}
        if type(self).raise_error is not None:
            raise type(self).raise_error
        return type(self).response


@pytest.fixture(autouse=True)
def _reset_fake_client():
    _FakeAsyncClient.last_request = None
    _FakeAsyncClient.response = _FakeResponse(200, {"id": "email_123"})
    _FakeAsyncClient.raise_error = None
    yield


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("EMAIL_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_PROVIDER_FROM_ADDRESS", raising=False)


class TestEmailProviderConfiguration:
    def test_not_configured_without_api_key(self):
        assert service.email_provider_configured() is False

    def test_configured_once_api_key_set(self, monkeypatch):
        monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "re_test_key")
        assert service.email_provider_configured() is True


class TestSendEmailFailsClearlyWhenUnconfigured:
    @pytest.mark.anyio
    async def test_missing_api_key_raises_unavailable_not_silent(self):
        with pytest.raises(service.EmailProviderUnavailable, match="EMAIL_PROVIDER_API_KEY is not configured"):
            await service.send_email(to="user@example.com", subject="Your code", html="<p>123456</p>")

    @pytest.mark.anyio
    async def test_missing_to_address_raises(self, monkeypatch):
        monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "re_test_key")
        with pytest.raises(service.EmailProviderError):
            await service.send_email(to="", subject="Your code", html="<p>123456</p>")


class TestSendEmailPayloadConstruction:
    @pytest.mark.anyio
    async def test_sends_correct_payload_and_headers(self, monkeypatch):
        monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "re_test_key")
        monkeypatch.setattr(service.httpx, "AsyncClient", _FakeAsyncClient)

        result = await service.send_email(
            to="new-user@example.com",
            subject="Your Empyralis verification code",
            html="<p>123456</p>",
            text="123456",
        )

        assert result == {"id": "email_123"}
        request = _FakeAsyncClient.last_request
        assert request is not None
        assert request["url"] == service.RESEND_API_URL
        assert request["headers"]["Authorization"] == "Bearer re_test_key"
        assert request["headers"]["Content-Type"] == "application/json"
        assert request["json"]["to"] == ["new-user@example.com"]
        assert request["json"]["subject"] == "Your Empyralis verification code"
        assert request["json"]["html"] == "<p>123456</p>"
        assert request["json"]["text"] == "123456"
        assert request["json"]["from"] == service.DEFAULT_FROM_ADDRESS

    @pytest.mark.anyio
    async def test_uses_custom_from_address_when_set(self, monkeypatch):
        monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "re_test_key")
        monkeypatch.setenv("EMAIL_PROVIDER_FROM_ADDRESS", "Empyralis <hello@empyralis.ai>")
        monkeypatch.setattr(service.httpx, "AsyncClient", _FakeAsyncClient)

        await service.send_email(to="user@example.com", subject="Subject", html="<p>hi</p>")

        assert _FakeAsyncClient.last_request["json"]["from"] == "Empyralis <hello@empyralis.ai>"

    @pytest.mark.anyio
    async def test_provider_rejection_raises_send_failed(self, monkeypatch):
        monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "re_test_key")
        _FakeAsyncClient.response = _FakeResponse(422, text="invalid 'to' field")
        monkeypatch.setattr(service.httpx, "AsyncClient", _FakeAsyncClient)

        with pytest.raises(service.EmailSendFailed, match="status 422"):
            await service.send_email(to="user@example.com", subject="Subject", html="<p>hi</p>")

    @pytest.mark.anyio
    async def test_network_failure_raises_send_failed(self, monkeypatch):
        monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "re_test_key")
        _FakeAsyncClient.raise_error = httpx.ConnectError("connection refused")
        monkeypatch.setattr(service.httpx, "AsyncClient", _FakeAsyncClient)

        with pytest.raises(service.EmailSendFailed, match="Could not reach the email provider"):
            await service.send_email(to="user@example.com", subject="Subject", html="<p>hi</p>")
