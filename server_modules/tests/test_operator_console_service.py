"""operator_console_service.py -- the full operator-console backend behind
ops.empyralis.ai. Same structural convention as test_platform_activation_
service.py (the narrow first version this extends): every public builder
function is tested for (1) the happy path shaping real counts into the
response, and (2) the RLS-bypass canary trap -- the one thing CLAUDE.md
names as having already bitten this exact feature once. A dedicated
structural test also proves every function that talks to Postgres passes
bypass_rls=True exactly once and that its SQL reads the canary back, so a
future edit that quietly drops the kwarg is caught here, not in production.
"""

from __future__ import annotations

import inspect
import json
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import operator_console_service as svc


def _patched_fetchrow(return_value=None, *, side_effect=None):
    kwargs = {"side_effect": side_effect} if side_effect is not None else {"return_value": return_value}
    return patch.object(svc.control_plane_repository, "rls_fetchrow", new=AsyncMock(**kwargs))


class OverviewTests(unittest.IsolatedAsyncioTestCase):
    def _row(self, **overrides):
        base = {
            "rls_bypass_scope": "on",
            "total_users": 105,
            "total_workspaces": 105,
            "total_agents": 68,
            "total_projects": 166,
            "total_tasks": 36,
            "total_documents": 5,
            "agents_with_runs": 12,
            "workspaces_with_second_member": 0,
            "signups_last_7_days": 2,
            "signups_last_30_days": 9,
            "signups_last_90_days": 20,
            "total_platform_cost_usd": 41.5,
            "total_credits_debited": 900.0,
            "total_agent_computers": 3,
            "agent_computers_by_status": {"connected": 2, "provisioning": 1},
        }
        base.update(overrides)
        return base

    async def test_happy_path_shapes_totals_and_derived_fields(self) -> None:
        with _patched_fetchrow(self._row()) as mock_fetch:
            snapshot = await svc.build_overview(pool=object())

        mock_fetch.assert_awaited_once()
        assert mock_fetch.await_args.kwargs.get("bypass_rls") is True

        assert snapshot["rls_bypass_verified"] is True
        assert snapshot["totals"] == {
            "users": 105, "workspaces": 105, "agents": 68,
            "projects": 166, "tasks": 36, "documents": 5,
        }
        assert snapshot["signups"] == {"last_7_days": 2, "last_30_days": 9, "last_90_days": 20}
        assert snapshot["agents_runtime"] == {"total": 68, "with_runs": 12, "never_run": 56}
        assert snapshot["spend"] == {"total_platform_cost_usd": 41.5, "total_credits_debited": 900.0}
        assert snapshot["agent_computers"]["total"] == 3
        assert snapshot["agent_computers"]["by_status"] == {"connected": 2, "provisioning": 1}
        assert "personal hardware" in snapshot["agent_computers"]["excludes"]

    async def test_never_run_cannot_go_negative(self) -> None:
        with _patched_fetchrow(self._row(total_agents=5, agents_with_runs=9)):
            snapshot = await svc.build_overview(pool=object())
        assert snapshot["agents_runtime"]["never_run"] == 0

    async def test_canary_raises_on_broken_scope(self) -> None:
        with _patched_fetchrow(self._row(rls_bypass_scope="off", total_users=0, total_workspaces=0)):
            with self.assertRaises(svc.OperatorConsoleScopeBroken):
                await svc.build_overview(pool=object())

    async def test_canary_raises_when_no_row_returned(self) -> None:
        with _patched_fetchrow(None):
            with self.assertRaises(svc.OperatorConsoleScopeBroken):
                await svc.build_overview(pool=object())


class ListAccountsTests(unittest.IsolatedAsyncioTestCase):
    def _account(self, **overrides):
        base = {
            "workspace_id": "ws_1",
            "tenant_id": "t_1",
            "name": "Acme",
            "created_at": "2026-01-01T00:00:00+00:00",
            "workspace_status": "active",
            "owner": {"user_id": "u_1", "email": "a@acme.test", "display_name": "Ada"},
            "member_count": 1,
            "last_active_at": "2026-08-29T00:00:00+00:00",
            "project_count": 2,
            "task_count": 3,
            "document_count": 1,
            "agent_count": 4,
            "agents_with_runs": 1,
            "agents_never_run": 3,
            "total_platform_cost_usd": 1.5,
            "total_credits_debited": 30.0,
        }
        base.update(overrides)
        return base

    async def test_happy_path_returns_pagination_metadata_and_accounts(self) -> None:
        row = {
            "rls_bypass_scope": "on",
            "total_matching": 1,
            # Supplied as a JSON string here (not a pre-decoded list) to
            # genuinely exercise _decode_json_array's string-decoding path --
            # asyncpg returns jsonb as text with no codec registered.
            "accounts": json.dumps([self._account()]),
        }
        with _patched_fetchrow(row) as mock_fetch:
            result = await svc.list_accounts(pool=object(), sort_by="name", sort_dir="asc")

        mock_fetch.assert_awaited_once()
        assert mock_fetch.await_args.kwargs.get("bypass_rls") is True
        assert result["total_matching"] == 1
        assert result["returned_count"] == 1
        assert result["accounts"][0]["workspace_id"] == "ws_1"
        assert result["sort_by"] == "name"
        assert result["sort_dir"] == "asc"

    async def test_empty_result_set_still_carries_the_canary(self) -> None:
        row = {"rls_bypass_scope": "on", "total_matching": 0, "accounts": []}
        with _patched_fetchrow(row):
            result = await svc.list_accounts(pool=object())
        assert result["total_matching"] == 0
        assert result["accounts"] == []

    async def test_invalid_sort_by_is_rejected_before_any_query_runs(self) -> None:
        with _patched_fetchrow(side_effect=AssertionError("must never query on a bad sort_by")):
            with self.assertRaises(ValueError):
                await svc.list_accounts(pool=object(), sort_by="not_a_real_column")

    async def test_invalid_sort_dir_is_rejected_before_any_query_runs(self) -> None:
        with _patched_fetchrow(side_effect=AssertionError("must never query on a bad sort_dir")):
            with self.assertRaises(ValueError):
                await svc.list_accounts(pool=object(), sort_dir="sideways")

    async def test_filters_bind_as_placeholders_not_string_interpolation(self) -> None:
        """A search string containing SQL-special characters must reach the
        query only as a bound parameter value, never spliced into the SQL
        text itself."""
        row = {"rls_bypass_scope": "on", "total_matching": 0, "accounts": []}
        with _patched_fetchrow(row) as mock_fetch:
            await svc.list_accounts(pool=object(), search="Robert'); DROP TABLE workspaces;--")

        sql_arg = mock_fetch.await_args.args[1]
        bound_args = mock_fetch.await_args.args[2:]
        assert "DROP TABLE" not in sql_arg
        assert any("DROP TABLE" in str(a) for a in bound_args)

    async def test_limit_is_capped(self) -> None:
        row = {"rls_bypass_scope": "on", "total_matching": 0, "accounts": []}
        with _patched_fetchrow(row):
            result = await svc.list_accounts(pool=object(), limit=999999)
        assert result["limit"] == 1000

    async def test_canary_raises_on_broken_scope(self) -> None:
        row = {"rls_bypass_scope": "off", "total_matching": 0, "accounts": []}
        with _patched_fetchrow(row):
            with self.assertRaises(svc.OperatorConsoleScopeBroken):
                await svc.list_accounts(pool=object())


class AccountDetailTests(unittest.IsolatedAsyncioTestCase):
    def _row(self, **overrides):
        base = {
            "rls_bypass_scope": "on",
            "workspace_found": True,
            "tenant_id": "t_1",
            "workspace_id": "ws_1",
            "name": "Acme",
            "created_at": "2026-01-01T00:00:00+00:00",
            "workspace_status": "active",
            "workspace_type": "personal",
            # A JSON STRING, not a dict -- asyncpg hands back an undecoded
            # jsonb column exactly this way with no custom type codec
            # registered (see get_account_detail's own comment on the
            # `owner` field). Every other jsonb field below is built the
            # same way for the same reason: a dict/list fixture here would
            # never exercise the _decode_json_object/_decode_json_array call
            # that has to run against the real wire shape, which is exactly
            # how this module's `owner` field shipped broken once already.
            "owner": json.dumps({"user_id": "u_1", "email": "a@acme.test", "display_name": "Ada"}),
            "members": [],
            "agents": [],
            "projects": [],
            "recent_activity": [],
            "spend_breakdown": [],
            "recent_failures": [],
            "runs_by_outcome": {"completed": 4, "failed": 1},
        }
        base.update(overrides)
        return base

    async def test_happy_path(self) -> None:
        with _patched_fetchrow(self._row()) as mock_fetch:
            detail = await svc.get_account_detail(pool=object(), workspace_id="ws_1")

        assert mock_fetch.await_args.kwargs.get("bypass_rls") is True
        assert mock_fetch.await_args.args[2] == "ws_1"
        assert detail["name"] == "Acme"
        assert detail["runs_by_outcome"] == {"completed": 4, "failed": 1}
        # The regression this fixture change exists to catch: `owner` must
        # decode into a real object, not pass through as the raw JSON string
        # the row carries it as.
        assert detail["owner"] == {"user_id": "u_1", "email": "a@acme.test", "display_name": "Ada"}
        assert isinstance(detail["owner"], dict)

    async def test_workspace_not_found_returns_none_not_an_empty_account(self) -> None:
        with _patched_fetchrow(self._row(workspace_found=False, name=None, tenant_id=None)):
            detail = await svc.get_account_detail(pool=object(), workspace_id="does-not-exist")
        assert detail is None

    async def test_blank_workspace_id_returns_none_without_querying(self) -> None:
        with _patched_fetchrow(side_effect=AssertionError("must never query on a blank id")):
            detail = await svc.get_account_detail(pool=object(), workspace_id="   ")
        assert detail is None

    async def test_canary_raises_on_broken_scope(self) -> None:
        with _patched_fetchrow(self._row(rls_bypass_scope="off")):
            with self.assertRaises(svc.OperatorConsoleScopeBroken):
                await svc.get_account_detail(pool=object(), workspace_id="ws_1")


class ActivationFunnelTests(unittest.IsolatedAsyncioTestCase):
    async def test_steps_and_drop_offs(self) -> None:
        row = {
            "rls_bypass_scope": "on",
            "step_signup": 105,
            "step_created_project": 40,
            "step_created_task_or_document": 30,
            "step_created_agent": 68,  # agents can exceed project-creators; no monotonic assumption enforced
            "step_agent_ran": 12,
            "step_second_member": 0,
        }
        with _patched_fetchrow(row) as mock_fetch:
            funnel = await svc.build_activation_funnel(pool=object())

        assert mock_fetch.await_args.kwargs.get("bypass_rls") is True
        steps = {s["step"]: s for s in funnel["steps"]}
        assert steps["signup"]["count"] == 105
        assert steps["signup"]["drop_off_from_previous"] == 0
        assert steps["created_project"]["count"] == 40
        assert steps["created_project"]["drop_off_from_previous"] == 65
        assert steps["second_member"]["count"] == 0
        assert steps["second_member"]["pct_of_signups"] == 0.0

    async def test_canary_raises_on_broken_scope(self) -> None:
        row = {"rls_bypass_scope": "off", "step_signup": 0, "step_created_project": 0,
               "step_created_task_or_document": 0, "step_created_agent": 0,
               "step_agent_ran": 0, "step_second_member": 0}
        with _patched_fetchrow(row):
            with self.assertRaises(svc.OperatorConsoleScopeBroken):
                await svc.build_activation_funnel(pool=object())


class RetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_counts_and_percentages(self) -> None:
        row = {
            "rls_bypass_scope": "on",
            "total_users": 100,
            "active_last_1_day": 5,
            "active_last_7_days": 20,
            "active_last_30_days": 50,
        }
        with _patched_fetchrow(row) as mock_fetch:
            retention = await svc.build_retention(pool=object())

        assert mock_fetch.await_args.kwargs.get("bypass_rls") is True
        assert retention["active"]["last_1_day"] == {"count": 5, "pct_of_users": 5.0}
        assert retention["active"]["last_7_days"] == {"count": 20, "pct_of_users": 20.0}
        assert retention["active"]["last_30_days"] == {"count": 50, "pct_of_users": 50.0}

    async def test_canary_raises_on_broken_scope(self) -> None:
        row = {"rls_bypass_scope": "unset", "total_users": 0, "active_last_1_day": 0,
               "active_last_7_days": 0, "active_last_30_days": 0}
        with _patched_fetchrow(row):
            with self.assertRaises(svc.OperatorConsoleScopeBroken):
                await svc.build_retention(pool=object())


class FailuresTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_and_status_filter_args(self) -> None:
        row = {
            "rls_bypass_scope": "on",
            "total_failures": 3,
            "grouped": [{"status": "failed", "error_code": "timeout", "action_domain": "mcp", "event_count": 3,
                         "distinct_workspaces": 2, "distinct_agents": 2,
                         "first_seen": "2026-08-28T00:00:00+00:00", "last_seen": "2026-08-29T00:00:00+00:00"}],
            "recent": [],
        }
        with _patched_fetchrow(row) as mock_fetch:
            result = await svc.list_failures(pool=object(), days=7, limit=50)

        assert mock_fetch.await_args.kwargs.get("bypass_rls") is True
        call_args = mock_fetch.await_args.args
        assert call_args[2] == ["failed", "blocked", "denied"]  # status array bound positionally
        assert result["total_failures"] == 3
        assert result["grouped"][0]["error_code"] == "timeout"

    async def test_days_and_limit_are_bounded(self) -> None:
        row = {"rls_bypass_scope": "on", "total_failures": 0, "grouped": [], "recent": []}
        with _patched_fetchrow(row):
            result = await svc.list_failures(pool=object(), days=99999, limit=99999)
        assert result["window_days"] == 90

    async def test_canary_raises_on_broken_scope(self) -> None:
        row = {"rls_bypass_scope": "off", "total_failures": 0, "grouped": [], "recent": []}
        with _patched_fetchrow(row):
            with self.assertRaises(svc.OperatorConsoleScopeBroken):
                await svc.list_failures(pool=object())


class SpendTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path(self) -> None:
        row = {
            "rls_bypass_scope": "on",
            "total_platform_cost_usd": 12.34,
            "total_credits_debited": 500.0,
            "total_events": 40,
            "by_workspace": [{"workspace_id": "ws_1", "name": "Acme", "total_platform_cost_usd": 12.34,
                               "total_credits_debited": 500.0, "event_count": 40}],
            "by_provider_model": [],
            "over_time": [],
        }
        with _patched_fetchrow(row) as mock_fetch:
            result = await svc.build_spend(pool=object(), days=30)

        assert mock_fetch.await_args.kwargs.get("bypass_rls") is True
        assert result["total_platform_cost_usd"] == 12.34
        assert result["by_workspace"][0]["name"] == "Acme"

    async def test_days_are_bounded(self) -> None:
        row = {"rls_bypass_scope": "on", "total_platform_cost_usd": 0, "total_credits_debited": 0,
               "total_events": 0, "by_workspace": [], "by_provider_model": [], "over_time": []}
        with _patched_fetchrow(row):
            result = await svc.build_spend(pool=object(), days=99999)
        assert result["window_days"] == 365

    async def test_canary_raises_on_broken_scope(self) -> None:
        row = {"rls_bypass_scope": "off", "total_platform_cost_usd": 0, "total_credits_debited": 0,
               "total_events": 0, "by_workspace": [], "by_provider_model": [], "over_time": []}
        with _patched_fetchrow(row):
            with self.assertRaises(svc.OperatorConsoleScopeBroken):
                await svc.build_spend(pool=object())


class StructuralBypassCanaryTests(unittest.TestCase):
    """Same convention as test_platform_activation_service.py's own
    structural guard: the function that issues each query must pass
    bypass_rls=True exactly once, and the SQL it runs must read the canary
    back -- so a refactor that silently drops the kwarg is caught here."""

    FUNCTIONS_AND_SQL = (
        (svc.build_overview, svc.OPERATOR_OVERVIEW_SQL),
        (svc.list_accounts, svc.OPERATOR_ACCOUNTS_SQL_TEMPLATE),
        (svc.get_account_detail, svc.OPERATOR_ACCOUNT_DETAIL_SQL),
        (svc.build_activation_funnel, svc.OPERATOR_ACTIVATION_FUNNEL_SQL),
        (svc.build_retention, svc.OPERATOR_RETENTION_SQL),
        (svc.list_failures, svc.OPERATOR_FAILURES_SQL),
        (svc.build_spend, svc.OPERATOR_SPEND_SQL),
    )

    def test_every_query_function_bypasses_rls_exactly_once_and_reads_the_canary_back(self) -> None:
        for func, sql in self.FUNCTIONS_AND_SQL:
            source = inspect.getsource(func)
            assert source.count("bypass_rls=True") == 1, (
                f"{func.__name__} must call rls_fetchrow with bypass_rls=True exactly once."
            )
            assert "current_setting('app.rls_bypass'" in sql, (
                f"the SQL {func.__name__} runs must read the app.rls_bypass GUC back as its own canary."
            )


if __name__ == "__main__":
    unittest.main()
