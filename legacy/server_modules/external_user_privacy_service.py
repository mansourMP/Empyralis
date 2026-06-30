from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, Optional

from server_modules import control_plane_repository
from server_modules import workspace_config_schema
from server_modules.cloud_cutover_config import CloudCutoverConfigError, resolve_privacy_policy_url
from server_modules.direct_tool_config_service import run_async_tool_call


_PUBLIC_COMMAND_RE = re.compile(r"^\s*/?(?P<command>[a-z_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(?P<remainder>.+))?\s*$", re.IGNORECASE)
_DEFAULT_PRIVACY_COPY = (
    "Privacy policy: {privacy_url}\n"
    "To request deletion of your saved conversation data, send /delete in this chat."
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _default_privacy_policy_url() -> str:
    try:
        return resolve_privacy_policy_url(os.environ)
    except CloudCutoverConfigError:
        if (os.getenv("EMPYRALIS_DEPLOY_ENV") or os.getenv("ORION_ENV") or "").strip().lower() in {
            "prod",
            "production",
            "staging",
        }:
            raise
    candidates = (
        os.getenv("EMPYRALIS_PUBLIC_FRONTEND_ORIGIN"),
        os.getenv("BACKEND_PUBLIC_ORIGIN"),
        os.getenv("BACKEND_PUBLIC_HOST"),
        os.getenv("FRONTEND_ORIGINS"),
    )
    for raw_candidate in candidates:
        token = _normalize_text(raw_candidate)
        if not token:
            continue
        if "," in token:
            token = token.split(",", 1)[0].strip()
        if token:
            return f"{token.rstrip('/')}/privacy"
    return "http://127.0.0.1:3000/privacy"


def _control_plane_call(coro: Any) -> Any:
    try:
        return run_async_tool_call(coro)
    except Exception:
        return None


class ExternalUserPrivacyService:
    def __init__(
        self,
        *,
        control_plane_runner: Callable[[Any], Any] = _control_plane_call,
        privacy_policy_url_resolver: Callable[[], str] = _default_privacy_policy_url,
    ) -> None:
        self.control_plane_runner = control_plane_runner
        self.privacy_policy_url_resolver = privacy_policy_url_resolver

    def _workspace_admin_defaults(self, workspace_id: Any) -> workspace_config_schema.WorkspaceAdminDefaultsConfig:
        clean_workspace_id = _normalize_text(workspace_id)
        if not clean_workspace_id:
            return workspace_config_schema.workspace_admin_defaults_from_metadata({})
        workspace = self.control_plane_runner(control_plane_repository.get_workspace_by_id(clean_workspace_id))
        metadata = _coerce_dict((workspace or {}).get("metadata")) if isinstance(workspace, dict) else {}
        return workspace_config_schema.workspace_admin_defaults_from_metadata(metadata)

    def privacy_policy_url(
        self,
        *,
        workspace_id: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        profile_payload = _coerce_dict(profile)
        explicit = (
            _normalize_text(profile_payload.get("privacy_policy_url"))
            or _normalize_text(profile_payload.get("platform_privacy_policy_url"))
        )
        if explicit:
            return explicit
        resolved_workspace_id = (
            _normalize_text(workspace_id)
            or _normalize_text(profile_payload.get("workspace_id"))
        )
        if resolved_workspace_id:
            configured = _normalize_text(
                self._workspace_admin_defaults(resolved_workspace_id).privacy_policy_url
            )
            if configured:
                return configured
        return _normalize_text(self.privacy_policy_url_resolver()) or _default_privacy_policy_url()

    def is_public_deployed_agent_profile(self, profile: Dict[str, Any]) -> bool:
        return bool(_normalize_text(_coerce_dict(profile).get("deployed_agent_id")))

    def public_command_action(self, profile: Dict[str, Any], raw_message_text: Any) -> Optional[Dict[str, Any]]:
        if not self.is_public_deployed_agent_profile(profile):
            return None
        match = _PUBLIC_COMMAND_RE.match(_normalize_text(raw_message_text))
        if not match:
            return None
        command = _normalize_text(match.group("command")).lower().lstrip("/")
        remainder = _normalize_text(match.group("remainder"))
        if command in {"privacy", "policy"}:
            return {"action": "privacy_policy", "routed": {"command": command}}
        if command in {"delete", "forget", "forgetme", "erase"}:
            return {"action": "privacy_delete_request", "routed": {"note": remainder, "command": command}}
        return None

    def privacy_policy_message(
        self,
        *,
        deployed_agent_name: Optional[str] = None,
        include_delete_hint: bool = True,
        workspace_id: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        label = _normalize_text(deployed_agent_name) or "This assistant"
        privacy_url = self.privacy_policy_url(workspace_id=workspace_id, profile=profile)
        base = f"{label}\n\n{_DEFAULT_PRIVACY_COPY.format(privacy_url=privacy_url)}"
        if not include_delete_hint:
            return base.split("\n", 1)[0] + f"\n\nPrivacy policy: {privacy_url}"
        return base

    def append_privacy_policy_line(
        self,
        text: str,
        *,
        include_delete_hint: bool = False,
        workspace_id: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        body = _normalize_text(text)
        privacy_url = self.privacy_policy_url(workspace_id=workspace_id, profile=profile)
        if privacy_url in body:
            return body
        suffix = f"Privacy: {privacy_url}"
        if include_delete_hint:
            suffix = f"{suffix}\nDelete saved data anytime with /delete."
        return f"{body}\n\n{suffix}".strip()

    def create_channel_deletion_request(
        self,
        *,
        profile: Dict[str, Any],
        workspace_id: str,
        external_user_id: str,
        channel_key: str,
        session_key: Optional[str] = None,
        requested_event_id: Optional[str] = None,
        note: Optional[str] = None,
        sender_username: Optional[str] = None,
        sender_display_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        profile_payload = _coerce_dict(profile)
        deployed_agent_id = _normalize_text(profile_payload.get("deployed_agent_id"))
        tenant_id = _normalize_text(profile_payload.get("tenant_id"))
        resolved_workspace_id = _normalize_text(profile_payload.get("workspace_id")) or _normalize_text(workspace_id)
        if not tenant_id and resolved_workspace_id:
            workspace = self.control_plane_runner(control_plane_repository.get_workspace_by_id(resolved_workspace_id))
            if isinstance(workspace, dict):
                tenant_id = _normalize_text(workspace.get("tenant_id"))
        if not deployed_agent_id or not resolved_workspace_id or not _normalize_text(external_user_id):
            return None
        return self.control_plane_runner(
            control_plane_repository.upsert_external_user_privacy_request(
                tenant_id=tenant_id,
                workspace_id=resolved_workspace_id,
                deployed_agent_id=deployed_agent_id,
                channel_key=_normalize_text(channel_key).lower() or "telegram",
                external_user_id=_normalize_text(external_user_id),
                request_source="channel_command",
                requested_via="telegram_command",
                session_key=_normalize_text(session_key) or None,
                requested_event_id=_normalize_text(requested_event_id) or None,
                status="requested",
                note=_normalize_text(note),
                metadata={
                    "sender_username": _normalize_text(sender_username) or None,
                    "sender_display_name": _normalize_text(sender_display_name) or None,
                },
            )
        )

    async def purge_deployed_agent_external_user_data(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        deployed_agent_id: str,
        channel_key: str,
        external_user_id: str,
        actor_user_id: Optional[str] = None,
        note: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        request = await control_plane_repository.upsert_external_user_privacy_request(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            channel_key=channel_key,
            external_user_id=external_user_id,
            request_source="owner_admin",
            requested_via="admin_delete",
            status="completed",
            note=_normalize_text(note),
            completed_by_user_id=_normalize_text(actor_user_id) or None,
            metadata=metadata,
        )
        deleted_counts = await control_plane_repository.delete_deployed_agent_external_user_data(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            channel_key=channel_key,
            external_user_id=external_user_id,
        )
        audit = await control_plane_repository.append_external_user_privacy_delete_audit(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            deployed_agent_id=deployed_agent_id,
            channel_key=channel_key,
            external_user_id=external_user_id,
            request_id=_normalize_text((request or {}).get("id")) or None,
            actor_user_id=_normalize_text(actor_user_id) or None,
            deleted_channel_event_count=int(deleted_counts.get("deleted_channel_event_count") or 0),
            deleted_memory_count=int(deleted_counts.get("deleted_memory_count") or 0),
            deleted_daily_usage_count=int(deleted_counts.get("deleted_daily_usage_count") or 0),
            deleted_acquisition_touch_count=int(deleted_counts.get("deleted_acquisition_touch_count") or 0),
            metadata=metadata,
        )
        return {
            "request": request,
            "audit": audit,
            "deleted_counts": deleted_counts,
        }


_EXTERNAL_USER_PRIVACY_SERVICE: Optional[ExternalUserPrivacyService] = None


def get_external_user_privacy_service() -> ExternalUserPrivacyService:
    global _EXTERNAL_USER_PRIVACY_SERVICE
    if _EXTERNAL_USER_PRIVACY_SERVICE is None:
        _EXTERNAL_USER_PRIVACY_SERVICE = ExternalUserPrivacyService()
    return _EXTERNAL_USER_PRIVACY_SERVICE
