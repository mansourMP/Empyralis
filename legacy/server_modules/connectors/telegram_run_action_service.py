from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class TelegramRunActionService:
    def __init__(
        self,
        *,
        onboarding_enabled: bool,
        space_status_enabled: bool,
        max_reply_chars: int,
        project_root: Path,
        onboarding_get_state: Callable[[str, str], Dict[str, Any]],
        onboarding_start: Callable[[str, str], Dict[str, Any]],
        onboarding_consume_answer: Callable[[str, str, str], Dict[str, Any]],
        onboarding_prompt: Callable[[int, bool], str],
        profile_get: Callable[[str, str], Dict[str, Any]],
        profile_has_context: Callable[[Dict[str, Any]], bool],
        help_text: Callable[[Dict[str, Any]], str],
        build_goal_with_profile: Callable[[str, Dict[str, Any]], str],
        build_goal_with_attachments: Callable[[str, List[Dict[str, Any]]], str],
        workspace_connector_context: Callable[..., Dict[str, Any]],
        build_goal_with_connector_context: Callable[[str, str], str],
        space_question_via_mcp: Callable[..., Dict[str, Any]],
        installed_skill_query: Callable[..., Dict[str, Any]],
        truncate_one_line: Callable[[str, int], str],
        send_message: Callable[..., Any],
        send_chat_action: Callable[..., Any],
        edit_message: Callable[..., Any],
        record_channel_event: Callable[..., Any],
        run_dispatch_service: Callable[[], Any],
        create_run: Callable[..., Dict[str, Any]],
    ) -> None:
        self.onboarding_enabled = onboarding_enabled
        self.space_status_enabled = space_status_enabled
        self.max_reply_chars = int(max_reply_chars)
        self.project_root = Path(project_root)
        self.onboarding_get_state = onboarding_get_state
        self.onboarding_start = onboarding_start
        self.onboarding_consume_answer = onboarding_consume_answer
        self.onboarding_prompt = onboarding_prompt
        self.profile_get = profile_get
        self.profile_has_context = profile_has_context
        self.help_text = help_text
        self.build_goal_with_profile = build_goal_with_profile
        self.build_goal_with_attachments = build_goal_with_attachments
        self.workspace_connector_context = workspace_connector_context
        self.build_goal_with_connector_context = build_goal_with_connector_context
        self.space_question_via_mcp = space_question_via_mcp
        self.installed_skill_query = installed_skill_query
        self.truncate_one_line = truncate_one_line
        self.send_message = send_message
        self.send_chat_action = send_chat_action
        self.edit_message = edit_message
        self.record_channel_event = record_channel_event
        self.run_dispatch_service = run_dispatch_service
        self.create_run = create_run

    def handle_run_action(
        self,
        *,
        routed: Dict[str, Any],
        message_text: str,
        explicit_run_command: bool,
        profile: Dict[str, Any],
        chat_profile: Dict[str, Any],
        stored_attachments: List[Dict[str, Any]],
        workspace_id: str,
        connector_id: str,
        connector_entry: Dict[str, Any],
        bot_token: str,
        chat_id: str,
        sender_id: str,
        update_id: int,
        inbound_message_id: Optional[str],
        session_key: str,
        trace_id: str,
        source_event_id: str,
    ) -> Dict[str, Any]:
        goal = str(routed.get("goal") or "").strip()
        goal_source = str(routed.get("source") or "").strip().lower()
        onboarding_state = self.onboarding_get_state(workspace_id, chat_id) if self.onboarding_enabled else {"active": False}
        onboarding_active = bool(onboarding_state.get("active"))
        has_profile_context = self.profile_has_context(chat_profile)

        if self.onboarding_enabled and onboarding_active and not explicit_run_command:
            onboarding_result = self.onboarding_consume_answer(workspace_id, chat_id, message_text)
            onboarding_reply = str(onboarding_result.get("reply") or "").strip() or self.onboarding_prompt(
                int(onboarding_state.get("step_index") or 0),
                True,
            )
            action = "onboarding_complete" if bool(onboarding_result.get("completed")) else "onboarding_step"
            if bool(onboarding_result.get("completed")):
                chat_profile = self.profile_get(workspace_id, chat_id)
            self.send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=onboarding_reply,
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
            )
            return {"handled": True, "action": action, "chat_profile": chat_profile, "run_id": ""}

        if (
            self.onboarding_enabled
            and not has_profile_context
            and not explicit_run_command
            and goal_source not in {"quick_goal", "image_only"}
        ):
            onboarding_state = self.onboarding_start(workspace_id, chat_id)
            step_index = int(onboarding_state.get("step_index") or 0)
            action = "onboarding_start_auto"
            self.send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text="Before I start, quick 3-question setup so I can personalize help.\n\n"
                + self.onboarding_prompt(step_index, False),
                workspace_id=workspace_id,
                action=action,
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
            )
            return {"handled": True, "action": action, "chat_profile": chat_profile, "run_id": ""}

        if not goal:
            self.send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=self.help_text(profile),
                workspace_id=workspace_id,
                action="help",
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
            )
            return {"handled": True, "action": "help", "chat_profile": chat_profile, "run_id": ""}

        run_goal = self.build_goal_with_profile(goal, chat_profile)
        run_goal = self.build_goal_with_attachments(run_goal, stored_attachments)
        connector_context = self.workspace_connector_context(
            goal=goal,
            workspace_id=workspace_id,
            current_connector_id=connector_id,
        )
        run_goal = self.build_goal_with_connector_context(
            run_goal,
            str(connector_context.get("prompt_append") or "").strip(),
        )
        mcp_space_query = self.space_question_via_mcp(
            goal,
            enabled=self.space_status_enabled,
            project_root=self.project_root,
        )
        skill_query = self.installed_skill_query(
            goal=goal,
            workspace_id=workspace_id,
            connector_id=connector_id,
            chat_id=chat_id,
            session_key=session_key,
        )
        run_goal = self.build_goal_with_connector_context(
            run_goal,
            str(skill_query.get("prompt_append") or "").strip(),
        )
        direct_response = str(mcp_space_query.get("response") or "").strip() if bool(mcp_space_query.get("handled")) else ""
        if not direct_response:
            direct_response = str(skill_query.get("response") or "").strip() if bool(skill_query.get("handled")) else ""
        if direct_response:
            summary = self.truncate_one_line(direct_response, self.max_reply_chars)
            self.record_channel_event(
                channel="telegram",
                direction="system",
                event_type="skill_reply",
                text=summary,
                workspace_id=workspace_id,
                session_key=session_key,
                session_id=session_key,
                parent_id=inbound_message_id or None,
                action="skill_reply",
                metadata={
                    "connector_id": connector_id,
                    "profile_id": profile.get("id"),
                    "trace_id": trace_id,
                    "source_event_id": source_event_id,
                    "skill_id": str(mcp_space_query.get("skill_id") or skill_query.get("skill_id") or "").strip(),
                },
            )
            self.send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=direct_response,
                workspace_id=workspace_id,
                action="skill_reply",
                connector_id=connector_id,
                parent_message_id=inbound_message_id or None,
                profile=profile,
                trace_id=trace_id,
                source_event_id=source_event_id,
            )
            return {"handled": True, "action": "skill_reply", "chat_profile": chat_profile, "run_id": ""}

        skill_override = routed.get("skill") if isinstance(routed.get("skill"), dict) else None
        run_result = self.run_dispatch_service().dispatch_run_action(
            bot_token=bot_token,
            chat_id=chat_id,
            workspace_id=workspace_id,
            connector_id=connector_id,
            sender_id=sender_id,
            update_id=update_id,
            inbound_message_id=inbound_message_id or None,
            profile_context=chat_profile,
            media_attachments=stored_attachments,
            skill_override=skill_override,
            trace_id=trace_id,
            source_event_id=source_event_id,
            connector_entry=connector_entry,
            connector_context=connector_context,
            session_key=session_key,
            profile=profile,
            action="run",
            goal=run_goal,
            create_run=self.create_run,
            record_channel_event=self.record_channel_event,
            send_chat_action=self.send_chat_action,
            send_message=self.send_message,
            edit_message=self.edit_message,
        )
        return {
            "handled": True,
            "action": "run",
            "chat_profile": chat_profile,
            "run_id": str(run_result.get("run_id") or ""),
        }
