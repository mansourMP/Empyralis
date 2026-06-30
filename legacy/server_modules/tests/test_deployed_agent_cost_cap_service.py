from __future__ import annotations

import importlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import deployed_agent_cost_cap_service
from server_modules import pricing_registry_service


def _deployed_agent(
    *,
    deployment_state: str = "live",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": "dagent_1",
        "tenant_id": "tenant-1",
        "owner_workspace_id": "ws-1",
        "name": "Store Assistant",
        "deployment_state": deployment_state,
        "metadata": dict(metadata or {}),
    }


def _run(
    *,
    usage_masked: dict[str, object] | None = None,
    usage_accounting: dict[str, object] | None = None,
    deployed_agent_id: str = "dagent_1",
) -> dict[str, object]:
    return {
        "context": {
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "user_goal": "Answer customer question",
            "metadata": {
                "deployed_agent_id": deployed_agent_id,
            },
        },
        "usage_accounting": dict(usage_accounting or {}),
        "usage_masked": dict(
            usage_masked
            or {
                "provider": "openai",
                "model": "gpt-4.1",
                "prompt_tokens": 1000,
                "completion_tokens": 1000,
                "estimated_cost_usd": 1.25,
                "pricing_known": True,
            }
        ),
    }


class DeployedAgentCostCapServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        global deployed_agent_cost_cap_service
        deployed_agent_cost_cap_service = importlib.import_module(
            "server_modules.deployed_agent_cost_cap_service"
        )
        self.workspace_patcher = patch(
            "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={"workspace_id": "ws-1", "tenant_id": "tenant-1"}),
        )
        self.workspace_patcher.start()
        self.credit_ledger_mock = AsyncMock(return_value={"id": "cled_1"})
        self.credit_ledger_patcher = patch(
            "server_modules.deployed_agent_cost_cap_service.control_plane_repository.record_credit_ledger_event",
            new=self.credit_ledger_mock,
        )
        self.credit_ledger_patcher.start()

    def tearDown(self) -> None:
        self.credit_ledger_patcher.stop()
        self.workspace_patcher.stop()

    async def test_settle_records_80_percent_notification_once(self) -> None:
        deployed_agent = _deployed_agent(metadata={"monthly_cost_cap_usd": 10.0})

        with (
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry",
                new=AsyncMock(return_value={"id": "dcost_1"}),
            ) as record_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.summarize_deployed_agent_monthly_cost_ledger",
                new=AsyncMock(
                    return_value={
                        "usage_month": "2026-04-01",
                        "runs_count": 1,
                        "total_cost_usd": 8.0,
                        "last_run_id": "run-1",
                        "last_run_completed_at": "2026-04-13T12:00:00Z",
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.update_deployed_agent",
                new=AsyncMock(return_value=deployed_agent),
            ) as update_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.set_deployed_agent_state",
                new=AsyncMock(),
            ) as pause_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.outbox_service.emit_notification_event",
            ) as notification_mock,
        ):
            result = await deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap(
                run_id="run-1",
                run=_run(
                    usage_masked={
                        "provider": "openai",
                        "model": "gpt-4.1",
                        "prompt_tokens": 1000,
                        "completion_tokens": 1000,
                        "estimated_cost_usd": 8.0,
                        "pricing_known": True,
                    }
                ),
                status="completed",
                now_iso="2026-04-13T12:00:00Z",
            )

        self.assertTrue(result["applied"])
        self.assertEqual(result["last_threshold_reached"], "80")
        self.assertFalse(result["auto_paused"])
        self.assertEqual(notification_mock.call_count, 1)
        self.assertEqual(notification_mock.call_args.kwargs["action"], "deployed_agent_budget_80")
        pause_mock.assert_not_awaited()
        ledger_metadata = record_mock.await_args.kwargs["metadata"]
        self.assertTrue(ledger_metadata["pricing_known"])
        self.assertEqual(
            ledger_metadata["pricing_registry_version"],
            pricing_registry_service.pricing_registry_version(),
        )
        self.assertEqual(ledger_metadata["source_surface"], "deployed_agent_channel")
        self.assertEqual(ledger_metadata["credit_type"], "ai_tokens")
        self.assertEqual(ledger_metadata["credit_item_type"], "ai_pro_tokens")
        self.assertEqual(ledger_metadata["credit_quantity"], 2000.0)
        self.assertEqual(ledger_metadata["credit_quantity_unit"], "tokens")
        self.assertEqual(ledger_metadata["billing_source"], "empyralis_credits")
        unified = ledger_metadata["unified_credit_ledger_event"]
        self.assertEqual(unified["surface"], "studio")
        self.assertEqual(unified["source_surface"], "deployed_agent_channel")
        self.assertEqual(unified["payer"], "platform_credits")
        self.assertEqual(unified["credit_type"], "ai_tokens")
        self.assertEqual(unified["agent_id"], "dagent_1")
        self.credit_ledger_mock.assert_awaited()
        self.assertEqual(self.credit_ledger_mock.await_args.kwargs["source_table"], "deployed_agent_monthly_cost_ledger")
        self.assertEqual(self.credit_ledger_mock.await_args.kwargs["source_event_id"], "dcost_1")
        budget_cycle = update_mock.await_args.kwargs["updates"]["operational_state"]["current_budget_cycle"]
        self.assertEqual(budget_cycle["current_burn_usd"], 8.0)
        self.assertEqual(budget_cycle["percent_used"], 80.0)
        self.assertEqual(budget_cycle["last_threshold_reached"], "80")
        self.assertEqual(budget_cycle["threshold_80_cap_usd"], 10.0)

    async def test_settle_uses_usage_accounting_when_legacy_usage_projection_is_missing(self) -> None:
        deployed_agent = _deployed_agent(metadata={"monthly_cost_cap_usd": 10.0})

        with (
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry",
                new=AsyncMock(return_value={"id": "dcost_1"}),
            ) as record_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.summarize_deployed_agent_monthly_cost_ledger",
                new=AsyncMock(
                    return_value={
                        "usage_month": "2026-04-01",
                        "runs_count": 1,
                        "total_cost_usd": 1.25,
                        "last_run_id": "run-usage-accounting",
                        "last_run_completed_at": "2026-04-13T12:00:00Z",
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.update_deployed_agent",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.outbox_service.emit_notification_event",
            ),
        ):
            result = await deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap(
                run_id="run-usage-accounting",
                run=_run(
                    usage_masked={},
                    usage_accounting={
                        "usage_record_id": "uacct_run-usage-accounting",
                        "run_id": "run-usage-accounting",
                        "tenant_id": "tenant-1",
                        "workspace_id": "ws-1",
                        "deployed_agent_id": "dagent_1",
                        "source_surface": "deployed_agent_channel",
                        "requested_provider": "openai",
                        "effective_provider": "openai",
                        "requested_model": "gpt-4.1",
                        "effective_model": "gpt-4.1",
                        "input_tokens": 1000,
                        "output_tokens": 1000,
                        "total_tokens": 2000,
                        "estimated_cost_usd": 1.25,
                        "pricing_known": True,
                        "pricing_registry_version": "2026-04-13",
                        "pricing_source": "https://platform.openai.com/pricing",
                        "estimation_mode": "provider_reported",
                        "completed_at": "2026-04-13T12:00:00Z",
                        "metadata": {},
                    },
                ),
                status="completed",
                now_iso="2026-04-13T12:00:00Z",
            )

        self.assertTrue(result["applied"])
        ledger_kwargs = record_mock.await_args.kwargs
        self.assertEqual(ledger_kwargs["estimated_cost_usd"], 1.25)
        self.assertEqual(ledger_kwargs["provider"], "openai")
        self.assertEqual(ledger_kwargs["model"], "gpt-4.1")
        self.assertEqual(ledger_kwargs["metadata"]["usage_record_id"], "uacct_run-usage-accounting")
        self.assertEqual(ledger_kwargs["metadata"]["platform_credit_enforced"], True)
        self.assertEqual(ledger_kwargs["metadata"]["credit_item_type"], "ai_pro_tokens")
        self.assertEqual(ledger_kwargs["metadata"]["credit_quantity"], 2000.0)

    async def test_platform_paid_studio_usage_fail_closes_without_cap_when_pricing_is_unknown(self) -> None:
        deployed_agent = _deployed_agent()

        with (
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry",
                new=AsyncMock(return_value={"id": "dcost_1"}),
            ) as record_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.update_deployed_agent",
                new=AsyncMock(return_value=deployed_agent),
            ) as update_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.set_deployed_agent_state",
                new=AsyncMock(return_value={**deployed_agent, "deployment_state": "suspended"}),
            ) as pause_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.outbox_service.emit_notification_event",
            ),
        ):
            result = await deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap(
                run_id="run-platform-unknown-pricing",
                run=_run(
                    usage_masked={
                        "provider": "openai",
                        "model": "unknown-frontier-model",
                        "prompt_tokens": 1000,
                        "completion_tokens": 1000,
                    }
                ),
                status="completed",
                now_iso="2026-04-13T12:00:00Z",
            )

        self.assertTrue(result["fail_closed"])
        pause_mock.assert_awaited_once()
        record_mock.assert_not_awaited()
        budget_cycle = update_mock.await_args.kwargs["updates"]["operational_state"]["current_budget_cycle"]
        self.assertEqual(budget_cycle["accounting_state"], "suspended_accounting_error")

    async def test_settle_skips_duplicate_80_percent_notification_with_same_cap(self) -> None:
        deployed_agent = _deployed_agent(
            metadata={
                "monthly_cost_cap_usd": 10.0,
                "current_budget_cycle": {
                    "usage_month": "2026-04-01",
                    "threshold_80_notified_at": "2026-04-10T00:00:00Z",
                    "threshold_80_cap_usd": 10.0,
                },
            }
        )

        with (
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry",
                new=AsyncMock(return_value={"id": "dcost_1"}),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.summarize_deployed_agent_monthly_cost_ledger",
                new=AsyncMock(
                    return_value={
                        "usage_month": "2026-04-01",
                        "runs_count": 2,
                        "total_cost_usd": 9.0,
                        "last_run_id": "run-2",
                        "last_run_completed_at": "2026-04-13T13:00:00Z",
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.update_deployed_agent",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.outbox_service.emit_notification_event",
            ) as notification_mock,
        ):
            result = await deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap(
                run_id="run-2",
                run=_run(
                    usage_masked={
                        "provider": "openai",
                        "model": "gpt-4.1",
                        "prompt_tokens": 1000,
                        "completion_tokens": 1000,
                        "estimated_cost_usd": 1.0,
                        "pricing_known": True,
                    }
                ),
                status="completed",
                now_iso="2026-04-13T13:00:00Z",
            )

        self.assertTrue(result["applied"])
        self.assertEqual(result["last_threshold_reached"], "80")
        notification_mock.assert_not_called()

    async def test_settle_emits_100_percent_notification_and_auto_pauses(self) -> None:
        deployed_agent = _deployed_agent(
            metadata={
                "monthly_cost_cap_usd": 10.0,
                "current_budget_cycle": {
                    "usage_month": "2026-04-01",
                    "threshold_80_notified_at": "2026-04-10T00:00:00Z",
                    "threshold_80_cap_usd": 10.0,
                },
            }
        )

        with (
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry",
                new=AsyncMock(return_value={"id": "dcost_1"}),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.summarize_deployed_agent_monthly_cost_ledger",
                new=AsyncMock(
                    return_value={
                        "usage_month": "2026-04-01",
                        "runs_count": 3,
                        "total_cost_usd": 10.25,
                        "last_run_id": "run-3",
                        "last_run_completed_at": "2026-04-13T14:00:00Z",
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.update_deployed_agent",
                new=AsyncMock(return_value=deployed_agent),
            ) as update_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.set_deployed_agent_state",
                new=AsyncMock(return_value={**deployed_agent, "deployment_state": "suspended"}),
            ) as pause_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.outbox_service.emit_notification_event",
            ) as notification_mock,
        ):
            result = await deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap(
                run_id="run-3",
                run=_run(
                    usage_masked={
                        "provider": "openai",
                        "model": "gpt-4.1",
                        "prompt_tokens": 1000,
                        "completion_tokens": 1000,
                        "estimated_cost_usd": 2.25,
                        "pricing_known": True,
                    }
                ),
                status="completed",
                now_iso="2026-04-13T14:00:00Z",
            )

        self.assertTrue(result["applied"])
        self.assertTrue(result["auto_paused"])
        self.assertTrue(result["auto_suspended"])
        pause_mock.assert_awaited_once()
        actions = [call.kwargs["action"] for call in notification_mock.call_args_list]
        self.assertEqual(actions, ["deployed_agent_budget_100"])
        budget_cycle = update_mock.await_args.kwargs["updates"]["operational_state"]["current_budget_cycle"]
        self.assertEqual(budget_cycle["last_threshold_reached"], "100")
        self.assertEqual(budget_cycle["auto_paused_at"], "2026-04-13T14:00:00Z")

    async def test_settle_fail_closes_when_cap_is_configured_but_usage_cannot_be_priced(self) -> None:
        deployed_agent = _deployed_agent(metadata={"monthly_cost_cap_usd": 10.0})

        with (
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.update_deployed_agent",
                new=AsyncMock(return_value=deployed_agent),
            ) as update_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.set_deployed_agent_state",
                new=AsyncMock(return_value={**deployed_agent, "deployment_state": "suspended"}),
            ) as pause_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.outbox_service.emit_notification_event",
            ) as notification_mock,
        ):
            result = await deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap(
                run_id="run-4",
                run=_run(
                    usage_masked={
                        "provider": "codex_cli",
                        "model": "gpt-5.4",
                        "prompt_tokens": 1000,
                        "completion_tokens": 1000,
                    }
                ),
                status="completed",
                now_iso="2026-04-13T15:00:00Z",
            )

        self.assertTrue(result["applied"])
        self.assertTrue(result["fail_closed"])
        self.assertTrue(result["auto_suspended"])
        pause_mock.assert_awaited_once()
        self.assertEqual(notification_mock.call_args.kwargs["action"], "deployed_agent_budget_tracking_failed")
        budget_cycle = update_mock.await_args.kwargs["updates"]["operational_state"]["current_budget_cycle"]
        self.assertEqual(budget_cycle["accounting_state"], "suspended_accounting_error")

    async def test_settle_falls_back_to_workspace_tenant_when_run_context_uses_default(self) -> None:
        deployed_agent = _deployed_agent(metadata={"monthly_cost_cap_usd": 10.0})
        run = _run(
            usage_masked={
                "provider": "openai",
                "model": "gpt-4.1",
                "prompt_tokens": 1000,
                "completion_tokens": 1000,
                "estimated_cost_usd": 2.0,
                "pricing_known": True,
            }
        )
        run["context"]["tenant_id"] = "default"

        with (
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_workspace_by_id",
                new=AsyncMock(return_value={"workspace_id": "ws-1", "tenant_id": "tenant-1"}),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.get_deployed_agent_by_id",
                new=AsyncMock(side_effect=[None, deployed_agent]),
            ) as get_deployed_agent_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.record_deployed_agent_monthly_cost_ledger_entry",
                new=AsyncMock(return_value={"id": "dcost_1"}),
            ) as record_mock,
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.summarize_deployed_agent_monthly_cost_ledger",
                new=AsyncMock(
                    return_value={
                        "usage_month": "2026-04-01",
                        "runs_count": 1,
                        "total_cost_usd": 2.0,
                        "last_run_id": "run-tenant-fallback",
                        "last_run_completed_at": "2026-04-13T15:30:00Z",
                    }
                ),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.control_plane_repository.update_deployed_agent",
                new=AsyncMock(return_value=deployed_agent),
            ),
            patch(
                "server_modules.deployed_agent_cost_cap_service.outbox_service.emit_notification_event",
            ),
        ):
            result = await deployed_agent_cost_cap_service.settle_deployed_agent_monthly_cost_cap(
                run_id="run-tenant-fallback",
                run=run,
                status="completed",
                now_iso="2026-04-13T15:30:00Z",
            )

        self.assertTrue(result["applied"])
        self.assertEqual(get_deployed_agent_mock.await_count, 2)
        self.assertEqual(record_mock.await_args.kwargs["tenant_id"], "tenant-1")


if __name__ == "__main__":
    unittest.main()
