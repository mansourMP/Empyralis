"""APNs sends produce THREE facts, and the whole point is that they cannot
be confused with each other.

    no key on this deployment  -> status "not_configured", NOTHING attempted
    Apple accepted             -> status "sent"
    Apple/the network refused  -> status "failed", carrying Apple's own code

This is workspace_invite_email_service's lesson applied to push: collapsing
"not configured" into "failed" sends an operator to fix a working system,
and collapsing either into "sent" is the lie. The tests below assert the
three are pairwise distinct explicitly, and assert CALL COUNTS -- "a send
happened" is satisfied by two sends just as happily as by one, and the
not_configured case only means anything if the transport was never touched.

The transport is MOCKED throughout. No test here may reach Apple (conftest's
socket guard would fail it anyway), and a test that pushed to a real device
would be worse than a failing one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from server_modules import apns_service


# A throwaway P-256 key, generated in-process. Never a checked-in secret,
# and never Apple's -- the signing path is real, only the key is ours.
def _test_key_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _configured_env() -> Dict[str, str]:
    return {
        "EMPYRALIS_APNS_KEY_P8": _test_key_pem(),
        "EMPYRALIS_APNS_KEY_ID": "ABC1234567",
        "EMPYRALIS_APNS_TEAM_ID": "TEAM123456",
        "EMPYRALIS_APNS_BUNDLE_ID": "ai.empyralis.app",
        "EMPYRALIS_APNS_ENVIRONMENT": "production",
    }


class _RecordingClient:
    """Stands in for httpx.AsyncClient and counts what was attempted."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.calls: List[Dict[str, Any]] = []

    async def post(self, url: str, json: Any = None, headers: Optional[Dict] = None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        return self._response

    async def aclose(self) -> None:  # pragma: no cover - never owned by send_push
        pass


def _response(status_code: int, body: Any = None, apns_id: str = "apns-1") -> httpx.Response:
    return httpx.Response(
        status_code,
        json=body if body is not None else {},
        headers={"apns-id": apns_id},
        request=httpx.Request("POST", "https://api.push.apple.com/3/device/tok"),
    )


@pytest.mark.asyncio
async def test_no_credentials_is_not_configured_and_attempts_nothing():
    client = _RecordingClient(_response(200))
    result = await apns_service.send_push(
        device_token="abc123",
        payload={"aps": {}},
        client=client,
        env={},
    )
    assert result.status == apns_service.SEND_NOT_CONFIGURED
    assert result.ok is False
    # The whole reason this state exists: nothing was tried, so there is
    # nothing to retry and no failure to report.
    assert client.calls == []
    assert result.reason == ""


@pytest.mark.asyncio
async def test_half_configured_is_still_not_configured_never_failed():
    """A missing key id can only ever produce rejections. Reporting those as
    `failed` is the "retry something that can never work" mistake."""
    env = _configured_env()
    env["EMPYRALIS_APNS_KEY_ID"] = ""
    client = _RecordingClient(_response(200))
    result = await apns_service.send_push(
        device_token="abc123", payload={"aps": {}}, client=client, env=env
    )
    assert result.status == apns_service.SEND_NOT_CONFIGURED
    assert client.calls == []


@pytest.mark.asyncio
async def test_apple_accepts_is_sent_exactly_once():
    client = _RecordingClient(_response(200, apns_id="apns-sent-1"))
    result = await apns_service.send_push(
        device_token="abc123",
        payload=apns_service.build_payload(title="Hi", body="There"),
        client=client,
        env=_configured_env(),
    )
    assert result.status == apns_service.SEND_SENT
    assert result.ok is True
    assert result.apns_id == "apns-sent-1"
    assert result.reason == ""
    assert result.token_deactivated is False
    # Exactly one -- "a send happened" is satisfied by two.
    assert len(client.calls) == 1
    assert client.calls[0]["url"].endswith("/3/device/abc123")
    assert client.calls[0]["headers"]["apns-topic"] == "ai.empyralis.app"
    assert client.calls[0]["headers"]["authorization"].startswith("bearer ")


@pytest.mark.asyncio
async def test_apple_rejects_is_failed_and_carries_apples_own_reason_code():
    client = _RecordingClient(_response(400, {"reason": "PayloadTooLarge"}))
    result = await apns_service.send_push(
        device_token="abc123", payload={"aps": {}}, client=client, env=_configured_env()
    )
    assert result.status == apns_service.SEND_FAILED
    assert result.ok is False
    assert result.reason == "PayloadTooLarge"
    assert result.status_code == 400
    # An ordinary rejection must NOT retire the device.
    assert result.token_deactivated is False


@pytest.mark.asyncio
async def test_the_three_states_are_pairwise_distinct():
    """The contract, asserted directly rather than implied by the tests
    above: no two of these ever share a status value."""
    env = _configured_env()
    sent = await apns_service.send_push(
        device_token="t", payload={}, client=_RecordingClient(_response(200)), env=env
    )
    failed = await apns_service.send_push(
        device_token="t",
        payload={},
        client=_RecordingClient(_response(400, {"reason": "BadTopic"})),
        env=env,
    )
    not_configured = await apns_service.send_push(
        device_token="t", payload={}, client=_RecordingClient(_response(200)), env={}
    )
    statuses = {sent.status, failed.status, not_configured.status}
    assert len(statuses) == 3
    assert statuses == {
        apns_service.SEND_SENT,
        apns_service.SEND_FAILED,
        apns_service.SEND_NOT_CONFIGURED,
    }
    # And only one of the three is success.
    assert [sent.ok, failed.ok, not_configured.ok] == [True, False, False]


@pytest.mark.asyncio
async def test_410_unregistered_deactivates_the_token():
    client = _RecordingClient(_response(410, {"reason": "Unregistered"}))
    deactivate = AsyncMock(return_value=True)
    with patch.object(
        apns_service.push_device_repository, "deactivate_token_globally", deactivate
    ):
        result = await apns_service.send_push(
            device_token="deadtoken",
            payload={"aps": {}},
            client=client,
            env=_configured_env(),
        )
    assert result.status == apns_service.SEND_FAILED
    assert result.reason == "Unregistered"
    assert result.token_deactivated is True
    deactivate.assert_awaited_once_with(device_token="deadtoken")


@pytest.mark.asyncio
async def test_bad_device_token_also_deactivates():
    client = _RecordingClient(_response(400, {"reason": "BadDeviceToken"}))
    deactivate = AsyncMock(return_value=True)
    with patch.object(
        apns_service.push_device_repository, "deactivate_token_globally", deactivate
    ):
        result = await apns_service.send_push(
            device_token="badtoken", payload={}, client=client, env=_configured_env()
        )
    assert result.token_deactivated is True
    deactivate.assert_awaited_once_with(device_token="badtoken")


@pytest.mark.asyncio
async def test_a_deactivation_failure_does_not_become_a_success():
    """If the row cannot be retired, the send is still `failed` and the
    result says the token was NOT deactivated -- never a silent claim."""
    client = _RecordingClient(_response(410, {"reason": "Unregistered"}))
    with patch.object(
        apns_service.push_device_repository,
        "deactivate_token_globally",
        AsyncMock(side_effect=RuntimeError("no pool")),
    ):
        result = await apns_service.send_push(
            device_token="deadtoken", payload={}, client=client, env=_configured_env()
        )
    assert result.status == apns_service.SEND_FAILED
    assert result.token_deactivated is False


@pytest.mark.asyncio
async def test_transport_error_is_failed_never_raised():
    class _Boom:
        async def post(self, *a, **k):
            raise httpx.ConnectError("nope")

        async def aclose(self):
            pass

    result = await apns_service.send_push(
        device_token="abc", payload={}, client=_Boom(), env=_configured_env()
    )
    assert result.status == apns_service.SEND_FAILED
    assert result.reason == "TransportError"


@pytest.mark.asyncio
async def test_sandbox_token_goes_to_the_sandbox_host():
    """The environment is a property of the TOKEN, not the deployment -- a
    sandbox token sent to the production host is rejected by Apple."""
    client = _RecordingClient(_response(200))
    await apns_service.send_push(
        device_token="abc",
        payload={},
        environment="sandbox",
        client=client,
        env=_configured_env(),
    )
    assert client.calls[0]["url"].startswith(apns_service.APNS_HOST_SANDBOX)


def test_provider_jwt_is_reused_within_its_window():
    """Apple refuses provider tokens minted more than once per 20 minutes,
    so mint-per-send breaks under the load it exists to serve."""
    config = apns_service.load_config(_configured_env())
    assert config is not None
    first = apns_service.build_provider_jwt(config, now=1_000_000)
    second = apns_service.build_provider_jwt(config, now=1_000_060)
    assert first == second
    later = apns_service.build_provider_jwt(config, now=1_000_000 + 60 * 60)
    assert later != first
    assert first.count(".") == 2


def test_key_path_that_does_not_exist_is_not_configured():
    env = _configured_env()
    env["EMPYRALIS_APNS_KEY_P8"] = "/nonexistent/path/to/key.p8"
    assert apns_service.load_config(env) is None
    assert apns_service.is_configured(env) is False
