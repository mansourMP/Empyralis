from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from server_modules import workspace_ai_route_service as service


def _catalog(*, hosted: bool = True, providers: list[dict] | None = None) -> dict:
    return {
        "workspace_id": "ws-1",
        "hosted_ai_enabled": hosted,
        "hosted_sage_ai": {
            "allowed": hosted,
            "monthly_cap_usd": 20.0,
            "monthly_cost_usd": 3.5,
            "monthly_remaining_usd": 16.5,
            "remaining_credits": 16500,
        },
        "providers": providers or [],
    }


def _profile(
    provider: str,
    *,
    profile_id: str | None = None,
    explicit: bool = False,
    credential_id: str = "cred_123456",
    auth_mode: str = "api_key",
    model: str | None = "default-model",
    chat_model_tier: str | None = None,
    billing_source: str | None = None,
) -> dict:
    metadata = {
        "chat_model_selection": "explicit" if explicit else "default",
    }
    if chat_model_tier is not None:
        metadata["chat_model_tier"] = chat_model_tier
    if billing_source is not None:
        metadata["billing_source"] = billing_source
    return {
        "id": profile_id or f"profile-{provider}",
        "provider": provider,
        "label": provider.title(),
        "credential_id": credential_id,
        "auth_mode": auth_mode,
        "priority": 10 if explicit else 100,
        "enabled": True,
        "model": model,
        "metadata": metadata,
    }


class WorkspaceAiRouteServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_model_defaults_to_hosted_workspace_ai(self) -> None:
        with patch.object(
            service.provider_catalog_service,
            "list_workspace_provider_catalog",
            AsyncMock(return_value=_catalog(hosted=True)),
        ), patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": []}),
        ):
            payload = await service.build_workspace_ai_route_payload("ws-1")

        self.assertEqual(payload["workspaceDefault"]["kind"], "empyralis_managed")
        self.assertEqual(payload["workspaceDefault"]["modelPreset"], "light")
        self.assertEqual(payload["usedBy"][0]["detail"], "Workspace default")
        self.assertEqual(payload["budgets"]["remainingCredits"], 16500)

    async def test_read_model_selects_managed_pro_platform_profile(self) -> None:
        profile = _profile(
            "deepseek",
            explicit=True,
            credential_id="",
            auth_mode="platform_runtime",
            model="deepseek-v4-pro",
            chat_model_tier="pro",
            billing_source="empyralis_credits",
        )
        with patch.object(
            service.provider_catalog_service,
            "list_workspace_provider_catalog",
            AsyncMock(return_value=_catalog(hosted=True)),
        ), patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": [profile]}),
        ):
            payload = await service.build_workspace_ai_route_payload("ws-1")

        self.assertEqual(payload["workspaceDefault"]["kind"], "empyralis_managed")
        self.assertEqual(payload["workspaceDefault"]["modelPreset"], "pro")
        self.assertEqual(payload["workspaceDefault"]["providerId"], "deepseek")
        self.assertEqual(payload["workspaceDefault"]["modelId"], "deepseek-v4-pro")

    async def test_read_model_selects_user_api_key_profile(self) -> None:
        provider = {
            "id": "openai",
            "label": "OpenAI",
            "usable": True,
            "configured": True,
            "state": "active",
            "credential_plane": "workspace_connection",
            "default_model": "gpt-4.1",
            "provider_scopes": ["sage_personal"],
        }
        with patch.object(
            service.provider_catalog_service,
            "list_workspace_provider_catalog",
            AsyncMock(return_value=_catalog(providers=[provider])),
        ), patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": [_profile("openai", explicit=True, model="gpt-4.1")]}),
        ):
            payload = await service.build_workspace_ai_route_payload("ws-1")

        self.assertEqual(payload["workspaceDefault"]["kind"], "user_api_key")
        self.assertEqual(payload["workspaceDefault"]["providerId"], "openai")
        self.assertEqual(payload["workspaceDefault"]["modelId"], "gpt-4.1")

    async def test_read_model_selects_local_model_profile(self) -> None:
        provider = {
            "id": "ollama",
            "label": "Ollama",
            "usable": True,
            "configured": True,
            "state": "active",
            "credential_plane": "local_runtime",
            "default_model": "llama3.2",
            "provider_scopes": ["local_only", "sage_personal"],
            "local_only": True,
        }
        with patch.object(
            service.provider_catalog_service,
            "list_workspace_provider_catalog",
            AsyncMock(return_value=_catalog(providers=[provider])),
        ), patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={
                "items": [
                    _profile(
                        "ollama",
                        explicit=True,
                        credential_id="",
                        auth_mode="none",
                        model="llama3.2",
                    )
                ],
            }),
        ):
            payload = await service.build_workspace_ai_route_payload("ws-1")

        self.assertEqual(payload["workspaceDefault"]["kind"], "local_model")
        self.assertEqual(payload["workspaceDefault"]["runtimeTarget"], "This Device")

    async def test_read_model_reports_setup_required_when_no_route_exists(self) -> None:
        with patch.object(
            service.provider_catalog_service,
            "list_workspace_provider_catalog",
            AsyncMock(return_value=_catalog(hosted=False, providers=[])),
        ), patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": []}),
        ):
            payload = await service.build_workspace_ai_route_payload("ws-1")

        self.assertEqual(payload["workspaceDefault"]["status"], "setup_required")
        self.assertFalse(payload["workspaceDefault"]["enabled"])

    async def test_update_marks_one_provider_explicit_and_others_default(self) -> None:
        profiles = [
            _profile("openai", profile_id="profile-openai", explicit=False, model="gpt-4.1"),
            _profile("gemini", profile_id="profile-gemini", explicit=True, model="gemini-2.5-flash"),
        ]
        upserts = []

        async def fake_upsert(body):
            upserts.append(body)
            return {"item": {"id": body.id, "provider": body.provider, "metadata": body.metadata}}

        with patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": profiles}),
        ), patch.object(
            service.connectors_core,
            "upsert_provider_profile",
            side_effect=fake_upsert,
        ), patch.object(
            service,
            "build_workspace_ai_route_payload",
            AsyncMock(return_value={"workspaceId": "ws-1"}),
        ):
            await service.update_workspace_default_ai_route(
                workspace_id="ws-1",
                provider="openai",
                kind="user_api_key",
                model="gpt-4.1",
            )

        by_provider = {body.provider: body for body in upserts}
        self.assertEqual(by_provider["openai"].metadata["chat_model_selection"], "explicit")
        self.assertEqual(by_provider["gemini"].metadata["chat_model_selection"], "default")
        self.assertEqual(
            sum(1 for body in upserts if body.metadata["chat_model_selection"] == "explicit"),
            1,
        )

    async def test_update_managed_pro_creates_platform_profile_when_no_profiles(self) -> None:
        upserts = []

        async def fake_upsert(body):
            upserts.append(body)
            return {"item": {"id": body.id, "provider": body.provider, "metadata": body.metadata}}

        with patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": []}),
        ), patch.object(
            service.connectors_core,
            "upsert_provider_profile",
            side_effect=fake_upsert,
        ), patch.object(
            service,
            "build_workspace_ai_route_payload",
            AsyncMock(return_value={"workspaceId": "ws-1"}),
        ):
            await service.update_workspace_default_ai_route(
                workspace_id="ws-1",
                route_id="empyralis_managed:pro",
            )

        self.assertEqual(len(upserts), 1)
        body = upserts[0]
        self.assertEqual(body.provider, "deepseek")
        self.assertEqual(body.auth_mode, "platform_runtime")
        self.assertIsNone(body.credential_id)
        self.assertEqual(body.model, "deepseek-v4-pro")
        self.assertEqual(body.metadata["chat_model_selection"], "explicit")
        self.assertEqual(body.metadata["chat_model_tier"], "pro")
        self.assertEqual(body.metadata["billing_source"], "empyralis_credits")

    async def test_update_managed_pro_preserves_existing_deepseek_byok_profile(self) -> None:
        profiles = [
            _profile(
                "deepseek",
                profile_id="profile-byok",
                explicit=True,
                credential_id="cred_123456",
                auth_mode="api_key",
                model="deepseek-chat",
                chat_model_tier="pro",
            )
        ]
        upserts = []

        async def fake_upsert(body):
            upserts.append(body)
            return {"item": {"id": body.id, "provider": body.provider, "metadata": body.metadata}}

        with patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": profiles}),
        ), patch.object(
            service.connectors_core,
            "upsert_provider_profile",
            side_effect=fake_upsert,
        ), patch.object(
            service,
            "build_workspace_ai_route_payload",
            AsyncMock(return_value={"workspaceId": "ws-1"}),
        ):
            await service.update_workspace_default_ai_route(
                workspace_id="ws-1",
                route_id="empyralis_managed:pro",
            )

        byok_profile = next(body for body in upserts if body.id == "profile-byok")
        platform_profile = next(body for body in upserts if body.id is None)
        self.assertEqual(byok_profile.auth_mode, "api_key")
        self.assertEqual(byok_profile.credential_id, "cred_123456")
        self.assertEqual(byok_profile.metadata["chat_model_selection"], "default")
        self.assertEqual(platform_profile.provider, "deepseek")
        self.assertEqual(platform_profile.auth_mode, "platform_runtime")
        self.assertIsNone(platform_profile.credential_id)
        self.assertEqual(platform_profile.model, "deepseek-v4-pro")
        self.assertEqual(platform_profile.metadata["chat_model_selection"], "explicit")
        self.assertEqual(platform_profile.metadata["chat_model_tier"], "pro")

    async def test_update_managed_pro_demotes_stale_profiles_without_credential_revalidation(self) -> None:
        profiles = [
            _profile(
                "deepseek",
                profile_id="profile-byok",
                explicit=True,
                credential_id="missing-deepseek-credential",
                auth_mode="api_key",
                model="deepseek-chat",
                chat_model_tier="pro",
            ),
            _profile(
                "gemini",
                profile_id="profile-gemini",
                explicit=True,
                credential_id="missing-gemini-credential",
                auth_mode="api_key",
                model="gemini-2.5-flash",
            ),
        ]
        upserts = []
        demotions = []

        async def fake_upsert(body):
            if body.id in {"profile-byok", "profile-gemini"}:
                raise AssertionError("stale profiles should not be credential-revalidated")
            upserts.append(body)
            return {"item": {"id": body.id, "provider": body.provider, "metadata": body.metadata}}

        def fake_demote(profile, **kwargs):
            demotions.append((profile["id"], kwargs["metadata"]))
            return True

        with patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": profiles}),
        ), patch.object(
            service.connectors_core,
            "upsert_provider_profile",
            side_effect=fake_upsert,
        ), patch.object(
            service,
            "_update_existing_provider_profile_route_metadata",
            side_effect=fake_demote,
        ), patch.object(
            service,
            "build_workspace_ai_route_payload",
            AsyncMock(return_value={"workspaceId": "ws-1"}),
        ):
            await service.update_workspace_default_ai_route(
                workspace_id="ws-1",
                route_id="empyralis_managed:pro",
            )

        self.assertEqual(len(upserts), 1)
        platform_profile = upserts[0]
        self.assertIsNone(platform_profile.id)
        self.assertEqual(platform_profile.provider, "deepseek")
        self.assertEqual(platform_profile.auth_mode, "platform_runtime")
        self.assertEqual(platform_profile.metadata["chat_model_selection"], "explicit")
        self.assertEqual(platform_profile.metadata["chat_model_tier"], "pro")
        self.assertEqual({item[0] for item in demotions}, {"profile-byok", "profile-gemini"})
        self.assertTrue(all(item[1]["chat_model_selection"] == "default" for item in demotions))

    async def test_personal_subscription_route_is_not_supported(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await service.update_workspace_default_ai_route(
                workspace_id="ws-1",
                route_id="subscription_passthrough:chatgpt",
            )

        self.assertEqual(raised.exception.status_code, 400)

    async def test_personal_subscription_providers_are_not_normal_available_routes(self) -> None:
        providers = [
            {
                "id": "anthropic",
                "label": "Anthropic",
                "usable": True,
                "configured": True,
                "state": "active",
                "credential_plane": "local_runtime",
                "default_auth_mode": "local_cli",
                "runtime_active_source": "claude_code_cli",
                "default_model": "claude-sonnet-4-6",
                "provider_scopes": ["sage_personal"],
            },
            {
                "id": "ollama",
                "label": "Ollama",
                "usable": True,
                "configured": True,
                "state": "active",
                "credential_plane": "local_runtime",
                "default_auth_mode": "none",
                "default_model": "llama3.2",
                "provider_scopes": ["local_only", "sage_personal"],
                "local_only": True,
            },
        ]
        with patch.object(
            service.provider_catalog_service,
            "list_workspace_provider_catalog",
            AsyncMock(return_value=_catalog(providers=providers)),
        ), patch.object(
            service.connectors_core,
            "list_provider_profiles",
            AsyncMock(return_value={"items": []}),
        ):
            payload = await service.build_workspace_ai_route_payload("ws-1")

        route_ids = {route["id"] for route in payload["availableRoutes"]}
        self.assertNotIn("provider:anthropic", route_ids)
        self.assertIn("provider:ollama", route_ids)


if __name__ == "__main__":
    unittest.main()
