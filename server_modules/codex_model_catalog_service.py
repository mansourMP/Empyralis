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
#
# Each is asked in ITS OWN native way on the box; nothing here normalises
# them into a shared protocol (founder, 2026-08-20: "we are going to make it
# work by how THEY provide the specific thing"):
#   codex       `codex app-server` JSON-RPC model/list  — structured, and the
#               only one carrying per-model reasoning-effort data
#   cursor_cli  `cursor-agent models`  ("List available models for this
#               account" — its own --help, read live 2026-08-20)
#   grok_build  `grok models`          ("List available models and exit" —
#               same, and verified live returning a real catalog)
# claude_code is deliberately absent: `claude` is a compiled binary with no
# models subcommand and no --list-models flag, so there is nothing to ask.
# Filling it in from documentation would be transcription, which CLAUDE.md
# already rules out as a source of truth.
LIVE_MODEL_LIST_CAPABLE_RUNTIMES = frozenset({"codex", "cursor_cli", "grok_build"})


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
        return {
            "supported": False,
            "auth_method": None,
            "models": [],
            "reason": (
                f"No live model catalog exists for runtime \"{runtime}\" — "
                "its CLI publishes no way to ask."
            ),
        }

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
        # default_reasoning_effort/supported_reasoning_efforts (2026-08-20):
        # read DEFENSIVELY, not required — the gateway's own RPC handler
        # (empyralis-gateway/src/llm/codex-app-server.ts's listModels(),
        # runtime.ts's listModelsForRuntime()) does not forward these two
        # fields yet, even though codex app-server's real `model/list` RPC
        # already returns them per model (verified live against this
        # box's own real, authenticated Codex install, 2026-08-20:
        # `supportedReasoningEfforts`/`defaultReasoningEffort` present on
        # every entry, including an effort level — "ultra", on
        # gpt-5.6-terra — no static table in this codebase had ever
        # modeled). Reading them with .get() rather than a required key
        # means this function does the right thing BOTH today (they are
        # simply absent, so every consumer falls back exactly as before)
        # and the moment the gateway starts forwarding them (see the
        # flagged follow-up for empyralis-gateway/src/llm/codex-app-
        # server.ts + runtime.ts) — no second change needed here.
        #
        # ABSENT AND EMPTY ARE DIFFERENT FACTS AND MUST NOT COLLAPSE (2026-08-20).
        # `None` here means "the gateway did not tell us" — which is the
        # MAJORITY live state, not an edge case: every fleet box still running
        # a gateway built before these fields were forwarded sends no key at
        # all. `[]` means the model positively reports no selectable levels.
        # The picker renders a static fallback ladder for the first and NO
        # control at all for the second, so flattening them would either take
        # the picker away from most of the fleet or leave a dead control on a
        # model that implements none of its options.
        supported_efforts_raw = m.get("supported_reasoning_efforts")
        supported_efforts: Optional[List[Dict[str, str]]] = None
        if isinstance(supported_efforts_raw, list):
            parsed: List[Dict[str, str]] = []
            for entry in supported_efforts_raw:
                if not isinstance(entry, dict):
                    continue
                # The gateway relays codex's own camelCase key verbatim inside
                # each entry (runtime.ts snake_cases only the OUTER field), so
                # both spellings are accepted here on purpose — this is the
                # seam where the two halves of the chain meet.
                effort = str(entry.get("reasoning_effort") or entry.get("reasoningEffort") or "").strip()
                if not effort:
                    continue
                parsed.append({
                    "reasoning_effort": effort,
                    "description": str(entry.get("description") or ""),
                })
            # Something was there and nothing survived parsing: that is
            # "unintelligible", not "the model says no".
            supported_efforts = None if (not parsed and supported_efforts_raw) else parsed
        models.append({
            "id": model_id,
            "display_name": str(m.get("display_name") or model_id),
            "description": str(m.get("description") or ""),
            "hidden": bool(m.get("hidden")),
            "is_default": bool(m.get("is_default")),
            "default_reasoning_effort": str(m.get("default_reasoning_effort") or "").strip() or None,
            "supported_reasoning_efforts": supported_efforts,
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
