from __future__ import annotations

import logging
from typing import Any, Dict, List

import pytest

from server_modules import activity_ledger_service
from server_modules import channel_execution_service
from server_modules import control_plane_repository
from server_modules import deployed_agent_service
from server_modules import run_state_repository
from server_modules import studio_proof_agent_seed_service
from server_modules import usage_accounting_service
from server_modules.channel_routing_models import ChannelRoutingContext
from server_modules.channel_turn_request_service import build_routing_context, bind_deployed_agent_source_to_context


class _QuotaSnapshot:
    max_runtime_seconds = 30


class _QuotaSlot:
    async def __aenter__(self) -> Dict[str, Any]:
        return {"quota_snapshot": _QuotaSnapshot()}

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _context(
    *,
    deployed_agent: Dict[str, Any] | None = None,
    connector_id: str | None = "gsheet-products-1",
    message: str = "Do you have Nike Air Max 90?",
) -> ChannelRoutingContext:
    return ChannelRoutingContext(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        channel_key="telegram",
        endpoint_key="@shop_bot",
        actor_id="customer-1",
        actor_display_name="Customer",
        session_key="session-1",
        thread_id="thread-1",
        message=message,
        owner_type="specialist",
        install={"id": "install-1", "owner_user_id": "owner-1"},
        manifest=None,
        responder_install_id="install-1",
        responder_label="Shop Assistant",
        runtime_mode="hosted_secure",
        runtime_profile_id=None,
        master_install_id="install-1",
        shared_metadata={"request_id": "req-1"},
        turn_request={"message": message},
        execution_owner={
            "user_id": "owner-1",
            "role": "owner",
            "is_admin": True,
            "workspace_roles": {"ws-1": "owner"},
            "workspace_access": {
                "ws-1": {
                    "workspace_id": "ws-1",
                    "tenant_id": "tenant-1",
                    "role": "owner",
                    "tenant_role": "owner",
                }
            },
        },
        connector_id=connector_id,
        deployed_agent=deployed_agent,
        deployed_agent_id="dagent-shop" if deployed_agent else None,
        deployed_agent_state="live" if deployed_agent else None,
    )


async def _wait_for(coro, *, timeout: int):
    return await coro


async def _generic_turn(**kwargs):
    return {"status": "completed", "reply": "generic response"}


@pytest.mark.anyio
async def test_shop_assistant_channel_turn_uses_revenue_evaluator_with_connector(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        channel_execution_service.channel_execution_quota_adapter,
        "channel_execution_slot",
        lambda **kwargs: _QuotaSlot(),
    )

    calls: list[Dict[str, Any]] = []

    async def fake_evaluate(**kwargs):
        calls.append(dict(kwargs))
        return {
            "status": "answered",
            "intent": "catalog_lookup",
            "answer": "Nike Air Max 90 is 8 in stock.",
            "approval": {"required": False},
            "activity_events": [{"event": "studio.proof.shop_assistant.inventory_hit"}],
        }

    monkeypatch.setattr(
        deployed_agent_service,
        "evaluate_deployed_shop_assistant_customer_question",
        fake_evaluate,
    )

    result = await channel_execution_service.execute_prepared_channel_turn(
        context=_context(deployed_agent={"template_slug": "shop-assistant"}),
        execute_turn=_generic_turn,
        wait_for=_wait_for,
    )

    assert result.status == "answered"
    assert result.reply == "Nike Air Max 90 is 8 in stock."
    assert result.metadata["response_class"] == "shop_assistant"
    assert result.payload["intent"] == "catalog_lookup"
    assert len(calls) == 1
    assert calls[0]["deployed_agent_id"] == "dagent-shop"
    assert calls[0]["current_user"]["user_id"] == "owner-1"
    assert calls[0]["current_user"]["workspace_access"]["ws-1"]["tenant_id"] == "tenant-1"
    assert calls[0]["owner_workspace_id"] == "ws-1"
    assert calls[0]["customer_message"] == "Do you have Nike Air Max 90?"
    assert calls[0]["connector_id"] == "gsheet-products-1"


@pytest.mark.anyio
async def test_non_shop_channel_turn_uses_generic_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        channel_execution_service.channel_execution_quota_adapter,
        "channel_execution_slot",
        lambda **kwargs: _QuotaSlot(),
    )

    async def fail_if_called(**kwargs):
        raise AssertionError("shop evaluator should not run for non-shop agents")

    monkeypatch.setattr(
        deployed_agent_service,
        "evaluate_deployed_shop_assistant_customer_question",
        fail_if_called,
    )

    result = await channel_execution_service.execute_prepared_channel_turn(
        context=_context(deployed_agent={"template_slug": "support-faq"}),
        execute_turn=_generic_turn,
        wait_for=_wait_for,
    )

    assert result.status == "completed"
    assert result.reply == "generic response"


@pytest.mark.anyio
async def test_shop_evaluator_failure_logs_and_falls_back_to_generic(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(
        channel_execution_service.channel_execution_quota_adapter,
        "channel_execution_slot",
        lambda **kwargs: _QuotaSlot(),
    )

    async def fake_evaluate(**kwargs):
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(
        deployed_agent_service,
        "evaluate_deployed_shop_assistant_customer_question",
        fake_evaluate,
    )

    with caplog.at_level(logging.ERROR):
        result = await channel_execution_service.execute_prepared_channel_turn(
            context=_context(deployed_agent={"template_slug": "shop-assistant"}),
            execute_turn=_generic_turn,
            wait_for=_wait_for,
        )

    assert result.status == "completed"
    assert result.reply == "generic response"
    assert "Shop assistant channel evaluation failed" in caplog.text


def test_build_routing_context_extracts_connector_id_from_metadata() -> None:
    context = build_routing_context(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        channel_key="telegram",
        endpoint_key="@shop_bot",
        customer_message="Do you have Nike Air Max 90?",
        install={"id": "install-1", "owner_user_id": "owner-1"},
        metadata={"connector_id": "gsheet-products-1"},
        validate_preflight=False,
    )

    assert context.connector_id == "gsheet-products-1"


def test_deployed_agent_channel_source_uses_stored_record_over_request_metadata() -> None:
    context = build_routing_context(
        tenant_id="tenant-1",
        workspace_id="ws-1",
        channel_key="telegram",
        endpoint_key="@shop_bot",
        customer_message="Do you have Nike Air Max 90?",
        install={
            "id": "install-1",
            "owner_user_id": "owner-1",
            "metadata": {
                "source": "deployed_agent",
                "provider": "openai",
                "model": "gpt-4.1",
            },
        },
        metadata={
            "provider": "openrouter",
            "model": "malicious/model",
            "payer": "BYOK",
        },
        validate_preflight=False,
    )
    context.deployed_agent_id = "dagent-1"
    context.deployed_agent_state = "live"

    bound = bind_deployed_agent_source_to_context(
        context,
        deployed_agent={
            "id": "dagent-1",
            "deployment_state": "live",
            "runtime_target": "cloud",
            "metadata": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "runtime_supply": {
                    "ai_source": {
                        "kind": "empyralis_credits",
                        "payer": "platform_credits",
                    }
                },
            },
        },
        request_id="req-1",
    )

    assert bound.shared_metadata["provider"] == "deepseek"
    assert bound.shared_metadata["model"] == "deepseek-v4-flash"
    assert bound.shared_metadata["payer"] == "platform_credits"
    assert bound.shared_metadata["ai_source_authority"] == "deployed_agent_record"
    assert bound.turn_request.context_hints["provider"] == "deepseek"
    assert bound.turn_request.context_hints["model"] == "deepseek-v4-flash"
    assert bound.turn_request.context_hints["metadata"]["ai_source"]["kind"] == "empyralis_credits"


@pytest.mark.anyio
async def test_channel_end_to_end_catalog_answer_persists_activity_and_billing(monkeypatch: pytest.MonkeyPatch):
    """Full pipeline: Telegram inbound → Shop Assistant → catalog lookup → activity → billing ledger."""
    monkeypatch.setattr(
        channel_execution_service.channel_execution_quota_adapter,
        "channel_execution_slot",
        lambda **kwargs: _QuotaSlot(),
    )

    demo_rows = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")["revenue_proof"]["demo_catalog_rows"]

    async def fake_get_workspace(workspace_id):
        return {"tenant_id": "tenant-1", "workspace_id": "ws-1"}

    async def fake_get_deployed_agent(deployed_agent_id, tenant_id, owner_workspace_id):
        return {
            "id": "dagent-shop",
            "tenant_id": "tenant-1",
            "owner_workspace_id": "ws-1",
            "template_slug": "shop-assistant",
            "metadata": {
                "public_tier": "pro",
                "effective_provider": "openai",
                "effective_model": "gpt-4o",
                "live_data_connectors": {
                    "product_catalog": {
                        "source_id": "demo-catalog",
                        "connector_id": "gsheet-products-1",
                        "connector_type": "demo_catalog",
                        "workspace_id": "ws-1",
                        "deployed_agent_id": "dagent-shop",
                        "rows": demo_rows,
                    }
                },
            },
        }

    captured_activity: List[Dict[str, Any]] = []
    captured_ledger: List[Dict[str, Any]] = []

    async def fake_append_activity_event(**kwargs):
        captured_activity.append(dict(kwargs))

    async def fake_record_ledger(**kwargs):
        captured_ledger.append(dict(kwargs))

    def fake_build_usage_record(*, run_id, tenant_id, workspace_id, deployed_agent_id, source_surface, effective_provider, effective_model, input_tokens, output_tokens, total_tokens, allow_provider_fallback):
        return {
            "provider": effective_provider,
            "model": effective_model,
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": 0.0045,
            "pricing_known": True,
            "pricing_source": "pricing_registry",
        }

    monkeypatch.setattr(control_plane_repository, "get_workspace_by_id", fake_get_workspace)
    monkeypatch.setattr(control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent)
    monkeypatch.setattr(activity_ledger_service, "append_activity_event", fake_append_activity_event)
    monkeypatch.setattr(control_plane_repository, "record_deployed_agent_monthly_cost_ledger_entry", fake_record_ledger)
    monkeypatch.setattr(usage_accounting_service, "build_usage_accounting_record", fake_build_usage_record)

    result = await channel_execution_service.execute_prepared_channel_turn(
        context=_context(
            deployed_agent={
                "template_slug": "shop-assistant",
                "metadata": {
                    "live_data_connectors": {
                        "product_catalog": {
                            "source_id": "demo-catalog",
                            "connector_type": "demo_catalog",
                            "rows": demo_rows,
                        }
                    }
                },
            }
        ),
        execute_turn=_generic_turn,
        wait_for=_wait_for,
    )

    assert result.status == "answered"
    assert "Nike Air Max 90" in result.reply
    assert result.metadata["response_class"] == "shop_assistant"

    # Activity events persisted
    activity_classes = {evt.get("event_class") for evt in captured_activity}
    assert "studio.proof.shop_assistant.inventory_hit" in activity_classes
    assert "studio.proof.shop_assistant.credit_consumed" in activity_classes

    # Billing ledger persisted
    assert len(captured_ledger) >= 1
    ai_entries = [e for e in captured_ledger if e.get("metadata", {}).get("credit_item_type", "").startswith("ai_")]
    assert len(ai_entries) >= 1
    assert ai_entries[0]["estimated_cost_usd"] == 0.0045
    assert ai_entries[0]["metadata"]["source_surface"] == "shop_assistant"


@pytest.mark.anyio
async def test_channel_end_to_end_discount_approval_creates_durable_approval(monkeypatch: pytest.MonkeyPatch):
    """Full pipeline: Telegram inbound → Shop Assistant → discount gate → durable approval + activity + billing."""
    monkeypatch.setattr(
        channel_execution_service.channel_execution_quota_adapter,
        "channel_execution_slot",
        lambda **kwargs: _QuotaSlot(),
    )

    demo_rows = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")["revenue_proof"]["demo_catalog_rows"]

    async def fake_get_workspace(workspace_id):
        return {"tenant_id": "tenant-1", "workspace_id": "ws-1"}

    async def fake_get_deployed_agent(deployed_agent_id, tenant_id, owner_workspace_id):
        return {
            "id": "dagent-shop",
            "tenant_id": "tenant-1",
            "owner_workspace_id": "ws-1",
            "template_slug": "shop-assistant",
            "metadata": {
                "public_tier": "pro",
                "effective_provider": "openai",
                "effective_model": "gpt-4o",
                "live_data_connectors": {
                    "product_catalog": {
                        "source_id": "demo-catalog",
                        "connector_id": "gsheet-products-1",
                        "connector_type": "demo_catalog",
                        "workspace_id": "ws-1",
                        "deployed_agent_id": "dagent-shop",
                        "rows": demo_rows,
                    }
                },
            },
        }

    captured_approvals: List[Dict[str, Any]] = []
    captured_activity: List[Dict[str, Any]] = []
    captured_ledger: List[Dict[str, Any]] = []

    async def fake_append_activity_event(**kwargs):
        captured_activity.append(dict(kwargs))

    async def fake_record_ledger(**kwargs):
        captured_ledger.append(dict(kwargs))

    def fake_create_approval(run_id, approval_id, request_payload, actor, trace_id, *, metadata=None, expires_at=None):
        captured_approvals.append({
            "run_id": run_id,
            "approval_id": approval_id,
            "request_payload": request_payload,
            "metadata": metadata,
        })
        return {"approval_id": approval_id}

    def fake_build_usage_record(*, run_id, tenant_id, workspace_id, deployed_agent_id, source_surface, effective_provider, effective_model, input_tokens, output_tokens, total_tokens, allow_provider_fallback):
        return {
            "provider": effective_provider,
            "model": effective_model,
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": 0.0045,
            "pricing_known": True,
            "pricing_source": "pricing_registry",
        }

    monkeypatch.setattr(control_plane_repository, "get_workspace_by_id", fake_get_workspace)
    monkeypatch.setattr(control_plane_repository, "get_deployed_agent_by_id", fake_get_deployed_agent)
    monkeypatch.setattr(activity_ledger_service, "append_activity_event", fake_append_activity_event)
    monkeypatch.setattr(control_plane_repository, "record_deployed_agent_monthly_cost_ledger_entry", fake_record_ledger)
    monkeypatch.setattr(run_state_repository, "sync_create_or_update_approval_request", fake_create_approval)
    monkeypatch.setattr(usage_accounting_service, "build_usage_accounting_record", fake_build_usage_record)

    result = await channel_execution_service.execute_prepared_channel_turn(
        context=_context(
            deployed_agent={
                "template_slug": "shop-assistant",
                "metadata": {
                    "live_data_connectors": {
                        "product_catalog": {
                            "source_id": "demo-catalog",
                            "connector_type": "demo_catalog",
                            "rows": demo_rows,
                        }
                    }
                },
            },
            message="Can I get a discount on Nike Air Max?",
        ),
        execute_turn=_generic_turn,
        wait_for=_wait_for,
    )

    assert result.status == "completed"
    assert result.metadata["approval_required"] is True
    assert result.metadata["approval_gate"] == "discount"

    # Durable approval created
    assert len(captured_approvals) == 1
    assert captured_approvals[0]["metadata"]["source_type"] == "deployed_agent"
    assert captured_approvals[0]["metadata"]["gate"] == "discount"

    # Activity events persisted
    activity_classes = {evt.get("event_class") for evt in captured_activity}
    assert "studio.proof.shop_assistant.owner_approval_required" in activity_classes
    assert "studio.proof.shop_assistant.credit_consumed" in activity_classes

    # Billing ledger persisted
    ai_entries = [e for e in captured_ledger if e.get("metadata", {}).get("credit_item_type", "").startswith("ai_")]
    assert len(ai_entries) >= 1
