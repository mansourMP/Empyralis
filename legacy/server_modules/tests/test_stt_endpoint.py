from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from server_modules import runtime_runtime_api


def _build_app() -> FastAPI:
    app = FastAPI()
    runtime_runtime_api.register_runtime_routes(app)
    app.dependency_overrides[runtime_runtime_api.require_api_key] = lambda: {"sub": "test-admin"}
    return app


@pytest.mark.anyio
@patch(
    "server_modules.runtime_runtime_api._transcribe_audio_bytes",
    new_callable=AsyncMock,
    return_value={"transcript": "hello from whisper", "confidence": 0.91},
)
async def test_transcript_returned(mock_transcribe: AsyncMock):
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/stt",
            content=b"RIFF....WAVEfmt",
            headers={"Content-Type": "audio/wav"},
        )

    assert response.status_code == 200
    assert response.json()["transcript"] == "hello from whisper"
    assert float(response.json()["confidence"]) == pytest.approx(0.91)
    mock_transcribe.assert_awaited_once()


@pytest.mark.anyio
async def test_unsupported_format_rejected():
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/stt",
            content=b"plain-text",
            headers={"Content-Type": "text/plain"},
        )

    assert response.status_code == 415
    assert "Unsupported audio format" in response.text
