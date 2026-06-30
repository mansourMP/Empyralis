import pytest

from server_modules import business_messaging_channel_adapter_service as service


def test_apple_messages_business_adapter_is_planned_official_business_lane() -> None:
    adapter = service.get_adapter("apple_messages_business")

    assert adapter["display_name"] == "Apple Messages for Business"
    assert adapter["provider"] == "apple_messages_business_msp"
    assert adapter["runtime_lane"] == "studio_business_connector"
    assert adapter["stage"] == "planned"
    assert adapter["launch_allowed"] is False
    assert adapter["setup_available"] is False
    assert adapter["requires_agent_computer"] is False
    assert adapter["official_business_channel"] is True
    assert adapter["personal_channel"] is False
    assert adapter["requires_msp"] is True
    assert adapter["requires_ai_disclosure"] is True
    assert adapter["requires_human_handoff"] is True
    assert adapter["requires_user_initiated_conversation"] is True


def test_apple_review_packet_tracks_required_registration_work() -> None:
    packet = service.build_review_packet(
        "apple_messages_business_msp",
        support_url="https://empyralis.example/support",
        privacy_url="https://empyralis.example/privacy",
    )
    by_id = {item["id"]: item for item in packet["checklist"]}

    assert packet["provider"] == "apple_messages_business"
    assert packet["launch_allowed"] is False
    assert by_id["apple_business_identity"]["ready"] is False
    assert by_id["approved_msp"]["ready"] is False
    assert by_id["ai_disclosure"]["ready"] is False
    assert by_id["human_handoff"]["ready"] is True
    assert by_id["privacy_and_retention"]["ready"] is True


def test_normalize_inbound_event_preserves_required_business_context() -> None:
    inbound = service.normalize_inbound_event(
        "apple_messages_business",
        {
            "apple_user_id": "apple-user-1",
            "conversation_id": "thread-1",
            "message_id": "msg-1",
            "text": "Can Sage prepare me before meetings?",
            "msp_provider": "infobip",
            "locale": "en-US",
        },
    )

    assert inbound["provider"] == "apple_messages_business"
    assert inbound["runtime_lane"] == "studio_business_connector"
    assert inbound["external_user_id"] == "apple-user-1"
    assert inbound["external_thread_id"] == "thread-1"
    assert inbound["inbound_event_id"] == "msg-1"
    assert inbound["text"] == "Can Sage prepare me before meetings?"
    assert inbound["requires_ai_disclosure"] is True
    assert inbound["requires_human_handoff"] is True
    assert inbound["user_initiated"] is True
    assert inbound["raw_metadata"]["msp_provider"] == "infobip"


def test_outbound_envelope_requires_ai_disclosure_and_human_handoff() -> None:
    with pytest.raises(service.BusinessMessagingAdapterError, match="human handoff"):
        service.build_outbound_envelope(
            "apple_messages_business",
            external_thread_id="thread-1",
            text="Hello",
            human_handoff_available=False,
            ai_disclosure_sent=True,
        )

    with pytest.raises(service.BusinessMessagingAdapterError, match="AI disclosure"):
        service.build_outbound_envelope(
            "apple_messages_business",
            external_thread_id="thread-1",
            text="Hello",
            human_handoff_available=True,
            ai_disclosure_sent=False,
        )

    envelope = service.build_outbound_envelope(
        "apple_messages_business",
        external_thread_id="thread-1",
        text="You're chatting with Sage, Empyralis's AI assistant.",
        human_handoff_available=True,
        ai_disclosure_sent=True,
        proof_log_id="proof-1",
    )

    assert envelope["provider"] == "apple_messages_business"
    assert envelope["runtime_provider"] == "apple_messages_business_msp"
    assert envelope["dispatch_allowed"] is False
    assert envelope["dispatch_blocker"] == "adapter_not_launch_ready"
    assert envelope["proof_log_id"] == "proof-1"
