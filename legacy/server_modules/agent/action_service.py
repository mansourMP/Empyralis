from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from server_modules import channel_user_acquisition_service
from server_modules import external_user_privacy_service


class TelegramActionService:
    def __init__(
        self,
        *,
        default_chat_prefix: str,
        onboarding_enabled: bool,
        help_text: Callable[[Dict[str, Any]], str],
        skills_menu_text: Callable[[Dict[str, Any]], str],
        menu_keyboard: Callable[[Dict[str, Any], str], Dict[str, Any]],
        onboarding_prompt: Callable[[int, bool], str],
        onboarding_start: Callable[[str, str], Dict[str, Any]],
        profile_text: Callable[[Dict[str, Any], Dict[str, Any]], str],
        profile_help_text: Callable[[Dict[str, Any]], str],
        profile_set: Callable[[str, str, str, str], Dict[str, Any]],
        profile_clear: Callable[[str, str, str], Dict[str, Any]],
        runtime_status_text: Callable[[str], str],
        approvals_list: Callable[[int, Optional[str]], Dict[str, Any]],
        approvals_text: Callable[[Dict[str, Any], str], str],
        approval_resolve: Callable[[str, bool, str, Optional[str]], Dict[str, Any]],
        approval_result_text: Callable[[Dict[str, Any], bool], str],
        send_message: Callable[..., Any],
        channel_origin: str = "telegram",
    ) -> None:
        self.channel_origin = channel_origin
        self.default_chat_prefix = default_chat_prefix
        self.onboarding_enabled = onboarding_enabled
        self.help_text = help_text
        self.skills_menu_text = skills_menu_text
        self.menu_keyboard = menu_keyboard
        self.onboarding_prompt = onboarding_prompt
        self.onboarding_start = onboarding_start
        self.profile_text = profile_text
        self.profile_help_text = profile_help_text
        self.profile_set = profile_set
        self.profile_clear = profile_clear
        self.runtime_status_text = runtime_status_text
        self.approvals_list = approvals_list
        self.approvals_text = approvals_text
        self.approval_resolve = approval_resolve
        self.approval_result_text = approval_result_text
        self.send_message = send_message

    def should_handle_public_start(self, profile: Dict[str, Any], raw_message_text: str) -> bool:
        return (
            channel_user_acquisition_service.get_channel_user_acquisition_service().public_start_enabled(profile)
            and channel_user_acquisition_service.get_channel_user_acquisition_service().start_payload(raw_message_text) is not None
        )

    def public_action(self, profile: Dict[str, Any], raw_message_text: str) -> Optional[Dict[str, Any]]:
        privacy_service = external_user_privacy_service.get_external_user_privacy_service()
        if self.should_handle_public_start(profile, raw_message_text):
            return {
                "action": "public_start",
                "routed": {
                    "campaign_token": channel_user_acquisition_service.get_channel_user_acquisition_service().start_payload(raw_message_text),
                },
            }
        matched = privacy_service.public_command_action(profile, raw_message_text)
        if isinstance(matched, dict):
            return matched
        return None

    def handle_non_run_action(
        self,
        *,
        action: str,
        routed: Dict[str, Any],
        profile: Dict[str, Any],
        chat_profile: Dict[str, Any],
        workspace_id: str,
        connector_id: str,
        bot_token: str,
        chat_id: str,
        inbound_message_id: str,
        trace_id: str,
        source_event_id: str,
        sender_id: Optional[str] = None,
        sender_username: Optional[str] = None,
        sender_display_name: Optional[str] = None,
        message_text: str = "",
    ) -> Dict[str, Any]:
        handled = False
        updated_profile = chat_profile
        if action == "public_start":
            start_payload = str(routed.get("campaign_token") or "").strip() or None
            prepared = channel_user_acquisition_service.get_channel_user_acquisition_service().prepare_public_start_response(
                profile=profile,
                workspace_id=workspace_id,
                external_user_id=str(sender_id or chat_id or "").strip(),
                sender_username=sender_username,
                sender_display_name=sender_display_name,
                campaign_token=start_payload,
            )
            start_text = external_user_privacy_service.get_external_user_privacy_service().append_privacy_policy_line(
                str(prepared.get("text") or "").strip(),
                include_delete_hint=True,
            )
            self.send_message(
                text=start_text,
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                reply_markup=prepared.get("reply_markup") if isinstance(prepared.get("reply_markup"), dict) else None,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "privacy_policy":
            self.send_message(
                text=external_user_privacy_service.get_external_user_privacy_service().privacy_policy_message(
                    deployed_agent_name=str(profile.get("deployed_agent_name") or profile.get("label") or "").strip() or None,
                    include_delete_hint=True,
                ),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "privacy_delete_request":
            deletion_request = external_user_privacy_service.get_external_user_privacy_service().create_channel_deletion_request(
                profile=profile,
                workspace_id=workspace_id,
                external_user_id=str(sender_id or chat_id or "").strip(),
                channel_key="telegram",
                note=str(routed.get("note") or "").strip() or None,
                sender_username=sender_username,
                sender_display_name=sender_display_name,
            )
            reply = (
                "We recorded your data deletion request. The deployment owner can now remove your saved conversation data for this channel."
                if isinstance(deletion_request, dict) and str(deletion_request.get("id") or "").strip()
                else "The deletion request could not be recorded right now. Please try again shortly."
            )
            reply = external_user_privacy_service.get_external_user_privacy_service().append_privacy_policy_line(reply)
            self.send_message(
                text=reply,
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "help":
            self.send_message(
                text=self.help_text(profile),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action in {"menu", "menu_main", "menu_study", "menu_project", "menu_context", "menu_skills"}:
            menu_id = "main"
            if action.endswith("_study"):
                menu_id = "study"
            elif action.endswith("_project"):
                menu_id = "project"
            elif action.endswith("_context"):
                menu_id = "context"
            elif action.endswith("_skills"):
                menu_id = "skills"
            menu_titles = {
                "main": "Main menu opened.",
                "study": "Work menu opened.",
                "project": "Project menu opened.",
                "context": "Context menu opened.",
                "skills": self.skills_menu_text(profile),
            }
            self.send_message(
                text=menu_titles.get(menu_id, "Menu opened."),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                reply_markup=self.menu_keyboard(profile, menu_id),
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "onboard_start":
            if self.onboarding_enabled:
                onboarding_state = self.onboarding_start(workspace_id, chat_id)
                step_index = int(onboarding_state.get("step_index") or 0)
                if bool(onboarding_state.get("active")):
                    action = "onboarding_start"
                    self.send_message(
                        text=self.onboarding_prompt(step_index, False),
                        workspace_id=workspace_id,
                        action=action,
                        connector_id=connector_id,
                        parent_message_id=inbound_message_id or None,
                        profile=profile,
                        trace_id=trace_id,
                        source_event_id=source_event_id,
                        bot_token=bot_token,
                        chat_id=chat_id,
                    )
                else:
                    action = "onboarding_already_complete"
                    self.send_message(
                        text="Your context is already set. Use `me set ...` anytime to update it.\n\n"
                        + self.profile_text(profile, chat_profile),
                        workspace_id=workspace_id,
                        action=action,
                        connector_id=connector_id,
                        parent_message_id=inbound_message_id or None,
                        profile=profile,
                        trace_id=trace_id,
                        source_event_id=source_event_id,
                        bot_token=bot_token,
                        chat_id=chat_id,
                    )
            else:
                self.send_message(
                    text=self.profile_text(profile, chat_profile),
                    workspace_id=workspace_id,
                    action="profile_show",
                    connector_id=connector_id,
                    parent_message_id=inbound_message_id or None,
                    profile=profile,
                    trace_id=trace_id,
                    source_event_id=source_event_id,
                    bot_token=bot_token,
                    chat_id=chat_id,
                )
                action = "profile_show"
            handled = True
        elif action == "profile_show":
            self.send_message(
                text=self.profile_text(profile, chat_profile),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "profile_help":
            self.send_message(
                text=self.profile_help_text(profile),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "profile_set":
            field_name = str(routed.get("field") or "").strip().lower()
            value = str(routed.get("value") or "").strip()
            if not field_name or not value:
                self.send_message(
                    text=self.profile_help_text(profile),
                    workspace_id=workspace_id,
                    action="profile_help",
                    connector_id=connector_id,
                    parent_message_id=inbound_message_id or None,
                    profile=profile,
                    trace_id=trace_id,
                    source_event_id=source_event_id,
                    bot_token=bot_token,
                    chat_id=chat_id,
                )
                action = "profile_help"
            else:
                updated_profile = self.profile_set(workspace_id, chat_id, field_name, value)
                self.send_message(
                    text=f"Saved {field_name}.\n\n{self.profile_text(profile, updated_profile)}",
                    workspace_id=workspace_id,
                    action=action,
                    connector_id=connector_id,
                    parent_message_id=inbound_message_id or None,
                    profile=profile,
                    trace_id=trace_id,
                    source_event_id=source_event_id,
                    bot_token=bot_token,
                    chat_id=chat_id,
                )
            handled = True
        elif action == "profile_clear":
            field_name = str(routed.get("field") or "").strip().lower()
            updated_profile = self.profile_clear(workspace_id, chat_id, field_name)
            cleared_label = field_name or "all context"
            self.send_message(
                text=f"Cleared {cleared_label}.\n\n{self.profile_text(profile, updated_profile)}",
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "status":
            self.send_message(
                text=self.runtime_status_text(workspace_id),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "approvals":
            limit = int(routed.get("limit") or 5)
            payload = self.approvals_list(limit, workspace_id)
            self.send_message(
                text=self.approvals_text(payload, prefix=str(profile.get("prefix") or self.default_chat_prefix)),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "approve":
            event_id = str(routed.get("event_id") or "").strip()
            note = str(routed.get("note") or "").strip()
            payload = self.approval_resolve(event_id, True, note, workspace_id)
            self.send_message(
                text=self.approval_result_text(payload, True),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True
        elif action == "reject":
            event_id = str(routed.get("event_id") or "").strip()
            note = str(routed.get("note") or "").strip()
            payload = self.approval_resolve(event_id, False, note, workspace_id)
            self.send_message(
                text=self.approval_result_text(payload, False),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            handled = True

        return {"handled": handled, "action": action, "chat_profile": updated_profile}
