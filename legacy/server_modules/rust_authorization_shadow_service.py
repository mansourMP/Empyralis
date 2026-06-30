"""Default-off Rust authorization cutover seam.

This module is intentionally small: existing Python policy/risk services remain
authoritative until callers explicitly opt into Rust authorization or shadow
comparison. All Rust execution failures fail closed when Rust is the effective
authority.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

from server_modules import rust_runtime_kernel_client


RUST_AUTHORIZATION_FLAG = "EMPYRALIS_USE_RUST_AUTHORIZATION"
RUST_AUTHORIZATION_SHADOW_FLAG = "EMPYRALIS_SHADOW_RUST_AUTHORIZATION"
_RUST_AUTHORIZATION_NEXT_ACTIONS = {
    "allow": "allow_tool_execution",
    "require_approval": "request_tool_execution_approval",
    "block": "deny_tool_execution",
}


def rust_authorization_enabled() -> bool:
    return _env_enabled(RUST_AUTHORIZATION_FLAG)


def rust_authorization_shadow_enabled() -> bool:
    return _env_enabled(RUST_AUTHORIZATION_SHADOW_FLAG)


def evaluate_authorization(
    *,
    policy: Mapping[str, Any],
    capability: str,
    action_class: str = "unknown",
    payload: Optional[Mapping[str, Any]] = None,
    requested_domain: Optional[str] = None,
    requested_path: Optional[str] = None,
    target_summary: Optional[str] = None,
    existing_decision: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate Rust authorization only when explicitly enabled.

    Default mode:
        Rust is not called. The existing Python decision remains effective.

    Shadow mode:
        Rust is called for comparison, but the existing Python decision remains
        effective.

    Opt-in mode:
        Rust is called and its decision becomes effective. Adapter failure or
        invalid Rust response is already represented as `decision: "block"` by
        the hardened client.
    """

    use_rust = rust_authorization_enabled()
    shadow_rust = rust_authorization_shadow_enabled()
    if not use_rust and not shadow_rust:
        return {
            "enabled": False,
            "shadow": False,
            "effective_source": "python_existing",
            "effective_decision": existing_decision,
            "rust": None,
            "decision_mismatch": False,
        }

    rust_response = rust_runtime_kernel_client.authorize_request(
        policy,
        capability=capability,
        action_class=action_class,
        payload=payload,
        requested_domain=requested_domain,
        requested_path=requested_path,
        target_summary=target_summary,
    )
    rust_response, rust_decision = _normalize_rust_authorization_response(rust_response)
    decision_mismatch = bool(existing_decision and rust_decision != existing_decision)

    if use_rust:
        return {
            "enabled": True,
            "shadow": shadow_rust,
            "effective_source": "rust_runtime_kernel",
            "effective_decision": rust_decision,
            "rust": rust_response,
            "decision_mismatch": decision_mismatch,
        }

    return {
        "enabled": False,
        "shadow": True,
        "effective_source": "python_existing",
        "effective_decision": existing_decision,
        "rust": rust_response,
        "decision_mismatch": decision_mismatch,
    }


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_rust_authorization_response(response: Any) -> tuple[Dict[str, Any], str]:
    rust_response: Dict[str, Any]
    if isinstance(response, dict):
        rust_response = dict(response)
    else:
        rust_response = {
            "ok": False,
            "decision": "block",
            "reason": "invalid_rust_authorization_response",
        }

    rust_decision = str(rust_response.get("decision") or "block").strip().lower() or "block"
    if rust_decision not in _RUST_AUTHORIZATION_NEXT_ACTIONS:
        rust_decision = "block"
        rust_response["decision"] = "block"

    next_action = str(rust_response.get("next_action") or "").strip()
    expected_next_action = _RUST_AUTHORIZATION_NEXT_ACTIONS[rust_decision]
    if rust_decision in {"allow", "require_approval"} and next_action != expected_next_action:
        rust_response.update(
            {
                "ok": False,
                "decision": "block",
                "reason": f"unexpected_next_action:{next_action or 'missing'}",
                "next_action": _RUST_AUTHORIZATION_NEXT_ACTIONS["block"],
            }
        )
        rust_decision = "block"
    elif rust_decision == "block" and next_action and next_action != expected_next_action:
        rust_response["reason"] = f"unexpected_next_action:{next_action}"
        rust_response["next_action"] = expected_next_action

    return rust_response, rust_decision
