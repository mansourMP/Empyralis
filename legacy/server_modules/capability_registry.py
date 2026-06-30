from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from server_modules import safe_mode_service


RiskLevel = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    capability_id: str
    display_name: str
    risk_level: RiskLevel
    requires_approval: bool
    reversible: bool
    required_os_permissions: List[str]
    allowed_environments: List[str]
    artifact_outputs: List[str]


def _contract(
    capability_id: str,
    display_name: str,
    *,
    risk_level: RiskLevel,
    requires_approval: bool,
    reversible: bool,
    required_os_permissions: List[str],
    allowed_environments: List[str],
    artifact_outputs: List[str],
) -> CapabilityContract:
    return CapabilityContract(
        capability_id=capability_id,
        display_name=display_name,
        risk_level=risk_level,
        requires_approval=requires_approval,
        reversible=reversible,
        required_os_permissions=list(required_os_permissions),
        allowed_environments=list(allowed_environments),
        artifact_outputs=list(artifact_outputs),
    )


CAPABILITY_REGISTRY: Dict[str, CapabilityContract] = {
    "workflow.trigger.manual": _contract(
        "workflow.trigger.manual",
        "Manual Workflow Trigger",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=[],
    ),
    "workflow.trigger.schedule": _contract(
        "workflow.trigger.schedule",
        "Scheduled Workflow Trigger",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=[],
    ),
    "workflow.trigger.webhook": _contract(
        "workflow.trigger.webhook",
        "Webhook Workflow Trigger",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=[],
    ),
    "workflow.trigger.connector_event": _contract(
        "workflow.trigger.connector_event",
        "Connector Workflow Trigger",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=[],
    ),
    "workflow.trigger.workflow": _contract(
        "workflow.trigger.workflow",
        "Workflow-to-Workflow Trigger",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=[],
    ),
    "workflow.trigger.file_watch": _contract(
        "workflow.trigger.file_watch",
        "File Watch Trigger",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["filesystem"],
        allowed_environments=["local_companion"],
        artifact_outputs=[],
    ),
    "workflow.agent.execute": _contract(
        "workflow.agent.execute",
        "Workflow Agent Step",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["text/plain", "application/json"],
    ),
    "workflow.decision.if_else": _contract(
        "workflow.decision.if_else",
        "Conditional Decision",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.decision.classifier": _contract(
        "workflow.decision.classifier",
        "Classifier Decision",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.decision.field_router": _contract(
        "workflow.decision.field_router",
        "Field Router Decision",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.human.approval": _contract(
        "workflow.human.approval",
        "Human Approval Checkpoint",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.human.review": _contract(
        "workflow.human.review",
        "Human Review Checkpoint",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.human.wait_for_reply": _contract(
        "workflow.human.wait_for_reply",
        "Wait For Reply Checkpoint",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.data.transform": _contract(
        "workflow.data.transform",
        "Transform Data",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.data.compose": _contract(
        "workflow.data.compose",
        "Compose Data",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.data.validate": _contract(
        "workflow.data.validate",
        "Validate Data",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.subflow.call_workflow": _contract(
        "workflow.subflow.call_workflow",
        "Call Subflow",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.loop.for_each": _contract(
        "workflow.loop.for_each",
        "For Each Loop",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.loop.while": _contract(
        "workflow.loop.while",
        "While Loop",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "workflow.loop.repeat": _contract(
        "workflow.loop.repeat",
        "Repeat Loop",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "screenshot.capture": _contract(
        "screenshot.capture",
        "Capture Screenshot",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["screen_recording"],
        allowed_environments=["local_companion", "cloud_computer"],
        artifact_outputs=["image/png"],
    ),
    "computer_control.ocr": _contract(
        "computer_control.ocr",
        "Read Screen Text",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["screen_recording"],
        allowed_environments=["local_companion"],
        artifact_outputs=["text/plain"],
    ),
    "computer_control.move": _contract(
        "computer_control.move",
        "Move Mouse",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["accessibility"],
        allowed_environments=["local_companion"],
        artifact_outputs=[],
    ),
    "computer_control.click": _contract(
        "computer_control.click",
        "Click Screen",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["accessibility"],
        allowed_environments=["local_companion"],
        artifact_outputs=[],
    ),
    "computer_control.type": _contract(
        "computer_control.type",
        "Type Into App",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["accessibility"],
        allowed_environments=["local_companion"],
        artifact_outputs=[],
    ),
    "computer_control.clipboard_read": _contract(
        "computer_control.clipboard_read",
        "Read Clipboard",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["clipboard"],
        allowed_environments=["local_companion"],
        artifact_outputs=["text/plain"],
    ),
    "computer_control.clipboard_write": _contract(
        "computer_control.clipboard_write",
        "Write Clipboard",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["clipboard"],
        allowed_environments=["local_companion"],
        artifact_outputs=[],
    ),
    "computer_control.launch_app": _contract(
        "computer_control.launch_app",
        "Launch Application",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["accessibility"],
        allowed_environments=["local_companion"],
        artifact_outputs=[],
    ),
    "computer_control.notify": _contract(
        "computer_control.notify",
        "Show Notification",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["notifications"],
        allowed_environments=["local_companion"],
        artifact_outputs=[],
    ),
    "computer_control.list_apps": _contract(
        "computer_control.list_apps",
        "List Running Apps",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["accessibility"],
        allowed_environments=["local_companion"],
        artifact_outputs=["application/json"],
    ),
    "computer_control.applescript": _contract(
        "computer_control.applescript",
        "Run AppleScript",
        risk_level="critical",
        requires_approval=True,
        reversible=False,
        required_os_permissions=["automation"],
        allowed_environments=["local_companion"],
        artifact_outputs=["text/plain"],
    ),
    "computer_control.speak": _contract(
        "computer_control.speak",
        "Speak Text",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["audio_output"],
        allowed_environments=["local_companion"],
        artifact_outputs=[],
    ),
    "browser_automation.interactive": _contract(
        "browser_automation.interactive",
        "Interactive Browser Automation",
        risk_level="medium",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["local_companion", "hosted"],
        artifact_outputs=["text/html", "image/png", "application/json"],
    ),
    "shell.execute": _contract(
        "shell.execute",
        "Execute Shell Command",
        risk_level="critical",
        requires_approval=True,
        reversible=False,
        required_os_permissions=["filesystem", "process_execution"],
        allowed_environments=["local_companion"],
        artifact_outputs=["text/plain", "application/json"],
    ),
    "filesystem.write": _contract(
        "filesystem.write",
        "Write File",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["filesystem"],
        allowed_environments=["local_companion"],
        artifact_outputs=["text/plain"],
    ),
    "filesystem.read": _contract(
        "filesystem.read",
        "Read File",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["filesystem"],
        allowed_environments=["local_companion"],
        artifact_outputs=["text/plain"],
    ),
    "filesystem.read_write": _contract(
        "filesystem.read_write",
        "Read Or Write Files",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["filesystem"],
        allowed_environments=["local_companion"],
        artifact_outputs=["text/plain"],
    ),
    "http_request": _contract(
        "http_request",
        "HTTP Request",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["network"],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json", "text/plain"],
    ),
    "connector.action.read": _contract(
        "connector.action.read",
        "Read Connector Data",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json", "text/plain"],
    ),
    "connector.action.write": _contract(
        "connector.action.write",
        "Write Connector Data",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json", "text/plain"],
    ),
    "connector.action.execute": _contract(
        "connector.action.execute",
        "Execute Connector Action",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json", "text/plain"],
    ),
    "send_message": _contract(
        "send_message",
        "Send Message",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "draft_email": _contract(
        "draft_email",
        "Draft Email",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "create_calendar_event": _contract(
        "create_calendar_event",
        "Create Calendar Event",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "spreadsheet_read": _contract(
        "spreadsheet_read",
        "Read Spreadsheet",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json", "text/plain"],
    ),
    "spreadsheet_create": _contract(
        "spreadsheet_create",
        "Create Spreadsheet",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "spreadsheet_update": _contract(
        "spreadsheet_update",
        "Update Spreadsheet",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "spreadsheet_append": _contract(
        "spreadsheet_append",
        "Append Spreadsheet",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "document_create": _contract(
        "document_create",
        "Create Document",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "document_update": _contract(
        "document_update",
        "Update Document",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "presentation_create": _contract(
        "presentation_create",
        "Create Presentation",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "presentation_update": _contract(
        "presentation_update",
        "Update Presentation",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "issue_write": _contract(
        "issue_write",
        "Write Issue Tracker",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "pr_write": _contract(
        "pr_write",
        "Write Pull Request",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "repo_write": _contract(
        "repo_write",
        "Write Repository",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "notion_write": _contract(
        "notion_write",
        "Write Notion",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "storage_write": _contract(
        "storage_write",
        "Write External Storage",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "external_research": _contract(
        "external_research",
        "External Research",
        risk_level="low",
        requires_approval=False,
        reversible=True,
        required_os_permissions=["network"],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json", "text/plain"],
    ),
    "publish_content": _contract(
        "publish_content",
        "Publish Content",
        risk_level="medium",
        requires_approval=True,
        reversible=True,
        required_os_permissions=[],
        allowed_environments=["hosted", "local_companion"],
        artifact_outputs=["application/json"],
    ),
    "code.execute_reviewed": _contract(
        "code.execute_reviewed",
        "Reviewed Code Execution",
        risk_level="critical",
        requires_approval=True,
        reversible=False,
        required_os_permissions=["filesystem", "process_execution"],
        allowed_environments=["local_companion"],
        artifact_outputs=["text/plain", "application/json"],
    ),
    # Compatibility tool ids retained as aliases so existing runtime paths can
    # resolve through the canonical registry during migration.
    "computer_control": _contract(
        "computer_control",
        "Computer Control",
        risk_level="critical",
        requires_approval=True,
        reversible=True,
        required_os_permissions=["accessibility", "screen_recording", "clipboard"],
        allowed_environments=["local_companion"],
        artifact_outputs=["image/png", "text/plain", "application/json"],
    ),
}


_CAPABILITY_ALIASES: Dict[str, str] = {
    "read_write_files": "filesystem.read_write",
    "capture_screenshot": "screenshot.capture",
    "browser_automation": "browser_automation.interactive",
    "execute_shell_command": "shell.execute",
    "browser.interactive": "browser_automation.interactive",
}


_WORKFLOW_NODE_CAPABILITY_IDS: Dict[str, str] = {
    "trigger:manual": "workflow.trigger.manual",
    "trigger:schedule": "workflow.trigger.schedule",
    "trigger:webhook": "workflow.trigger.webhook",
    "trigger:connector_event": "workflow.trigger.connector_event",
    "trigger:workflow": "workflow.trigger.workflow",
    "trigger:file_watch": "workflow.trigger.file_watch",
    "agent:": "workflow.agent.execute",
    "decision:if_else": "workflow.decision.if_else",
    "decision:classifier": "workflow.decision.classifier",
    "decision:field_router": "workflow.decision.field_router",
    "human:approval": "workflow.human.approval",
    "human:review": "workflow.human.review",
    "human:wait_for_reply": "workflow.human.wait_for_reply",
    "data:transform": "workflow.data.transform",
    "data:compose": "workflow.data.compose",
    "data:validate": "workflow.data.validate",
    "subflow:call_workflow": "workflow.subflow.call_workflow",
    "loop:for_each": "workflow.loop.for_each",
    "loop:while": "workflow.loop.while",
    "loop:repeat": "workflow.loop.repeat",
}


_CONNECTOR_ACTION_CAPABILITY_MAP: Dict[str, str] = {
    "send_email": "send_message",
    "send_message": "send_message",
    "send_embed": "send_message",
    "send_dm": "send_message",
    "delete_message": "send_message",
    "post_reply": "send_message",
    "publish_reply": "send_message",
    "send_media": "send_message",
    "update_message": "send_message",
    "upload_file": "send_message",
    "create_issue": "issue_write",
    "comment_on_issue": "issue_write",
    "update_issue": "issue_write",
    "add_comment": "issue_write",
    "create_pull_request": "pr_write",
    "create_or_update_file": "repo_write",
    "create_page": "notion_write",
    "update_page": "notion_write",
    "append_blocks": "notion_write",
    "create_database_item": "notion_write",
    "draft_email": "draft_email",
    "create_calendar_event": "create_calendar_event",
    "create_doc": "document_create",
    "create_document": "document_create",
    "create_sheet": "spreadsheet_create",
    "create_spreadsheet": "spreadsheet_create",
    "upload_drive_file": "filesystem.read_write",
    "http_request": "http_request",
    "signed_webhook": "http_request",
    "delete": "storage_write",
    "move": "storage_write",
    "delete_object": "storage_write",
    "create_bucket": "storage_write",
    "publish_content": "publish_content",
    "external_research": "external_research",
}


_READ_ONLY_CONNECTOR_ACTIONS = {
    "fetch_emails",
    "list_calendar_events",
    "list_drive_files",
    "list_channels",
    "get_history",
    "list_guilds",
    "list_members",
    "get_message_history",
    "list_repos",
    "get_repo",
    "list_issues",
    "list_pull_requests",
    "get_file_content",
    "list_buckets",
    "list_objects",
    "download_file",
    "get_presigned_url",
    "list_folder",
    "search",
    "get_page",
    "query_database",
    "list_teams",
    "list_projects",
    "get_issue",
    "list_commits",
    "list_objects",
    "download_file",
}


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def canonical_capability_id(capability_id: Any) -> str:
    token = str(capability_id or "").strip().lower()
    if not token:
        return ""
    return _CAPABILITY_ALIASES.get(token, token)


def workflow_tool_capability_id(variant: Any, config: Optional[Dict[str, Any]] = None) -> str:
    clean_variant = _normalize_token(variant)
    cfg = dict(config or {})
    if clean_variant == "browser":
        return "browser_automation.interactive"
    if clean_variant == "file":
        return "filesystem.read_write"
    if clean_variant == "shell":
        return "shell.execute"
    if clean_variant == "code":
        return "code.execute_reviewed"
    if clean_variant == "http":
        return "http_request"
    if clean_variant == "document":
        operation = _normalize_token(cfg.get("operation") or cfg.get("mode") or "create")
        file_path = str(cfg.get("file_path") or cfg.get("path") or "").strip().lower()
        if file_path.endswith(".pptx"):
            return "presentation_update" if operation in {"update", "edit", "append"} else "presentation_create"
        return "document_update" if operation in {"update", "edit", "append"} else "document_create"
    if clean_variant == "spreadsheet":
        operation = _normalize_token(cfg.get("operation") or cfg.get("mode") or "read")
        if operation == "append":
            return "spreadsheet_append"
        if operation in {"update", "edit"}:
            return "spreadsheet_update"
        if operation in {"create", "new"}:
            return "spreadsheet_create"
        return "spreadsheet_read"
    if clean_variant == "connector_action":
        action_id = _normalize_token(cfg.get("action_id"))
        connector_id = _normalize_token(cfg.get("connector"))
        if action_id in _CONNECTOR_ACTION_CAPABILITY_MAP:
            return _CONNECTOR_ACTION_CAPABILITY_MAP[action_id]
        if connector_id == "custom_api" and action_id in {"http_request", "signed_webhook"}:
            return "http_request"
        if action_id in _READ_ONLY_CONNECTOR_ACTIONS:
            return "connector.action.read"
        if action_id:
            return "connector.action.write"
    return "connector.action.execute"


def workflow_node_capability_id(
    node_type: Any,
    *,
    variant: Any = None,
    config: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, Any]] = None,
) -> str:
    raw_policy = dict(policy or {})
    existing = _normalize_token(raw_policy.get("capability_id"))
    if existing:
        resolved = canonical_capability_id(existing)
        if resolved in CAPABILITY_REGISTRY:
            return resolved

    clean_type = _normalize_token(node_type)
    clean_variant = _normalize_token(variant)
    if clean_type == "tool":
        return workflow_tool_capability_id(clean_variant, config=config)

    token = _WORKFLOW_NODE_CAPABILITY_IDS.get(f"{clean_type}:{clean_variant}") or _WORKFLOW_NODE_CAPABILITY_IDS.get(f"{clean_type}:")
    if token:
        return token
    return "connector.action.execute"


def resolve_capability(
    capability_id: str,
    *,
    workspace_id: Optional[str] = None,
    machine_id: Optional[str] = None,
    enforce_kill_switch: bool = True,
) -> Optional[CapabilityContract]:
    canonical = canonical_capability_id(capability_id)
    if not canonical:
        return None
    contract = CAPABILITY_REGISTRY.get(canonical)
    if contract is None:
        return None
    if enforce_kill_switch and safe_mode_service.is_capability_disabled(
        canonical,
        workspace_id=workspace_id,
        machine_id=machine_id,
    ):
        return None
    return contract
