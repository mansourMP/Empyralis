from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.blackbox_db


def test_public_web_turn_blackbox(blackbox_runtime) -> None:
    owner = blackbox_runtime.register_owner(email_prefix="web-turn")
    session_id = f"thread_{uuid.uuid4().hex[:10]}"
    events = blackbox_runtime.stream_turn(
        owner=owner,
        payload={
            "workspace_id": owner["workspace_id"],
            "thread_id": session_id,
            "session_id": session_id,
            "channel": "web",
            "actor": {"type": "user", "id": owner["user_id"]},
            "message": "hello from blackbox",
            "execution_mode": "sync",
            "response_mode": "stream",
        },
    )

    final_payload = next(item["data"] for item in events if item["event"] == "final")
    assert final_payload["reply"] == "Blackbox web reply: hello from blackbox"
    assert final_payload["mode"] == "answer"

    session = blackbox_runtime.get_session(owner=owner, session_id=session_id)
    assert session["session_id"] == session_id
    assert session["workspace_id"] == owner["workspace_id"]

    threads_payload = blackbox_runtime.list_threads(owner=owner, include_turns=True)
    matching_threads = [
        item
        for item in (threads_payload.get("items") or [])
        if any("hello from blackbox" in str(turn.get("content") or "") for turn in (item.get("turns") or []))
    ]
    assert matching_threads, threads_payload
    thread = matching_threads[0]
    assert thread["workspace_id"] == owner["workspace_id"]
    assert len(thread["turns"]) >= 2
    assert thread["turns"][0]["role"] == "user"
    assert thread["turns"][-1]["role"] == "assistant"
    assert "hello from blackbox" in thread["turns"][0]["content"]
