from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ACP_PROTOCOL_VERSION = "1.0"
ACP_SESSION_PREFIX = "acp_"

SUPPORTED_ACP_MESSAGE_TYPES = {
    "agent.turn",
    "tool.result",
    "tool.use",
    "session.create",
    "session.close",
    "health.check",
}

ACP_TO_GATEWAY_MAP: Dict[str, str] = {
    "agent.turn": "tool.invoke",
    "tool.result": "tool.result",
    "tool.use": "tool.invoke",
    "health.check": "gateway.heartbeat",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def acp_session_id(gateway_session_id: str) -> str:
    if gateway_session_id.startswith(ACP_SESSION_PREFIX):
        return gateway_session_id
    return f"{ACP_SESSION_PREFIX}{gateway_session_id}"


def gateway_session_id(acp_session_id: str) -> str:
    if acp_session_id.startswith(ACP_SESSION_PREFIX):
        return acp_session_id[len(ACP_SESSION_PREFIX):]
    return acp_session_id


def parse_acp_message(raw: Dict[str, Any]) -> Dict[str, Any]:
    msg_type = str(raw.get("type") or raw.get("method") or "").strip()
    if msg_type not in SUPPORTED_ACP_MESSAGE_TYPES:
        return {
            "error": "unsupported_message_type",
            "detail": f"Unsupported ACP message type: {msg_type}. Supported: {', '.join(sorted(SUPPORTED_ACP_MESSAGE_TYPES))}",
            "acp_message_type": msg_type,
        }
    return {
        "id": str(raw.get("id") or _new_id()),
        "type": msg_type,
        "session_id": str(raw.get("session_id") or raw.get("sessionId") or ""),
        "payload": raw.get("payload") or raw.get("params") or {},
        "timestamp": _utc_now_iso(),
    }


def translate_acp_to_gateway(acp_msg: Dict[str, Any]) -> Dict[str, Any]:
    msg_type = str(acp_msg.get("type", ""))
    gw_method = ACP_TO_GATEWAY_MAP.get(msg_type, "tool.invoke")
    payload = acp_msg.get("payload", {})
    if msg_type == "agent.turn":
        return {
            "frame_id": acp_msg.get("id", _new_id()),
            "kind": "request",
            "method": gw_method,
            "params": {
                "capability": "sage.chat",
                "message": str(payload.get("message") or payload.get("prompt") or ""),
                "session_id": gateway_session_id(acp_msg.get("session_id", "")),
                "workspace_id": str(payload.get("workspace_id") or ""),
                "thinking": str(payload.get("thinking") or "medium"),
            },
        }
    if msg_type == "tool.result":
        return {
            "frame_id": acp_msg.get("id", _new_id()),
            "kind": "response",
            "method": gw_method,
            "params": {
                "tool_call_id": str(payload.get("tool_call_id") or ""),
                "result": payload.get("result") or payload.get("output"),
                "error": payload.get("error"),
            },
        }
    if msg_type == "session.create":
        return {
            "frame_id": acp_msg.get("id", _new_id()),
            "kind": "request",
            "method": "session.create",
            "params": {
                "workspace_id": str(payload.get("workspace_id") or ""),
                "channel": "acp",
                "metadata": {
                    "acp_client": str(payload.get("client") or "unknown"),
                    "acp_version": str(payload.get("version") or ACP_PROTOCOL_VERSION),
                },
            },
        }
    if msg_type == "health.check":
        return {
            "frame_id": acp_msg.get("id", _new_id()),
            "kind": "request",
            "method": "gateway.heartbeat",
            "params": {},
        }
    return {
        "frame_id": acp_msg.get("id", _new_id()),
        "kind": "request",
        "method": gw_method,
        "params": {"acp_raw": payload},
    }


def translate_gateway_to_acp(gw_frame: Dict[str, Any], acp_msg_id: str) -> Dict[str, Any]:
    kind = str(gw_frame.get("kind") or "response")
    result = gw_frame.get("result") or gw_frame.get("params") or {}
    error = gw_frame.get("error")

    if error:
        return {
            "id": acp_msg_id,
            "type": "error",
            "error": {
                "code": str(error.get("code") or "gateway_error"),
                "message": str(error.get("message") or str(error)),
            },
            "timestamp": _utc_now_iso(),
        }

    if kind == "event":
        return {
            "id": acp_msg_id,
            "type": "event",
            "event": {
                "type": str(gw_frame.get("event") or "update"),
                "data": result,
            },
            "timestamp": _utc_now_iso(),
        }

    return {
        "id": acp_msg_id,
        "type": "response",
        "result": {
            "reply": str(result.get("reply") or result.get("message") or ""),
            "session_id": str(result.get("session_id") or ""),
            "provider": str(result.get("provider") or ""),
            "model": str(result.get("model") or ""),
            "metadata": result.get("metadata", {}),
        },
        "timestamp": _utc_now_iso(),
    }


def build_acp_health_response() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "protocol": "acp",
        "version": ACP_PROTOCOL_VERSION,
        "supported_message_types": sorted(SUPPORTED_ACP_MESSAGE_TYPES),
        "timestamp": _utc_now_iso(),
    }


def build_acp_error(code: str, message: str, msg_id: str = "") -> Dict[str, Any]:
    return {
        "id": msg_id or _new_id(),
        "type": "error",
        "error": {"code": code, "message": message},
        "timestamp": _utc_now_iso(),
    }
