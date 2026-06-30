from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from server import request_body_limit_guard


def _build_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(request_body_limit_guard)

    @app.post("/api/echo")
    async def echo(request: Request):
        body = await request.body()
        return {"size": len(body)}

    @app.post("/channels/slack/events")
    async def webhook_echo(request: Request):
        body = await request.body()
        return {"size": len(body)}

    @app.post("/api/sage/stt/transcribe")
    async def audio_echo(request: Request):
        body = await request.body()
        return {"size": len(body)}

    return app


def test_normal_api_request_body_limit_returns_413():
    app = _build_app()
    with patch.dict(
        "os.environ",
        {
            "EMPYRALIS_MAX_REQUEST_BODY_BYTES": "8",
            "EMPYRALIS_MAX_WEBHOOK_BODY_BYTES": "8",
            "EMPYRALIS_MAX_AUDIO_BODY_BYTES": "32",
        },
        clear=False,
    ):
        response = TestClient(app).post("/api/echo", content=b"0123456789")

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_body_too_large"


def test_webhook_request_uses_webhook_body_limit():
    app = _build_app()
    with patch.dict(
        "os.environ",
        {
            "EMPYRALIS_MAX_REQUEST_BODY_BYTES": "1024",
            "EMPYRALIS_MAX_WEBHOOK_BODY_BYTES": "8",
            "EMPYRALIS_MAX_AUDIO_BODY_BYTES": "32",
        },
        clear=False,
    ):
        response = TestClient(app).post("/channels/slack/events", content=b"0123456789")

    assert response.status_code == 413


def test_audio_request_uses_audio_body_limit():
    app = _build_app()
    with patch.dict(
        "os.environ",
        {
            "EMPYRALIS_MAX_REQUEST_BODY_BYTES": "8",
            "EMPYRALIS_MAX_WEBHOOK_BODY_BYTES": "8",
            "EMPYRALIS_MAX_AUDIO_BODY_BYTES": "32",
        },
        clear=False,
    ):
        response = TestClient(app).post("/api/sage/stt/transcribe", content=b"0123456789")

    assert response.status_code == 200
    assert response.json()["size"] == 10
