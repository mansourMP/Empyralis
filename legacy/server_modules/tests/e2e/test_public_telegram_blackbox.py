from __future__ import annotations

import time
from typing import Any, Dict, Tuple

import pytest

pytestmark = pytest.mark.blackbox_db


def _wait_for_conversation_item(
    blackbox_runtime,
    *,
    owner: Dict[str, Any],
    deployed_agent_id: str,
    timeout_seconds: float = 15.0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    last_payload: Dict[str, Any] = {}
    while time.time() < deadline:
        last_payload = blackbox_runtime.list_conversations(
            owner=owner,
            deployed_agent_id=deployed_agent_id,
        )
        items = last_payload.get("items") or []
        if items:
            return last_payload, items[0]
        time.sleep(0.2)
    assert (last_payload.get("items") or []), last_payload
    items = last_payload.get("items") or []
    return last_payload, items[0]


def _telegram_texts(blackbox_runtime, *, after: int = 0) -> list[str]:
    calls = (
        blackbox_runtime.captured_telegram_calls(method_name="editMessageText")
        + blackbox_runtime.captured_telegram_calls(method_name="sendMessage")
    )
    ordered_calls = sorted(calls, key=lambda call: float(call.get("perf_counter") or 0.0))
    return [
        str((call.get("payload") or {}).get("text") or "")
        for call in ordered_calls[max(0, int(after or 0)) :]
        if isinstance(call, dict)
    ]


def test_studio_text_agent_launch_drill_blackbox(blackbox_runtime) -> None:
    owner = blackbox_runtime.register_owner(email_prefix="studio-launch")
    draft = blackbox_runtime.create_deployed_agent(
        owner=owner,
        name="Launch Drill Agent",
        config_overrides={
            "persona": "Business support assistant",
            "system_prompt": "Answer customers clearly using approved business information.",
        },
    )

    assert draft["deployment_state"] == "draft"
    assert draft["runtime_target"] == "cloud"
    assert draft["provider"] == "openai"
    assert draft["model"] == "gpt-4o-mini"
    config = draft["config"]
    assert config["studio_agent_mode"] == "text_agent"
    assert config["runtime_placement"] == "managed_cloud"
    assert float(config["commerce_policy"]["monthly_cost_cap_usd"]) > 0.0
    assert draft["metadata"]["privacy_contract_snapshot"]["where_it_runs"]["studio_agent_mode"] == "text_agent"

    private_test = blackbox_runtime.test_deployed_agent_turn(
        owner=owner,
        deployed_agent_id=draft["id"],
        message="Can you confirm this private test works?",
    )
    assert private_test["deployed_agent_id"] == draft["id"]
    assert private_test["reply"].startswith("[TEST MODE]")
    assert "Can you confirm this private test works?" in private_test["reply"]
    assert private_test["trace_id"]
    assert any(
        item.get("policy") == "runtime_mode" and item.get("allowed") is True
        for item in private_test["policy_decisions"]
    )

    connector = blackbox_runtime.create_telegram_connector(
        owner=owner,
        deployed_agent=draft,
    )
    bound = blackbox_runtime.bind_telegram_connector(
        owner=owner,
        deployed_agent=draft,
        connector=connector,
    )
    live = blackbox_runtime.deploy_deployed_agent(
        owner=owner,
        deployed_agent_id=bound["id"],
    )
    assert live["deployment_state"] == "live"
    privacy = live["metadata"]["privacy_contract_snapshot"]
    assert privacy["accepted_at"]
    assert privacy["accepted_by_user_id"] == owner["user_id"]

    response = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="Need launch drill help",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "accepted"
    run_id = str(payload.get("run_id") or "").strip()
    assert run_id
    terminal_run = blackbox_runtime.wait_for_run_terminal(run_id)
    assert str(terminal_run.get("status") or "").strip().lower() == "completed"
    delivery_before = len(_telegram_texts(blackbox_runtime))
    blackbox_runtime.pump_outbox_until(
        lambda: any("Need launch drill help" in text for text in _telegram_texts(blackbox_runtime, after=delivery_before)),
        tenant_id=owner["tenant_id"],
        workspace_id=owner["workspace_id"],
        run_id=run_id,
        event_type="channel_run_delivery",
    )
    assert any("Need launch drill help" in text for text in _telegram_texts(blackbox_runtime, after=delivery_before))

    _, conversation = _wait_for_conversation_item(
        blackbox_runtime,
        owner=owner,
        deployed_agent_id=live["id"],
    )
    detail = blackbox_runtime.get_conversation_detail(
        owner=owner,
        deployed_agent_id=live["id"],
        session_id=conversation["session_id"],
    )
    assert detail["channel"] == "telegram"
    assert run_id in detail["run_ids"]
    texts = [str(item.get("text") or "") for item in (detail.get("messages") or [])]
    assert any("Need launch drill help" in text for text in texts)

    memory_rows = blackbox_runtime.list_deployed_agent_memory(
        owner=owner,
        deployed_agent_id=live["id"],
    )
    assert memory_rows
    assert int(memory_rows[0]["source_message_count"]) >= 2
    assert memory_rows[0]["metadata"]["last_user_message"] == "Need launch drill help"

    cost_summary = blackbox_runtime.summarize_deployed_agent_monthly_cost(
        owner=owner,
        deployed_agent_id=live["id"],
    )
    assert int(cost_summary["runs_count"]) >= 1
    assert cost_summary["last_run_id"] == run_id
    assert float(cost_summary["total_cost_usd"]) > 0.0

    paused = blackbox_runtime.pause_deployed_agent(
        owner=owner,
        deployed_agent_id=live["id"],
    )
    assert paused["deployment_state"] == "paused"
    send_before = len(blackbox_runtime.captured_telegram_calls(method_name="sendMessage"))
    blocked = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="This should not run after pause",
    )
    assert blocked.status_code == 200, blocked.text
    blocked_payload = blocked.json()
    assert blocked_payload["action"] == "paused"
    assert not blocked_payload.get("run_id")
    new_send_calls = blackbox_runtime.captured_telegram_calls(method_name="sendMessage")[send_before:]
    assert new_send_calls
    assert "paused" in str(new_send_calls[-1]["payload"].get("text") or "").lower()


def test_public_telegram_deployed_agent_blackbox_flow(blackbox_runtime) -> None:
    owner = blackbox_runtime.register_owner(email_prefix="telegram-public")
    live = blackbox_runtime.create_live_telegram_deployed_agent(owner=owner, name="Brakebox")
    deployed_agent = live["deployed_agent"]
    connector = live["connector"]

    response = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="Need brake pads",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "accepted"
    run_id = str(payload.get("run_id") or "").strip()
    assert run_id

    send_calls = blackbox_runtime.captured_telegram_calls(method_name="sendMessage")
    assert send_calls
    assert str(send_calls[-1]["payload"].get("text") or "").strip() == "Thinking..."

    terminal_run = blackbox_runtime.wait_for_run_terminal(run_id)
    assert str(terminal_run.get("status") or "").strip().lower() == "completed"
    delivery_before = len(_telegram_texts(blackbox_runtime))
    blackbox_runtime.pump_outbox_until(
        lambda: any("Need brake pads" in text for text in _telegram_texts(blackbox_runtime, after=delivery_before)),
        tenant_id=owner["tenant_id"],
        workspace_id=owner["workspace_id"],
        run_id=run_id,
        event_type="channel_run_delivery",
    )

    final_text = "\n".join(_telegram_texts(blackbox_runtime, after=delivery_before))
    assert "Blackbox durable reply:" in final_text
    assert "Need brake pads" in final_text
    assert "Execution Result" in final_text

    _, conversation = _wait_for_conversation_item(
        blackbox_runtime,
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
    )
    assert conversation["channel"] == "telegram"
    assert conversation["latest_run_id"] == run_id

    detail = blackbox_runtime.get_conversation_detail(
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
        session_id=conversation["session_id"],
    )
    texts = [str(item.get("text") or "") for item in (detail.get("messages") or [])]
    assert any("Need brake pads" in text for text in texts)
    assert detail["channel"] == "telegram"
    assert detail["run_ids"]


def test_public_daily_quota_cta_wins_first_visible_denial(blackbox_runtime) -> None:
    owner = blackbox_runtime.register_owner(email_prefix="telegram-quota")
    live = blackbox_runtime.create_live_telegram_deployed_agent(
        owner=owner,
        name="QuotaBot",
        config_overrides={
            "customer_policy": {
                "daily_message_limit": 1,
                "upgrade_cta_label": "Upgrade",
                "upgrade_cta_url": "https://blackbox.example/upgrade",
            },
        },
    )
    deployed_agent = live["deployed_agent"]
    connector = live["connector"]

    first = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="First question",
    )
    assert first.status_code == 200, first.text
    first_run_id = str(first.json().get("run_id") or "").strip()
    assert first_run_id
    blackbox_runtime.wait_for_run_terminal(first_run_id)
    delivery_before = len(_telegram_texts(blackbox_runtime))
    blackbox_runtime.pump_outbox_until(
        lambda: any("First question" in text for text in _telegram_texts(blackbox_runtime, after=delivery_before)),
        tenant_id=owner["tenant_id"],
        workspace_id=owner["workspace_id"],
        run_id=first_run_id,
        event_type="channel_run_delivery",
    )

    _, conversation = _wait_for_conversation_item(
        blackbox_runtime,
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
    )

    send_before = len(blackbox_runtime.captured_telegram_calls(method_name="sendMessage"))
    second = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="Second question should be capped",
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["action"] == "rate_limited"
    assert not second_payload.get("run_id")

    new_send_calls = blackbox_runtime.captured_telegram_calls(method_name="sendMessage")[send_before:]
    assert new_send_calls
    denial_text = str(new_send_calls[-1]["payload"].get("text") or "").strip()
    assert "has reached today's free message limit" in denial_text
    assert "Upgrade" in denial_text
    assert "https://blackbox.example/upgrade" in denial_text
    assert "still finishing the previous message" not in denial_text
    assert "helping other customers" not in denial_text
    assert "current service window" not in denial_text

    activity_rows = blackbox_runtime.list_activity_events(
        owner=owner,
        backing_install_id=deployed_agent["backing_install_id"],
        session_key=conversation["session_id"],
    )
    assert any(str(row.get("action") or "").strip() == "daily_message_limit_exceeded" for row in activity_rows), activity_rows
