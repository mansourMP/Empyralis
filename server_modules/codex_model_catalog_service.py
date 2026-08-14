"""Live Codex model catalog: dispatch for the `llm.models.list` capability
(empyralis-gateway/src/llm/runtime.ts / codex-app-server.ts).

Why this exists (URGENT fix, 2026-08-14): a Fleet agent configured with
cli_subscription mode + runtime "codex" + an explicit model id ("gpt-5.4")
got a raw provider error mid-turn — "The 'gpt-5.4' model is not supported
when using Codex with a ChatGPT account" — because OpenAI had retired that
model from Codex's own catalog (replaced by gpt-5.6-terra/luna) and nothing
on our side ever re-checked a saved model id against the LIVE, currently-
supported set. Our own picker (frontend/lib/workspace/fleet/fleet-provider-
constants.ts's MODELS_BY_PROVIDER["openai-codex"]) was a hand-typed mirror
that had already rotted the same way — verified live against a real,
pinned codex 0.144.1 install: none of "gpt-5.4"/"gpt-5.3-codex"/"gpt-5.2"
exist in a real `model/list` response any more.

The fix is the same discipline CLAUDE.md already applies elsewhere in this
codebase (the OpenClaw channel manifest, the DeepSeek retirement fix): the
expected set must come from the PROVIDER's own live surface, never a
hand-typed list — codex's `model/list` + `getAuthStatus` JSON-RPCs are
exactly that surface, already scoped by whatever auth mode/plan is actually
logged in on the box, and cost nothing (metadata only, no inference, no
billing).

Dispatch shape mirrors gateway_doctor_service.run_gateway_doctor() and
gateway_self_update_service.trigger_gateway_self_update(): a routed call
through gateway_execution_service.execute_tool_via_gateway() over the
gateway's existing outbound cloud WS / tool-invoke transport — no SSH, no
new transport.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from server_modules import gateway_execution_service

# Must match LLM_MODELS_LIST_CAPABILITY in empyralis-gateway/src/llm/runtime.ts.
MODELS_LIST_CAPABILITY = "llm.models.list"

# Metadata-only RPC pair (getAuthStatus + model/list) against an already-warm
# or freshly-spawned app-server daemon — generous but not doctor/self-update
# scale (this never downloads an artifact or runs repairs).
DEFAULT_TIMEOUT_SECONDS = 20

# cli_subscription runtimes this capability can answer for today — mirrors
# runtime.ts's own MODEL_LIST_CAPABLE_RUNTIMES so a caller can short-circuit
# without a round trip for a runtime we already know has no live catalog.
LIVE_MODEL_LIST_CAPABLE_RUNTIMES = frozenset({"codex"})


class CodexModelCatalogError(RuntimeError):
    """Raised by fetch_codex_model_catalog(); the route translates
    status_code + this message directly into an HTTPException, same pattern
    as GatewayDoctorError."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _status_code_for_reason(reason: str) -> int:
    r = str(reason or "").strip().lower()
    if "offline" in r or "heartbeat_stale" in r or "unhealthy" in r or "not currently connected" in r:
        return 409
    return 400


async def fetch_codex_model_catalog(
    *,
    gateway_id: str,
    workspace_id: str,
    runtime: str = "codex",
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Returns the LIVE model catalog for one cli_subscription runtime on one
    paired Gateway.

    Shape: {"supported": bool, "auth_method": str|None, "models": [...]}.
    `supported: False` (never an exception) means "this runtime has no
    verified live-catalog capability yet" — an honest, structured refusal,
    not a guess (see runtime.ts's own MODEL_LIST_CAPABLE_RUNTIMES). A real
    dispatch failure (Gateway offline, Codex not installed/authenticated,
    timeout) raises CodexModelCatalogError instead — the caller decides
    whether that's a hard-stop (save-time validation) or a soft "couldn't
    check right now" (the picker's own honest fallback).
    """
    if str(runtime or "").strip().lower() not in LIVE_MODEL_LIST_CAPABLE_RUNTIMES:
        return {"supported": False, "auth_method": None, "models": []}

    run_id = f"llm-models-list-{uuid4().hex[:12]}"
    trace_id = run_id
    try:
        execution = await gateway_execution_service.execute_tool_via_gateway(
            gateway_id=gateway_id,
            capability_id=MODELS_LIST_CAPABILITY,
            arguments={"runtime": runtime},
            run_id=run_id,
            trace_id=trace_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            agent_scope="sage",
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        )
    except (ValueError, PermissionError) as exc:
        reason = str(exc)
        raise CodexModelCatalogError(
            reason,
            status_code=403 if isinstance(exc, PermissionError) else _status_code_for_reason(reason),
        ) from exc

    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    supported = bool(result.get("supported"))
    models_raw = result.get("models") if isinstance(result.get("models"), list) else []
    models: List[Dict[str, Any]] = []
    for m in models_raw:
        if not isinstance(m, dict):
            continue
        model_id = str(m.get("id") or "").strip()
        if not model_id:
            continue
        models.append({
            "id": model_id,
            "display_name": str(m.get("display_name") or model_id),
            "description": str(m.get("description") or ""),
            "hidden": bool(m.get("hidden")),
            "is_default": bool(m.get("is_default")),
        })
    return {
        "supported": supported,
        "auth_method": (str(result.get("auth_method")).strip() or None) if result.get("auth_method") else None,
        "models": models,
        "reason": str(result.get("reason") or "").strip() or None,
    }


def model_ids_in_catalog(catalog: Dict[str, Any]) -> List[str]:
    """Every model id in a fetch_codex_model_catalog() result, hidden ones
    included — save-time validation must accept a real-but-hidden model
    (e.g. one an owner already has selected before it became hidden), never
    just what the default picker shows."""
    return [str(m.get("id") or "").strip() for m in (catalog.get("models") or []) if isinstance(m, dict) and m.get("id")]
