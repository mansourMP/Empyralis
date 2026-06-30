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


def _wait_for_no_conversations(
    blackbox_runtime,
    *,
    owner: Dict[str, Any],
    deployed_agent_id: str,
    timeout_seconds: float = 15.0,
) -> Dict[str, Any]:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    last_payload: Dict[str, Any] = {}
    while time.time() < deadline:
        last_payload = blackbox_runtime.list_conversations(
            owner=owner,
            deployed_agent_id=deployed_agent_id,
        )
        if not (last_payload.get("items") or []):
            return last_payload
        time.sleep(0.2)
    assert not (last_payload.get("items") or []), last_payload
    return last_payload


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


def test_monthly_cap_auto_pause_blackbox(blackbox_runtime) -> None:
    owner = blackbox_runtime.register_owner(email_prefix="telegram-cap")
    live = blackbox_runtime.create_live_telegram_deployed_agent(
        owner=owner,
        name="BudgetBot",
        config_overrides={
            "customer_policy": {
                "paused_message": "BudgetBot is paused after hitting its monthly cap.",
            },
            "commerce_policy": {
                "monthly_cost_cap_usd": 0.000001,
            },
        },
    )
    deployed_agent = live["deployed_agent"]
    connector = live["connector"]

    first = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="Trigger the monthly cap",
    )
    assert first.status_code == 200, first.text
    run_id = str(first.json().get("run_id") or "").strip()
    assert run_id
    blackbox_runtime.wait_for_run_terminal(run_id)
    delivery_before = len(_telegram_texts(blackbox_runtime))
    blackbox_runtime.pump_outbox_until(
        lambda: any("Trigger the monthly cap" in text for text in _telegram_texts(blackbox_runtime, after=delivery_before)),
        tenant_id=owner["tenant_id"],
        workspace_id=owner["workspace_id"],
        run_id=run_id,
        event_type="channel_run_delivery",
    )

    suspended = blackbox_runtime.wait_for_deployed_agent_state(
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
        expected_state="suspended",
    )
    budget_cycle = (
        ((suspended.get("operational_state") or {}).get("current_budget_cycle") or {})
        if isinstance(suspended.get("operational_state"), dict)
        else {}
    )
    assert budget_cycle.get("auto_paused_at")
    assert float(budget_cycle.get("percent_used") or 0.0) >= 100.0

    send_before = len(blackbox_runtime.captured_telegram_calls(method_name="sendMessage"))
    second = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="Second message after auto-pause",
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["action"] == "suspended"
    new_send_calls = blackbox_runtime.captured_telegram_calls(method_name="sendMessage")[send_before:]
    assert new_send_calls
    paused_text = str(new_send_calls[-1]["payload"].get("text") or "").strip()
    assert "suspended" in paused_text.lower()


def test_public_privacy_delete_blackbox(blackbox_runtime) -> None:
    owner = blackbox_runtime.register_owner(email_prefix="telegram-privacy")
    live = blackbox_runtime.create_live_telegram_deployed_agent(owner=owner, name="PrivacyBot")
    deployed_agent = live["deployed_agent"]
    connector = live["connector"]

    first = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="Remember that my order number is 12345",
    )
    assert first.status_code == 200, first.text
    run_id = str(first.json().get("run_id") or "").strip()
    assert run_id
    blackbox_runtime.wait_for_run_terminal(run_id)
    delivery_before = len(_telegram_texts(blackbox_runtime))
    blackbox_runtime.pump_outbox_until(
        lambda: any("order number is 12345" in text for text in _telegram_texts(blackbox_runtime, after=delivery_before)),
        tenant_id=owner["tenant_id"],
        workspace_id=owner["workspace_id"],
        run_id=run_id,
        event_type="channel_run_delivery",
    )

    _, conversation = _wait_for_conversation_item(
        blackbox_runtime,
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
    )
    session_id = str(conversation["session_id"] or "").strip()
    external_user_id = "telegram-user-1"

    send_before = len(blackbox_runtime.captured_telegram_calls(method_name="sendMessage"))
    deletion = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="/delete erase history",
    )
    assert deletion.status_code == 200, deletion.text
    deletion_payload = deletion.json()
    assert deletion_payload["action"] == "privacy_delete_request"

    new_send_calls = blackbox_runtime.captured_telegram_calls(method_name="sendMessage")[send_before:]
    assert new_send_calls
    delete_text = str(new_send_calls[-1]["payload"].get("text") or "").strip()
    assert "We recorded your data deletion request." in delete_text

    privacy_request = blackbox_runtime.get_privacy_request(
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
        channel_key="telegram",
        external_user_id=external_user_id,
    )
    assert privacy_request is not None
    assert str(privacy_request.get("status") or "").strip() == "requested"

    delete_result = blackbox_runtime.delete_external_user_data(
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
        external_user_id=external_user_id,
        channel="telegram",
        session_id=session_id,
    )
    assert int((delete_result.get("deleted_counts") or {}).get("deleted_channel_event_count") or 0) > 0

    _wait_for_no_conversations(
        blackbox_runtime,
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
    )

    second = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="Start fresh after delete",
    )
    assert second.status_code == 200, second.text
    second_run_id = str(second.json().get("run_id") or "").strip()
    assert second_run_id
    blackbox_runtime.wait_for_run_terminal(second_run_id)
    delivery_before = len(_telegram_texts(blackbox_runtime))
    blackbox_runtime.pump_outbox_until(
        lambda: any("Start fresh after delete" in text for text in _telegram_texts(blackbox_runtime, after=delivery_before)),
        tenant_id=owner["tenant_id"],
        workspace_id=owner["workspace_id"],
        run_id=second_run_id,
        event_type="channel_run_delivery",
    )

    _, refreshed_conversation = _wait_for_conversation_item(
        blackbox_runtime,
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
    )
    refreshed_detail = blackbox_runtime.get_conversation_detail(
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
        session_id=refreshed_conversation["session_id"],
    )
    texts = [str(item.get("text") or "") for item in (refreshed_detail.get("messages") or [])]
    assert any("Start fresh after delete" in text for text in texts)
    assert all("order number is 12345" not in text for text in texts)


def test_public_health_safety_blackbox(blackbox_runtime) -> None:
    owner = blackbox_runtime.register_owner(email_prefix="telegram-safety")
    live = blackbox_runtime.create_live_telegram_deployed_agent(
        owner=owner,
        name="HealthGuide",
        config_overrides={
            "safety_policy": {
                "health_safety_enabled": True,
                "assistant_name": "HealthGuide",
            },
        },
    )
    deployed_agent = live["deployed_agent"]
    connector = live["connector"]

    response = blackbox_runtime.telegram_webhook(
        connector_id=connector["id"],
        text="I have chest pain and trouble breathing",
    )
    assert response.status_code == 200, response.text
    run_id = str(response.json().get("run_id") or "").strip()
    assert run_id
    blackbox_runtime.wait_for_run_terminal(run_id)
    delivery_before = len(_telegram_texts(blackbox_runtime))
    blackbox_runtime.pump_outbox_until(
        lambda: bool(_telegram_texts(blackbox_runtime, after=delivery_before)),
        tenant_id=owner["tenant_id"],
        workspace_id=owner["workspace_id"],
        run_id=run_id,
        event_type="channel_run_delivery",
    )

    final_text = "\n".join(_telegram_texts(blackbox_runtime, after=delivery_before))
    assert "urgent medical attention" in final_text.lower()
    assert "Blackbox durable reply:" not in final_text

    _, conversation = _wait_for_conversation_item(
        blackbox_runtime,
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
    )
    detail = blackbox_runtime.get_conversation_detail(
        owner=owner,
        deployed_agent_id=deployed_agent["id"],
        session_id=conversation["session_id"],
    )
    assert detail["channel"] == "telegram"
    assert detail["run_ids"]

    activity_rows = blackbox_runtime.list_activity_events(
        owner=owner,
        backing_install_id=deployed_agent["backing_install_id"],
        session_key=conversation["session_id"],
        run_id=run_id,
    )
    assert any(str(row.get("action") or "").strip() == "escalated" for row in activity_rows), activity_rows
