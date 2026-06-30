from __future__ import annotations

from typing import Any, Dict


AGENT_COMPUTER_CONNECTED_LABEL = "Connected"
AGENT_COMPUTER_CONNECTING_LABEL = "Connecting to your Mac..."
AGENT_COMPUTER_RUNNING_LABEL = "Running on your Mac"
AGENT_COMPUTER_DONE_LABEL = "Done"
AGENT_COMPUTER_REQUIRED_LABEL = "Needs Agent Computer"
AGENT_COMPUTER_OFFLINE_LABEL = "Agent Computer offline"
AGENT_COMPUTER_APPROVAL_LABEL = "Sage needs confirmation"

_CONNECTING_STATES = {"queued", "connecting", "starting", "started", "pending"}
_RUNNING_STATES = {"running"}
_DONE_STATES = {"completed", "complete", "done", "ready", "success", "succeeded"}
_APPROVAL_STATES = {"waiting_approval", "waiting", "approval_required"}
_REQUIRED_STATES = {"required", "requires_agent_computer", "needs_agent_computer"}
_OFFLINE_STATES = {"offline", "degraded"}
_STOPPED_STATES = {"terminated", "stopped", "cancelled", "canceled"}
_FAILED_STATES = {"failed", "failure", "error", "blocked", "denied"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def agent_computer_activity_shape(state: Any) -> Dict[str, str]:
    token = _text(state).lower() or "running"
    if token in _CONNECTING_STATES:
        return {
            "type": "connecting_hardware",
            "label": AGENT_COMPUTER_CONNECTING_LABEL,
            "status": "active",
        }
    if token in _RUNNING_STATES:
        return {
            "type": "hardware_running",
            "label": AGENT_COMPUTER_RUNNING_LABEL,
            "status": "active",
        }
    if token in _DONE_STATES or token in _STOPPED_STATES:
        return {
            "type": "done",
            "label": AGENT_COMPUTER_DONE_LABEL,
            "status": "completed",
        }
    if token in _APPROVAL_STATES:
        return {
            "type": "waiting_approval",
            "label": AGENT_COMPUTER_APPROVAL_LABEL,
            "status": "active",
        }
    if token in _REQUIRED_STATES:
        return {
            "type": "error",
            "label": AGENT_COMPUTER_REQUIRED_LABEL,
            "status": "failed",
        }
    if token in _OFFLINE_STATES or token in _FAILED_STATES:
        return {
            "type": "error",
            "label": AGENT_COMPUTER_OFFLINE_LABEL,
            "status": "failed",
        }
    return {
        "type": "hardware_running",
        "label": AGENT_COMPUTER_RUNNING_LABEL,
        "status": "active",
    }


def agent_computer_public_status_label(state: Any) -> str:
    token = _text(state).lower()
    if token in {"connected", "online", "ready"}:
        return AGENT_COMPUTER_CONNECTED_LABEL
    if token in _CONNECTING_STATES:
        return AGENT_COMPUTER_CONNECTING_LABEL
    if token in _RUNNING_STATES:
        return AGENT_COMPUTER_RUNNING_LABEL
    if token in _DONE_STATES or token in _STOPPED_STATES:
        return AGENT_COMPUTER_DONE_LABEL
    if token in _APPROVAL_STATES:
        return AGENT_COMPUTER_APPROVAL_LABEL
    if token in _OFFLINE_STATES or token in _FAILED_STATES:
        return AGENT_COMPUTER_OFFLINE_LABEL
    if token in _REQUIRED_STATES:
        return AGENT_COMPUTER_REQUIRED_LABEL
    return AGENT_COMPUTER_CONNECTED_LABEL


def agent_computer_offline_summary() -> str:
    return AGENT_COMPUTER_OFFLINE_LABEL
