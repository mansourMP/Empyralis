"""Real concurrency regression test for C1/S1
(docs/design/audit-silent-failures.md): two-plus concurrent callers
patching the SAME workspace_agent_installs row's metadata/tool_toggles must
not silently discard one another's write.

Before this fix, `agent_registry_repository.update_workspace_agent_install`
did a plain read -> merge in Python -> blind UPDATE, with no lock and no
version check. Two concurrent callers reading the same pre-image and
writing back their own locally-merged copy would race: whichever UPDATE
committed last won, silently discarding the other caller's patch (this is
exactly what made `fleet_message_agent`'s writes to `metadata.fleet_inbox`
unreliable even before accounting for the fact that nothing ever read
fleet_inbox back at all).

This test runs against a REAL Postgres control-plane database (not a mock)
-- the fix is a `SELECT ... FOR UPDATE` row lock, and only a real database
enforces real row-level locking; a mocked connection can't prove anything
about actual concurrent-transaction serialization. Skips cleanly if no
DATABASE_URL is reachable, matching this repo's existing `blackbox_db`
convention (see server_modules/blackbox_runtime_support.py).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.blackbox_db


def _database_url() -> str:
    url = str(os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url
    try:
        from dotenv import load_dotenv

        # MAN-202: scoped to this checkout's own repo-root .env rather than
        # a bare load_dotenv(), which would search UP the directory tree
        # from cwd and could reach a different checkout's .env (see
        # server_modules/runtime_config.py for the full writeup).
        repo_root_env = Path(__file__).resolve().parent.parent.parent / ".env"
        load_dotenv(repo_root_env)
    except Exception:
        pass
    return str(os.getenv("DATABASE_URL") or "").strip()


@pytest.fixture
def _require_database_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("No reachable DATABASE_URL -- concurrency test needs a real Postgres control-plane DB.")
    return url


@pytest.mark.asyncio
async def test_concurrent_metadata_patches_all_survive(_require_database_url: str) -> None:
    from server_modules import agent_registry_repository as repo
    from server_modules import control_plane_repository
    from server_modules import fleet_tools

    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        pytest.skip("Postgres control-plane schema unavailable -- falling back to SQLite, no row-lock semantics to test.")

    run_id = uuid.uuid4().hex[:12]
    tenant_id = f"test-tenant-concurrency-{run_id}"
    workspace_id = f"test-workspace-concurrency-{run_id}"

    created = await fleet_tools.fleet_create_agent(
        actor_id="test-actor",
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        name=f"Concurrency Test Agent {run_id}",
        capability_preset="standard",
    )
    assert created.get("ok"), f"fixture setup failed: {created}"
    agent_id = str(created["agent_id"])

    try:
        concurrency = 10

        async def _patch_metadata(i: int) -> None:
            await repo.update_workspace_agent_install(
                agent_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                metadata={f"concurrency_test_key_{i}": True},
            )

        # All N calls fire "at once" against the SAME row. Without the
        # SELECT...FOR UPDATE fix, each would read the same pre-image and
        # the last UPDATE to commit would silently discard the others'
        # keys. With the fix, Postgres serializes the row lock across the
        # N concurrent transactions regardless of asyncio scheduling --
        # every key must survive.
        await asyncio.gather(*[_patch_metadata(i) for i in range(concurrency)])

        final = await repo.get_workspace_agent_install_bundle(
            agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
        )
        assert final is not None
        metadata = final.get("metadata") or {}
        missing = [
            f"concurrency_test_key_{i}" for i in range(concurrency)
            if metadata.get(f"concurrency_test_key_{i}") is not True
        ]
        assert not missing, (
            f"Lost update(s) detected -- {len(missing)}/{concurrency} concurrent "
            f"metadata patches vanished: {missing}. This is exactly the C1/S1 "
            "lost-update race docs/design/audit-silent-failures.md describes."
        )

        # Same shape, different JSONB column (tool_toggles) -- the audit
        # named this as also affected by the identical read-merge-write
        # pattern (S1).
        async def _patch_tool_toggle(i: int) -> None:
            await repo.update_workspace_agent_install(
                agent_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                tool_toggles={f"concurrency_test_tool_{i}": True},
            )

        await asyncio.gather(*[_patch_tool_toggle(i) for i in range(concurrency)])

        final_2 = await repo.get_workspace_agent_install_bundle(
            agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
        )
        assert final_2 is not None
        tool_toggles = final_2.get("tool_toggles") or {}
        missing_toggles = [
            f"concurrency_test_tool_{i}" for i in range(concurrency)
            if tool_toggles.get(f"concurrency_test_tool_{i}") is not True
        ]
        assert not missing_toggles, (
            f"Lost update(s) detected in tool_toggles -- {len(missing_toggles)}/"
            f"{concurrency} concurrent patches vanished: {missing_toggles}."
        )

        # And the original metadata keys from the first round must STILL
        # be present -- proves the tool_toggles round didn't itself
        # silently clobber metadata (i.e. the fix isn't just "last field
        # wins" restated).
        metadata_after = final_2.get("metadata") or {}
        still_missing = [
            f"concurrency_test_key_{i}" for i in range(concurrency)
            if metadata_after.get(f"concurrency_test_key_{i}") is not True
        ]
        assert not still_missing, (
            f"Prior round's metadata keys were lost after a second concurrent "
            f"round on a different column: {still_missing}"
        )
    finally:
        try:
            await control_plane_repository.rls_execute(
                pool,
                "DELETE FROM workspace_agent_installs WHERE tenant_id = $1 AND workspace_id = $2",
                tenant_id, workspace_id,
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
            await control_plane_repository.rls_execute(
                pool,
                "DELETE FROM agent_definitions WHERE tenant_id = $1 AND workspace_id = $2",
                tenant_id, workspace_id,
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
            await control_plane_repository.rls_execute(
                pool,
                "DELETE FROM runtime_profiles WHERE tenant_id = $1 AND workspace_id = $2",
                tenant_id, workspace_id,
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
        except Exception:
            pass  # best-effort cleanup -- unique per-run ids mean leftovers never collide
