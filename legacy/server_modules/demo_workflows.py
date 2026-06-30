from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from server_modules import artifact_service, run_state_repository, runs_output, runtime_run_access_service
from server_modules.run_service import (
    build_server_run_execution_services,
    execute_run_start_request_via_turn_runtime,
)
from server_modules.runtime_models import RunStartRequest


FIRST_RUN_DEMO_ID = "desktop_first_run_screenshot_description"


def _late_server_export(name: str) -> Any:
    import server as _server

    return getattr(_server, name)


def build_first_run_demo_request(*, workspace_id: str) -> RunStartRequest:
    return RunStartRequest(
        engine="orion",
        workflow_id=None,
        workspace_id=str(workspace_id or "default").strip() or "default",
        user_goal="Capture the current screen and summarize what is visible locally.",
        business_plan="Prove the local agent can see the screen, preserve a screenshot artifact, and extract local text without leaving the desktop shell.",
        agent_role="operator",
        metadata={
            "outcome_pack": "local-execution-v1",
            "execution_target": "local_companion",
            "trust_mode": "auto",
            "demo_workflow_id": FIRST_RUN_DEMO_ID,
            "pack_inputs": {
                "continue_on_error": True,
                "operations": [
                    {
                        "tool": "screenshot.capture",
                        "monitor": "primary",
                        "summary": "Capture the current desktop screen.",
                    },
                    {
                        "tool": "computer_control",
                        "action": "ocr",
                        "summary": "Extract visible on-screen text locally.",
                    },
                ],
            },
        },
    )


async def start_first_run_demo(*, workspace_id: str, current_user: Any) -> Dict[str, Any]:
    services = build_server_run_execution_services(
        stamp_request_owner=runtime_run_access_service.stamp_request_owner,
        late_server_export=_late_server_export,
    )
    result = await execute_run_start_request_via_turn_runtime(
        build_first_run_demo_request(workspace_id=workspace_id),
        current_user=current_user,
        stamp_request_owner_fn=runtime_run_access_service.stamp_request_owner,
        services=services,
    )
    run_id = str(result.get("run_id") or "").strip()
    if not run_id:
        raise HTTPException(status_code=500, detail="First-run demo did not return a run id.")
    return {
        "ok": True,
        "run_id": run_id,
        "status": str(result.get("status") or "queued").strip().lower() or "queued",
    }


def _snapshot_for_run(run_id: str) -> Dict[str, Any]:
    try:
        return runs_output._get_replay_payload(run_id)
    except HTTPException:
        live = run_state_repository.sync_get_live_run(run_id)
        if isinstance(live, dict):
            return runs_output._serialize_run_snapshot(run_id, live)
        raise


def _artifact_payload(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    result_data = snapshot.get("result_data") if isinstance(snapshot.get("result_data"), dict) else {}
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    artifacts = outputs.get("artifacts") if isinstance(outputs.get("artifacts"), list) else []
    preferred = None
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if str(artifact.get("kind") or "").strip().lower() in {"screenshot", "image"}:
            preferred = artifact
            break
    if preferred is None:
        preferred = next((artifact for artifact in artifacts if isinstance(artifact, dict)), None)
    if not isinstance(preferred, dict):
        return None
    reference = str(
        preferred.get("uri")
        or preferred.get("uri_or_path")
        or preferred.get("file_path")
        or preferred.get("path")
        or ""
    ).strip()
    metadata = artifact_service.load_artifact_metadata(reference) if reference else None
    merged = dict(metadata or {})
    merged.update(preferred)
    location = str(
        merged.get("uri")
        or merged.get("uri_or_path")
        or merged.get("file_path")
        or merged.get("path")
        or reference
    ).strip()
    if not location:
        return None
    artifact_id = str(merged.get("artifact_id") or "").strip() or artifact_service.artifact_id_from_reference(location)
    return {
        "artifact_id": artifact_id or None,
        "label": str(merged.get("label") or merged.get("file_name") or "Screenshot").strip() or "Screenshot",
        "uri": location,
        "content_type": merged.get("content_type") or merged.get("mime_type"),
        "byte_size": merged.get("byte_size"),
        "kind": str(merged.get("kind") or "artifact").strip().lower() or "artifact",
    }


def _ocr_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    result_data = snapshot.get("result_data") if isinstance(snapshot.get("result_data"), dict) else {}
    outputs = result_data.get("outputs") if isinstance(result_data.get("outputs"), dict) else {}
    actions = outputs.get("actions") if isinstance(outputs.get("actions"), list) else []
    errors = outputs.get("errors") if isinstance(outputs.get("errors"), list) else []
    for action in reversed(actions):
        if not isinstance(action, dict):
            continue
        if str(action.get("computer_action") or "").strip().lower() != "ocr":
            continue
        text = str(action.get("text_preview") or "").strip()
        return {
            "status": "completed",
            "text": text,
            "error": None,
        }
    for item in errors:
        if not isinstance(item, dict):
            continue
        if str(item.get("tool") or "").strip().lower() != "computer_control":
            continue
        return {
            "status": "failed",
            "text": "",
            "error": str(item.get("message") or "Local OCR failed.").strip() or "Local OCR failed.",
        }
    return {
        "status": "unavailable",
        "text": "",
        "error": None,
    }


def _deterministic_description(*, ocr_text: str, ocr_status: str, artifact_ready: bool, ocr_error: Optional[str]) -> str:
    preview = " ".join(str(ocr_text or "").split())
    if preview:
        bounded = preview[:280].rstrip()
        if len(preview) > 280:
            bounded += "…"
        return f"Empyralis captured your screen and read visible local text. Preview: {bounded}"
    if ocr_status == "failed":
        return (
            "Empyralis captured your screen locally, but text extraction is not available on this machine yet. "
            f"{str(ocr_error or '').strip()}".strip()
        )
    if artifact_ready:
        return "Empyralis captured your screen locally. No readable text was detected in the current view."
    return "Empyralis started the demo, but the screenshot artifact is not ready yet."


def first_run_demo_status(run_id: str) -> Dict[str, Any]:
    token = str(run_id or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="run_id is required.")
    snapshot = _snapshot_for_run(token)
    status = str(snapshot.get("status") or "unknown").strip().lower() or "unknown"
    artifact = _artifact_payload(snapshot)
    ocr = _ocr_payload(snapshot)
    description = _deterministic_description(
        ocr_text=str(ocr.get("text") or "").strip(),
        ocr_status=str(ocr.get("status") or "unavailable").strip().lower() or "unavailable",
        artifact_ready=isinstance(artifact, dict),
        ocr_error=str(ocr.get("error") or "").strip() or None,
    )
    terminal = status in {"completed", "failed", "timeout", "waiting_for_input"}
    if status in {"queued", "queued_local", "starting", "running", "running_local"}:
        terminal = False
    return {
        "ok": True,
        "run_id": token,
        "workspace_id": str(snapshot.get("workspace_id") or "default").strip() or "default",
        "status": status,
        "terminal": terminal,
        "summary": str(snapshot.get("result_summary") or "").strip() or description,
        "description": description,
        "artifact": artifact,
        "ocr": ocr,
        "run_status": status,
        "failure_message": str(snapshot.get("result_summary") or "").strip() if status in {"failed", "timeout"} else None,
    }
