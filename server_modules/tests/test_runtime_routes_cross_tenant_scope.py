"""A user of tenant A must never see tenant B's rows through the runtime routes.

`require_api_key` (`runtime_common.py`) resolves ANY authenticated user of ANY
tenant -- it answers "is someone logged in", never "may this person see this
workspace".  Four live routes carried it as their only gate:

    GET /runtime/runtimes/status        every tenant's machine_id / hostname /
    GET /local/workers/status           workspace_id / tenant_id / current task
    GET /runtime/runtimes/reliability   up to 10 other tenants' failed run ids
    GET /health/internal                global top-5 {workspace_id, count}

Every test below fails against the pre-fix code: each one asserts on the
ABSENCE of a foreign tenant's row, and the pre-fix routes returned the whole
fleet to any signed-in caller.  Each is paired with an operator-principal case,
so a "fix" that simply blanked the route for everybody would fail too.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import routes_health, run_state_repository, runtime_runtime_api


def _worker(worker_id: str, tenant_id: str, workspace_id: str, **overrides):
    record = {
        "worker_id": worker_id,
        "runtime_id": worker_id,
        "machine_id": worker_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "display_name": f"{worker_id}.local",
        "platform": "macos",
        "runtime_type": "local_companion",
        "status": "idle",
        "online": True,
        "current_run_id": None,
        "control_state": "active",
        "execution_targets": ["local_companion"],
        "capabilities": ["read_write_files"],
    }
    record.update(overrides)
    return record


# Two tenants, one machine each. `tenant-b` is the tenant our caller must never
# learn anything about.
FLEET_STATUS = {
    "enabled": True,
    "lease_seconds": 30,
    "summary": {
        "known": 2,
        "online": 2,
        "busy": 1,
        "idle": 1,
        "offline": 0,
        "pending_runs": 3,
        "claimed_runs": 1,
    },
    "capability_queue": {
        "read_write_files": ["worker-a", "worker-b"],
    },
    "watchdog": {},
    "pressure": {},
    "items": [
        _worker("worker-a", "tenant-a", "ws-a"),
        _worker("worker-b", "tenant-b", "ws-b", current_run_id="run-b-secret"),
    ],
}


def _tenant_a_user():
    """An ordinary signed-in customer of tenant A. No operator authority."""
    return {
        "user_id": "user-a",
        "auth_type": "bearer",
        "email": "a@example.com",
        "role": "owner",
        "is_admin": False,
        "auth_admin": False,
        "workspace_ids": ["ws-a"],
        "workspace_access": {
            "ws-a": {
                "workspace_id": "ws-a",
                "tenant_id": "tenant-a",
                "role": "owner",
                "tenant_role": "owner",
            }
        },
    }


def _operator_user():
    """The platform service key -- possession of ORION_API_KEY."""
    return {"user_id": "service", "auth_type": "api_key", "role": "service"}


class _StatusRouteScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            patch("server_modules.local_queue.recover_orphaned_local_runs_on_startup"),
            patch(
                "server_modules.local_queue.handle_get_local_workers_status",
                return_value=FLEET_STATUS,
            ),
            patch(
                "server_modules.outbox_service.get_outbox_delivery_status",
                return_value={"undelivered_count": 0},
            ),
            # workspace_tenant_id() resolves the tenant PER WORKSPACE (never off
            # the stale users.tenant_id column); stub the workspace->tenant
            # lookup rather than the resolution path itself.
            patch(
                "server_modules.auth.tenant_id_for_workspace",
                side_effect=lambda workspace_id: {"ws-a": "tenant-a", "ws-b": "tenant-b"}[workspace_id],
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_tenant_a_user_never_sees_tenant_b_machine(self):
        payload = runtime_runtime_api.scoped_runtime_status_payload(_tenant_a_user())

        machine_ids = [item.get("machine_id") for item in payload["items"]]
        self.assertEqual(machine_ids, ["worker-a"])

        serialized = repr(payload)
        for leaked in ("worker-b", "tenant-b", "ws-b", "run-b-secret"):
            self.assertNotIn(leaked, serialized, f"{leaked} leaked into a tenant-a response")

    def test_summary_is_recomputed_from_the_scoped_items(self):
        # Reporting the whole fleet's counts beside a filtered item list is
        # still a cross-tenant disclosure, just an arithmetic one.
        payload = runtime_runtime_api.scoped_runtime_status_payload(_tenant_a_user())

        self.assertEqual(payload["summary"]["known"], 1)
        self.assertEqual(payload["summary"]["online"], 1)
        self.assertEqual(payload["summary"]["busy"], 0)

    def test_capability_queue_drops_foreign_worker_ids(self):
        payload = runtime_runtime_api.scoped_runtime_status_payload(_tenant_a_user())

        self.assertEqual(payload["capability_queue"], {"read_write_files": ["worker-a"]})

    def test_user_with_no_workspace_grant_sees_nothing(self):
        stranger = {
            "user_id": "user-c",
            "auth_type": "bearer",
            "is_admin": False,
            "auth_admin": False,
            "workspace_ids": [],
            "workspace_access": {},
        }

        payload = runtime_runtime_api.scoped_runtime_status_payload(stranger)

        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["summary"]["known"], 0)

    def test_operator_principal_still_gets_the_global_fleet(self):
        # The ops daemon (scripts/orion_ops_daemon.py) and the orion_*.sh
        # scripts poll this route with X-API-Key: $ORION_API_KEY and act on
        # summary.online -- scoping them to nothing would make the auto-recover
        # daemon restart a healthy runtime in a loop.
        payload = runtime_runtime_api.scoped_runtime_status_payload(_operator_user())

        machine_ids = sorted(item.get("machine_id") for item in payload["items"])
        self.assertEqual(machine_ids, ["worker-a", "worker-b"])
        self.assertEqual(payload["summary"]["online"], 2)
        self.assertEqual(payload["view"], "global_operator")

    def test_legacy_local_workers_route_is_scoped_too(self):
        payload = runtime_runtime_api.scoped_legacy_local_workers_status_payload(_tenant_a_user())

        self.assertEqual([item.get("machine_id") for item in payload["items"]], ["worker-a"])
        self.assertEqual(payload["known"], 1)
        self.assertEqual(payload["online_workers"], 1)
        self.assertNotIn("worker-b", repr(payload))


class _ReliabilityRouteScopeTests(unittest.TestCase):
    """`run_archive` was read with no WHERE at all, `live_runs` with none either."""

    def setUp(self) -> None:
        self.live_calls = []
        self.archive_calls = []

        def _live(states, *, workspace_ids=None):
            self.live_calls.append(workspace_ids)
            rows = [
                {"run_id": "run-a", "workspace_id": "ws-a", "status": "failed"},
                {"run_id": "run-b-secret", "workspace_id": "ws-b", "status": "failed"},
            ]
            if workspace_ids is None:
                return rows
            return [row for row in rows if row["workspace_id"] in set(workspace_ids)]

        def _archive(limit=200, *, workspace_ids=None):
            self.archive_calls.append(workspace_ids)
            rows = [
                {"run_id": "archived-a", "workspace_id": "ws-a"},
                {"run_id": "archived-b-secret", "workspace_id": "ws-b"},
            ]
            if workspace_ids is None:
                return rows
            return [row for row in rows if row["workspace_id"] in set(workspace_ids)]

        patches = [
            patch(
                "server_modules.runtime_runtime_api.run_state_repository.sync_list_live_runs_by_state",
                side_effect=_live,
            ),
            patch(
                "server_modules.runtime_runtime_api.run_state_repository.sync_list_run_archive",
                side_effect=_archive,
            ),
            patch(
                "server_modules.runtime_runtime_api.runs_output._serialize_run_snapshot",
                side_effect=lambda run_id, item: dict(item),
            ),
            patch(
                "server_modules.outbox_service.get_outbox_delivery_status",
                return_value={"undelivered_count": 0},
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _incomplete_ids(self, payload):
        for value in payload.values():
            if isinstance(value, dict) and "incomplete_run_ids" in value:
                return value["incomplete_run_ids"]
        self.fail("reliability payload carried no incomplete_run_ids section")

    def test_tenant_a_user_never_sees_tenant_b_run_ids(self):
        payload = runtime_runtime_api.scoped_runtime_reliability_payload(_tenant_a_user())

        ids = self._incomplete_ids(payload)
        self.assertIn("run-a", ids)
        self.assertNotIn("run-b-secret", ids)
        self.assertNotIn("archived-b-secret", repr(payload))

    def test_workspace_predicate_reaches_the_sql_not_just_a_python_filter(self):
        # Filtering in Python after a LIMITed global read would silently return
        # an empty page whenever other tenants fill the limit.
        runtime_runtime_api.scoped_runtime_reliability_payload(_tenant_a_user())

        self.assertEqual(self.live_calls, [["ws-a"]])
        self.assertEqual(self.archive_calls, [["ws-a"]])

    def test_operator_principal_still_gets_the_global_snapshot(self):
        payload = runtime_runtime_api.scoped_runtime_reliability_payload(_operator_user())

        ids = self._incomplete_ids(payload)
        self.assertIn("run-a", ids)
        self.assertIn("run-b-secret", ids)
        self.assertEqual(self.live_calls, [None])


class _InternalHealthScopeTests(unittest.TestCase):
    HEALTH = {
        "ok": True,
        "scale_safety_baseline": {
            "local_queue": {
                "workspace_hotspots": [
                    {"workspace_id": "ws-a", "count": 4},
                    {"workspace_id": "ws-b", "count": 9},
                ],
                "dead_letters": {
                    "dead_letter_count": 13,
                    "workspace_hotspots": [
                        {"workspace_id": "ws-a", "count": 2},
                        {"workspace_id": "ws-b", "count": 11},
                    ],
                },
            }
        },
    }

    def test_tenant_a_user_never_sees_tenant_b_workspace_hotspots(self):
        scoped = routes_health.scope_internal_health_payload(self.HEALTH, _tenant_a_user())

        local_queue = scoped["scale_safety_baseline"]["local_queue"]
        self.assertEqual(local_queue["workspace_hotspots"], [{"workspace_id": "ws-a", "count": 4}])
        self.assertEqual(
            local_queue["dead_letters"]["workspace_hotspots"],
            [{"workspace_id": "ws-a", "count": 2}],
        )
        self.assertNotIn("ws-b", repr(scoped))

    def test_scoping_does_not_mutate_the_shared_payload(self):
        routes_health.scope_internal_health_payload(self.HEALTH, _tenant_a_user())

        self.assertEqual(
            len(self.HEALTH["scale_safety_baseline"]["local_queue"]["workspace_hotspots"]),
            2,
        )

    def test_operator_principal_still_sees_every_hotspot(self):
        scoped = routes_health.scope_internal_health_payload(self.HEALTH, _operator_user())

        self.assertEqual(
            scoped["scale_safety_baseline"]["local_queue"]["workspace_hotspots"],
            self.HEALTH["scale_safety_baseline"]["local_queue"]["workspace_hotspots"],
        )

    def test_route_handler_applies_the_scope(self):
        with patch("server_modules.health_core.health", new=AsyncMock(return_value=self.HEALTH)):
            scoped = asyncio.run(routes_health.internal_health(current_user=_tenant_a_user()))

        self.assertNotIn("ws-b", repr(scoped))


class _RepositoryFailsClosedTests(unittest.TestCase):
    """`($1 = '' OR tenant_id = $1)` fails OPEN when an argument is forgotten."""

    def test_list_fleet_workers_refuses_an_unscoped_read(self):
        with self.assertRaises(ValueError):
            asyncio.run(run_state_repository.list_fleet_workers())

    def test_sync_list_fleet_workers_refuses_an_unscoped_read(self):
        # The sync wrapper must raise on its own: `_run_sync` swallows an
        # exception into `fallback`, so validation only inside the coroutine
        # would turn a forgotten scope into a silent empty list.
        with self.assertRaises(ValueError):
            run_state_repository.sync_list_fleet_workers()

    def test_list_fleet_queue_partitions_refuses_an_unscoped_read(self):
        with self.assertRaises(ValueError):
            asyncio.run(run_state_repository.list_fleet_queue_partitions())

    def test_a_deliberate_global_read_is_allowed_when_named(self):
        with patch(
            "server_modules.run_state_repository._read_pool",
            new=AsyncMock(return_value=None),
        ):
            self.assertEqual(
                asyncio.run(run_state_repository.list_fleet_workers(include_all_tenants=True)),
                [],
            )

    def test_empty_state_list_does_not_discard_the_workspace_scope(self):
        # `list_live_runs_by_state([])` delegates to `list_live_runs()`, which
        # has no workspace predicate -- the scope must still be applied.
        rows = [
            {"run_id": "run-a", "workspace_id": "ws-a"},
            {"run_id": "run-b-secret", "workspace_id": "ws-b"},
        ]
        with patch(
            "server_modules.run_state_repository.list_live_runs",
            new=AsyncMock(return_value=rows),
        ):
            scoped = asyncio.run(
                run_state_repository.list_live_runs_by_state([], workspace_ids=["ws-a"])
            )

        self.assertEqual([row["run_id"] for row in scoped], ["run-a"])

    def test_empty_workspace_scope_returns_nothing_rather_than_everything(self):
        # An empty allow-list means "this caller may see no workspace" and must
        # never be widened back into "no filter".
        with patch(
            "server_modules.run_state_repository._read_pool",
            new=AsyncMock(side_effect=AssertionError("must not reach the database")),
        ):
            self.assertEqual(
                asyncio.run(run_state_repository.list_run_archive(workspace_ids=[])),
                [],
            )
            self.assertEqual(
                asyncio.run(run_state_repository.list_live_runs_by_state(["failed"], workspace_ids=[])),
                [],
            )


if __name__ == "__main__":
    unittest.main()
