import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server_modules.auth import enforce_workspace_access
from server_modules.builder_schema import normalize_builder_generated_workflow
from server_modules.connector_manifests import list_connector_manifests
from server_modules.model_router import call_model, resolve_call_credentials
from server_modules.runtime_common import require_api_key


router = APIRouter()

ALLOWED_NODE_TYPES = {"trigger", "agent", "tool", "decision", "human", "data", "subflow", "loop"}
BUILDER_SYSTEM_PROMPT = """You are a workflow builder AI for Empyralist, an AI operations platform.
Given a plain language description of a business outcome, return ONLY valid JSON.
Structure:
{
  "version": "empyralist.workflow.v2",
  "nodes": [
    {
      "id": "trigger_1",
      "type": "trigger",
      "variant": "manual",
      "label": "Manual trigger",
      "subtitle": "Test only",
      "x": 100,
      "y": 100,
      "config": {
        "test_only": true
      }
    },
    {
      "id": "agent_1",
      "type": "agent",
      "label": "Triage agent",
      "subtitle": "Classifies the request and decides the next move",
      "x": 320,
      "y": 100,
      "config": {
        "identity": {
          "name": "Triage agent",
          "role": "Triage",
          "goal": "Classify the request and decide next action"
        },
        "runtime": {
          "execution_target": "auto"
        }
      }
    }
  ],
  "edges": [
    {"source": "trigger_1", "target": "agent_1"}
  ]
}
Node types allowed: trigger, agent, tool, decision, human, data, subflow, loop.
Trigger variants: connector_event, schedule, webhook, workflow, file_watch, manual.
Tool variants: connector_action, http, browser, file, shell, document, spreadsheet, code.
Decision variants: if_else, classifier, field_router.
Human variants: approval, review, wait_for_reply.
Data variants: transform, compose, validate.
Subflow variants: call_workflow.
Loop variants: for_each, while, repeat.
Manual triggers are test-only.
Published workflows require at least one non-manual trigger, but drafts may use manual.
Code is a tool variant, never a top-level node family.
Memory belongs inside agent config, never as a canvas node.
Loop nodes must provide body.nodes and body.edges for the nested sub-workflow.
Provide concise labels/subtitles and numeric x/y positions suitable for a left-to-right flow.
Prefer a real trigger variant when the prompt clearly implies one.
Use real connector ids, trigger ids, and action ids from the connector registry when possible.
When a deterministic external step is needed, prefer a Tool node over hiding it inside the agent.
If details are unknown, leave explicit placeholders inside config rather than inventing fake IDs.
No markdown, no prose, no backticks."""


class BuilderGenerateRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    profile_id: Optional[str] = None
    workspace_id: Optional[str] = None


def _clean_json_text(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


def _parse_workflow_payload(raw: str) -> Dict[str, Any]:
    try:
        payload = json.loads(_clean_json_text(raw))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Builder returned invalid JSON: {exc.msg}.") from exc
    return normalize_builder_generated_workflow(payload)


def _connector_registry_prompt() -> str:
    parts = []
    manifests = list_connector_manifests().get("items") or []
    for item in manifests:
        if not isinstance(item, dict):
            continue
        connector_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or connector_id).strip() or connector_id
        triggers = ", ".join(
            str(entry.get("id") or "").strip()
            for entry in (item.get("triggers") if isinstance(item.get("triggers"), list) else [])
            if isinstance(entry, dict) and str(entry.get("id") or "").strip()
        ) or "none"
        actions = ", ".join(
            str(entry.get("id") or "").strip()
            for entry in (item.get("actions") if isinstance(item.get("actions"), list) else [])
            if isinstance(entry, dict) and str(entry.get("id") or "").strip()
        ) or "none"
        parts.append(f"- {connector_id} ({label}) | triggers: {triggers} | actions: {actions}")
    return "Connector registry:\n" + "\n".join(parts)


@router.get("/api/v1/builder/manifests/connectors", dependencies=[Depends(require_api_key)])
async def builder_connector_manifests():
    return list_connector_manifests()


@router.post("/api/v1/builder/generate", dependencies=[Depends(require_api_key)])
async def builder_generate(body: BuilderGenerateRequest, current_user=Depends(require_api_key)):
    prompt = str(body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required.")
    body.workspace_id = enforce_workspace_access(current_user, body.workspace_id)

    resolution = resolve_call_credentials(
        model=body.model,
        workspace_id=body.workspace_id,
        profile_id=body.profile_id,
    )
    credentials = resolution.get("credentials")
    provider = str(resolution.get("provider") or "").strip() or "openai"
    resolved_model = str(resolution.get("model") or "").strip()

    if not credentials:
        raise HTTPException(
            status_code=400,
            detail=f"No credential available for provider '{provider}'. Add a provider profile or platform key first.",
        )

    try:
        result = await call_model(
            messages=[
                {"role": "system", "content": BUILDER_SYSTEM_PROMPT},
                {"role": "system", "content": _connector_registry_prompt()},
                {"role": "user", "content": prompt},
            ],
            model=resolved_model,
            provider=provider,
            profile_id=body.profile_id,
            credentials=credentials,
            max_tokens=1500,
            temperature=0.3,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    workflow = _parse_workflow_payload(str(result.get("content") or ""))
    return {
        "workflow": workflow,
        "usage": result.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "model": result.get("model") or resolved_model,
        "provider": result.get("provider") or provider,
    }
