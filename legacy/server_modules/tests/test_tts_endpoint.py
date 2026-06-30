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
    "server_modules.runtime_runtime_api._synthesize_tts_chunks",
    new_callable=AsyncMock,
    return_value=[b"audio-1", b"audio-2"],
)
async def test_audio_streamed_back(mock_synthesize: AsyncMock):
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/tts",
            json={"text": "Read this aloud.", "voice": "alloy", "speed": 1.0},
        )

    assert response.status_code == 200
    assert response.headers.get("content-type") == "audio/mpeg"
    assert response.content == b"audio-1audio-2"
    mock_synthesize.assert_awaited_once_with("Read this aloud.", "alloy", 1.0)


def test_text_split_at_4096_chars():
    long_text = ("Sentence. " * 600).strip()

    chunks = runtime_runtime_api._split_tts_text(long_text)

    assert len(chunks) > 1
    assert all(len(chunk) <= runtime_runtime_api.TTS_MAX_CHARS for chunk in chunks)
    assert " ".join(chunks).replace("  ", " ").strip() == long_text
