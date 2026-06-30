import importlib
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import direct_chat_hosted_usage_service


class DirectChatHostedUsageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        global direct_chat_hosted_usage_service
        direct_chat_hosted_usage_service = importlib.import_module(
            "server_modules.direct_chat_hosted_usage_service"
        )

    def test_persist_direct_chat_hosted_usage_best_effort_records_platform_runtime_usage(self) -> None:
        with patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry",
            new=AsyncMock(return_value={"id": "shost_1"}),
        ) as record_ledger, patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_credit_ledger_event",
            new=AsyncMock(return_value={"id": "cled_1"}),
        ) as record_credit_ledger, patch(
            "server_modules.billing_service.debit_workspace_credit_balance_for_hosted_usage",
            return_value={"ok": True, "debited_usd": 0.0},
        ) as debit_credits:
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                availability_payload={
                    "credential_plane": "platform_runtime",
                    "platform_runtime_allowed": True,
                },
                usage_masked={
                    "usage_accounting": {
                        "input_tokens": 10,
                        "output_tokens": 6,
                        "total_tokens": 16,
                        "estimated_cost_usd": 0.0011,
                        "effective_provider": "deepseek",
                        "effective_model": "deepseek-chat",
                    }
                },
                requested_provider="deepseek",
                effective_provider="deepseek",
                requested_model="deepseek-chat",
                effective_model="deepseek-chat",
            )

        record_ledger.assert_awaited_once()
        record_credit_ledger.assert_awaited_once()
        debit_credits.assert_called_once_with(
            workspace_id="ws-1",
            tenant_id="tenant-1",
            request_id="req-1",
        )
        kwargs = record_ledger.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-1")
        self.assertEqual(kwargs["workspace_id"], "ws-1")
        self.assertEqual(kwargs["request_id"], "req-1")
        self.assertEqual(kwargs["thread_id"], "thread-1")
        self.assertEqual(kwargs["provider"], "deepseek")
        self.assertEqual(kwargs["model"], "deepseek-chat")
        self.assertEqual(kwargs["prompt_tokens"], 10)
        self.assertEqual(kwargs["completion_tokens"], 6)
        self.assertEqual(kwargs["total_tokens"], 16)
        self.assertEqual(kwargs["metadata"]["credit_type"], "ai_tokens")
        self.assertEqual(kwargs["metadata"]["credit_item_type"], "ai_pro_tokens")
        self.assertEqual(kwargs["metadata"]["credit_quantity"], 16.0)
        self.assertEqual(kwargs["metadata"]["credit_quantity_unit"], "tokens")
        self.assertEqual(kwargs["metadata"]["billing_source"], "empyralis_credits")
        unified = kwargs["metadata"]["unified_credit_ledger_event"]
        self.assertEqual(unified["surface"], "sage")
        self.assertEqual(unified["source_surface"], "sage_direct_chat")
        self.assertEqual(unified["payer"], "platform_credits")
        self.assertEqual(unified["credit_type"], "ai_tokens")
        self.assertEqual(unified["provider"], "deepseek")
        self.assertEqual(unified["model"], "deepseek-chat")
        self.assertEqual(unified["run_id"], "req-1")
        self.assertEqual(unified["credits_debited"], 22.0)
        credit_kwargs = record_credit_ledger.await_args.kwargs
        self.assertEqual(credit_kwargs["source_table"], "workspace_hosted_ai_monthly_cost_ledger")
        self.assertEqual(credit_kwargs["source_event_id"], "shost_1")

    def test_persist_direct_chat_hosted_usage_best_effort_records_byok_transparency_row(self) -> None:
        with patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry",
            new=AsyncMock(return_value={"id": "shost_1"}),
        ) as record_ledger, patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_credit_ledger_event",
            new=AsyncMock(return_value={"id": "cled_byok"}),
        ) as record_credit_ledger:
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                availability_payload={
                    "credential_plane": "workspace_connection",
                    "platform_runtime_allowed": True,
                },
                usage_masked={
                    "usage_accounting": {
                        "input_tokens": 10,
                        "output_tokens": 6,
                        "total_tokens": 16,
                        "estimated_cost_usd": 0.0011,
                        "effective_provider": "openai",
                        "effective_model": "gpt-4o",
                    }
                },
                requested_provider="openai",
                effective_provider="openai",
                requested_model="gpt-4o",
                effective_model="gpt-4o",
            )

        record_ledger.assert_not_awaited()
        record_credit_ledger.assert_awaited_once()
        kwargs = record_credit_ledger.await_args.kwargs
        event = kwargs["event"]
        self.assertEqual(kwargs["source_table"], "direct_chat_transparency")
        self.assertEqual(kwargs["source_event_id"], "req-1")
        self.assertEqual(event["surface"], "sage")
        self.assertEqual(event["payer"], "BYOK")
        self.assertEqual(event["credits_debited"], 0.0)
        self.assertEqual(event["provider"], "openai")
        self.assertEqual(event["model"], "gpt-4o")

    def test_persist_direct_chat_hosted_usage_best_effort_swallows_byok_transparency_failure(self) -> None:
        with patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_credit_ledger_event",
            new=AsyncMock(return_value=None),
        ):
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                availability_payload={
                    "credential_plane": "workspace_connection",
                    "platform_runtime_allowed": True,
                },
                usage_masked={
                    "usage_accounting": {
                        "input_tokens": 10,
                        "output_tokens": 6,
                        "total_tokens": 16,
                        "estimated_cost_usd": 0.0011,
                        "effective_provider": "openai",
                        "effective_model": "gpt-4o",
                    }
                },
                requested_provider="openai",
                effective_provider="openai",
                requested_model="gpt-4o",
                effective_model="gpt-4o",
            )

    def test_persist_direct_chat_hosted_usage_best_effort_records_subscription_transparency_row(self) -> None:
        with patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_credit_ledger_event",
            new=AsyncMock(return_value={"id": "cled_subscription"}),
        ) as record_credit_ledger:
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-cli-1"},
                availability_payload={
                    "credential_plane": "local_subscription",
                    "billing_source": "subscription_passthrough",
                },
                usage_masked={
                    "provider": "codex_cli",
                    "model": "gpt-5.4",
                    "total_tokens": 0,
                },
                requested_provider="codex_cli",
                effective_provider="codex_cli",
                requested_model="gpt-5.4",
                effective_model="gpt-5.4",
            )

        event = record_credit_ledger.await_args.kwargs["event"]
        self.assertEqual(event["payer"], "subscription_passthrough")
        self.assertEqual(event["runtime_target"], "local_companion")
        self.assertEqual(event["credits_debited"], 0.0)

    def test_persist_direct_chat_hosted_usage_best_effort_fails_closed_on_unknown_pricing(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unknown pricing"):
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                availability_payload={
                    "credential_plane": "platform_runtime",
                    "platform_runtime_allowed": True,
                },
                usage_masked={
                    "usage_accounting": {
                        "input_tokens": 10,
                        "output_tokens": 6,
                        "total_tokens": 16,
                        "effective_provider": "openai",
                        "effective_model": "unknown-frontier-model",
                    }
                },
                requested_provider="openai",
                effective_provider="openai",
                requested_model="unknown-frontier-model",
                effective_model="unknown-frontier-model",
            )

    def test_persist_direct_chat_hosted_usage_best_effort_fails_closed_on_missing_usage(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "usage is missing"):
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                availability_payload={
                    "credential_plane": "platform_runtime",
                    "platform_runtime_allowed": True,
                },
                usage_masked=None,
                requested_provider="deepseek",
                effective_provider="deepseek",
                requested_model="deepseek-chat",
                effective_model="deepseek-chat",
            )

    def test_persist_direct_chat_hosted_usage_best_effort_fails_closed_on_missing_availability(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "known AI source"):
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                availability_payload=None,
                usage_masked={
                    "usage_accounting": {
                        "input_tokens": 10,
                        "output_tokens": 6,
                        "total_tokens": 16,
                        "estimated_cost_usd": 0.0011,
                        "effective_provider": "openai",
                        "effective_model": "gpt-4o",
                    }
                },
                requested_provider="openai",
                effective_provider="openai",
                requested_model="gpt-4o",
                effective_model="gpt-4o",
            )

    def test_persist_direct_chat_hosted_usage_best_effort_fails_closed_when_platform_runtime_not_allowed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not allowed"):
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="thread-1",
                session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                availability_payload={
                    "credential_plane": "platform_runtime",
                    "platform_runtime_allowed": False,
                },
                usage_masked={
                    "usage_accounting": {
                        "input_tokens": 10,
                        "output_tokens": 6,
                        "total_tokens": 16,
                        "estimated_cost_usd": 0.0011,
                        "effective_provider": "deepseek",
                        "effective_model": "deepseek-chat",
                    }
                },
                requested_provider="deepseek",
                effective_provider="deepseek",
                requested_model="deepseek-chat",
                effective_model="deepseek-chat",
            )

    def test_persist_direct_chat_hosted_usage_best_effort_fails_closed_on_missing_tenant(self) -> None:
        with patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.get_workspace_by_id",
            new=AsyncMock(return_value={}),
        ):
            with self.assertRaisesRegex(RuntimeError, "tenant scope"):
                direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={"request_id": "req-1"},
                    availability_payload={
                        "credential_plane": "platform_runtime",
                        "platform_runtime_allowed": True,
                    },
                    usage_masked={
                        "usage_accounting": {
                            "input_tokens": 10,
                            "output_tokens": 6,
                            "total_tokens": 16,
                            "estimated_cost_usd": 0.0011,
                            "effective_provider": "deepseek",
                            "effective_model": "deepseek-chat",
                        }
                    },
                    requested_provider="deepseek",
                    effective_provider="deepseek",
                    requested_model="deepseek-chat",
                    effective_model="deepseek-chat",
                )

    def test_persist_direct_chat_hosted_usage_best_effort_fails_closed_on_missing_request_id(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "request id"):
            direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                workspace_id="ws-1",
                thread_id="",
                session_ctx={"tenant_id": "tenant-1"},
                availability_payload={
                    "credential_plane": "platform_runtime",
                    "platform_runtime_allowed": True,
                },
                usage_masked={
                    "usage_accounting": {
                        "input_tokens": 10,
                        "output_tokens": 6,
                        "total_tokens": 16,
                        "estimated_cost_usd": 0.0011,
                        "effective_provider": "deepseek",
                        "effective_model": "deepseek-chat",
                    }
                },
                requested_provider="deepseek",
                effective_provider="deepseek",
                requested_model="deepseek-chat",
                effective_model="deepseek-chat",
            )

    def test_persist_direct_chat_hosted_usage_best_effort_surfaces_debit_failure(self) -> None:
        with patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry",
            new=AsyncMock(return_value={"id": "shost_1"}),
        ), patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_credit_ledger_event",
            new=AsyncMock(return_value={"id": "cled_1"}),
        ), patch(
            "server_modules.billing_service.debit_workspace_credit_balance_for_hosted_usage",
            side_effect=RuntimeError("ledger unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "credit debit failed") as ctx:
                direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                    availability_payload={
                        "credential_plane": "platform_runtime",
                        "platform_runtime_allowed": True,
                    },
                    usage_masked={
                        "usage_accounting": {
                            "input_tokens": 10,
                            "output_tokens": 6,
                            "total_tokens": 16,
                            "estimated_cost_usd": 0.0011,
                            "effective_provider": "deepseek",
                            "effective_model": "deepseek-chat",
                        }
                    },
                    requested_provider="deepseek",
                    effective_provider="deepseek",
                    requested_model="deepseek-chat",
                    effective_model="deepseek-chat",
                )
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)
        self.assertIn("ledger unavailable", str(ctx.exception.__cause__))

    def test_persist_direct_chat_hosted_usage_best_effort_surfaces_debit_result_failure(self) -> None:
        with patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry",
            new=AsyncMock(return_value={"id": "shost_1"}),
        ), patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_credit_ledger_event",
            new=AsyncMock(return_value={"id": "cled_1"}),
        ), patch(
            "server_modules.billing_service.debit_workspace_credit_balance_for_hosted_usage",
            return_value={"ok": False, "error": "insufficient_credits"},
        ), patch(
            "server_modules.direct_chat_hosted_usage_service.release_direct_chat_hosted_usage_reservation_best_effort",
        ) as release_reservation:
            with self.assertRaisesRegex(RuntimeError, "insufficient_credits"):
                direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                    availability_payload={
                        "credential_plane": "platform_runtime",
                        "platform_runtime_allowed": True,
                    },
                    usage_masked={
                        "usage_accounting": {
                            "input_tokens": 10,
                            "output_tokens": 6,
                            "total_tokens": 16,
                            "estimated_cost_usd": 0.0011,
                            "effective_provider": "deepseek",
                            "effective_model": "deepseek-chat",
                        }
                    },
                    requested_provider="deepseek",
                    effective_provider="deepseek",
                    requested_model="deepseek-chat",
                    effective_model="deepseek-chat",
                )
        release_reservation.assert_not_called()

    def test_persist_direct_chat_hosted_usage_best_effort_surfaces_ledger_none(self) -> None:
        with patch(
            "server_modules.direct_chat_hosted_usage_service.control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "cost ledger persistence failed"):
                direct_chat_hosted_usage_service.persist_direct_chat_hosted_usage_best_effort(
                    workspace_id="ws-1",
                    thread_id="thread-1",
                    session_ctx={"tenant_id": "tenant-1", "request_id": "req-1"},
                    availability_payload={
                        "credential_plane": "platform_runtime",
                        "platform_runtime_allowed": True,
                    },
                    usage_masked={
                        "usage_accounting": {
                            "input_tokens": 10,
                            "output_tokens": 6,
                            "total_tokens": 16,
                            "estimated_cost_usd": 0.0011,
                            "effective_provider": "deepseek",
                            "effective_model": "deepseek-chat",
                        }
                    },
                    requested_provider="deepseek",
                    effective_provider="deepseek",
                    requested_model="deepseek-chat",
                    effective_model="deepseek-chat",
                )
