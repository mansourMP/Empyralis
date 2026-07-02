"""Stub — approval gates removed. Agent acts on its own reasoning.

All gateway approval checks return approved/empty. Observability is handled
by activity_ledger_service.py. This module exists only to prevent import
errors in callers that still reference gateway_approval_service.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)


async def request_gateway_tool_approval(
    registration: Dict[str, Any] | None = None,
    capability_id: str = "",
    arguments: Dict[str, Any] | None = None,
    run_id: str = "",
    trace_id: str = "",
    request_id: str = "",
    runtime_session_id: str = "",
    runtime_target: str = "",
    runtime_access_mode: str = "",
    runtime_session_binding: str = "",
    thread_id: str = "",
    agent_scope: str = "",
) -> Dict[str, Any]:
    """Always approved — approval gates removed."""
    return {"approved": True, "approval_id": ""}


def list_gateway_tool_approvals(
    gateway_id: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Always empty — no approval gate means no pending approvals."""
    return []


def capability_requires_owner_approval(
    capability_id: str,
    arguments: Dict[str, Any] | None = None,
) -> bool:
    """Never requires approval — gates removed."""
    return False
