from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from server_modules import data_retention_service, mini_apps_service, sage_memory_service, workspace_context


def test_catalog_exposes_required_retention_classes_and_stores() -> None:
    classes = data_retention_service.retention_classes_catalog()
    assert {"ephemeral", "active_conversation", "compact_summary", "business_aggregate", "audit_record"} <= set(
        classes.keys()
    )
    stores = {item["store_id"] for item in data_retention_service.data_store_catalog()}
    assert "deployed_agent_conversation_memory" in stores
    assert "agent_channel_events" in stores
    assert "sage_memory" in stores
    assert "mini_apps_state" in stores


@pytest.mark.anyio
async def test_workspace_inventory_merges_sql_and_file_backed_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inventory(**_kwargs):
        return {
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "stores": {
                "deployed_agent_conversation_memory_count": 3,
                "agent_channel_events_count": 9,
                "activity_ledger_events_count": 5,
            },
        }

    monkeypatch.setattr(
        data_retention_service.control_plane_repository,
        "build_workspace_data_retention_inventory",
        fake_inventory,
    )
    monkeypatch.setattr(
        data_retention_service.mini_apps_service,
        "export_mini_apps_state",
        lambda workspace_id: {"workspace_id": workspace_id, "total_records": 7},
    )
    monkeypatch.setattr(
        data_retention_service.sage_memory_service,
        "export_sage_memory",
        lambda workspace_id: {"workspace_id": workspace_id, "summary": {"total_count": 11}},
    )

    payload = await data_retention_service.build_workspace_retention_inventory(
        tenant_id="tenant-1",
        workspace_id="ws-1",
    )

    by_store = {item["store_id"]: item for item in payload["stores"]}
    assert by_store["deployed_agent_conversation_memory"]["row_count"] == 3
    assert by_store["agent_channel_events"]["row_count"] == 9
    assert by_store["mini_apps_state"]["row_count"] == 7
    assert by_store["sage_memory"]["row_count"] == 11


@pytest.mark.anyio
async def test_export_workspace_data_sanitizes_insight_records(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_inventory(**_kwargs):
        return {"tenant_id": "tenant-1", "workspace_id": "ws-1", "retention_classes": {}, "stores": []}

    async def fake_insights(**_kwargs):
        return [
            {
                "id": "bins-1",
                "metadata": {
                    "external_user_id": "customer-1",
                    "session_id": "sess-1",
                    "channel_key": "telegram",
                },
                "redacted_examples": [
                    "my name is John Doe. email me@example.com and call +1 415 555 1234. token sk-secret-12345678"
                ],
            }
        ]

    monkeypatch.setattr(data_retention_service, "build_workspace_retention_inventory", fake_inventory)
    monkeypatch.setattr(
        data_retention_service.sage_memory_service,
        "export_sage_memory",
        lambda workspace_id: {"workspace_id": workspace_id, "summary": {"total_count": 0}, "items": []},
    )
    monkeypatch.setattr(
        data_retention_service.mini_apps_service,
        "export_mini_apps_state",
        lambda workspace_id: {"workspace_id": workspace_id, "apps": [], "total_records": 0},
    )
    monkeypatch.setattr(
        data_retention_service.control_plane_repository,
        "list_deployed_agent_business_insights",
        fake_insights,
    )

    payload = await data_retention_service.export_workspace_data(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-1",
    )

    items = payload["business_insights"]["items"]
    assert len(items) == 1
    entry = items[0]
    assert "external_user_id" not in entry["metadata"]
    assert "session_id" not in entry["metadata"]
    rendered = " ".join(entry["redacted_examples"])
    assert "me@example.com" not in rendered
    assert "415 555 1234" not in rendered
    assert "sk-secret-12345678" not in rendered
    assert "[name]" in rendered


@pytest.mark.anyio
async def test_workspace_purge_requires_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    delete_mock_called = False

    async def fake_delete(**_kwargs):
        nonlocal delete_mock_called
        delete_mock_called = True
        return {}

    monkeypatch.setattr(
        data_retention_service.control_plane_repository,
        "delete_workspace_scope_data",
        fake_delete,
    )

    with pytest.raises(ValueError):
        await data_retention_service.purge_workspace_scope_data(
            tenant_id="tenant-1",
            workspace_id="ws-1",
            confirm=False,
        )

    assert delete_mock_called is False


@pytest.mark.anyio
async def test_customer_and_agent_purges_are_boundary_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_external: dict[str, Any] = {}
    observed_agent: dict[str, Any] = {}

    async def fake_external_delete(**kwargs):
        observed_external.update(kwargs)
        return {"deleted_channel_event_count": 1}

    async def fake_agent_delete(**kwargs):
        observed_agent.update(kwargs)
        return {"deleted_channel_event_count": 2}

    monkeypatch.setattr(
        data_retention_service.control_plane_repository,
        "delete_deployed_agent_external_user_data",
        fake_external_delete,
    )
    monkeypatch.setattr(
        data_retention_service.control_plane_repository,
        "delete_deployed_agent_scope_data",
        fake_agent_delete,
    )

    await data_retention_service.purge_deployed_agent_external_customer_data(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-A",
        channel_key="telegram",
        external_user_id="customer-1",
    )
    await data_retention_service.purge_deployed_agent_scope_data(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        deployed_agent_id="dagent-B",
    )

    assert observed_external == {
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "deployed_agent_id": "dagent-A",
        "channel_key": "telegram",
        "external_user_id": "customer-1",
    }
    assert observed_agent == {
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "deployed_agent_id": "dagent-B",
    }


def test_purge_workspace_clears_sage_and_mini_app_state() -> None:
    with tempfile.TemporaryDirectory(prefix="phase16-retention-") as temp_dir:
        workspace_root = Path(temp_dir) / "workspace"
        original_workspace_dir = workspace_context._WORKSPACE_DIR
        workspace_context._WORKSPACE_DIR = workspace_root
        try:
            mini_apps_service.upsert_mini_app_contract(
                "ws-1",
                "calorie_tracking",
                records=[{"id": "meal-1", "kind": "meal", "summary": "Chicken bowl"}],
            )
            sage_memory_service.upsert_memory_entry(
                workspace_id="ws-1",
                category="safe_general",
                title="Tone",
                content="Keep concise.",
            )

            async def _run() -> dict:
                async def fake_delete(**_kwargs):
                    return {
                        "deleted_channel_event_count": 1,
                        "deleted_memory_count": 1,
                        "deleted_daily_usage_count": 1,
                        "deleted_acquisition_touch_count": 0,
                        "deleted_upgrade_click_count": 0,
                        "deleted_business_insight_count": 0,
                        "deleted_privacy_request_count": 0,
                        "deleted_privacy_audit_count": 0,
                        "deleted_activity_event_count": 0,
                    }

                original_delete = data_retention_service.control_plane_repository.delete_workspace_scope_data
                data_retention_service.control_plane_repository.delete_workspace_scope_data = fake_delete
                try:
                    return await data_retention_service.purge_workspace_scope_data(
                        tenant_id="tenant-1",
                        workspace_id="ws-1",
                        confirm=True,
                    )
                finally:
                    data_retention_service.control_plane_repository.delete_workspace_scope_data = original_delete

            import asyncio

            payload = asyncio.run(_run())
            assert payload["deleted_counts"]["deleted_channel_event_count"] == 1
            assert payload["mini_apps_wipe"]["deleted_records_count"] == 1
            assert payload["sage_memory_wipe"]["deleted_count"] == 1
            assert mini_apps_service.list_mini_app_contracts("ws-1")["count"] == 0
            assert len(sage_memory_service.list_sage_memory(workspace_id="ws-1")["items"]) == 0
        finally:
            workspace_context._WORKSPACE_DIR = original_workspace_dir
