from __future__ import annotations

import json
from typing import Any, Dict, Optional

from scripts.orion_local_worker_llm import generate_chat_reply_with_provider_fallback


_LLM_TASK_SYSTEM_PROMPT = (
    "You are a focused sub-task assistant. Complete only the requested sub-task. "
    "Do not call tools. Return concise output."
)


def _validate_json_shape(value: Any, schema: Any) -> bool:
    if not isinstance(schema, dict):
        return True
    schema_type = str(schema.get("type") or "").strip().lower()
    if schema_type == "object":
        if not isinstance(value, dict):
            return False
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for key in required:
            if key not in value:
                return False
        for key, property_schema in properties.items():
            if key not in value:
                continue
            if not _validate_json_shape(value.get(key), property_schema):
                return False
        return True
    if schema_type == "array":
        if not isinstance(value, list):
            return False
        item_schema = schema.get("items")
        if item_schema is None:
            return True
        return all(_validate_json_shape(item, item_schema) for item in value)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    return True


def llm_task(
    prompt: str,
    *,
    schema: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise RuntimeError("llm_task requires a prompt.")
    system_prompt = _LLM_TASK_SYSTEM_PROMPT
    if isinstance(schema, dict) and schema:
        system_prompt += (
            "\nReturn only JSON that matches this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, sort_keys=True)}"
        )
    reply, _usage, _attempted, error = generate_chat_reply_with_provider_fallback(
        context=dict(context or {}),
        metadata=dict(metadata or {}),
        user_goal=normalized_prompt,
        system_prompt=system_prompt,
        prior_messages=[],
    )
    if error:
        raise RuntimeError(str(error))
    if not isinstance(schema, dict) or not schema:
        return str(reply or "").strip()
    try:
        parsed = json.loads(str(reply or "").strip())
    except Exception as exc:
        raise RuntimeError(f"llm_task expected JSON output but got invalid JSON: {exc}") from exc
    if not _validate_json_shape(parsed, schema):
        raise RuntimeError("llm_task JSON output did not match the requested schema.")
    return parsed
