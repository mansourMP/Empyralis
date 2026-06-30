from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from server_modules import multimodal_provider_service as service


def _run(coro):
    return asyncio.run(coro)


def test_provider_metadata_describes_openai_stt_without_secrets():
    metadata = service.provider_metadata(modality="speech_to_text", provider="openai")

    payload = metadata.as_dict()
    assert payload["provider"] == "openai"
    assert payload["model"]
    assert "api" not in str(payload).lower()
    assert "key" not in str(payload).lower()


def test_openai_stt_adapter_preserves_transcript_contract():
    with patch(
        "server_modules.multimodal_provider_service._transcribe_with_openai",
        new=AsyncMock(return_value={"transcript": "hello", "confidence": 1.0, "provider": "openai"}),
    ) as mock_transcribe:
        result = _run(service.transcribe_audio_bytes(b"audio", "audio/webm", provider="openai"))

    assert result["transcript"] == "hello"
    assert result["provider"] == "openai"
    mock_transcribe.assert_awaited_once_with(b"audio", "audio/webm")


def test_tts_routes_through_provider_service():
    with patch(
        "server_modules.multimodal_provider_service._synthesize_with_openai",
        new=AsyncMock(return_value=b"audio"),
    ) as mock_synthesize:
        result = _run(service.synthesize_speech_chunks("Read this.", "alloy", 1.0, provider="openai"))

    assert result == [b"audio"]
    mock_synthesize.assert_awaited_once_with("Read this.", "alloy", 1.0)


def test_missing_tts_provider_returns_clear_error():
    with pytest.raises(service.MultimodalProviderUnavailable, match="Text-to-speech provider is not configured"):
        _run(service.synthesize_speech_chunks("Read this.", "alloy", 1.0, provider="missing_provider"))


def test_elevenlabs_adapter_requires_configuration(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_APIKEY", raising=False)

    with pytest.raises(service.MultimodalProviderUnavailable, match="ELEVENLABS_API_KEY is not configured"):
        _run(service.transcribe_audio_bytes(b"audio", "audio/webm", provider="elevenlabs"))


def test_image_and_video_interfaces_fail_closed():
    with pytest.raises(service.UnsupportedModalityError, match="image_generation"):
        _run(service.generate_image(prompt="make a diagram"))

    with pytest.raises(service.UnsupportedModalityError, match="video_generation"):
        _run(service.generate_video(prompt="make a clip"))


def test_voice_policy_still_blocks_transcribed_approval_attempt():
    from server_modules import voice_notification_policy_service

    with (
        patch(
            "server_modules.multimodal_provider_service._transcribe_with_openai",
            new=AsyncMock(return_value={"transcript": "approve sap_abc1234567890", "confidence": 1.0}),
        ),
        patch("server_modules.voice_notification_policy_service.activity_ledger_service.append_activity_event", new=AsyncMock()),
        patch("server_modules.voice_notification_policy_service.security_audit_service.emit_security_audit_event"),
        patch("server_modules.sage_turn_adapter.execute_sage_turn", new=AsyncMock()) as mock_execute,
    ):
        transcript = _run(service.transcribe_audio_bytes(b"audio", "audio/webm", provider="openai"))["transcript"]
        result = _run(voice_notification_policy_service.execute_voice_sage_task(
            workspace_id="ws-1",
            tenant_id="tenant-1",
            transcript=transcript,
            current_user={"user_id": "user-1"},
        ))

    assert result["error"] == voice_notification_policy_service.VOICE_APPROVAL_BLOCKED_ERROR
    mock_execute.assert_not_called()
