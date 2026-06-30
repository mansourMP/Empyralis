from __future__ import annotations

import json
from typing import Any, Callable, Dict


def tool_call_signature(
    tool_call: Dict[str, Any],
    *,
    tool_arguments_payload_fn: Callable[[Any], Dict[str, Any]],
    parse_tool_name_fn: Callable[[str], tuple[str, str]],
    parse_json_object_loose_fn: Callable[[str], Any],
) -> str:
    name = str(tool_call.get("name") or "").strip()
    argument_payload = tool_arguments_payload_fn(tool_call.get("arguments"))
    if "__" in name:
        connector_id, _action_id = parse_tool_name_fn(name)
        if connector_id in {"file", "shell", "screenshot", "computer"} and isinstance(argument_payload.get("input"), str):
            nested_input = parse_json_object_loose_fn(str(argument_payload.get("input") or ""))
            if isinstance(nested_input, dict):
                argument_payload = nested_input
    try:
        normalized_payload = json.dumps(argument_payload, sort_keys=True, ensure_ascii=False)
    except Exception:
        normalized_payload = str(argument_payload)
    return f"{name}:{normalized_payload}"


def record_direct_tool_signature(
    session_key: str,
    tool_call: Dict[str, Any],
    *,
    loop_state: Dict[str, Dict[str, Any]],
    repeat_limit: int,
    tool_call_signature_fn: Callable[[Dict[str, Any]], str],
) -> bool:
    signature = tool_call_signature_fn(tool_call)
    state = loop_state.get(session_key) or {"signature": "", "count": 0}
    if state.get("signature") == signature:
        state["count"] = int(state.get("count") or 0) + 1
    else:
        state = {"signature": signature, "count": 1}
    loop_state[session_key] = state
    return int(state.get("count") or 0) >= int(repeat_limit)


def clear_direct_tool_loop_state(
    session_key: str,
    *,
    loop_state: Dict[str, Dict[str, Any]],
) -> None:
    loop_state.pop(session_key, None)
