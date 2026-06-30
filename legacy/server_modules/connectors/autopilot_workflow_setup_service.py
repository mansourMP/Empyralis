from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus


class AutopilotWorkflowSetupService:
    def __init__(
        self,
        *,
        workflow_api_url: str,
        runtime_url: str,
        web_url: str,
        init_runtime: Callable[[], None],
        classify_automation_intent: Callable[[str], str],
        list_vault_connectors: Callable[[str], List[Dict[str, Any]]],
        http_json_request: Callable[..., Dict[str, Any]],
        runtime_api_headers: Callable[[], Dict[str, str]],
        camera_setup_service: Callable[[], Any],
    ) -> None:
        self.workflow_api_url = str(workflow_api_url or "").strip().rstrip("/")
        self.runtime_url = str(runtime_url or "").strip().rstrip("/")
        self.web_url = str(web_url or "").strip().rstrip("/")
        self.init_runtime = init_runtime
        self.classify_automation_intent = classify_automation_intent
        self.list_vault_connectors = list_vault_connectors
        self.http_json_request = http_json_request
        self.runtime_api_headers = runtime_api_headers
        self.camera_setup_service = camera_setup_service

    def workspace_connector_flags(self, workspace_id: str) -> Dict[str, bool]:
        self.init_runtime()
        try:
            rows = self.list_vault_connectors(str(workspace_id or "").strip() or "default")
        except Exception:
            rows = []
        flags = {"telegram": False, "email": False}
        if not isinstance(rows, list):
            return flags
        for row in rows:
            if not isinstance(row, dict):
                continue
            connector = str(row.get("connector") or row.get("provider") or "").strip().lower()
            if connector == "telegram_bot":
                flags["telegram"] = True
            if connector in {"google_workspace", "microsoft_365"}:
                flags["email"] = True
        return flags

    def primary_email_connector_id(self, workspace_id: str) -> Optional[str]:
        self.init_runtime()
        try:
            rows = self.list_vault_connectors(str(workspace_id or "").strip() or "default")
        except Exception:
            rows = []
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            connector = str(row.get("connector") or row.get("provider") or "").strip().lower()
            if connector in {"google_workspace", "microsoft_365"}:
                connector_id = str(row.get("id") or "").strip()
                if connector_id:
                    return connector_id
        return None

    def email_summary_workflow_definition(self, target_label: str, *, telegram_connected: bool) -> Dict[str, Any]:
        label = str(target_label or "").strip() or "Inbox"
        return {
            "nodes": [
                {
                    "id": "trigger-daily",
                    "type": "trigger",
                    "position": {"x": 265, "y": 50},
                    "data": {"label": "Daily Summary", "triggerType": "schedule"},
                },
                {
                    "id": "agent-summary",
                    "type": "agent",
                    "position": {"x": 265, "y": 220},
                    "data": {
                        "label": "Inbox Summary",
                        "modelId": "gpt-4o",
                        "prompt": f"Summarize important messages from {label} and highlight what needs action.",
                        "tools": ["email"],
                        "provider": "openai",
                        "role": "Summary",
                        "duty": f"Review {label} and produce a concise daily summary.",
                        "status": "ready",
                        "description": f"{label} daily summary",
                    },
                },
                {
                    "id": "action-summary",
                    "type": "action",
                    "position": {"x": 265, "y": 390},
                    "data": {
                        "label": "Send Telegram" if telegram_connected else "Write Report",
                        "actionType": "send_telegram" if telegram_connected else "write_file",
                    },
                },
            ],
            "edges": [
                {
                    "id": "edge-daily-summary",
                    "source": "trigger-daily",
                    "target": "agent-summary",
                    "sourceHandle": "bottom",
                    "targetHandle": "top",
                    "type": "smoothstep",
                },
                {
                    "id": "edge-summary-action",
                    "source": "agent-summary",
                    "target": "action-summary",
                    "sourceHandle": "bottom",
                    "targetHandle": "top",
                    "type": "smoothstep",
                },
            ],
            "meta": {"automationMode": "scheduled", "created_from": "email_summary_chat_bridge"},
        }

    def lead_followup_workflow_definition(
        self,
        flow_label: str,
        *,
        email_connected: bool,
        telegram_connected: bool,
    ) -> Dict[str, Any]:
        label = str(flow_label or "").strip() or "Leads"
        action_type = "send_email" if email_connected else "send_telegram" if telegram_connected else "write_file"
        action_label = "Send Email" if email_connected else "Send Telegram" if telegram_connected else "Write Draft"
        return {
            "nodes": [
                {
                    "id": "trigger-followup",
                    "type": "trigger",
                    "position": {"x": 265, "y": 50},
                    "data": {"label": "Follow-up Review", "triggerType": "schedule"},
                },
                {
                    "id": "agent-followup",
                    "type": "agent",
                    "position": {"x": 265, "y": 220},
                    "data": {
                        "label": "Lead Follow-up",
                        "modelId": "gpt-4o",
                        "prompt": f"Review {label} and draft concise follow-up messages for the leads that need attention.",
                        "tools": ["crm"],
                        "provider": "openai",
                        "role": "Follow-up",
                        "duty": f"Prepare next-step follow-up messages for {label}.",
                        "status": "ready",
                        "description": f"{label} lead follow-up",
                    },
                },
                {
                    "id": "action-followup",
                    "type": "action",
                    "position": {"x": 265, "y": 390},
                    "data": {"label": action_label, "actionType": action_type},
                },
            ],
            "edges": [
                {
                    "id": "edge-followup-agent",
                    "source": "trigger-followup",
                    "target": "agent-followup",
                    "sourceHandle": "bottom",
                    "targetHandle": "top",
                    "type": "smoothstep",
                },
                {
                    "id": "edge-followup-action",
                    "source": "agent-followup",
                    "target": "action-followup",
                    "sourceHandle": "bottom",
                    "targetHandle": "top",
                    "type": "smoothstep",
                },
            ],
            "meta": {"automationMode": "scheduled", "created_from": "lead_followup_chat_bridge"},
        }

    def create_published_workflow_record(self, name: str, description: str, definition: Dict[str, Any]) -> Optional[str]:
        create_res = self.http_json_request(
            f"{self.workflow_api_url}/workflows?workspaceId=default",
            method="POST",
            headers={"Content-Type": "application/json"},
            payload={
                "name": name,
                "description": description,
                "definition": definition,
            },
            timeout=20,
        )
        create_json = create_res.get("json") if isinstance(create_res.get("json"), dict) else {}
        workflow_id = str(create_json.get("id") or "").strip()
        if workflow_id:
            try:
                self.http_json_request(
                    f"{self.workflow_api_url}/workflows/{quote_plus(workflow_id)}/publish",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    timeout=20,
                )
            except Exception:
                pass
        return workflow_id or None

    def create_email_summary_visibility_record(self, target_label: str, *, telegram_connected: bool) -> Optional[str]:
        label = str(target_label or "").strip() or "Inbox"
        return self.create_published_workflow_record(
            f"Summarize {label} Daily",
            f"Daily inbox summary for {label}",
            self.email_summary_workflow_definition(label, telegram_connected=telegram_connected),
        )

    def create_email_summary_execution_schedules(self, workspace_id: str, target_label: str) -> int:
        connector_id = self.primary_email_connector_id(workspace_id)
        if not connector_id:
            return 0
        label = str(target_label or "").strip() or "Inbox"
        run_request = {
            "engine": "orion",
            "workspace_id": str(workspace_id or "").strip() or "default",
            "user_goal": f"Summarize the most recent emails from {label} and highlight what needs attention.",
            "agent_role": "support",
            "metadata": {
                "source": "scheduled",
                "execution_target": "cloud",
                "outcome_pack": "customer-ops-autopilot",
                "outcome_pack_label": "Client Workflow Autopilot",
                "outcome_scope": ["Inbox triage"],
                "connector_credential_id": connector_id,
                "pack_inputs": {"inbox": "", "leads": "", "slots": ""},
                "automation_kind": "email_summary_recent",
                "automation_label": label,
                "summary_limit": 5,
            },
        }
        created = 0
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"):
            self.http_json_request(
                f"{self.runtime_url}/schedules/weekly",
                method="POST",
                headers=self.runtime_api_headers(),
                payload={
                    "name": f"Email Summary · {label} · {day}",
                    "workspace_id": str(workspace_id or "").strip() or "default",
                    "enabled": True,
                    "day_of_week": day,
                    "time_hhmm": "08:00",
                    "timezone": "local",
                    "run_request": run_request,
                },
                timeout=20,
            )
            created += 1
        return created

    def create_lead_followup_execution_schedules(self, workspace_id: str, flow_label: str) -> int:
        connector_id = self.primary_email_connector_id(workspace_id)
        if not connector_id:
            return 0
        label = str(flow_label or "").strip() or "Leads"
        run_request = {
            "engine": "orion",
            "workspace_id": str(workspace_id or "").strip() or "default",
            "user_goal": f"Review the most recent leads from {label} and draft the next outbound follow-ups.",
            "agent_role": "support",
            "metadata": {
                "source": "scheduled",
                "execution_target": "cloud",
                "outcome_pack": "customer-ops-autopilot",
                "outcome_pack_label": "Client Workflow Autopilot",
                "outcome_scope": ["Lead follow-up"],
                "connector_credential_id": connector_id,
                "pack_inputs": {"inbox": "", "leads": "", "slots": ""},
                "automation_kind": "lead_followup_recent",
                "automation_label": label,
                "summary_limit": 5,
            },
        }
        created = 0
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"):
            self.http_json_request(
                f"{self.runtime_url}/schedules/weekly",
                method="POST",
                headers=self.runtime_api_headers(),
                payload={
                    "name": f"Lead Follow-up · {label} · {day}",
                    "workspace_id": str(workspace_id or "").strip() or "default",
                    "enabled": True,
                    "day_of_week": day,
                    "time_hhmm": "09:00",
                    "timezone": "local",
                    "run_request": run_request,
                },
                timeout=20,
            )
            created += 1
        return created

    def create_lead_followup_visibility_record(
        self,
        flow_label: str,
        *,
        email_connected: bool,
        telegram_connected: bool,
    ) -> Optional[str]:
        label = str(flow_label or "").strip() or "Leads"
        return self.create_published_workflow_record(
            f"Follow up {label}",
            f"Lead follow-up automation for {label}",
            self.lead_followup_workflow_definition(
                label,
                email_connected=email_connected,
                telegram_connected=telegram_connected,
            ),
        )

    def email_summary_completion_text(
        self,
        target_label: str,
        *,
        schedule_count: int,
        email_connected: bool,
        workflow_id: Optional[str] = None,
    ) -> str:
        label = str(target_label or "").strip() or "Inbox"
        is_active = schedule_count > 0
        lines = [
            f"Done. Your {label} daily summary is {'active' if is_active else 'ready'}.",
            "It will run every morning and add results to your activity feed." if is_active else (
                "Finish setup to run daily summaries automatically." if email_connected else "Connect Google Workspace or Microsoft 365 to start daily summaries."
            ),
            f"Open automations: {self.web_url}/workflows",
        ]
        if workflow_id:
            lines.append(f"Open automation: {self.web_url}/workflows/{workflow_id}")
        if not email_connected:
            lines.append(f"Connect email → {self.web_url}/credentials")
        if email_connected and not is_active:
            lines.append(f"Finish setup → {self.web_url}/setup")
        return "\n\n".join(lines)

    def lead_followup_completion_text(
        self,
        flow_label: str,
        *,
        schedule_count: int,
        email_connected: bool,
        workflow_id: Optional[str] = None,
    ) -> str:
        label = str(flow_label or "").strip() or "Leads"
        is_active = schedule_count > 0
        lines = [
            f"Done. Your {label} follow-up automation is {'active' if is_active else 'ready'}.",
            "It will review recent leads every morning and prepare outbound follow-ups." if is_active else (
                "Finish setup to run follow-ups automatically." if email_connected else "Connect an email account to send follow-ups automatically."
            ),
            f"Open automations: {self.web_url}/workflows",
        ]
        if workflow_id:
            lines.append(f"Open automation: {self.web_url}/workflows/{workflow_id}")
        if not email_connected:
            lines.append(f"Connect email → {self.web_url}/credentials")
        if email_connected and not is_active:
            lines.append(f"Finish setup → {self.web_url}/setup")
        return "\n\n".join(lines)

    def handle_telegram_guided_automation_setup(
        self,
        *,
        workspace_id: str,
        chat_id: str,
        message_text: str,
        enabled: bool,
    ) -> Dict[str, Any]:
        return self.camera_setup_service().handle_guided_automation_setup(
            workspace_id=workspace_id,
            chat_id=chat_id,
            message_text=message_text,
            enabled=enabled,
            classify_intent=self.classify_automation_intent,
            workspace_connector_flags=self.workspace_connector_flags,
            create_email_summary_visibility_record=self.create_email_summary_visibility_record,
            create_email_summary_execution_schedules=self.create_email_summary_execution_schedules,
            email_summary_completion_text=self.email_summary_completion_text,
            create_lead_followup_visibility_record=self.create_lead_followup_visibility_record,
            create_lead_followup_execution_schedules=self.create_lead_followup_execution_schedules,
            lead_followup_completion_text=self.lead_followup_completion_text,
        )
