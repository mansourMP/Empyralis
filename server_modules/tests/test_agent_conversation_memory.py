"""Unit coverage for the Conversations-view read API added to
agent_conversation_memory.py: parsing conversation_key back into
{surface_channel, remote_jid}, the channel_label lookup, and the
list_workspace_conversations / load_conversation functions themselves.

The write side (append_turn, load_recent_turns, record_exchange) is
exercised indirectly here (it is how these tests seed real on-disk
fixtures) but is otherwise unchanged and already covered by its
production caller, personal_channel_sage_bridge_service.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server_modules import agent_conversation_memory as m


@pytest.fixture(autouse=True)
def _isolated_conversations_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test in this file gets its own conversations directory --
    agent_conversation_memory._CONVERSATIONS_ROOT is computed once at
    import time from an env var, so touching the env var alone (after
    import) would not isolate it; the module attribute itself must be
    monkeypatched."""
    root = tmp_path / "conversations"
    monkeypatch.setattr(m, "_CONVERSATIONS_ROOT", root)
    return root


# ── channel_label / _split_conversation_key ─────────────────────────────


@pytest.mark.parametrize(
    "surface,expected_label",
    [
        ("telegram_personal", "Telegram"),
        ("whatsapp_personal", "WhatsApp"),
        ("signal_personal", "Signal"),
        ("imessage_personal", "iMessage"),
        ("wechat_personal", "WeChat"),
        ("discord_personal", "Discord"),
        ("slack", "Slack"),
        ("", "Unknown"),
    ],
)
def test_channel_label_known_surfaces(surface: str, expected_label: str) -> None:
    assert m.channel_label(surface) == expected_label


def test_channel_label_unknown_surface_is_humanized_not_raw() -> None:
    assert m.channel_label("some_new_channel_personal") == "Some New Channel"
    assert m.channel_label("weird_thing") == "Weird Thing"


@pytest.mark.parametrize(
    "conversation_key,expected_surface,expected_jid",
    [
        ("telegram_personal:5551234567", "telegram_personal", "5551234567"),
        ("whatsapp_personal:15551234567@s.whatsapp.net", "whatsapp_personal", "15551234567_s.whatsapp.net"),
        ("discord_personal:998877", "discord_personal", "998877"),
        ("slack:C0123ABC456", "slack", "C0123ABC456"),
    ],
)
def test_split_conversation_key_recovers_known_surfaces(
    conversation_key: str, expected_surface: str, expected_jid: str,
) -> None:
    # Round-trip through the exact same sanitizer the write path uses, so
    # this test fails the moment _safe_segment's behavior and this parser's
    # assumptions about it drift apart.
    sanitized = m._safe_segment(conversation_key, fallback="_main")
    surface, jid = m._split_conversation_key(sanitized)
    assert surface == expected_surface
    assert jid == expected_jid


def test_split_conversation_key_unknown_surface_is_honest_not_a_wrong_guess() -> None:
    surface, jid = m._split_conversation_key("totally_unrecognized_format_12345")
    assert surface == "unknown"
    assert jid == "totally_unrecognized_format_12345"


# ── _is_safe_path_segment / _parse_conversation_id ──────────────────────


def test_sage_sentinel_is_a_safe_path_segment() -> None:
    """"_sage" is the literal directory _safe_segment's own fallback
    injects for an empty agent_id -- it must validate even though running
    it through _safe_segment a SECOND time would strip its leading "_"
    (turning it into "sage") and therefore fail a naive round-trip check."""
    assert m._is_safe_path_segment("_sage") is True
    assert m._safe_segment("_sage", fallback="") == "sage"  # documents the quirk this special-case guards against


def test_normal_agent_id_is_a_safe_path_segment() -> None:
    assert m._is_safe_path_segment("agent-42") is True
    assert m._is_safe_path_segment("a1b2c3d4-uuid-like-token") is True


@pytest.mark.parametrize("token", ["", "../../etc", "..", "a/b", "__", "."])
def test_unsafe_tokens_are_rejected(token: str) -> None:
    assert m._is_safe_path_segment(token) is False


def test_parse_conversation_id_round_trips_a_real_id() -> None:
    parsed = m._parse_conversation_id("agent-42:whatsapp_personal_15551230000")
    assert parsed == ("agent-42", "whatsapp_personal_15551230000")


def test_parse_conversation_id_round_trips_the_sage_bucket() -> None:
    parsed = m._parse_conversation_id("_sage:telegram_personal_owner-self")
    assert parsed == ("_sage", "telegram_personal_owner-self")


@pytest.mark.parametrize(
    "conversation_id",
    [
        "",
        "no-colon-at-all",
        "agent-42:",
        ":no-agent-bucket",
        "agent-42:../../../etc/passwd",
        "../../etc:passwd",
    ],
)
def test_parse_conversation_id_rejects_malformed_or_crafted_ids(conversation_id: str) -> None:
    assert m._parse_conversation_id(conversation_id) is None


# ── list_workspace_conversations ─────────────────────────────────────────


def test_list_workspace_conversations_empty_workspace_returns_empty_list() -> None:
    assert m.list_workspace_conversations(workspace_id="never-seen-workspace") == []


def test_list_workspace_conversations_tags_agent_channel_and_sender() -> None:
    m.append_turn(
        workspace_id="ws-1", agent_id="", conversation_key="telegram_personal:owner-self",
        role="user", content="hello sage",
    )
    m.append_turn(
        workspace_id="ws-1", agent_id="agent-42",
        conversation_key="whatsapp_personal:15551230000@s.whatsapp.net",
        role="user", content="hi there",
    )
    m.append_turn(
        workspace_id="ws-1", agent_id="agent-42",
        conversation_key="whatsapp_personal:15551230000@s.whatsapp.net",
        role="assistant", content="hello, how can I help?",
    )

    conversations = m.list_workspace_conversations(workspace_id="ws-1")
    assert len(conversations) == 2

    by_agent = {c["agent_id"]: c for c in conversations}
    assert by_agent[""]["channel_label"] == "Telegram"
    assert by_agent[""]["sender"] == "owner-self"
    assert by_agent[""]["turn_count"] == 1

    assert by_agent["agent-42"]["channel_label"] == "WhatsApp"
    assert by_agent["agent-42"]["turn_count"] == 2
    assert by_agent["agent-42"]["last_message_preview"] == "hello, how can I help?"
    assert by_agent["agent-42"]["last_message_role"] == "assistant"


def test_list_workspace_conversations_never_crosses_workspaces() -> None:
    m.append_turn(workspace_id="ws-1", agent_id="a1", conversation_key="telegram_personal:1", role="user", content="ws-1 message")
    m.append_turn(workspace_id="ws-2", agent_id="a1", conversation_key="telegram_personal:1", role="user", content="ws-2 message")

    ws1_conversations = m.list_workspace_conversations(workspace_id="ws-1")
    assert len(ws1_conversations) == 1
    assert ws1_conversations[0]["last_message_preview"] == "ws-1 message"

    ws2_conversations = m.list_workspace_conversations(workspace_id="ws-2")
    assert len(ws2_conversations) == 1
    assert ws2_conversations[0]["last_message_preview"] == "ws-2 message"


def test_list_workspace_conversations_sorts_newest_activity_first(monkeypatch: pytest.MonkeyPatch) -> None:
    m.append_turn(workspace_id="ws-1", agent_id="a1", conversation_key="telegram_personal:older", role="user", content="older")
    older_path = m.conversation_path(workspace_id="ws-1", agent_id="a1", conversation_key="telegram_personal:older")

    m.append_turn(workspace_id="ws-1", agent_id="a1", conversation_key="telegram_personal:newer", role="user", content="newer")
    newer_path = m.conversation_path(workspace_id="ws-1", agent_id="a1", conversation_key="telegram_personal:newer")

    # Force a real mtime gap rather than relying on filesystem write speed.
    import os
    import time

    now = time.time()
    os.utime(older_path, (now - 3600, now - 3600))
    os.utime(newer_path, (now, now))

    conversations = m.list_workspace_conversations(workspace_id="ws-1")
    assert [c["remote_jid"] for c in conversations] == ["newer", "older"]


def test_list_workspace_conversations_skips_empty_file(tmp_path: Path) -> None:
    m.append_turn(workspace_id="ws-1", agent_id="a1", conversation_key="telegram_personal:real", role="user", content="hi")
    empty_path = m.conversation_path(workspace_id="ws-1", agent_id="a1", conversation_key="telegram_personal:empty")
    empty_path.parent.mkdir(parents=True, exist_ok=True)
    empty_path.write_text("", encoding="utf-8")

    conversations = m.list_workspace_conversations(workspace_id="ws-1")
    assert len(conversations) == 1
    assert conversations[0]["remote_jid"] == "real"


# ── load_conversation ─────────────────────────────────────────────────────


def test_load_conversation_returns_full_tagged_transcript() -> None:
    m.append_turn(workspace_id="ws-1", agent_id="agent-42", conversation_key="slack:C0123ABC456", role="user", content="ping")
    m.append_turn(workspace_id="ws-1", agent_id="agent-42", conversation_key="slack:C0123ABC456", role="assistant", content="pong")

    [summary] = m.list_workspace_conversations(workspace_id="ws-1")
    conversation = m.load_conversation(workspace_id="ws-1", conversation_id=summary["conversation_id"])

    assert conversation is not None
    assert conversation["agent_id"] == "agent-42"
    assert conversation["channel_label"] == "Slack"
    assert conversation["turns"] == [
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": "pong"},
    ]


def test_load_conversation_missing_file_returns_none() -> None:
    assert m.load_conversation(workspace_id="ws-1", conversation_id="agent-42:does_not_exist") is None


def test_load_conversation_malformed_id_returns_none() -> None:
    assert m.load_conversation(workspace_id="ws-1", conversation_id="not-a-valid-id") is None


def test_load_conversation_cannot_cross_workspaces_even_with_a_valid_id() -> None:
    """The isolation guarantee at the service layer, independent of the
    route's own auth gate: workspace_id and conversation_id are both
    required, and a conversation_id that is only valid in ws-2 must not
    resolve to anything when workspace_id="ws-1"."""
    m.append_turn(workspace_id="ws-2", agent_id="agent-99", conversation_key="telegram_personal:999", role="user", content="ws-2 secret")

    [ws2_summary] = m.list_workspace_conversations(workspace_id="ws-2")
    leaked = m.load_conversation(workspace_id="ws-1", conversation_id=ws2_summary["conversation_id"])

    assert leaked is None
