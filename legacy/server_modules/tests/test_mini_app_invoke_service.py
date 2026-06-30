from __future__ import annotations

from server_modules import mini_app_invoke_service


def test_invoke_mini_app_uses_thin_cloud_execution(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        mini_app_invoke_service,
        "_resolve_cloud_provider_for_workspace",
        lambda workspace_id, requested_provider="": ("deepseek", {"api_key": "test-key"}),
    )

    def _fake_generate(context, metadata, user_goal, system_prompt, prior_messages=None):
        captured["context"] = context
        captured["metadata"] = metadata
        captured["user_goal"] = user_goal
        captured["system_prompt"] = system_prompt
        captured["prior_messages"] = prior_messages
        return (
            "Clean mini app response",
            {"provider": "deepseek", "model": "deepseek-chat"},
            "deepseek",
            "",
        )

    monkeypatch.setattr(
        mini_app_invoke_service,
        "generate_chat_reply_with_provider_fallback",
        _fake_generate,
    )

    payload = mini_app_invoke_service.invoke_mini_app(
        "ws-1",
        "writing",
        "Rewrite this in a warmer tone.",
    )

    assert payload["app_id"] == "writing"
    assert payload["mode"] == "invoke"
    assert payload["memory_scope"] == "none"
    assert payload["reply"] == "Clean mini app response"
    assert payload["provider"] == "deepseek"
    assert payload["model"] == "deepseek-chat"
    assert payload["attempted_providers"] == ["deepseek"]
    assert captured["prior_messages"] is None
    assert captured["context"]["source"] == "mini_app_invoke"
    assert captured["context"]["disable_provider_fallback"] is True
    assert captured["metadata"]["provider"] == "deepseek"
    assert "personal agent" in captured["system_prompt"].lower()


def test_invoke_mini_app_rejects_unknown_app():
    try:
        mini_app_invoke_service.invoke_mini_app("ws-1", "unknown", "hello")
    except KeyError as exc:
        assert "does not support invoke mode" in str(exc)
    else:
        raise AssertionError("Expected KeyError for unknown mini app")
