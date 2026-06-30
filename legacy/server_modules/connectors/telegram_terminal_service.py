from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional


class TelegramTerminalService:
    def __init__(
        self,
        *,
        normalize_workspace_id: Callable[[Any], str],
        chat_id_from_session_key: Callable[[str], str],
        list_connector_entries: Callable[..., List[Dict[str, Any]]],
        get_secret: Callable[[Dict[str, Any]], Dict[str, Any]],
        resolve_profile: Callable[[Dict[str, Any]], Dict[str, Any]],
        route_message: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        get_chat_profile: Callable[[str, str], Dict[str, Any]],
        build_goal_with_profile: Callable[[str, Dict[str, Any]], str],
        workspace_connector_context: Callable[..., Dict[str, Any]],
        build_goal_with_connector_context: Callable[[str, str], str],
        installed_skill_query: Callable[..., Dict[str, Any]],
        create_run: Callable[..., Dict[str, Any]],
        wait_for_run_terminal_status: Callable[[str, Optional[int], Optional[int]], Dict[str, Any]],
        runs_get: Callable[[str], Any],
        session_key: Callable[[str], str],
        safe_path_token: Callable[[Any], str],
        send_message: Callable[..., Any],
        set_connector_state: Callable[[str, Dict[str, Any]], Any],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.normalize_workspace_id = normalize_workspace_id
        self.chat_id_from_session_key = chat_id_from_session_key
        self.list_connector_entries = list_connector_entries
        self.get_secret = get_secret
        self.resolve_profile = resolve_profile
        self.route_message = route_message
        self.get_chat_profile = get_chat_profile
        self.build_goal_with_profile = build_goal_with_profile
        self.workspace_connector_context = workspace_connector_context
        self.build_goal_with_connector_context = build_goal_with_connector_context
        self.installed_skill_query = installed_skill_query
        self.create_run = create_run
        self.wait_for_run_terminal_status = wait_for_run_terminal_status
        self.runs_get = runs_get
        self.session_key = session_key
        self.safe_path_token = safe_path_token
        self.send_message = send_message
        self.set_connector_state = set_connector_state
        self.utc_now_iso = utc_now_iso

    def _select_connector(
        self,
        *,
        workspace_id: Optional[str],
        session_key: Optional[str],
        chat_id: Optional[str],
        connector_id: Optional[str] = None,
        require_chat_match: bool = False,
    ) -> Dict[str, Any]:
        requested_workspace = self.normalize_workspace_id(workspace_id)
        requested_connector_id = str(connector_id or "").strip()
        requested_chat_id = str(chat_id or "").strip() or self.chat_id_from_session_key(str(session_key or "").strip())
        try:
            entries = self.list_connector_entries(requested_workspace)
        except TypeError:
            entries = self.list_connector_entries()
        if requested_workspace:
            entries = [item for item in entries if self.normalize_workspace_id(item.get("workspace_id")) == requested_workspace]
        if requested_connector_id:
            entries = [item for item in entries if str(item.get("id") or "").strip() == requested_connector_id]
        if not entries:
            scope = requested_workspace or "visible workspaces"
            raise RuntimeError(f"No Telegram connector found for workspace '{scope}'.")

        selected_entry: Optional[Dict[str, Any]] = None
        selected_secret: Optional[Dict[str, Any]] = None
        target_chat_id = ""
        last_error = ""
        for entry in entries:
            try:
                secret = self.get_secret(entry)
                bot_token = str(secret.get("bot_token") or "").strip()
                configured_chat_id = str(secret.get("chat_id") or "").strip()
                if not bot_token:
                    raise RuntimeError("Connector is missing bot_token.")
                resolved_chat_id = requested_chat_id or configured_chat_id
                if not resolved_chat_id:
                    raise RuntimeError("Connector is missing chat_id.")
                if require_chat_match and requested_chat_id and configured_chat_id and requested_chat_id != configured_chat_id:
                    continue
                selected_entry = entry
                selected_secret = secret
                target_chat_id = resolved_chat_id
                break
            except Exception as exc:
                last_error = str(exc)
                continue
        if not selected_entry or not selected_secret:
            if last_error:
                raise RuntimeError(last_error)
            raise RuntimeError("Unable to resolve Telegram connector credentials.")
        return {
            "entry": selected_entry,
            "secret": selected_secret,
            "target_chat_id": target_chat_id,
            "requested_workspace": requested_workspace,
        }

    async def handle_send_message(
        self,
        *,
        text: str,
        workspace_id: Optional[str] = None,
        session_key: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        message = str(text or "").strip()
        if not message:
            raise RuntimeError("Message text is required.")
        if len(message) > 3900:
            raise RuntimeError("Message is too long for Telegram. Keep it under 3900 characters.")

        selected = self._select_connector(
            workspace_id=workspace_id,
            session_key=session_key,
            chat_id=chat_id,
        )
        selected_entry = selected["entry"]
        selected_secret = selected["secret"]
        target_chat_id = str(selected["target_chat_id"] or "").strip()
        connector_id = str(selected_entry.get("id") or "").strip()
        connector_label = str(selected_entry.get("label") or "Telegram Bot").strip()
        workspace_norm = self.normalize_workspace_id(selected_entry.get("workspace_id"))
        bot_token = str(selected_secret.get("bot_token") or "").strip()
        trace_id = f"tg-terminal:{self.safe_path_token(target_chat_id)}:{str(uuid.uuid4())[:10]}"

        self.send_message(
            bot_token=bot_token,
            chat_id=target_chat_id,
            text=message,
            workspace_id=workspace_norm,
            action="terminal_send",
            connector_id=connector_id,
            trace_id=trace_id,
        )
        self.set_connector_state(
            connector_id,
            {
                "label": connector_label,
                "workspace_id": workspace_norm,
                "last_action": "terminal_send",
                "last_chat_id": target_chat_id,
                "last_error": None,
                "last_processed_at": self.utc_now_iso(),
                "last_poll_at": self.utc_now_iso(),
            },
        )
        return {
            "ok": True,
            "channel": "telegram",
            "connector_id": connector_id,
            "connector_label": connector_label,
            "workspace_id": workspace_norm,
            "chat_id": target_chat_id,
            "session_key": self.session_key(target_chat_id),
            "text": message,
            "trace_id": trace_id,
            "sent_at": self.utc_now_iso(),
        }

    async def handle_autopilot_test_message(
        self,
        *,
        text: str,
        workspace_id: Optional[str] = None,
        session_key: Optional[str] = None,
        chat_id: Optional[str] = None,
        connector_id: Optional[str] = None,
        sender_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        message = str(text or "").strip()
        if not message:
            raise RuntimeError("Message text is required.")

        selected = self._select_connector(
            workspace_id=workspace_id,
            session_key=session_key,
            chat_id=chat_id,
            connector_id=connector_id,
            require_chat_match=True,
        )
        selected_entry = selected["entry"]
        target_chat_id = str(selected["target_chat_id"] or "").strip()
        requested_workspace = str(selected["requested_workspace"] or "").strip()

        profile = self.resolve_profile(selected_entry)
        routed = self.route_message(message, profile)
        action = str(routed.get("action") or "ignore").strip().lower()
        if action != "run":
            return {
                "ok": True,
                "channel": "telegram",
                "mode": "autopilot_test",
                "action": action,
                "routed": routed,
                "message": "Telegram autopilot routed the test message without starting a run.",
            }

        workspace_norm = self.normalize_workspace_id(selected_entry.get("workspace_id")) or requested_workspace or "default"
        connector_id_value = str(selected_entry.get("id") or "").strip()
        sender_value = str(sender_id or "telegram-test-user").strip() or "telegram-test-user"
        chat_profile = self.get_chat_profile(workspace_norm, target_chat_id)
        goal = str(routed.get("goal") or "").strip()
        run_goal = self.build_goal_with_profile(goal, chat_profile)
        connector_context = self.workspace_connector_context(
            goal=goal,
            workspace_id=workspace_norm,
            current_connector_id=connector_id_value,
        )
        run_goal = self.build_goal_with_connector_context(
            run_goal,
            str(connector_context.get("prompt_append") or "").strip(),
        )
        skill_query = self.installed_skill_query(
            goal=goal,
            workspace_id=workspace_norm,
            connector_id=connector_id_value,
            chat_id=target_chat_id,
            session_key=self.session_key(target_chat_id),
        )
        run_goal = self.build_goal_with_connector_context(
            run_goal,
            str(skill_query.get("prompt_append") or "").strip(),
        )
        direct_response = str(skill_query.get("response") or "").strip() if bool(skill_query.get("handled")) else ""
        if direct_response:
            return {
                "ok": True,
                "channel": "telegram",
                "mode": "autopilot_test",
                "connector_id": connector_id_value,
                "chat_id": target_chat_id,
                "profile_id": str(profile.get("id") or "").strip(),
                "routed": routed,
                "connector_context": connector_context,
                "skill_query": skill_query,
                "result": {
                    "status": "completed",
                    "summary": direct_response,
                    "reply": direct_response,
                },
            }

        run_info = self.create_run(
            goal=run_goal,
            workspace_id=workspace_norm,
            connector_id=connector_id_value,
            chat_id=target_chat_id,
            sender_id=sender_value,
            update_id=int(time.time()),
            message_id=f"test-{str(uuid.uuid4())[:10]}",
            profile_context=chat_profile,
            trace_id=f"tg-test:{self.safe_path_token(target_chat_id)}:{str(uuid.uuid4())[:10]}",
            source_event_id="telegram-test-message",
            connector_entry=selected_entry,
            connector_context=connector_context,
        )
        run_id = str(run_info.get("run_id") or "").strip()
        if not run_id:
            raise RuntimeError("Telegram autopilot test did not create a run.")
        result = await asyncio.to_thread(
            self.wait_for_run_terminal_status,
            run_id,
            timeout_seconds,
            None,
        )
        run = self.runs_get(run_id)
        context = run.get("context") if isinstance(run, dict) and isinstance(run.get("context"), dict) else {}
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        return {
            "ok": True,
            "channel": "telegram",
            "mode": "autopilot_test",
            "connector_id": connector_id_value,
            "chat_id": target_chat_id,
            "profile_id": str(profile.get("id") or "").strip(),
            "run_id": run_id,
            "routed": routed,
            "route": run_info.get("route"),
            "connector_context": connector_context,
            "skill_query": skill_query,
            "run_metadata": metadata,
            "result": result,
        }
