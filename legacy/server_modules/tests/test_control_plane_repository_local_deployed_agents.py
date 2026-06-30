from __future__ import annotations

from pathlib import Path

from server_modules import control_plane_repository as repository


def _record(agent_id: str = "dagent_local_1") -> dict[str, object]:
    return {
        "id": agent_id,
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "backing_install_id": "install-1",
        "created_by_user_id": "user-1",
        "name": "Support Agent",
        "avatar": None,
        "persona": "Helpful support",
        "system_prompt": "Answer support questions.",
        "deployment_state": "draft",
        "channels": {"web": {"enabled": True}},
        "knowledge_sources": [{"id": "ks-1"}],
        "runtime_target": "local",
        "billing_plan": "free",
        "is_public": False,
        "quality_stars": None,
        "cost_tier": None,
        "category": None,
        "metadata": {"studio_agent_mode": "single"},
        "operational_state": {"health": "ready"},
        "last_deployed_at": None,
        "last_paused_at": None,
        "created_at": "2026-05-27T00:00:00Z",
        "updated_at": "2026-05-27T00:00:00Z",
    }


def test_local_deployed_agent_store_survives_process_memory_reset(monkeypatch, tmp_path: Path) -> None:
    db_file = tmp_path / "control-plane.sqlite3"
    monkeypatch.setattr(repository, "LOCAL_CONTROL_PLANE_DB_FILE", db_file)
    repository.LOCAL_CONTROL_PLANE_SCHEMA_READY_PATHS.clear()

    stored = repository._local_store_deployed_agent(_record())
    assert stored is not None
    assert stored["channels"] == {"web": {"enabled": True}}
    assert stored["metadata"] == {"studio_agent_mode": "single"}

    repository.LOCAL_CONTROL_PLANE_SCHEMA_READY_PATHS.clear()

    listed = repository._local_list_deployed_agents_for_workspace("ws-1", tenant_id="tenant-1")
    assert [item["id"] for item in listed] == ["dagent_local_1"]

    fetched = repository._local_get_deployed_agent(
        "dagent_local_1",
        tenant_id="tenant-1",
        owner_workspace_id="ws-1",
    )
    assert fetched is not None
    assert fetched["knowledge_sources"] == [{"id": "ks-1"}]

    by_install = repository._local_get_deployed_agent_by_backing_install_id(
        "install-1",
        tenant_id="tenant-1",
        owner_workspace_id="ws-1",
    )
    assert by_install is not None
    assert by_install["id"] == "dagent_local_1"


def test_local_deployed_agent_update_uses_durable_store(monkeypatch, tmp_path: Path) -> None:
    db_file = tmp_path / "control-plane.sqlite3"
    monkeypatch.setattr(repository, "LOCAL_CONTROL_PLANE_DB_FILE", db_file)
    repository.LOCAL_CONTROL_PLANE_SCHEMA_READY_PATHS.clear()
    repository._local_store_deployed_agent(_record())

    updated = repository._local_update_deployed_agent(
        "dagent_local_1",
        tenant_id="tenant-1",
        owner_workspace_id="ws-1",
        updates={
            "deployment_state": "live",
            "channels": {"web": {"enabled": False}, "telegram": {"enabled": True}},
            "operational_state": {"health": "deployed"},
        },
    )

    assert updated is not None
    assert updated["deployment_state"] == "live"
    assert updated["channels"]["telegram"] == {"enabled": True}

    repository.LOCAL_CONTROL_PLANE_SCHEMA_READY_PATHS.clear()
    fetched = repository._local_get_deployed_agent(
        "dagent_local_1",
        tenant_id="tenant-1",
        owner_workspace_id="ws-1",
    )
    assert fetched is not None
    assert fetched["operational_state"] == {"health": "deployed"}
