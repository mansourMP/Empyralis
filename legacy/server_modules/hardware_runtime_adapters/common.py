from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from server_modules import agent_trace_service


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def text(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def list_dicts(value: Any) -> List[Dict[str, Any]]:
    return [dict(item) for item in list(value or []) if isinstance(item, dict)]


async def resolve_trace_context(
    trace_context: Any,
    *,
    trace_id: str,
    tenant_id: str,
    workspace_id: str,
    thread_id: Optional[str],
    run_id: str,
) -> Any:
    if trace_context is not None:
        return trace_context
    if not trace_id:
        return None
    return await agent_trace_service.resume_trace(
        trace_id=trace_id,
        tenant_id=text(tenant_id) or "default",
        workspace_id=text(workspace_id) or "default",
        thread_id=text(thread_id) or None,
        run_id=run_id,
        root_agent_id="sage",
    )
