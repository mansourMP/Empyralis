from __future__ import annotations

import pytest

from server_modules import agent_action_metering_service


@pytest.mark.asyncio
async def test_metered_tool_action_writes_action_and_credit_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    async def fake_action_event(**kwargs):
        calls["action"] = kwargs
        return {"id": "aact-1", **kwargs["event"]}

    async def fake_credit_event(**kwargs):
        calls["credit"] = kwargs
        return {"id": "cled-1"}

    async def fake_activity_event(**kwargs):
        calls["activity"] = kwargs
        return {"id": "act-1"}

    monkeypatch.setattr(agent_action_metering_service.control_plane_repository, "record_agent_action_event", fake_action_event)
    monkeypatch.setattr(agent_action_metering_service.control_plane_repository, "record_credit_ledger_event", fake_credit_event)
    monkeypatch.setattr(agent_action_metering_service.activity_ledger_service, "append_activity_event", fake_activity_event)

    row = await agent_action_metering_service.record_completed(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        surface="sage",
        source_surface="sage_direct_tool",
        action_domain="tool",
        action_type="execute",
        action_name="browser.click",
        tool_kind="direct_tool",
        tool_id="browser__click",
        payer="platform_credits",
        billing_mode="metered",
        credit_type="tool_execution",
        credits_debited=1,
        platform_cost_usd=0.0001,
        run_id="run-1",
        thread_id="thread-1",
        input_summary="click submit",
        output_summary="clicked",
        source_table="direct_tool_calls",
        source_event_id="run-1:browser-click",
    )

    assert row["status"] == "completed"
    assert calls["action"]["event"]["action_domain"] == "tool"
    assert calls["credit"]["source_table"] == "agent_action_events"
    assert calls["credit"]["event"]["credit_type"] == "tool_execution"
    assert calls["credit"]["event"]["credits_debited"] == 1.0
    assert calls["activity"]["payload"]["agent_action_event_id"] == "aact-1"


@pytest.mark.asyncio
async def test_agent_action_event_rejects_ai_token_debit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_action_event(**kwargs):  # pragma: no cover - should not be called
        raise AssertionError("action event should not be persisted")

    monkeypatch.setattr(agent_action_metering_service.control_plane_repository, "record_agent_action_event", fake_action_event)

    with pytest.raises(ValueError, match="ai_tokens"):
        await agent_action_metering_service.record_completed(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            surface="sage",
            source_surface="sage_direct_tool",
            action_domain="tool",
            action_type="execute",
            action_name="shell.execute",
            payer="platform_credits",
            billing_mode="metered",
            credit_type="ai_tokens",
        )


@pytest.mark.asyncio
async def test_ai_usage_reference_does_not_write_second_credit_row(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, int] = {"credit": 0}

    async def fake_action_event(**kwargs):
        return {"id": "aact-usage-ref", **kwargs["event"]}

    async def fake_credit_event(**kwargs):
        calls["credit"] += 1
        return {"id": "cled-1"}

    async def fake_activity_event(**kwargs):
        return {"id": "act-1"}

    monkeypatch.setattr(agent_action_metering_service.control_plane_repository, "record_agent_action_event", fake_action_event)
    monkeypatch.setattr(agent_action_metering_service.control_plane_repository, "record_credit_ledger_event", fake_credit_event)
    monkeypatch.setattr(agent_action_metering_service.activity_ledger_service, "append_activity_event", fake_activity_event)

    row = await agent_action_metering_service.record_completed(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        surface="sage",
        source_surface="sage_direct_chat",
        action_domain="tool",
        action_type="invoke",
        action_name="model_tool_call",
        payer="platform_credits",
        billing_mode="ai_token_usage_ref",
        credit_type="ai_tokens",
        usage_ref={"credit_ledger_event_id": "cled-ai-1"},
    )

    assert row["billing_mode"] == "ai_token_usage_ref"
    assert calls["credit"] == 0


@pytest.mark.asyncio
async def test_agent_action_summaries_redact_unknown_high_entropy_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}
    token = "Rt9_Zx7Lm5Qp3Nc8Yv2Ha6Ks1Wd4Fg"

    async def fake_action_event(**kwargs):
        calls["action"] = kwargs
        return {"id": "aact-redacted", **kwargs["event"]}

    async def fake_activity_event(**kwargs):
        calls["activity"] = kwargs
        return {"id": "act-redacted"}

    monkeypatch.setattr(agent_action_metering_service.control_plane_repository, "record_agent_action_event", fake_action_event)
    monkeypatch.setattr(agent_action_metering_service.activity_ledger_service, "append_activity_event", fake_activity_event)

    row = await agent_action_metering_service.record_completed(
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        surface="sage",
        source_surface="sage_direct_tool",
        action_domain="tool",
        action_type="execute",
        action_name="sap.lookup",
        billing_mode="transparency",
        input_summary=f"lookup with {token}",
        output_summary=f"result contained {token}",
        source_table="direct_tool_calls",
        source_event_id="run-1:sap-lookup",
    )

    event = calls["action"]["event"]
    assert row["status"] == "completed"
    assert token not in event["input_summary"]
    assert token not in event["output_summary"]
    assert "[redacted-secret]" in event["input_summary"]
    assert "[redacted-secret]" in event["output_summary"]
