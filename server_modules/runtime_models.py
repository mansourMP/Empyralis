import re
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

_MEMORY_MAX_TEXT_CHARS = 2400
_NORMALIZE_MEMORY_BUCKET: Callable[[Any], Any]
_NORMALIZE_ACTION_ID: Callable[[Any], str]
_PROVIDER_CATALOG: Dict[str, Any] = {}
_CONNECTOR_CATALOG: Dict[str, Any] = {}
_CHANNEL_CATALOG: Dict[str, Any] = {}


def _noop_normalize_memory_bucket(value: Any, required: bool = True) -> Any:
    if value is None and required:
        raise HTTPException(status_code=400, detail="bucket is required.")
    return value


def _default_normalize_action_id(value: Any) -> str:
    return str(value or "").strip().lower()


_NORMALIZE_MEMORY_BUCKET = _noop_normalize_memory_bucket
_NORMALIZE_ACTION_ID = _default_normalize_action_id


def configure_runtime_model_context(
    *,
    memory_max_text_chars: int,
    normalize_memory_bucket: Callable[[Any, bool], Any],
    normalize_action_id: Callable[[Any], str],
    provider_catalog: Dict[str, Any],
    connector_catalog: Dict[str, Any],
    channel_catalog: Optional[Dict[str, Any]] = None,
) -> None:
    global _MEMORY_MAX_TEXT_CHARS
    global _NORMALIZE_MEMORY_BUCKET
    global _NORMALIZE_ACTION_ID
    global _PROVIDER_CATALOG
    global _CONNECTOR_CATALOG
    global _CHANNEL_CATALOG
    _MEMORY_MAX_TEXT_CHARS = int(memory_max_text_chars)
    _NORMALIZE_MEMORY_BUCKET = normalize_memory_bucket
    _NORMALIZE_ACTION_ID = normalize_action_id
    _PROVIDER_CATALOG = provider_catalog
    _CONNECTOR_CATALOG = connector_catalog
    _CHANNEL_CATALOG = channel_catalog or {}


class RunStartRequest(BaseModel):
    engine: str = "orion"
    workflow_id: Optional[str] = None
    workflow_version_id: Optional[str] = None
    workspace_id: Optional[str] = None
    user_goal: Optional[str] = None
    business_plan: Optional[str] = None
    max_iterations: Optional[int] = None
    agent_role: Optional[str] = None
    parent_run_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    credential_id: Optional[str] = None
    agents: Optional[List[dict]] = None
    metadata: Optional[dict] = None


class LocalQueueCleanupRequest(BaseModel):
    workspace_id: str
    older_than_seconds: int = 600
    limit: int = 200
    dry_run: bool = True
    reason: Optional[str] = None

    def validate_fields(self) -> None:
        if not str(self.workspace_id or "").strip():
            raise HTTPException(status_code=400, detail="workspace_id is required.")
        if int(self.older_than_seconds) < 60 or int(self.older_than_seconds) > 604800:
            raise HTTPException(status_code=400, detail="older_than_seconds must be between 60 and 604800.")
        if int(self.limit) < 1 or int(self.limit) > 1000:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 1000.")


class DelegationChildRequest(BaseModel):
    agent_role: str
    user_goal: str
    business_plan: Optional[str] = None
    metadata: Optional[dict] = None


class RunDelegationRequest(BaseModel):
    note: Optional[str] = None
    children: List[DelegationChildRequest]

    def validate_fields(self) -> None:
        if not isinstance(self.children, list) or not self.children:
            raise HTTPException(status_code=400, detail="At least one delegated child run is required.")
        if len(self.children) > 6:
            raise HTTPException(status_code=400, detail="Delegation is limited to 6 child runs per request.")
        for idx, child in enumerate(self.children):
            if not isinstance(child, DelegationChildRequest):
                raise HTTPException(status_code=400, detail=f"children[{idx}] is invalid.")
            if not str(child.agent_role or "").strip():
                raise HTTPException(status_code=400, detail=f"children[{idx}].agent_role is required.")
            if not str(child.user_goal or "").strip():
                raise HTTPException(status_code=400, detail=f"children[{idx}].user_goal is required.")
            if child.metadata is not None and not isinstance(child.metadata, dict):
                raise HTTPException(status_code=400, detail=f"children[{idx}].metadata must be an object.")


class RunAutoDelegationRequest(BaseModel):
    note: Optional[str] = None
    max_children: Optional[int] = 3

    def validate_fields(self) -> None:
        if self.max_children is None:
            self.max_children = 3
        max_children = int(self.max_children)
        if max_children < 1 or max_children > 6:
            raise HTTPException(status_code=400, detail="max_children must be between 1 and 6.")


class RunDelegationRetryRequest(BaseModel):
    failed_run_ids: Optional[List[str]] = None
    note: Optional[str] = None

    def validate_fields(self) -> None:
        if self.failed_run_ids is None:
            return
        if not isinstance(self.failed_run_ids, list):
            raise HTTPException(status_code=400, detail="failed_run_ids must be a list.")
        if len(self.failed_run_ids) > 12:
            raise HTTPException(status_code=400, detail="failed_run_ids cannot exceed 12 items.")
        normalized: List[str] = []
        seen = set()
        for idx, raw in enumerate(self.failed_run_ids):
            token = str(raw or "").strip()
            if not token:
                raise HTTPException(status_code=400, detail=f"failed_run_ids[{idx}] is invalid.")
            if token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        self.failed_run_ids = normalized


class MemoryUpsertRequest(BaseModel):
    text: str
    bucket: str = "session"
    workspace_id: Optional[str] = None
    profile_id: Optional[str] = None
    project_id: Optional[str] = None
    session_key: Optional[str] = None
    source: Optional[str] = None
    retention_days: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    id: Optional[str] = None

    def validate_fields(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise HTTPException(status_code=400, detail="Memory text is required.")
        if len(self.text) > _MEMORY_MAX_TEXT_CHARS:
            raise HTTPException(status_code=400, detail=f"Memory text exceeds {_MEMORY_MAX_TEXT_CHARS} chars.")
        _NORMALIZE_MEMORY_BUCKET(self.bucket, required=True)
        if self.retention_days is not None:
            if int(self.retention_days) < 1 or int(self.retention_days) > 3650:
                raise HTTPException(status_code=400, detail="retention_days must be between 1 and 3650.")
        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise HTTPException(status_code=400, detail="metadata must be an object.")


class MemorySearchRequest(BaseModel):
    query: str
    bucket: Optional[str] = None
    workspace_id: Optional[str] = None
    profile_id: Optional[str] = None
    project_id: Optional[str] = None
    session_key: Optional[str] = None
    k: int = 5

    def validate_fields(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise HTTPException(status_code=400, detail="query is required.")
        if self.bucket is not None:
            _NORMALIZE_MEMORY_BUCKET(self.bucket, required=True)
        if int(self.k) < 1 or int(self.k) > 50:
            raise HTTPException(status_code=400, detail="k must be between 1 and 50.")


class WeeklyScheduleUpsertRequest(BaseModel):
    name: str
    workspace_id: Optional[str] = None
    enabled: bool = True
    day_of_week: str
    time_hhmm: str
    timezone: str = "local"
    wake_mode: str = "now"
    delivery: str = "announce"
    run_request: RunStartRequest

    def validate_fields(self) -> None:
        if not self.name or len(self.name.strip()) < 2:
            raise HTTPException(status_code=400, detail="Schedule name is required.")
        valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        day = str(self.day_of_week or "").strip().lower()
        if day not in valid_days:
            raise HTTPException(status_code=400, detail="day_of_week must be a valid weekday name.")
        if not re.match(r"^\d{2}:\d{2}$", str(self.time_hhmm or "").strip()):
            raise HTTPException(status_code=400, detail="time_hhmm must be in HH:MM format.")
        hh, mm = self.time_hhmm.split(":")
        if int(hh) > 23 or int(mm) > 59:
            raise HTTPException(status_code=400, detail="time_hhmm has invalid hour/minute.")
        tz_mode = str(self.timezone or "").strip().lower()
        if tz_mode not in {"local", "utc"}:
            raise HTTPException(status_code=400, detail="timezone must be local or utc.")
        wake_mode = str(self.wake_mode or "").strip().lower()
        if wake_mode not in {"now", "next-heartbeat"}:
            raise HTTPException(status_code=400, detail="wake_mode must be now or next-heartbeat.")
        delivery = str(self.delivery or "").strip().lower()
        if delivery not in {"announce", "silent"}:
            raise HTTPException(status_code=400, detail="delivery must be announce or silent.")
        if not isinstance(self.run_request, RunStartRequest):
            raise HTTPException(status_code=400, detail="run_request is required.")


class WeeklySchedulePatchRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    day_of_week: Optional[str] = None
    time_hhmm: Optional[str] = None
    timezone: Optional[str] = None
    wake_mode: Optional[str] = None
    delivery: Optional[str] = None
    run_request: Optional[RunStartRequest] = None

    def validate_fields(self) -> None:
        valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        if self.day_of_week is not None and str(self.day_of_week).strip().lower() not in valid_days:
            raise HTTPException(status_code=400, detail="day_of_week must be a valid weekday name.")
        if self.time_hhmm is not None:
            value = str(self.time_hhmm).strip()
            if not re.match(r"^\d{2}:\d{2}$", value):
                raise HTTPException(status_code=400, detail="time_hhmm must be in HH:MM format.")
            hh, mm = value.split(":")
            if int(hh) > 23 or int(mm) > 59:
                raise HTTPException(status_code=400, detail="time_hhmm has invalid hour/minute.")
        if self.timezone is not None:
            tz_mode = str(self.timezone).strip().lower()
            if tz_mode not in {"local", "utc"}:
                raise HTTPException(status_code=400, detail="timezone must be local or utc.")
        if self.wake_mode is not None:
            wake_mode = str(self.wake_mode).strip().lower()
            if wake_mode not in {"now", "next-heartbeat"}:
                raise HTTPException(status_code=400, detail="wake_mode must be now or next-heartbeat.")
            self.wake_mode = wake_mode
        if self.delivery is not None:
            delivery = str(self.delivery).strip().lower()
            if delivery not in {"announce", "silent"}:
                raise HTTPException(status_code=400, detail="delivery must be announce or silent.")
            self.delivery = delivery


class CronScheduleUpsertRequest(BaseModel):
    name: str
    workspace_id: Optional[str] = None
    enabled: bool = True
    cron: str
    timezone: str = "local"
    wake_mode: str = "now"
    delivery: str = "announce"
    run_request: RunStartRequest

    def validate_fields(self) -> None:
        try:
            from croniter import croniter
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"croniter is unavailable: {exc}")
        if not self.name or len(self.name.strip()) < 2:
            raise HTTPException(status_code=400, detail="Schedule name is required.")
        cron_value = str(self.cron or "").strip()
        if not cron_value:
            raise HTTPException(status_code=400, detail="cron is required.")
        if not croniter.is_valid(cron_value):
            raise HTTPException(status_code=400, detail="cron must be a valid cron expression.")
        tz_mode = str(self.timezone or "").strip().lower()
        if tz_mode not in {"local", "utc"}:
            raise HTTPException(status_code=400, detail="timezone must be local or utc.")
        wake_mode = str(self.wake_mode or "").strip().lower()
        if wake_mode not in {"now", "next-heartbeat"}:
            raise HTTPException(status_code=400, detail="wake_mode must be now or next-heartbeat.")
        delivery = str(self.delivery or "").strip().lower()
        if delivery not in {"announce", "silent"}:
            raise HTTPException(status_code=400, detail="delivery must be announce or silent.")
        if not isinstance(self.run_request, RunStartRequest):
            raise HTTPException(status_code=400, detail="run_request is required.")


class CronSchedulePatchRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    cron: Optional[str] = None
    timezone: Optional[str] = None
    wake_mode: Optional[str] = None
    delivery: Optional[str] = None
    run_request: Optional[RunStartRequest] = None

    def validate_fields(self) -> None:
        try:
            from croniter import croniter
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"croniter is unavailable: {exc}")
        if self.cron is not None:
            cron_value = str(self.cron or "").strip()
            if not cron_value:
                raise HTTPException(status_code=400, detail="cron is required.")
            if not croniter.is_valid(cron_value):
                raise HTTPException(status_code=400, detail="cron must be a valid cron expression.")
        if self.timezone is not None:
            tz_mode = str(self.timezone).strip().lower()
            if tz_mode not in {"local", "utc"}:
                raise HTTPException(status_code=400, detail="timezone must be local or utc.")
        if self.wake_mode is not None:
            wake_mode = str(self.wake_mode).strip().lower()
            if wake_mode not in {"now", "next-heartbeat"}:
                raise HTTPException(status_code=400, detail="wake_mode must be now or next-heartbeat.")
            self.wake_mode = wake_mode
        if self.delivery is not None:
            delivery = str(self.delivery).strip().lower()
            if delivery not in {"announce", "silent"}:
                raise HTTPException(status_code=400, detail="delivery must be announce or silent.")
            self.delivery = delivery


class DecisionPayload(BaseModel):
    decision: str
    note: Optional[str] = None
    scope: Optional[str] = None

    def validate_fields(self) -> None:
        if not self.decision or len(self.decision.strip()) == 0:
            raise HTTPException(status_code=400, detail="Decision is required")
        if len(self.decision) > 256:
            raise HTTPException(status_code=400, detail="Decision too long")
        if self.note is not None and len(str(self.note)) > 2000:
            raise HTTPException(status_code=400, detail="Decision note too long")
        if self.scope is not None:
            normalized_scope = str(self.scope or "").strip().lower()
            if normalized_scope != "once":
                raise HTTPException(status_code=400, detail="Decision scope is invalid")
            self.scope = normalized_scope


class ToolPolicyEvaluateRequest(BaseModel):
    tool_ids: List[str]
    trust_mode: Optional[str] = None
    target: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def validate_fields(self) -> None:
        if not isinstance(self.tool_ids, list) or not self.tool_ids:
            raise HTTPException(status_code=400, detail="tool_ids must be a non-empty list.")
        if len(self.tool_ids) > 200:
            raise HTTPException(status_code=400, detail="tool_ids cannot exceed 200 items.")
        for raw in self.tool_ids:
            tool_id = _NORMALIZE_ACTION_ID(raw)
            if not tool_id:
                raise HTTPException(status_code=400, detail=f"Invalid tool_id '{raw}'.")


class ToolContractUpdateRequest(BaseModel):
    enabled: bool


class RuntimeSkillsStateUpsertRequest(BaseModel):
    custom_skills: Optional[List[Dict[str, Any]]] = None
    bindings: Optional[Dict[str, List[str]]] = None

    def validate_fields(self) -> None:
        if self.custom_skills is not None and not isinstance(self.custom_skills, list):
            raise HTTPException(status_code=400, detail="custom_skills must be a list.")
        if self.bindings is not None and not isinstance(self.bindings, dict):
            raise HTTPException(status_code=400, detail="bindings must be an object.")


class SkillInstallRequest(BaseModel):
    source_url: Optional[str] = None
    zip_path: Optional[str] = None

    def validate_fields(self) -> None:
        source_url = str(self.source_url or "").strip()
        zip_path = str(self.zip_path or "").strip()
        if not source_url and not zip_path:
            raise HTTPException(status_code=400, detail="source_url or zip_path is required.")


class SkillUpdateRequest(BaseModel):
    enabled: bool


class SkillPublishRequest(BaseModel):
    name: str
    output_path: Optional[str] = None

    def validate_fields(self) -> None:
        if not str(self.name or "").strip():
            raise HTTPException(status_code=400, detail="name is required.")


class ProviderProfileUpsertRequest(BaseModel):
    id: Optional[str] = None
    provider: str
    label: str
    credential_id: Optional[str] = None
    auth_mode: Optional[str] = None
    workspace_id: Optional[str] = None
    priority: int = 100
    enabled: bool = True
    model: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def validate_fields(self) -> None:
        provider = str(self.provider or "").strip().lower()
        if provider not in _PROVIDER_CATALOG:
            raise HTTPException(status_code=400, detail=f"Unsupported provider '{self.provider}'")
        if not self.label or len(self.label.strip()) < 2:
            raise HTTPException(status_code=400, detail="Profile label is required.")
        if self.credential_id is not None and self.credential_id and len(str(self.credential_id).strip()) < 6:
            raise HTTPException(status_code=400, detail="credential_id is required.")
        if self.priority < 0 or self.priority > 10000:
            raise HTTPException(status_code=400, detail="priority must be between 0 and 10000.")
        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise HTTPException(status_code=400, detail="metadata must be an object.")


class ApprovalResolvePayload(BaseModel):
    decision: str
    note: Optional[str] = None
    approval_scope: Optional[str] = None

    def validate_fields(self) -> None:
        decision = str(self.decision or "").strip().lower()
        if decision not in {
            "proceed",
            "approve",
            "approved",
            "yes",
            "y",
            "continue",
            "ok",
            "hold",
            "reject",
            "rejected",
            "no",
            "n",
            "abort",
            "stop",
            "cancel",
            "escalate",
            "escalated",
        }:
            raise HTTPException(status_code=400, detail="Unsupported decision value.")
        if self.note is not None and len(str(self.note)) > 2000:
            raise HTTPException(status_code=400, detail="note is too long.")
        if self.approval_scope is not None and str(self.approval_scope).strip().lower() not in {"once", "session"}:
            raise HTTPException(status_code=400, detail="Unsupported approval scope.")


class CredentialUpsertRequest(BaseModel):
    label: str
    provider: str
    workspace_id: Optional[str] = None
    mode: str = "byok"
    credentials: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None
    skip_validation: bool = False

    def validate_fields(self) -> None:
        provider = (self.provider or "").strip().lower()
        if provider not in _PROVIDER_CATALOG:
            raise HTTPException(status_code=400, detail=f"Unsupported provider '{self.provider}'")
        if not self.label or len(self.label.strip()) < 2:
            raise HTTPException(status_code=400, detail="Credential label is required.")
        if self.mode not in ["byok", "managed", "vertex"]:
            raise HTTPException(status_code=400, detail="Unsupported credential mode.")
        if not isinstance(self.credentials, dict) or len(self.credentials) == 0:
            raise HTTPException(status_code=400, detail="Credential payload is required.")


class CredentialTestRequest(BaseModel):
    provider: str
    credentials: Dict[str, Any]

    def validate_fields(self) -> None:
        provider = (self.provider or "").strip().lower()
        if provider not in _PROVIDER_CATALOG:
            raise HTTPException(status_code=400, detail=f"Unsupported provider '{self.provider}'")
        if not isinstance(self.credentials, dict) or len(self.credentials) == 0:
            raise HTTPException(status_code=400, detail="Credential payload is required.")


class ConnectorUpsertRequest(BaseModel):
    label: str
    connector: str
    workspace_id: Optional[str] = None
    credentials: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None

    def validate_fields(self) -> None:
        connector = (self.connector or "").strip().lower()
        if connector not in _CONNECTOR_CATALOG and connector not in _CHANNEL_CATALOG:
            raise HTTPException(status_code=400, detail=f"Unsupported connector '{self.connector}'")
        if not self.label or len(self.label.strip()) < 2:
            raise HTTPException(status_code=400, detail="Connector label is required.")
        if not isinstance(self.credentials, dict) or len(self.credentials) == 0:
            raise HTTPException(status_code=400, detail="Connector credentials are required.")


class ConnectorPatchRequest(BaseModel):
    label: Optional[str] = None
    workspace_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def validate_fields(self) -> None:
        if self.label is not None and len(self.label.strip()) < 2:
            raise HTTPException(status_code=400, detail="Connector label must be at least 2 characters.")
        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise HTTPException(status_code=400, detail="Connector metadata must be an object.")


class TelegramSendRequest(BaseModel):
    text: str
    workspace_id: Optional[str] = None
    session_key: Optional[str] = None
    chat_id: Optional[str] = None

    def validate_fields(self) -> None:
        message = str(self.text or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message text is required.")
        if len(message) > 3900:
            raise HTTPException(status_code=400, detail="Message exceeds Telegram safe limit (3900 chars).")


class TelegramAutopilotTestRequest(BaseModel):
    text: str
    workspace_id: Optional[str] = None
    session_key: Optional[str] = None
    chat_id: Optional[str] = None
    connector_id: Optional[str] = None
    sender_id: Optional[str] = None
    timeout_seconds: Optional[int] = None

    def validate_fields(self) -> None:
        message = str(self.text or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message text is required.")
        if len(message) > 3900:
            raise HTTPException(status_code=400, detail="Message exceeds Telegram safe limit (3900 chars).")
        if self.timeout_seconds is not None and int(self.timeout_seconds) <= 0:
            raise HTTPException(status_code=400, detail="timeout_seconds must be greater than 0.")


class VaultRotateKeyRequest(BaseModel):
    new_passphrase: str

    def validate_fields(self) -> None:
        if not self.new_passphrase or len(self.new_passphrase.strip()) < 16:
            raise HTTPException(status_code=400, detail="New passphrase must be at least 16 characters.")


class VaultExportRequest(BaseModel):
    workspace_id: Optional[str] = None
    passphrase: str

    def validate_fields(self) -> None:
        if not self.passphrase or len(self.passphrase.strip()) < 8:
            raise HTTPException(status_code=400, detail="Export passphrase must be at least 8 characters.")


class VaultImportRequest(BaseModel):
    workspace_id: Optional[str] = None
    passphrase: str
    bundle: str
    overwrite: bool = False

    def validate_fields(self) -> None:
        if not self.passphrase or len(self.passphrase.strip()) < 8:
            raise HTTPException(status_code=400, detail="Import passphrase must be at least 8 characters.")
        if not self.bundle or len(self.bundle.strip()) < 20:
            raise HTTPException(status_code=400, detail="Import bundle is required.")
