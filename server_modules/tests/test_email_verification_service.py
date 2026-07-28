from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from server_modules import control_plane_repository, email_provider_service
from server_modules import email_verification_service as service


@pytest.fixture(autouse=True)
def _mock_send_email(monkeypatch):
    """Every test in this file exercises the verification-code business
    logic, not real email delivery -- send_email is mocked here the same way
    test_multimodal_provider_service.py mocks out the underlying HTTP call.
    Individual tests that specifically want to assert send_email's own
    behavior (payload construction, missing-key handling) live in
    test_email_provider_service.py instead."""
    mock = AsyncMock(return_value={"id": "email_mocked"})
    monkeypatch.setattr(service.email_provider_service, "send_email", mock)
    return mock


class TestGenerateCode:
    def test_code_is_six_digits_numeric(self):
        code = service.generate_code()
        assert len(code) == 6
        assert code.isdigit()

    def test_codes_vary_across_calls(self):
        codes = {service.generate_code() for _ in range(50)}
        # Astronomically unlikely to collide 50/50 times if this were
        # predictable or constant.
        assert len(codes) > 1

    def test_zero_pads_small_values(self, monkeypatch):
        monkeypatch.setattr(service.secrets, "randbelow", lambda _n: 7)
        assert service.generate_code() == "000007"


class TestHashCode:
    def test_hash_is_deterministic_for_same_inputs(self):
        first = service._hash_code("123456", user_id="user-1")
        second = service._hash_code("123456", user_id="user-1")
        assert first == second

    def test_hash_differs_across_users_for_same_code(self):
        first = service._hash_code("123456", user_id="user-1")
        second = service._hash_code("123456", user_id="user-2")
        assert first != second

    def test_hash_is_not_the_plaintext_code(self):
        digest = service._hash_code("123456", user_id="user-1")
        assert "123456" not in digest

    def test_verify_code_hash_round_trips(self):
        digest = service._hash_code("654321", user_id="user-9")
        assert service._verify_code_hash("654321", user_id="user-9", code_hash=digest) is True
        assert service._verify_code_hash("000000", user_id="user-9", code_hash=digest) is False


class TestStartVerification:
    @pytest.mark.anyio
    async def test_creates_pending_code_and_sends_email(self, _mock_send_email):
        await service.start_verification(user_id="user-1", email="new-user@example.com")

        record = await control_plane_repository.get_latest_email_verification_code("user-1")
        assert record is not None
        assert record["status"] == "pending"
        assert record["attempts"] == 0
        assert record["email"] == "new-user@example.com"
        assert record["expires_at"] > int(time.time())

        _mock_send_email.assert_awaited_once()
        kwargs = _mock_send_email.await_args.kwargs
        assert kwargs["to"] == "new-user@example.com"
        assert "verification code" in kwargs["subject"].lower()

    @pytest.mark.anyio
    async def test_rejects_missing_user_id_or_email(self):
        with pytest.raises(HTTPException) as exc_info:
            await service.start_verification(user_id="", email="user@example.com")
        assert exc_info.value.status_code == 400

    @pytest.mark.anyio
    async def test_propagates_provider_unavailable_instead_of_swallowing_it(self, _mock_send_email):
        # start_verification must not swallow a provider failure -- it's the
        # caller's job to decide what that means (register_user logs and
        # proceeds; the resend route turns it into an HTTP 503). Real,
        # unmocked "missing API key" behavior is covered end to end in
        # test_email_provider_service.py; here we only need to prove this
        # service layer doesn't catch and hide the exception.
        _mock_send_email.side_effect = email_provider_service.EmailProviderUnavailable(
            "EMAIL_PROVIDER_API_KEY is not configured."
        )

        with pytest.raises(email_provider_service.EmailProviderUnavailable):
            await service.start_verification(user_id="user-1", email="user@example.com")

        # The code row was still created (account creation / code issuance
        # is not rolled back just because the send failed).
        record = await control_plane_repository.get_latest_email_verification_code("user-1")
        assert record is not None
        assert record["status"] == "pending"


class TestVerifyCode:
    @pytest.mark.anyio
    async def test_correct_code_marks_verified(self, monkeypatch):
        monkeypatch.setattr(service, "generate_code", lambda: "424242")
        await service.start_verification(user_id="user-1", email="user@example.com")

        await service.verify_code(user_id="user-1", code="424242")

        assert await service.verification_status("user-1") == "verified"
        assert await service.is_verified("user-1") is True

    @pytest.mark.anyio
    async def test_incorrect_code_raises_400_and_increments_attempts(self, monkeypatch):
        monkeypatch.setattr(service, "generate_code", lambda: "424242")
        await service.start_verification(user_id="user-1", email="user@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await service.verify_code(user_id="user-1", code="000000")
        assert exc_info.value.status_code == 400

        record = await control_plane_repository.get_latest_email_verification_code("user-1")
        assert record["attempts"] == 1
        assert record["status"] == "pending"

    @pytest.mark.anyio
    async def test_locks_out_after_max_attempts(self, monkeypatch):
        monkeypatch.setenv("EMAIL_VERIFICATION_MAX_ATTEMPTS", "2")
        monkeypatch.setattr(service, "generate_code", lambda: "424242")
        await service.start_verification(user_id="user-1", email="user@example.com")

        for _ in range(2):
            with pytest.raises(HTTPException) as exc_info:
                await service.verify_code(user_id="user-1", code="000000")
            assert exc_info.value.status_code == 400

        # Third attempt is locked out even with the correct code.
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_code(user_id="user-1", code="424242")
        assert exc_info.value.status_code == 429

    @pytest.mark.anyio
    async def test_expired_code_raises_410(self):
        expired_record = await control_plane_repository.create_email_verification_code(
            user_id="user-1",
            email="user@example.com",
            code_hash=service._hash_code("111111", user_id="user-1"),
            expires_at_epoch=int(time.time()) - 5,
        )
        assert expired_record is not None

        with pytest.raises(HTTPException) as exc_info:
            await service.verify_code(user_id="user-1", code="111111")
        assert exc_info.value.status_code == 410

    @pytest.mark.anyio
    async def test_no_code_ever_issued_raises_404(self):
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_code(user_id="never-signed-up", code="123456")
        assert exc_info.value.status_code == 404

    @pytest.mark.anyio
    async def test_verifying_already_verified_code_is_a_no_op_success(self, monkeypatch):
        monkeypatch.setattr(service, "generate_code", lambda: "424242")
        await service.start_verification(user_id="user-1", email="user@example.com")
        await service.verify_code(user_id="user-1", code="424242")

        # Calling verify again (e.g. a duplicate submit) should not error.
        await service.verify_code(user_id="user-1", code="424242")

    @pytest.mark.anyio
    async def test_blank_code_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_code(user_id="user-1", code="   ")
        assert exc_info.value.status_code == 400


class TestResendVerification:
    @pytest.mark.anyio
    async def test_resend_within_min_interval_is_rate_limited(self, monkeypatch):
        monkeypatch.setenv("EMAIL_VERIFICATION_MIN_RESEND_INTERVAL_SECONDS", "60")
        await service.start_verification(user_id="user-1", email="user@example.com")

        with pytest.raises(HTTPException) as exc_info:
            await service.resend_verification(user_id="user-1", email="user@example.com")
        assert exc_info.value.status_code == 429

    @pytest.mark.anyio
    async def test_resend_after_interval_issues_new_code(self, monkeypatch, _mock_send_email):
        monkeypatch.setenv("EMAIL_VERIFICATION_MIN_RESEND_INTERVAL_SECONDS", "0")
        await service.start_verification(user_id="user-1", email="user@example.com")
        first = await control_plane_repository.get_latest_email_verification_code("user-1")

        await service.resend_verification(user_id="user-1", email="user@example.com")
        second = await control_plane_repository.get_latest_email_verification_code("user-1")

        assert second["id"] != first["id"]
        assert _mock_send_email.await_count == 2

    @pytest.mark.anyio
    async def test_resend_after_verification_is_not_rate_limited(self, monkeypatch):
        monkeypatch.setattr(service, "generate_code", lambda: "424242")
        monkeypatch.setenv("EMAIL_VERIFICATION_MIN_RESEND_INTERVAL_SECONDS", "600")
        await service.start_verification(user_id="user-1", email="user@example.com")
        await service.verify_code(user_id="user-1", code="424242")

        # Already verified -- a resend request right after should still be
        # allowed (status isn't 'pending' anymore, so the cooldown doesn't
        # apply). Harmless: the frontend only calls this from the
        # not-yet-verified screen, but the backend should not 429 here.
        await service.resend_verification(user_id="user-1", email="user@example.com")


class TestVerificationStatus:
    @pytest.mark.anyio
    async def test_none_when_never_issued(self):
        assert await service.verification_status("brand-new-legacy-user") == "none"
        assert await service.is_verified("brand-new-legacy-user") is True

    @pytest.mark.anyio
    async def test_pending_after_start(self):
        await service.start_verification(user_id="user-1", email="user@example.com")
        assert await service.verification_status("user-1") == "pending"
        assert await service.is_verified("user-1") is False
