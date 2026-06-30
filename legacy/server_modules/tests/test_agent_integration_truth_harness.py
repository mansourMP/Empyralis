import asyncio
import copy
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import agent_registry_api
from server_modules.agent_manifest import AgentManifest, AgentManifestBible, AgentManifestSkillBinding


class _FakeApp:
    def __init__(self) -> None:
        self.routes = {}

    def _register(self, method, path, **kwargs):
        def _decorator(fn):
            self.routes[(method, path)] = fn
            return fn

        return _decorator

    def post(self, path, **kwargs):
        return self._register("POST", path, **kwargs)

    def get(self, path, **kwargs):
        return self._register("GET", path, **kwargs)

    def patch(self, path, **kwargs):
        return self._register("PATCH", path, **kwargs)

    def put(self, path, **kwargs):
        return self._register("PUT", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._register("DELETE", path, **kwargs)


def _manifest_payload(manifest: AgentManifest) -> dict:
    return manifest.model_dump(mode="json")


def _build_manifest() -> AgentManifest:
    return AgentManifest(
        manifest_id="manifest-parts-pro",
        identity={
            "name": "Parts Pro",
            "role": "Inventory Specialist",
            "archetype": "support_specialist",
            "summary": "Help customers find available parts.",
        },
        role={
            "job_to_be_done": "Answer inventory and fitment questions.",
            "success_definition": "Provide correct stock and routing answers.",
            "escalation_owner": "Shop owner",
        },
        voice={
            "tone": "Concise",
            "response_style": "Answer directly",
            "service_boundaries": "Escalate uncertain fitment",
        },
        bible={
            "mission": "Help customers buy the right parts.",
            "hard_context": "Use only owner-authored inventory facts.",
            "operational_policy": "Never invent stock or pricing.",
        },
        skills=[{"id": "inventory-tool", "enabled": True}],
        channels={"web_chat": True, "telegram": True},
    )


class AgentIntegrationTruthHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        global agent_registry_api, AgentManifest, AgentManifestBible, AgentManifestSkillBinding
        agent_registry_api = importlib.import_module("server_modules.agent_registry_api")
        agent_manifest_module = importlib.import_module("server_modules.agent_manifest")
        AgentManifest = agent_manifest_module.AgentManifest
        AgentManifestBible = agent_manifest_module.AgentManifestBible
        AgentManifestSkillBinding = agent_manifest_module.AgentManifestSkillBinding

    def test_specialist_lifecycle_routes_stay_glued_across_save_reload_and_preview(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            create_route = app.routes[("POST", "/agent-registry/specialists")]
            update_manifest_route = app.routes[("PATCH", "/agent-registry/specialists/{install_id}")]
            save_bible_route = app.routes[("PUT", "/agent-registry/specialists/{install_id}/bible")]
            save_skills_route = app.routes[("PUT", "/agent-registry/specialists/{install_id}/skills")]
            save_channels_route = app.routes[("PUT", "/agent-registry/specialists/{install_id}/channels")]
            save_runtime_route = app.routes[("PUT", "/agent-registry/specialists/{install_id}/runtime")]
            get_specialist_route = app.routes[("GET", "/agent-registry/specialists/{install_id}")]
            preview_route = app.routes[("POST", "/agents/customer-preview/respond")]

            manifest = _build_manifest()
            store: dict[str, dict] = {}

            def _clone_record(record: dict) -> dict:
                return copy.deepcopy(record)

            def _mutate_manifest(install_id: str) -> AgentManifest:
                return AgentManifest.model_validate(store[install_id]["metadata"]["agent_manifest"])

            async def _create_workspace_specialist(**kwargs):
                install_id = "install-parts-pro"
                record = {
                    "id": install_id,
                    "tenant_id": kwargs["tenant_id"],
                    "workspace_id": kwargs["workspace_id"],
                    "label": kwargs.get("label") or kwargs["manifest"].identity.name,
                    "status": "active",
                    "enabled": True,
                    "runtime_mode": kwargs.get("runtime_mode") or "hosted_secure",
                    "runtime_profile_id": kwargs.get("runtime_profile_id"),
                    "runtime_profile": {
                        "id": kwargs.get("runtime_profile_id"),
                        "label": "Empyralis Cloud",
                        "runtime_class": "cloud_worker",
                    } if kwargs.get("runtime_profile_id") else None,
                    "metadata": {
                        **dict(kwargs.get("metadata") or {}),
                        "agent_manifest": _manifest_payload(kwargs["manifest"]),
                        "channel_registry_bindings": dict(kwargs.get("channel_bindings") or {}),
                    },
                }
                store[install_id] = record
                return _clone_record(record)

            async def _get_workspace_specialist(install_id, **kwargs):
                record = store.get(install_id)
                return _clone_record(record) if isinstance(record, dict) else None

            async def _update_workspace_specialist_manifest(install_id, **kwargs):
                next_manifest = kwargs["manifest"]
                store[install_id]["metadata"]["agent_manifest"] = _manifest_payload(next_manifest)
                store[install_id]["runtime_mode"] = str(kwargs.get("runtime_mode") or store[install_id].get("runtime_mode") or "hosted_secure")
                return _clone_record(store[install_id])

            async def _save_workspace_specialist_bible(install_id, **kwargs):
                current_manifest = _mutate_manifest(install_id)
                current_manifest.bible = AgentManifestBible.model_validate({
                    **current_manifest.model_dump(mode="json")["bible"],
                    "mission": kwargs["bible"],
                })
                store[install_id]["metadata"]["agent_manifest"] = _manifest_payload(current_manifest)
                return _clone_record(store[install_id])

            async def _save_workspace_specialist_skill_bindings(install_id, **kwargs):
                current_manifest = _mutate_manifest(install_id)
                current_manifest.skills = [
                    AgentManifestSkillBinding(id=skill_id, enabled=True)
                    for skill_id in list(kwargs.get("skill_ids") or [])
                ]
                current_manifest = AgentManifest.model_validate(current_manifest.model_dump(mode="json"))
                store[install_id]["metadata"]["agent_manifest"] = _manifest_payload(current_manifest)
                return _clone_record(store[install_id])

            async def _save_workspace_specialist_channel_bindings(install_id, **kwargs):
                store[install_id]["metadata"]["channel_registry_bindings"] = dict(kwargs.get("channel_bindings") or {})
                return _clone_record(store[install_id])

            async def _save_workspace_specialist_runtime_profile(install_id, **kwargs):
                store[install_id]["runtime_mode"] = kwargs["runtime_mode"]
                store[install_id]["runtime_profile_id"] = kwargs.get("runtime_profile_id")
                store[install_id]["runtime_profile"] = {
                    "id": kwargs.get("runtime_profile_id"),
                    "label": "Owner Desktop",
                    "runtime_class": "desktop_companion",
                } if kwargs.get("runtime_profile_id") else None
                return _clone_record(store[install_id])

            async def _get_workspace_agent_install_bundle(install_id, **kwargs):
                record = store.get(install_id)
                return _clone_record(record) if isinstance(record, dict) else None

            user = {"user_id": "user-1", "role": "owner", "is_admin": True}

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch("server_modules.agent_registry_api.agent_specialist_repository.create_workspace_specialist", new=AsyncMock(side_effect=_create_workspace_specialist)),
                patch("server_modules.agent_registry_api.agent_specialist_repository.get_workspace_specialist", new=AsyncMock(side_effect=_get_workspace_specialist)),
                patch("server_modules.agent_registry_api.agent_specialist_repository.update_workspace_specialist_manifest", new=AsyncMock(side_effect=_update_workspace_specialist_manifest)),
                patch("server_modules.agent_registry_api.agent_specialist_repository.save_workspace_specialist_bible", new=AsyncMock(side_effect=_save_workspace_specialist_bible)),
                patch("server_modules.agent_registry_api.agent_specialist_repository.save_workspace_specialist_skill_bindings", new=AsyncMock(side_effect=_save_workspace_specialist_skill_bindings)),
                patch("server_modules.agent_registry_api.agent_specialist_repository.save_workspace_specialist_channel_bindings", new=AsyncMock(side_effect=_save_workspace_specialist_channel_bindings)),
                patch("server_modules.agent_registry_api.agent_specialist_repository.save_workspace_specialist_runtime_profile", new=AsyncMock(side_effect=_save_workspace_specialist_runtime_profile)),
                patch("server_modules.agent_registry_api.agent_registry_repository.get_workspace_agent_install_bundle", new=AsyncMock(side_effect=_get_workspace_agent_install_bundle)),
                patch(
                    "server_modules.agent_registry_api.specialist_service.build_specialist_service_contract",
                    new=AsyncMock(
                        return_value={
                            "service_kind": "business_specialist",
                            "install_id": "install-parts-pro",
                            "runtime_policy": {"runtime_mode": "local_secure", "selection_status": "ready"},
                        }
                    ),
                ),
                patch(
                    "server_modules.agent_registry_api.universal_operator.execute_customer_turn",
                    new=AsyncMock(return_value={"status": "ok", "reply": "Live customer preview", "steps": []}),
                ) as execute_mock,
            ):
                created = asyncio.run(
                    create_route(
                        agent_registry_api.SpecialistCreateRequest(
                            workspace_id="workspace-1",
                            label="Parts Pro",
                            manifest=manifest,
                            channel_bindings={
                                "telegram": {"enabled": True, "is_inbound_owner": True, "endpoint_key": "@partspro_bot"},
                            },
                        ),
                        current_user=user,
                    )
                )
                self.assertEqual(created["id"], "install-parts-pro")

                updated_manifest = manifest.model_copy(deep=True)
                updated_manifest.role.job_to_be_done = "Resolve inventory and fitment questions."
                updated_manifest.voice.tone = "Confident"
                updated = asyncio.run(
                    update_manifest_route(
                        "install-parts-pro",
                        agent_registry_api.SpecialistManifestUpdateRequest(
                            workspace_id="workspace-1",
                            manifest=updated_manifest,
                        ),
                        current_user=user,
                    )
                )
                self.assertEqual(updated["metadata"]["agent_manifest"]["role"]["job_to_be_done"], "Resolve inventory and fitment questions.")

                bible_saved = asyncio.run(
                    save_bible_route(
                        "install-parts-pro",
                        agent_registry_api.SpecialistBibleUpdateRequest(
                            workspace_id="workspace-1",
                            bible="Only answer from owner-approved catalog rows.",
                        ),
                        current_user=user,
                    )
                )
                self.assertEqual(bible_saved["metadata"]["agent_manifest"]["bible"]["mission"], "Only answer from owner-approved catalog rows.")

                skills_saved = asyncio.run(
                    save_skills_route(
                        "install-parts-pro",
                        agent_registry_api.SpecialistSkillBindingsUpdateRequest(
                            workspace_id="workspace-1",
                            skill_ids=["inventory-tool", "web-search"],
                        ),
                        current_user=user,
                    )
                )
                self.assertEqual(
                    [item["id"] for item in skills_saved["metadata"]["agent_manifest"]["skills"]],
                    ["inventory-tool", "web-search"],
                )

                channels_saved = asyncio.run(
                    save_channels_route(
                        "install-parts-pro",
                        agent_registry_api.SpecialistChannelBindingsUpdateRequest(
                            workspace_id="workspace-1",
                            channel_bindings={
                                "telegram": {"enabled": True, "is_inbound_owner": True, "endpoint_key": "@partspro_bot"},
                                "web_chat": {"enabled": True, "is_inbound_owner": False, "endpoint_key": "workspace-default"},
                            },
                        ),
                        current_user=user,
                    )
                )
                self.assertEqual(
                    channels_saved["metadata"]["channel_registry_bindings"]["telegram"]["endpoint_key"],
                    "@partspro_bot",
                )

                runtime_saved = asyncio.run(
                    save_runtime_route(
                        "install-parts-pro",
                        agent_registry_api.SpecialistRuntimeProfileUpdateRequest(
                            workspace_id="workspace-1",
                            runtime_mode="local_secure",
                            runtime_profile_id="runtime-desktop-1",
                        ),
                        current_user=user,
                    )
                )
                self.assertEqual(runtime_saved["runtime_mode"], "local_secure")
                self.assertEqual(runtime_saved["runtime_profile"]["runtime_class"], "desktop_companion")

                refreshed = asyncio.run(
                    get_specialist_route(
                        "install-parts-pro",
                        workspace_id="workspace-1",
                        current_user=user,
                    )
                )
                self.assertEqual(refreshed["metadata"]["agent_manifest"]["voice"]["tone"], "Confident")
                self.assertEqual(refreshed["runtime_mode"], "local_secure")
                self.assertEqual(refreshed["metadata"]["channel_registry_bindings"]["telegram"]["endpoint_key"], "@partspro_bot")
                self.assertEqual(refreshed["service_contract"]["service_kind"], "business_specialist")

                stale_manifest = manifest.model_copy(deep=True)
                stale_manifest.bible.mission = "STALE CLIENT BIBLE"
                preview = asyncio.run(
                    preview_route(
                        agent_registry_api.AgentCustomerPreviewRequest(
                            install_id="install-parts-pro",
                            workspace_id="workspace-1",
                            customer_message="Do you have Tesla wipers?",
                            manifest=stale_manifest,
                        ),
                        current_user=user,
                    )
                )

            self.assertEqual(preview["status"], "ok")
            execute_manifest = execute_mock.await_args.kwargs["manifest"]
            self.assertEqual(execute_manifest.bible.mission, "Only answer from owner-approved catalog rows.")
            self.assertEqual(execute_manifest.voice.tone, "Confident")
            self.assertEqual(execute_mock.await_args.kwargs["runtime_mode"], "local_secure")

        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server

    def test_inbound_channel_api_surface_preserves_owner_and_audit_payload(self):
        fake_server = types.ModuleType("server")
        fake_server.require_api_key = object()

        previous_server = sys.modules.get("server")
        sys.modules["server"] = fake_server
        try:
            app = _FakeApp()
            agent_registry_api.register_agent_registry_routes(app)
            route = app.routes[("POST", "/agent-registry/channels/inbound")]

            with (
                patch("server_modules.agent_registry_api.enforce_workspace_access", return_value="workspace-1"),
                patch("server_modules.agent_registry_api.workspace_tenant_id", return_value="tenant-1"),
                patch(
                    "server_modules.agent_registry_api.agent_channel_router.route_inbound_channel_message",
                    new=AsyncMock(return_value={
                        "workspace_id": "workspace-1",
                        "tenant_id": "tenant-1",
                        "channel_key": "telegram",
                        "endpoint_key": "@partspro_bot",
                        "owner": {"install_id": "install-parts-pro", "label": "Parts Pro", "type": "specialist"},
                        "reply": "I found 3 Tesla wipers in stock.",
                        "status": "ok",
                        "audit": {"inbound_event_id": "evt-in", "outbound_event_id": "evt-out"},
                    }),
                ) as route_mock,
            ):
                result = asyncio.run(
                    route(
                        agent_registry_api.AgentChannelInboundRequest(
                            workspace_id="workspace-1",
                            channel_key="telegram",
                            endpoint_key="@partspro_bot",
                            customer_message="Do you have 2022 Tesla Model 3 wipers?",
                            actor_id="customer-1",
                        ),
                        current_user={"user_id": "user-1", "role": "owner", "is_admin": True},
                    )
                )

            self.assertEqual(result["owner"]["install_id"], "install-parts-pro")
            self.assertEqual(result["audit"]["inbound_event_id"], "evt-in")
            self.assertEqual(result["audit"]["outbound_event_id"], "evt-out")
            self.assertEqual(route_mock.await_args.kwargs["endpoint_key"], "@partspro_bot")
        finally:
            if previous_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = previous_server


if __name__ == "__main__":
    unittest.main()
