"""
Customer Ops pack helpers.

Extracted from server.py to reduce hotspot size.
All function signatures and behaviour are unchanged.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from server_modules.connector_metadata import _connector_identity_signature
from server_modules.external_write_safety import execute_external_write_once, stable_value_fingerprint

_server = None  # populated by _init()


def _init():
    """Late-bind references to server.py globals. Called on first use."""
    global _server
    if _server is not None:
        return
    import server as _s
    _server = _s


def classify_inbox_priority(message: str) -> str:
    raw = message.lower()
    high_tokens = ["urgent", "angry", "refund", "cancel", "complaint", "asap", "failed", "broken"]
    medium_tokens = ["price", "quote", "availability", "question", "follow up", "follow-up"]
    if any(token in raw for token in high_tokens):
        return "high"
    if any(token in raw for token in medium_tokens):
        return "medium"
    return "low"


def classify_inbox_category(message: str) -> str:
    raw = message.lower()
    if any(token in raw for token in ["book", "booking", "appointment", "schedule", "slot"]):
        return "booking"
    if any(token in raw for token in ["price", "quote", "cost", "budget"]):
        return "pricing"
    if any(token in raw for token in ["refund", "cancel", "complaint", "issue", "broken"]):
        return "support"
    return "general"


def classify_lead_stage(lead_line: str) -> str:
    raw = lead_line.lower()
    if any(token in raw for token in ["hot", "ready", "buy", "book now", "today"]):
        return "hot"
    if any(token in raw for token in ["warm", "interested", "follow", "demo", "quote"]):
        return "warm"
    return "cold"


def extract_lead_name(lead_line: str, index: int) -> str:
    if "|" in lead_line:
        first = lead_line.split("|", 1)[0].strip()
        if first:
            return first
    if "," in lead_line:
        first = lead_line.split(",", 1)[0].strip()
        if first:
            return first
    return f"Lead {index + 1}"


def _connector_account_identity(
    connector_provider: str,
    connector_credential_id: Any,
    connector_credentials: Dict[str, Any],
) -> Optional[str]:
    credential_token = str(connector_credential_id or "").strip()
    if credential_token:
        return f"credential:{credential_token}"
    signature = _connector_identity_signature(
        connector_provider,
        connector_credentials if isinstance(connector_credentials, dict) else {},
    ) or ""
    signature = signature.strip()
    if signature:
        return f"signature:{stable_value_fingerprint(signature)}"
    return None


def execute_customer_ops_pack(context: Dict[str, Any], run_id: Optional[str] = None) -> Dict[str, Any]:
    _init()
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    pack_inputs = metadata.get("pack_inputs") if isinstance(metadata.get("pack_inputs"), dict) else {}
    workspace_id = context.get("workspace_id") or metadata.get("workspace_id")
    connector_credential_id = metadata.get("connector_credential_id")
    connector_payload = metadata.get("pack_connectors") if isinstance(metadata.get("pack_connectors"), dict) else {}
    trust_mode = _server.normalize_trust_mode(metadata.get("trust_mode"))
    selected_target = _server.normalize_execution_target(
        metadata.get("execution_target_selected") or metadata.get("execution_target")
    )

    inbox_items = _server.parse_text_items(pack_inputs.get("inbox", ""))
    lead_items = _server.parse_text_items(pack_inputs.get("leads", ""))
    slot_items = _server.parse_text_items(pack_inputs.get("slots", ""))
    if not slot_items:
        slot_items = list(_server.DEFAULT_BOOKING_SLOTS)
    automation_kind = str(metadata.get("automation_kind") or "").strip().lower()
    summary_limit = max(1, min(int(metadata.get("summary_limit") or 5), 10))

    connector_details: Dict[str, Any] = {
        "enabled": False,
        "connector": "",
        "account_profile": None,
        "email_drafts_created": 0,
        "email_sent_count": 0,
        "calendar_events_created": 0,
        "warnings": [],
    }
    connector_credentials: Dict[str, Any] = {}
    connector_provider = ""
    google_token = ""
    calendar_id = "primary"
    calendar_timezone = "UTC"
    connector_apply_changes_raw = metadata.get("connector_apply_changes")
    operator_forced_apply = bool(connector_apply_changes_raw) if isinstance(connector_apply_changes_raw, bool) else False

    if isinstance(connector_credential_id, str) and connector_credential_id.strip():
        try:
            secret = _server.resolve_vault_credential(
                connector_credential_id.strip(),
                str(workspace_id) if workspace_id else None,
            )
            provider = str(secret.get("_provider") or "").strip()
            if provider not in {"google_workspace", "microsoft_365"}:
                connector_details["warnings"].append("Selected connector credential is not supported for customer ops.")
            else:
                connector_credentials = dict(secret)
                connector_provider = provider
                connector_details["connector"] = provider
                google_token = str(secret.get("access_token") or "")
                calendar_id = str(secret.get("calendar_id") or "primary")
                calendar_timezone = str(secret.get("timezone") or "UTC")
        except Exception as exc:
            connector_details["warnings"].append(f"Connector credential unavailable: {exc}")
    elif isinstance(connector_payload, dict):
        payload_provider = str(
            connector_payload.get("connector") or connector_payload.get("_provider") or ""
        ).strip()
        if not payload_provider and _server.google_workspace_uses_local_cli(connector_payload):
            payload_provider = "google_workspace"
        if payload_provider in {"google_workspace", "microsoft_365"}:
            connector_credentials = dict(connector_payload)
            connector_provider = payload_provider
            connector_details["connector"] = payload_provider
            google_token = str(connector_payload.get("access_token") or "")
            calendar_id = str(connector_payload.get("calendar_id") or "primary")
            calendar_timezone = str(connector_payload.get("timezone") or "UTC")

    if (
        connector_provider == "google_workspace" and (
            google_token or _server.google_workspace_uses_local_cli(connector_credentials)
        )
    ) or (
        connector_provider == "microsoft_365"
        and bool(str(connector_credentials.get("access_token") or "").strip())
    ):
        connector_details["enabled"] = True
    connector_account_id = _connector_account_identity(
        connector_provider,
        connector_credential_id,
        connector_credentials,
    )

    if not inbox_items and automation_kind == "email_summary_recent" and connector_details["enabled"]:
        try:
            recent_messages = _server.list_recent_connector_messages(connector_credentials, limit=summary_limit)
        except Exception as exc:
            connector_label = connector_provider or "Connector"
            connector_details["warnings"].append(f"{connector_label} inbox fetch failed: {exc}")
            recent_messages = []
        inbox_items = [
            "\n".join(
                part for part in [
                    f"From: {str(item.get('from') or '').strip()}".strip(),
                    f"Subject: {str(item.get('subject') or '').strip()}".strip(),
                    str(item.get("snippet") or "").strip(),
                ] if part
            )
            for item in recent_messages
            if isinstance(item, dict)
        ]

    if not lead_items and automation_kind == "lead_followup_recent" and connector_details["enabled"]:
        try:
            recent_messages = _server.list_recent_connector_messages(connector_credentials, limit=summary_limit)
        except Exception as exc:
            connector_label = connector_provider or "Connector"
            connector_details["warnings"].append(f"{connector_label} lead fetch failed: {exc}")
            recent_messages = []
        lead_items = [
            " | ".join(
                part for part in [
                    str(item.get("from") or "").strip(),
                    str(item.get("subject") or "").strip(),
                    str(item.get("snippet") or "").strip(),
                ] if part
            )
            for item in recent_messages
            if isinstance(item, dict) and any(
                str(item.get(field) or "").strip() for field in ("from", "subject", "snippet")
            )
        ]

    def evaluate_runtime_tool(tool_id: str, phase: str, lead_name: Optional[str] = None) -> Dict[str, Any]:
        evaluation = _server.evaluate_tool_policy_decision(
            tool_id=tool_id,
            trust_mode=trust_mode,
            target=selected_target,
            metadata=metadata,
        )
        _server._append_run_tool_policy_audit(
            run_id,
            evaluation,
            source="pack_runtime_tool",
            metadata={"pack_id": _server.CUSTOMER_OPS_PACK_ID, "phase": phase, "lead_name": lead_name},
        )
        return evaluation

    draft_eval = evaluate_runtime_tool("draft_email", "prepare")
    calendar_eval = evaluate_runtime_tool("create_calendar_event", "prepare")
    send_eval = evaluate_runtime_tool("send_message", "prepare")

    connector_details["tool_policy"] = {
        "draft_email": draft_eval,
        "create_calendar_event": calendar_eval,
        "send_message": send_eval,
    }
    connector_details["apply_changes"] = bool(
        connector_details["enabled"] and (operator_forced_apply or trust_mode == _server.TRUST_MODE_AUTO)
    )
    if connector_details["enabled"] and not connector_details["apply_changes"]:
        connector_details["warnings"].append(
            "Connector actions are in safe simulation mode (no external writes)."
        )

    def connector_headers() -> Dict[str, str]:
        if not google_token:
            raise RuntimeError("Google access token is missing.")
        return {"Authorization": f"Bearer {google_token}", "Content-Type": "application/json"}

    def connector_display_name() -> str:
        if connector_provider == "microsoft_365":
            return "Microsoft 365"
        if connector_provider == "google_workspace":
            return "Google Workspace"
        return "Workspace connector"

    def connector_warning_prefix() -> str:
        return connector_display_name()

    def connector_profile_email(profile: Dict[str, Any]) -> Optional[str]:
        if connector_provider == "microsoft_365":
            value = str(profile.get("mail") or profile.get("userPrincipalName") or "").strip()
            return value or None
        value = str(profile.get("emailAddress") or "").strip()
        return value or None

    def connector_get_profile() -> Dict[str, Any]:
        if connector_provider == "google_workspace":
            if _server.google_workspace_uses_local_cli(connector_credentials):
                return _server.google_workspace_local_get_profile(connector_credentials)
            res = _server.http_json_request(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers=connector_headers(),
            )
            body = res.get("json")
            if isinstance(body, dict):
                return body
            raise RuntimeError("Google profile response was invalid.")
        if connector_provider == "microsoft_365":
            return _server.microsoft_365_get_profile(connector_credentials, _server.http_json_request)
        raise RuntimeError("Connector profile lookup is unavailable.")

    def extract_lead_email(lead_line: str) -> Optional[str]:
        match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", lead_line)
        if not match:
            return None
        return match.group(0)

    def connector_send_message(to_email: str, subject: str, body_text: str) -> Dict[str, Any]:
        def _perform_send() -> Dict[str, Any]:
            if connector_provider == "google_workspace":
                if _server.google_workspace_uses_local_cli(connector_credentials):
                    return _server.google_workspace_local_send_message(
                        connector_credentials, to_email, subject, body_text
                    )
                message = (
                    f"To: {to_email}\r\n"
                    f"Subject: {subject}\r\n"
                    "Content-Type: text/plain; charset=UTF-8\r\n"
                    "\r\n"
                    f"{body_text}\r\n"
                )
                raw_encoded = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8").rstrip("=")
                payload = {"raw": raw_encoded}
                res = _server.http_json_request(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                    method="POST",
                    headers=connector_headers(),
                    payload=payload,
                )
                body = res.get("json")
                if isinstance(body, dict):
                    return body
                raise RuntimeError("Gmail send response was invalid.")
            if connector_provider == "microsoft_365":
                return _server.microsoft_365_send_message(
                    connector_credentials,
                    _server.http_json_request,
                    to_email,
                    subject,
                    body_text,
                )
            raise RuntimeError("Connector send action is unavailable.")

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"customer_ops:send_message:{to_email.strip().lower()}",
            category="customer_ops_pack_send_message",
            operation={"connector": connector_provider, "action_id": "send_message", "recipient": to_email, "subject": subject},
            target_system=connector_provider,
            account_identity=connector_account_id,
            target_identity={"action_id": "send_message", "recipient": to_email, "subject": subject},
            payload={"recipient": to_email, "subject": subject, "body": body_text, "connector": connector_provider},
            execute=_perform_send,
        )

    def connector_create_draft(to_email: str, subject: str, body_text: str) -> Dict[str, Any]:
        def _perform_draft() -> Dict[str, Any]:
            if connector_provider == "google_workspace":
                if _server.google_workspace_uses_local_cli(connector_credentials):
                    return _server.google_workspace_local_create_draft(
                        connector_credentials, to_email, subject, body_text
                    )
                message = (
                    f"To: {to_email}\r\n"
                    f"Subject: {subject}\r\n"
                    "Content-Type: text/plain; charset=UTF-8\r\n"
                    "\r\n"
                    f"{body_text}\r\n"
                )
                raw_encoded = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8").rstrip("=")
                payload = {"message": {"raw": raw_encoded}}
                res = _server.http_json_request(
                    "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                    method="POST",
                    headers=connector_headers(),
                    payload=payload,
                )
                body = res.get("json")
                if isinstance(body, dict):
                    return body
                raise RuntimeError("Gmail draft response was invalid.")
            if connector_provider == "microsoft_365":
                return _server.microsoft_365_create_draft(
                    connector_credentials,
                    _server.http_json_request,
                    to_email,
                    subject,
                    body_text,
                )
            raise RuntimeError("Connector draft action is unavailable.")

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"customer_ops:draft_email:{to_email.strip().lower()}",
            category="customer_ops_pack_draft_email",
            operation={"connector": connector_provider, "action_id": "draft_email", "recipient": to_email, "subject": subject},
            target_system=connector_provider,
            account_identity=connector_account_id,
            target_identity={"action_id": "draft_email", "recipient": to_email, "subject": subject},
            payload={"recipient": to_email, "subject": subject, "body": body_text, "connector": connector_provider},
            execute=_perform_draft,
        )

    def parse_slot(slot: str) -> Optional[tuple[datetime, datetime]]:
        raw = slot.strip()
        if not raw:
            return None
        if "/" in raw:
            start_raw, end_raw = raw.split("/", 1)
            try:
                start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                return start_dt, end_dt
            except Exception:
                return None
        try:
            start_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            return start_dt, start_dt + timedelta(minutes=30)
        except Exception:
            return None

    def calendar_create_event(slot: str, lead_name: str) -> Dict[str, Any]:
        parsed = parse_slot(slot)
        if not parsed:
            raise RuntimeError(f"Slot '{slot}' is not ISO date-time. Use format YYYY-MM-DDTHH:MM:SSZ.")
        start_dt, end_dt = parsed
        if connector_provider == "microsoft_365":
            payload = {
                "subject": f"Empyralis Booking - {lead_name}",
                "body": {
                    "contentType": "Text",
                    "content": "Auto-created by Empyralis Customer Ops Autopilot.",
                },
                "start": {"dateTime": start_dt.isoformat(), "timeZone": calendar_timezone},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": calendar_timezone},
            }
        else:
            payload = {
                "summary": f"Empyralis Booking - {lead_name}",
                "description": "Auto-created by Empyralis Customer Ops Autopilot.",
                "start": {"dateTime": start_dt.isoformat(), "timeZone": calendar_timezone},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": calendar_timezone},
            }

        def _perform_calendar_event() -> Dict[str, Any]:
            if connector_provider == "microsoft_365":
                return _server.microsoft_365_create_calendar_event(
                    connector_credentials,
                    _server.http_json_request,
                    payload=payload,
                )
            if _server.google_workspace_uses_local_cli(connector_credentials):
                return _server.google_workspace_local_create_calendar_event(
                    connector_credentials,
                    calendar_id=calendar_id,
                    send_updates="none",
                    payload=payload,
                )
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/{quote_plus(calendar_id)}"
                f"/events?sendUpdates=none"
            )
            res = _server.http_json_request(url, method="POST", headers=connector_headers(), payload=payload)
            body = res.get("json")
            if isinstance(body, dict):
                return body
            raise RuntimeError("Calendar create event response was invalid.")

        return execute_external_write_once(
            run_id=run_id,
            context=context,
            step_key=f"customer_ops:create_calendar_event:{lead_name.strip().lower()}:{slot.strip()}",
            category="customer_ops_pack_calendar_event",
            operation={"connector": connector_provider, "action_id": "create_calendar_event", "lead_name": lead_name},
            target_system=connector_provider,
            account_identity=connector_account_id,
            target_identity={
                "action_id": "create_calendar_event",
                "lead_name": lead_name,
                "slot": slot,
                "payload_fingerprint": stable_value_fingerprint(payload),
            },
            payload={"connector": connector_provider, "lead_name": lead_name, "slot": slot, "payload": payload},
            execute=_perform_calendar_event,
        )

    triage: List[Dict[str, Any]] = []
    for idx, message in enumerate(inbox_items):
        priority = classify_inbox_priority(message)
        category = classify_inbox_category(message)
        action = "Escalate to human owner" if priority == "high" else "Draft direct response"
        triage.append(
            {
                "id": f"inbox-{idx+1}",
                "message": message,
                "priority": priority,
                "category": category,
                "action": action,
            }
        )

    follow_ups: List[Dict[str, Any]] = []
    for idx, lead in enumerate(lead_items):
        stage = classify_lead_stage(lead)
        lead_name = extract_lead_name(lead, idx)
        slot = slot_items[idx % len(slot_items)] if slot_items else "TBD"
        if stage == "hot":
            cta = f"Offer immediate booking slot: {slot}"
        elif stage == "warm":
            cta = f"Share value summary and offer slot: {slot}"
        else:
            cta = "Nurture with helpful content and check-in next week"

        item = {
            "id": f"lead-{idx+1}",
            "lead_name": lead_name,
            "raw": lead,
            "stage": stage,
            "next_action": cta,
            "draft_message": (
                f"Hi {lead_name}, thanks for your interest. "
                f"We can help with this today. {cta}."
            ),
        }
        if connector_details["enabled"] and connector_details["apply_changes"]:
            send_decision = evaluate_runtime_tool("send_message", "follow_up", lead_name)
            draft_decision = evaluate_runtime_tool("draft_email", "follow_up", lead_name)
            email = extract_lead_email(lead)
            if not email:
                item["draft_created"] = False
                item["draft_error"] = "No email found in lead line."
                follow_ups.append({**item})
                continue
            if str(send_decision.get("decision") or "") == "allow":
                try:
                    sent = connector_send_message(
                        email,
                        "Quick follow-up from Empyralis",
                        str(item["draft_message"]),
                    )
                    duplicate_guard = sent.get("duplicate_guard") if isinstance(sent, dict) else {}
                    duplicate_protected = isinstance(duplicate_guard, dict) and not bool(duplicate_guard.get("executed", True))
                    item["sent_message_id"] = sent.get("id") or sent.get("messageId")
                    item["sent"] = True
                    item["duplicate_protected"] = duplicate_protected
                    item["delivery"] = "duplicate_protected" if duplicate_protected else "sent"
                    if not duplicate_protected:
                        connector_details["email_sent_count"] += 1
                except Exception as exc:
                    item["sent"] = False
                    item["send_error"] = str(exc)
                    connector_details["warnings"].append(
                        f"{connector_warning_prefix()} send failed for {lead_name}: {exc}"
                    )
            elif str(draft_decision.get("decision") or "") != "allow":
                item["draft_created"] = False
                item["draft_error"] = (
                    f"Tool policy {draft_decision.get('decision')}: {draft_decision.get('reason')}"
                )
                follow_ups.append({**item})
                continue
            if not item.get("sent"):
                try:
                    draft = connector_create_draft(
                        email,
                        "Quick follow-up from Empyralis",
                        str(item["draft_message"]),
                    )
                    duplicate_guard = draft.get("duplicate_guard") if isinstance(draft, dict) else {}
                    duplicate_protected = isinstance(duplicate_guard, dict) and not bool(duplicate_guard.get("executed", True))
                    item["draft_id"] = draft.get("id")
                    item["draft_created"] = True
                    item["duplicate_protected"] = duplicate_protected
                    item["delivery"] = "duplicate_protected" if duplicate_protected else "draft"
                    if not duplicate_protected:
                        connector_details["email_drafts_created"] += 1
                except Exception as exc:
                    item["draft_created"] = False
                    item["draft_error"] = str(exc)
                    connector_details["warnings"].append(
                        f"{connector_warning_prefix()} draft failed for {lead_name}: {exc}"
                    )
        elif connector_details["enabled"]:
            item["draft_created"] = False
            item["draft_error"] = "Connector write disabled by safety policy."
        follow_ups.append({**item})

    bookings: List[Dict[str, Any]] = []
    for idx, item in enumerate(follow_ups):
        if item["stage"] not in ["hot", "warm"]:
            continue
        booking_item = {
            "id": f"booking-{idx+1}",
            "lead_name": item["lead_name"],
            "proposed_slot": slot_items[idx % len(slot_items)] if slot_items else "TBD",
            "status": "pending_confirmation",
        }
        if connector_details["enabled"] and connector_details["apply_changes"]:
            calendar_decision = evaluate_runtime_tool(
                "create_calendar_event",
                "booking",
                str(booking_item.get("lead_name") or ""),
            )
            if str(calendar_decision.get("decision") or "") != "allow":
                booking_item["event_created"] = False
                booking_item["event_error"] = (
                    f"Tool policy {calendar_decision.get('decision')}: {calendar_decision.get('reason')}"
                )
                bookings.append(booking_item)
                continue
            try:
                event = calendar_create_event(
                    str(booking_item["proposed_slot"]),
                    str(booking_item["lead_name"]),
                )
                duplicate_guard = event.get("duplicate_guard") if isinstance(event, dict) else {}
                duplicate_protected = isinstance(duplicate_guard, dict) and not bool(duplicate_guard.get("executed", True))
                booking_item["event_id"] = event.get("id")
                booking_item["event_created"] = True
                booking_item["duplicate_protected"] = duplicate_protected
                if not duplicate_protected:
                    connector_details["calendar_events_created"] += 1
            except Exception as exc:
                booking_item["event_created"] = False
                booking_item["event_error"] = str(exc)
                connector_details["warnings"].append(
                    f"{connector_warning_prefix()} calendar event failed for {booking_item['lead_name']}: {exc}"
                )
        elif connector_details["enabled"]:
            booking_item["event_created"] = False
            booking_item["event_error"] = "Connector write disabled by safety policy."
        bookings.append(booking_item)

    urgent_count = len([item for item in triage if item["priority"] == "high"])
    outbound_actions = len(follow_ups) + len(bookings)

    if connector_details["enabled"]:
        try:
            profile = connector_get_profile()
            connector_details["account_profile"] = {
                "emailAddress": connector_profile_email(profile),
                "displayName": profile.get("displayName"),
            }
        except Exception as exc:
            connector_details["warnings"].append(f"{connector_warning_prefix()} profile check failed: {exc}")

    connector_summary = (
        f"Connector {connector_display_name()}: email_sent={connector_details['email_sent_count']}, "
        f"email_drafts={connector_details['email_drafts_created']}, "
        f"calendar_events={connector_details['calendar_events_created']}."
        if connector_details["enabled"]
        else "Connector actions disabled."
    )
    summary = (
        f"Customer Ops Autopilot completed: triaged {len(triage)} inbox items, "
        f"prepared {len(follow_ups)} lead follow-ups, and proposed {len(bookings)} bookings. "
        f"Urgent inbox items: {urgent_count}. "
        f"{connector_summary}"
    )

    next_steps = [
        "Review urgent inbox items first.",
        "Send follow-up drafts for hot/warm leads.",
        "Confirm pending booking slots with leads.",
    ]

    return {
        "pack_id": _server.CUSTOMER_OPS_PACK_ID,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": summary,
        "inputs": {
            "inbox_count": len(inbox_items),
            "lead_count": len(lead_items),
            "slot_count": len(slot_items),
        },
        "outputs": {
            "triage": triage,
            "follow_ups": follow_ups,
            "bookings": bookings,
            "urgent_count": urgent_count,
            "outbound_actions": outbound_actions,
        },
        "connector": connector_details,
        "next_steps": next_steps,
    }
