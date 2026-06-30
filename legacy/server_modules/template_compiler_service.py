from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from server_modules.builder_schema import normalize_builder_generated_workflow
from server_modules import agent_registry_repository


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _normalize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    code = str(issue.get("code") or "").strip()
    message = str(issue.get("message") or "").strip()
    node_id = str(issue.get("node_id") or issue.get("nodeId") or "").strip() or None
    warning_markers = (
        "requires approval",
        "prefer",
        "warn",
        "reviewed",
        "session-backed",
        "interactive",
    )
    level = "warning" if any(marker in message.lower() or marker in code.lower() for marker in warning_markers) else "error"
    return {
        "code": code or "workflow_issue",
        "message": message or "Workflow validation issue.",
        "level": level,
        "nodeId": node_id,
    }


def build_validation_summary(definition: Dict[str, Any]) -> Dict[str, Any]:
    draft_normalized = normalize_builder_generated_workflow(definition, for_publish=False)
    draft_issues = [
        _normalize_issue(issue)
        for issue in (_list(draft_normalized.get("issues")))
        if isinstance(issue, dict)
    ]

    publish_issues: List[Dict[str, Any]] = []
    try:
        publish_normalized = normalize_builder_generated_workflow(definition, for_publish=True)
        publish_issues = [
            _normalize_issue(issue)
            for issue in (_list(publish_normalized.get("issues")))
            if isinstance(issue, dict)
        ]
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Workflow cannot be published yet."
        publish_issues = [{
            "code": "publish_blocked",
            "message": detail,
            "level": "error",
            "nodeId": None,
        }]

    draft_error_count = sum(1 for issue in draft_issues if issue.get("level") == "error")
    publish_error_count = sum(1 for issue in publish_issues if issue.get("level") == "error")
    draft_warning_count = sum(1 for issue in draft_issues if issue.get("level") == "warning")
    publish_warning_count = sum(1 for issue in publish_issues if issue.get("level") == "warning")
    return {
        "draftIssues": draft_issues,
        "publishIssues": publish_issues,
        "hasDraftErrors": draft_error_count > 0,
        "hasPublishErrors": publish_error_count > 0,
        "draftErrorCount": draft_error_count,
        "publishErrorCount": publish_error_count,
        "draftWarningCount": draft_warning_count,
        "publishWarningCount": publish_warning_count,
    }


def _empty_validation_summary() -> Dict[str, Any]:
    return {
        "draftIssues": [],
        "publishIssues": [],
        "hasDraftErrors": False,
        "hasPublishErrors": False,
        "draftErrorCount": 0,
        "publishErrorCount": 0,
        "draftWarningCount": 0,
        "publishWarningCount": 0,
    }


def _execution_target_from_runtime_profile(runtime_profile: Dict[str, Any]) -> str:
    default_target = str(runtime_profile.get("default_execution_target") or "").strip().lower()
    if default_target in {"auto", "cloud", "local_companion"}:
        return default_target
    runtime_class = str(runtime_profile.get("runtime_class") or "").strip().lower()
    if runtime_class in {"desktop_companion", "mobile_runtime"}:
        return "local_companion"
    if runtime_class in {"cloud_worker", "self_hosted_business_node", "self_hosted_worker", "enterprise_node"}:
        return "cloud"
    return "auto"


def _source_workflow_definition(bundle: Dict[str, Any]) -> Dict[str, Any]:
    version = _dict(bundle.get("agent_definition_version"))
    manifest = _dict(version.get("manifest"))
    workflow_definition = _dict(manifest.get("workflow_definition"))
    if workflow_definition:
        return workflow_definition
    return {
        "version": "empyralist.workflow.v2",
        "nodes": [],
        "edges": [],
        "meta": {},
    }


def _outcome_pack_for_bundle(bundle: Dict[str, Any]) -> Optional[str]:
    version = _dict(bundle.get("agent_definition_version"))
    manifest = _dict(version.get("manifest"))
    policy_manifest = _dict(version.get("policy_manifest"))
    install_metadata = _dict(bundle.get("metadata"))
    for source in (
        manifest,
        policy_manifest,
        install_metadata,
    ):
        token = str(source.get("outcome_pack") or "").strip().lower()
        if token:
            return token
    return None


def _action_policy_from_tool_toggles(tool_toggles: Dict[str, Any]) -> Dict[str, Any]:
    allow_actions = sorted(
        key for key, enabled in tool_toggles.items() if str(key or "").strip() and bool(enabled)
    )
    blocked_actions = sorted(
        key for key, enabled in tool_toggles.items() if str(key or "").strip() and not bool(enabled)
    )
    return {
        "allow_actions": allow_actions,
        "blocked_actions": blocked_actions,
    }


def compile_install_template(bundle: Dict[str, Any]) -> Dict[str, Any]:
    install = dict(bundle or {})
    agent_definition = _dict(install.get("agent_definition"))
    version = _dict(install.get("agent_definition_version"))
    runtime_profile = _dict(install.get("runtime_profile"))
    tool_toggles = _dict(install.get("tool_toggles"))
    folder_grants = _list(install.get("folder_grants"))
    connector_bindings = _dict(install.get("connector_bindings"))
    policy_context_overrides = _dict(install.get("policy_context_overrides"))
    memory_scope_overrides = _dict(install.get("memory_scope_overrides"))
    source_definition = _source_workflow_definition(install)
    outcome_pack = _outcome_pack_for_bundle(install)
    raw_nodes = _list(source_definition.get("nodes"))
    if raw_nodes:
        normalized_definition = normalize_builder_generated_workflow(source_definition, for_publish=False)
        validation = build_validation_summary(normalized_definition)
        if bool(validation.get("hasDraftErrors")):
            raise HTTPException(status_code=409, detail="Compiled workflow template is invalid.")
    else:
        normalized_definition = {
            "version": "empyralist.workflow.v2",
            "nodes": [],
            "edges": _list(source_definition.get("edges")),
            "meta": _dict(source_definition.get("meta")),
        }
        validation = _empty_validation_summary()

    runtime_label = str(runtime_profile.get("label") or runtime_profile.get("slug") or "").strip()
    execution_target = _execution_target_from_runtime_profile(runtime_profile)
    action_policy = _action_policy_from_tool_toggles(tool_toggles)

    definition_meta = _dict(normalized_definition.get("meta"))
    definition_meta.update(
        {
            "compiled_hidden": True,
            "template_source": "workspace_agent_install",
            "workspace_agent_install_id": install.get("id"),
            "agent_definition_id": install.get("agent_definition_id"),
            "agent_definition_version_id": install.get("agent_definition_version_id"),
            "runtime_profile_id": install.get("runtime_profile_id"),
            "runtime_profile_label": runtime_label or None,
            "execution_target": execution_target,
            "tool_toggles": tool_toggles,
            "folder_grants": folder_grants,
            "connector_bindings": connector_bindings,
        }
    )
    if outcome_pack:
        definition_meta["outcome_pack"] = outcome_pack
    normalized_definition["meta"] = {key: value for key, value in definition_meta.items() if value not in (None, "", [], {})}

    workflow_name = str(install.get("label") or agent_definition.get("name") or "Installed Agent").strip() or "Installed Agent"
    workflow_description = (
        str(agent_definition.get("description") or "").strip()
        or f"Compiled execution artifact for {workflow_name}."
    )
    workflow_metadata = {
        "hidden": True,
        "source": "template_compiler",
        "workspace_agent_install_id": install.get("id"),
        "agent_definition_id": install.get("agent_definition_id"),
        "agent_definition_version_id": install.get("agent_definition_version_id"),
        "runtime_profile_id": install.get("runtime_profile_id"),
        "runtime_profile_label": runtime_label or None,
        "execution_target": execution_target,
        "outcome_pack": outcome_pack,
        "tool_toggles": tool_toggles,
        "folder_grants": folder_grants,
        "connector_bindings": connector_bindings,
        "policy_context_overrides": policy_context_overrides,
        "memory_scope_overrides": memory_scope_overrides,
        "action_policy": action_policy,
    }
    run_metadata = {
        **workflow_metadata,
        "machine_id": str(runtime_profile.get("machine_id") or "").strip() or None,
        "runtime_id": str(runtime_profile.get("runtime_id") or "").strip() or None,
        "execution_target_selected": execution_target,
        "root_folder_uri": str(install.get("root_folder_uri") or runtime_profile.get("root_folder_uri") or "").strip() or None,
    }

    workflow_snapshot = {
        "id": str(install.get("compiled_workflow_id") or "").strip() or None,
        "name": workflow_name,
        "description": workflow_description,
        "status": "compiled",
        "definition": normalized_definition,
        "validation": validation,
        "workflowVersionId": str(install.get("compiled_workflow_version_id") or "").strip() or None,
        "metadata": workflow_metadata,
    }

    return {
        "workflow_name": workflow_name,
        "workflow_description": workflow_description,
        "definition": normalized_definition,
        "validation": validation,
        "workflow_metadata": workflow_metadata,
        "run_metadata": run_metadata,
        "workflow_snapshot": workflow_snapshot,
    }


async def ensure_install_compiled_artifact(
    install_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    compiled_by_user_id: Optional[str] = None,
    force_recompile: bool = False,
) -> Dict[str, Any]:
    bundle = await agent_registry_repository.get_workspace_agent_install_bundle(
        install_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not isinstance(bundle, dict):
        raise HTTPException(status_code=404, detail="Installed agent not found.")
    if not bool(bundle.get("enabled", True)) or str(bundle.get("status") or "active").strip().lower() not in {"active", "published"}:
        raise HTTPException(status_code=409, detail="Installed agent is not active.")

    compiled_workflow_id = str(bundle.get("compiled_workflow_id") or "").strip()
    compiled_workflow_version_id = str(bundle.get("compiled_workflow_version_id") or "").strip()
    if compiled_workflow_id and compiled_workflow_version_id and not force_recompile:
        snapshot = await agent_registry_repository.fetch_workflow_snapshot(
            compiled_workflow_id,
            workflow_version_id=compiled_workflow_version_id,
            tenant_id=str(bundle.get("tenant_id") or "").strip() or None,
            workspace_id=str(bundle.get("workspace_id") or "").strip() or None,
        )
        if isinstance(snapshot, dict):
            compiled = compile_install_template(bundle)
            compiled["workflow_snapshot"] = snapshot
            return {
                "install": bundle,
                "workflow_id": compiled_workflow_id,
                "workflow_version_id": compiled_workflow_version_id,
                "definition": compiled["definition"],
                "validation": compiled["validation"],
                "workflow_metadata": compiled["workflow_metadata"],
                "run_metadata": compiled["run_metadata"],
                "workflow_snapshot": snapshot,
            }

    compiled = compile_install_template(bundle)
    artifact = await agent_registry_repository.create_compiled_workflow_artifact(
        tenant_id=str(bundle.get("tenant_id") or "").strip(),
        workspace_id=str(bundle.get("workspace_id") or "").strip(),
        name=compiled["workflow_name"],
        description=compiled["workflow_description"],
        definition=compiled["definition"],
        validation=compiled["validation"],
        metadata=compiled["workflow_metadata"],
        created_by_user_id=compiled_by_user_id,
    )
    if not isinstance(artifact, dict):
        raise HTTPException(status_code=503, detail="Unable to persist compiled template artifact.")

    artifact_workflow_id = str(artifact.get("id") or "").strip() or None
    artifact_version_id = str(artifact.get("workflowVersionId") or "").strip() or None
    updated_install = await agent_registry_repository.update_workspace_agent_install_compiled_artifact(
        install_id,
        tenant_id=str(bundle.get("tenant_id") or "").strip(),
        workspace_id=str(bundle.get("workspace_id") or "").strip(),
        compiled_workflow_version_id=artifact_version_id,
        metadata={"compiled_workflow_id": artifact_workflow_id},
    )
    return {
        "install": updated_install or bundle,
        "workflow_id": artifact_workflow_id,
        "workflow_version_id": artifact_version_id,
        "definition": compiled["definition"],
        "validation": compiled["validation"],
        "workflow_metadata": compiled["workflow_metadata"],
        "run_metadata": compiled["run_metadata"],
        "workflow_snapshot": artifact,
    }
