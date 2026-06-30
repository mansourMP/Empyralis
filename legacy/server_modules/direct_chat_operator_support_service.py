from __future__ import annotations

from typing import Any, Dict, List, Optional

from server_modules import direct_chat_context_service
from server_modules import direct_chat_metadata_service
from server_modules import run_state_repository
from server_modules import skills_service


def normalize_tool_capabilities(availability: Any) -> List[Dict[str, Any]]:
    return skills_service.normalize_availability_capability_payloads(availability)


def tool_capability(availability: Dict[str, Any], tool_id: str) -> Optional[Dict[str, Any]]:
    item = skills_service.availability_capability(availability, tool_id)
    return dict(item) if isinstance(item, dict) else None


def tool_connected(availability: Dict[str, Any], tool_id: str) -> bool:
    return skills_service.availability_capability_connected(availability, tool_id)


def tool_runtime_usable(availability: Dict[str, Any], tool_id: str) -> Optional[bool]:
    return skills_service.availability_capability_runtime_usable(availability, tool_id)


def local_worker_available(availability: Dict[str, Any]) -> bool:
    if not isinstance(availability, dict):
        return False
    local_worker_online = availability.get("local_worker_online")
    if isinstance(local_worker_online, bool):
        return local_worker_online
    capability_truth = availability.get("capability_truth") if isinstance(availability.get("capability_truth"), dict) else {}
    my_computer = capability_truth.get("my_computer") if isinstance(capability_truth.get("my_computer"), dict) else {}
    local_tools_available = my_computer.get("local_tools_available")
    if isinstance(local_tools_available, bool):
        return local_tools_available
    local_gateway_online = availability.get("local_gateway_online")
    if isinstance(local_gateway_online, bool):
        runtime_ok = availability.get("runtime_ok")
        if isinstance(runtime_ok, bool):
            return local_gateway_online and runtime_ok
        return local_gateway_online
    runtime_ok = availability.get("runtime_ok")
    if isinstance(runtime_ok, bool):
        return runtime_ok
    return True


def active_run_count(workspace_id: str) -> int:
    try:
        live_runs = run_state_repository.sync_list_live_runs()
    except Exception:
        live_runs = []
    return direct_chat_context_service.active_run_count(
        workspace_id,
        live_runs={
            str(item.get("run_id") or idx): item
            for idx, item in enumerate(live_runs)
            if isinstance(item, dict)
        },
    )


def recent_run_prompts_for_suggestions(workspace_id: str) -> List[str]:
    try:
        from server_modules.shared import RUN_HISTORY, RUN_HISTORY_LOCK
    except Exception:
        return []
    with RUN_HISTORY_LOCK:
        history_items = list(RUN_HISTORY)
    return direct_chat_metadata_service.recent_run_prompts_for_suggestions(
        workspace_id,
        run_history=history_items,
    )
