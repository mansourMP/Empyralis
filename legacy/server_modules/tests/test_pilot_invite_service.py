import os
import time
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from server_modules import control_plane_repository, pilot_invite_service, routes_pilot


@pytest.fixture(autouse=True)
def _reset_env():
    original = os.environ.get("ORION_PILOT_SIGNUP_MODE", "")
    yield
    if original:
        os.environ["ORION_PILOT_SIGNUP_MODE"] = original
    else:
        os.environ.pop("ORION_PILOT_SIGNUP_MODE", None)
    pilot_invite_service.ORION_PILOT_SIGNUP_MODE = str(os.environ.get("ORION_PILOT_SIGNUP_MODE") or "").strip().lower()
    pilot_invite_service.ORION_PILOT_SIGNUP_ENABLED = pilot_invite_service.ORION_PILOT_SIGNUP_MODE in {"open", "invite_only"}


class TestPilotInviteService:
    @pytest.mark.anyio
    async def test_create_pilot_invite_generates_code(self):
        invite = await pilot_invite_service.create_pilot_invite(
            email="test@example.com",
            role="owner",
            plan_id="pilot",
            max_uses=5,
        )
        assert invite["code"]
        assert len(invite["code"]) == 12
        assert invite["email"] == "test@example.com"
        assert invite["role"] == "owner"
        assert invite["plan_id"] == "pilot"
        assert invite["max_uses"] == 5
        assert invite["uses_count"] == 0
        assert invite["status"] == "active"

    @pytest.mark.anyio
    async def test_validate_pilot_invite_code(self):
        invite = await pilot_invite_service.create_pilot_invite()
        result = await pilot_invite_service.validate_pilot_invite_code(invite["code"])
        assert result["valid"] is True
        assert result["code"] == invite["code"]
        assert result["uses_remaining"] == 1

    @pytest.mark.anyio
    async def test_validate_rejects_expired_code(self):
        invite = await pilot_invite_service.create_pilot_invite(expires_hours=-1)
        with pytest.raises(HTTPException) as exc_info:
            await pilot_invite_service.validate_pilot_invite_code(invite["code"])
        assert exc_info.value.status_code == 410

    @pytest.mark.anyio
    async def test_validate_rejects_unknown_code(self):
        with pytest.raises(HTTPException) as exc_info:
            await pilot_invite_service.validate_pilot_invite_code("UNKNOWN123")
        assert exc_info.value.status_code == 404

    @pytest.mark.anyio
    async def test_claim_consumes_use(self):
        invite = await pilot_invite_service.create_pilot_invite(max_uses=2)
        result1 = await pilot_invite_service.claim_pilot_invite_code(invite["code"])
        assert result1["claimed"] is True

        result2 = await pilot_invite_service.claim_pilot_invite_code(invite["code"])
        assert result2["claimed"] is True

        with pytest.raises(HTTPException) as exc_info:
            await pilot_invite_service.claim_pilot_invite_code(invite["code"])
        assert exc_info.value.status_code == 404

    @pytest.mark.anyio
    async def test_revoke_pilot_invite(self):
        invite = await pilot_invite_service.create_pilot_invite()
        revoked = await pilot_invite_service.revoke_pilot_invite(invite["id"])
        assert revoked["status"] == "revoked"

        with pytest.raises(HTTPException) as exc_info:
            await pilot_invite_service.validate_pilot_invite_code(invite["code"])
        assert exc_info.value.status_code == 410

    @pytest.mark.anyio
    async def test_pilot_signup_requires_invite_when_mode_is_invite_only(self):
        os.environ["ORION_PILOT_SIGNUP_MODE"] = "invite_only"
        pilot_invite_service.ORION_PILOT_SIGNUP_MODE = "invite_only"
        pilot_invite_service.ORION_PILOT_SIGNUP_ENABLED = True
        assert pilot_invite_service.pilot_signup_requires_invite() is True

    @pytest.mark.anyio
    async def test_pilot_signup_does_not_require_invite_when_open(self):
        os.environ["ORION_PILOT_SIGNUP_MODE"] = "open"
        pilot_invite_service.ORION_PILOT_SIGNUP_MODE = "open"
        pilot_invite_service.ORION_PILOT_SIGNUP_ENABLED = True
        assert pilot_invite_service.pilot_signup_requires_invite() is False

    @pytest.mark.anyio
    async def test_pilot_signup_disabled_by_default(self):
        os.environ.pop("ORION_PILOT_SIGNUP_MODE", None)
        pilot_invite_service.ORION_PILOT_SIGNUP_MODE = ""
        pilot_invite_service.ORION_PILOT_SIGNUP_ENABLED = False
        assert pilot_invite_service.pilot_signup_requires_invite() is False


class TestPilotInviteRoutes:
    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(routes_pilot.router, prefix="/api")
        return app

    def _seed_browser_session(self, client: AsyncClient, *, csrf: bool) -> None:
        client.cookies.set(routes_pilot.auth.AUTH_ACCESS_COOKIE_NAME, "access-cookie", path="/")
        if csrf:
            client.cookies.set(routes_pilot.auth.AUTH_CSRF_COOKIE_NAME, "csrf-cookie", path="/")

    @pytest.mark.anyio
    async def test_public_validate_endpoint(self):
        invite = await pilot_invite_service.create_pilot_invite()
        app = self._build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/pilot/invites/validate", json={"code": invite["code"]})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["valid"] is True

    @pytest.mark.anyio
    async def test_public_validate_rejects_bad_code(self):
        app = self._build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/pilot/invites/validate", json={"code": "BADCODE"})
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_admin_create_requires_auth(self):
        app = self._build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/pilot/invites", json={})
        assert response.status_code == 401

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("method", "path", "json_body"),
        [
            ("post", "/api/pilot/invites", {}),
            ("delete", "/api/pilot/invites/invite-1", None),
        ],
    )
    async def test_admin_invite_mutations_reject_cookie_session_without_csrf(
        self,
        monkeypatch: pytest.MonkeyPatch,
        method: str,
        path: str,
        json_body: dict[str, object] | None,
    ):
        app = self._build_app()
        routes_pilot.auth.CSRF_FAILURE_RATE_LIMIT_BUCKETS.clear()

        def fail_decode_token(_token: str):
            pytest.fail("cookie auth token should not be decoded before CSRF validation")

        monkeypatch.setattr(routes_pilot.auth, "_decode_token_payload", fail_decode_token)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            self._seed_browser_session(client, csrf=False)
            response = await client.request(method.upper(), path, json=json_body)

        assert response.status_code == 403
        assert response.json()["detail"] == "CSRF validation failed."

    @pytest.mark.anyio
    async def test_admin_list_requires_auth(self):
        app = self._build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/pilot/invites")
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_claim_endpoint(self):
        invite = await pilot_invite_service.create_pilot_invite(max_uses=2)
        app = self._build_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/pilot/invites/claim", json={"code": invite["code"]})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["claimed"] is True
