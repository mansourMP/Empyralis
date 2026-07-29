"""Every failure shape a real producer in this codebase emits, plus the
false-positive cases the old substring sniff got wrong.

The shapes below were enumerated by tracing every producer that can reach
direct_chat_generation_service's tool loop: skills_service's builtin dispatch,
local_tool_executor, runs_execution, the hardware/gateway adapters,
mcp_registry_service and the two timeout returns.
"""

from __future__ import annotations

import json

from server_modules import tool_result_status as status


def _failed(result) -> bool:
    return status.classify_tool_result(result).failed


# --- the one shape the old sniff caught ------------------------------------


def test_timeout_payload_is_a_failure():
    # skills_service._timeout_tool_result and the inline _cf.TimeoutError
    # branch in direct_chat_generation_service both emit exactly this.
    assert _failed(json.dumps({"error": "timeout", "message": "The tool 'web__search' timed out after 30s."}))


# --- shapes the old sniff missed entirely ----------------------------------


def test_ok_false_is_a_failure():
    # subagent__spawn, fleet__* — {"ok": false, "error": "<code>", ...}
    assert _failed(json.dumps({"ok": False, "error": "subagent_depth_exceeded", "message": "Too deep."}))
    assert _failed(json.dumps({"ok": False, "error": "Fleet tool requires operator role.", "agents": []}))


def test_ok_false_without_any_error_key_is_a_failure():
    # subagent wait-timeout carries no `error` key at all.
    assert _failed(json.dumps({"ok": False, "run_id": "r1", "status": "timeout", "spawns_used": 1}))


def test_status_failed_is_a_failure():
    assert _failed(json.dumps({"status": "failed", "reason": "hardware_action_failed"}))


def test_hardware_and_gateway_status_vocabulary_is_a_failure():
    # hardware_action_broker_service / gateway_adapter return values, not raises.
    for token in ("offline", "degraded", "failed", "not_found", "denied", "unauthorized"):
        assert _failed(json.dumps({"status": token, "reason": "agent_computer_offline"})), token


def test_filesystem_error_shapes_are_failures():
    # local_tool_executor.filesystem_read / _write / _list
    assert _failed(json.dumps({"content": "", "path": "/x", "error": "file not found", "status": "not_found"}))
    assert _failed(json.dumps({"path": "/x", "error": "permission denied", "status": "error"}))
    assert _failed(json.dumps({"entries": [], "path": "/x", "error": "invalid path", "status": "error"}))


def test_nonzero_exit_code_is_a_failure_even_when_status_says_completed():
    # The gateway shell envelope hardcodes "status": "completed" and reports
    # the truth only in exit_code — explicit failure beats explicit success.
    assert _failed(json.dumps({"status": "completed", "exit_code": 1, "stdout": "", "stderr": "boom"}))
    # local_tool_executor.shell_execute's own failure shapes have no error key.
    assert _failed(json.dumps({"stdout": "", "stderr": "Command timed out after 120s", "exit_code": -1, "status": "timeout"}))


def test_bare_error_string_payload_is_a_failure():
    assert _failed(json.dumps({"error": "repository not found"}))


def test_http_style_code_is_a_failure():
    assert _failed(json.dumps({"code": 500, "message": "Internal Server Error"}))
    assert _failed(json.dumps({"code": 404}))


def test_memory_search_incomplete_is_a_failure():
    assert _failed(
        json.dumps(
            {
                "results": [],
                "files_searched": 3,
                "errors": [{"path": "/a", "reason": "unreadable"}],
                "status": "incomplete",
                "message": "Some files could not be read.",
            }
        )
    )


def test_mcp_is_error_envelope_is_a_failure():
    # format_mcp_tool_result's explicit envelope for CallToolResult.isError.
    assert _failed(
        json.dumps({"ok": False, "error": "Error: repo not found", "reply": "Error: repo not found", "result": {"text": "..."}})
    )


def test_mcp_nested_result_error_is_a_failure():
    # Older/other envelope with no outer flag — one bounded unwrap into
    # `result` finds the server's own error shape.
    assert _failed(json.dumps({"reply": "call failed", "result": {"isError": True, "text": "boom"}}))
    assert _failed(json.dumps({"reply": "call failed", "result": {"error": "repo not found"}}))


def test_none_and_empty_results_are_failures():
    assert _failed(None)
    assert _failed("")
    assert _failed("   ")


# --- FALSE POSITIVES: successful results whose CONTENT mentions an error ----
# This is the main risk in replacing the sniff. The classifier reads parsed
# TOP-LEVEL KEYS only and never scans result text for error-ish words.


def test_reading_a_log_file_full_of_errors_is_not_a_failure():
    # local_tool_executor.filesystem_read puts `content` FIRST, so the old
    # 120-char text sniff marked this successful read as failed.
    payload = json.dumps(
        {
            "content": '{"error": "connection refused", "level": "ERROR"}\nERROR: retry failed\n',
            "path": "/var/log/app.log",
            "size_bytes": 512,
            "status": "completed",
        }
    )
    assert not _failed(payload)


def test_summarising_a_bug_report_is_not_a_failure():
    payload = json.dumps(
        {
            "ok": True,
            "reply": 'The bug: the handler swallows {"error": ...} and returns 500 anyway.',
        }
    )
    assert not _failed(payload)


def test_explicit_ok_true_with_null_error_is_not_a_failure():
    assert not _failed(json.dumps({"ok": True, "error": None, "data": [1, 2, 3]}))
    assert not _failed(json.dumps({"ok": True, "error": "", "errors": []}))


def test_upstream_payload_with_empty_errors_list_is_not_a_failure():
    # memory__search success always carries "errors": [] — and "errors" is
    # never a failure signal on its own.
    assert not _failed(
        json.dumps({"results": [{"path": "/a"}], "files_searched": 2, "errors": [], "status": "matches_found"})
    )


def test_browser_navigate_to_a_404_page_is_not_a_failure():
    # The navigation SUCCEEDED; status_code describes the page, not the tool.
    assert not _failed(json.dumps({"url": "https://x.test/missing", "title": "Not Found", "status_code": 404}))


def test_grep_results_mentioning_error_are_not_a_failure():
    assert not _failed(
        json.dumps({"status": "completed", "exit_code": 0, "stdout": 'app.py:12: raise RuntimeError("error")'})
    )


def test_plain_prose_result_is_not_a_failure():
    assert not _failed("1. Example result\nURL: https://x.test\nSnippet: error handling in Python")
    assert not _failed("Connector action completed: github.list_issues.")


def test_empty_list_result_is_not_a_failure():
    assert not _failed("[]")
    assert not _failed([])


def test_multi_element_list_with_one_error_record_is_not_a_failure():
    # A batch of records where one happens to carry an `error` field must not
    # condemn the whole call.
    assert not _failed(json.dumps([{"id": 1, "error": "nope"}, {"id": 2}]))


def test_truncated_json_is_ambiguous_not_failed():
    # format_mcp_tool_result slices at 8000 chars and can cut mid-object.
    assert not _failed('{"reply": "fine", "result": {"items": [{"a": 1}, {"b"')


# --- precedence and metadata ----------------------------------------------


def test_explicit_failure_beats_explicit_success():
    assert _failed(json.dumps({"ok": True, "status": "failed"}))
    assert _failed(json.dumps({"status": "completed", "error": "it actually broke"}))


def test_waiting_approval_is_pending_not_failed():
    assert not _failed(json.dumps({"status": "waiting_approval", "approval": {"id": "a1"}}))


def test_outcome_carries_error_text_and_reason():
    outcome = status.classify_tool_result(json.dumps({"ok": False, "error": "repo not found"}))
    assert outcome.failed is True
    assert outcome.signal == "explicit_flag"
    assert outcome.reason == "ok_false"
    assert outcome.error_text == "repo not found"


def test_already_parsed_dicts_and_lists_are_accepted():
    assert _failed({"ok": False, "error": "x"})
    assert not _failed({"ok": True})
    assert _failed([{"isError": True}])


def test_failure_summary_helper():
    assert status.failure_summary(json.dumps({"ok": True})) is None
    assert status.failure_summary(json.dumps({"status": "failed", "reason": "offline"})) == "offline"
    # The fallback only fills in when the payload carried no message of its own.
    assert status.failure_summary("") == "The tool returned an empty result."
    assert status.failure_summary(json.dumps({"ok": False}), fallback="nothing came back") == "nothing came back"


def test_exit_code_zero_is_not_a_failure():
    assert not _failed(json.dumps({"status": "completed", "exit_code": 0, "stdout": "hi"}))


def test_boolean_exit_code_is_not_treated_as_nonzero():
    assert not _failed(json.dumps({"status": "completed", "exit_code": False}))
