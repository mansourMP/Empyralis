"""Speech-to-text for personal-channel voice messages (Feature B, media pipeline).

Deliberately separate from ``multimodal_provider_service.transcribe_audio_bytes``:
that function is wired to *platform* credentials only (``OPENAI_API_KEY`` /
``ELEVENLABS_API_KEY`` read straight from the process environment — see its
``_normalized_openai_api_key()``). Personal-channel voice notes transcribe
using the *agent's own* configured provider — resolved through
``agent_capability_service``'s speech_to_text capability (byok_api or
platform_credits, per-agent — see the fleet Capabilities tab), falling back
to the workspace-level ``direct_chat_provider_service.direct_chat_credentials
(workspace_id, "openai")`` lookup this module used exclusively before
per-agent capability config existed. That fallback is exact-behavior-preserving
for every call site that can't yet supply an agent_id (e.g. the local-bridge
channel path) and for any agent that has never touched its Capabilities tab.
Reusing the platform-only helper would either ignore a configured key or leak
platform credentials into workspaces/agents that never configured any — so
this module calls OpenAI's Whisper endpoint directly with the resolved key
instead.

Never raises for "no provider configured" or "call failed" — every entry
point returns an ``{"ok": bool, ...}`` envelope so a transcription failure
degrades the reply to a placeholder instead of dropping the whole turn.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_WHISPER_MODEL = "whisper-1"
TRANSCRIBE_TIMEOUT_SECONDS = 60.0
# Generous but not unbounded — a personal-channel voice note is a phone-app
# recording (WhatsApp/Telegram cap these in the tens of MB anyway); this is a
# belt-and-suspenders cap independent of whatever the gateway-fetch layer
# already enforces.
MAX_TRANSCRIBE_AUDIO_BYTES = 24 * 1024 * 1024

UNAVAILABLE_TEXT = "[Voice message — transcription unavailable]"


async def _resolve_openai_api_key(workspace_id: str, agent_id: str = "") -> Optional[str]:
    """Per-agent BYOK/platform-credits first (agent_capability_service's
    speech_to_text capability), falling back to the workspace-level lookup
    this module used exclusively before per-agent capability config existed.

    Returns None (never raises) when nothing usable resolves, which the
    caller treats as "STT not configured" and degrades gracefully, per this
    feature's contract.
    """
    clean_agent_id = str(agent_id or "").strip()
    if clean_agent_id:
        try:
            from server_modules import agent_capability_service as _cap_svc

            resolution = await _cap_svc.resolve_agent_capability_provider_by_id(
                workspace_id=workspace_id, agent_id=clean_agent_id, capability=_cap_svc.SPEECH_TO_TEXT,
            )
            if resolution.available:
                resolved_key = str((resolution.credentials or {}).get("api_key") or "").strip()
                if resolved_key:
                    return resolved_key
        except Exception:
            logger.warning(
                "transcription: capability resolution failed for workspace=%s agent=%s — falling back to workspace lookup",
                workspace_id, agent_id, exc_info=True,
            )

    try:
        from server_modules.direct_chat_provider_service import direct_chat_credentials

        creds = direct_chat_credentials(str(workspace_id or "default").strip() or "default", "openai")
    except Exception:
        logger.warning("transcription: failed to resolve OpenAI credentials for workspace=%s", workspace_id, exc_info=True)
        return None
    if not isinstance(creds, dict):
        return None
    api_key = str(creds.get("api_key") or "").strip()
    if api_key:
        return api_key
    # Some OpenAI credential records are OAuth-token based (see
    # direct_chat_provider_service.supports_direct_message_native_chat's
    # "openai" branch) rather than a plain api_key. Whisper's REST endpoint
    # accepts a bearer token the same way, so best-effort try it too.
    bearer = str(creds.get("access_token") or creds.get("oauth_token") or "").strip()
    return bearer or None


async def transcribe_voice_bytes(
    *,
    workspace_id: str,
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    filename: str = "voice-message",
    agent_id: str = "",
) -> Dict[str, Any]:
    """Transcribe one voice/audio attachment using the agent's own configured
    key (falling back to the workspace-level one — see
    _resolve_openai_api_key's docstring). `agent_id` is optional: omitting it
    (or passing one with no capability_config) resolves exactly the way this
    function always did, unchanged.

    Returns ``{"ok": True, "transcript": str, "provider": "openai", "model": str}``
    on success, or ``{"ok": False, "error": str}`` on any failure (including
    "no provider configured") — this function never raises.
    """
    if not audio_bytes:
        return {"ok": False, "error": "empty_audio_payload"}
    if len(audio_bytes) > MAX_TRANSCRIBE_AUDIO_BYTES:
        return {"ok": False, "error": "audio_too_large"}

    api_key = await _resolve_openai_api_key(workspace_id, agent_id)
    if not api_key:
        return {"ok": False, "error": "stt_provider_not_configured"}

    upload_name = str(filename or "voice-message").strip() or "voice-message"
    if "." not in upload_name:
        # Whisper infers format from the filename extension; fall back to a
        # generic container guess from the mime type so the API can decode it.
        guessed_ext = str(mime_type or "").split("/")[-1].split(";")[0].strip() or "ogg"
        upload_name = f"{upload_name}.{guessed_ext}"

    try:
        async with httpx.AsyncClient(timeout=TRANSCRIBE_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.post(
                OPENAI_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": DEFAULT_WHISPER_MODEL, "response_format": "json"},
                files={"file": (upload_name, audio_bytes, str(mime_type or "application/octet-stream"))},
            )
    except Exception as exc:
        logger.warning("transcription: OpenAI Whisper request failed for workspace=%s: %s", workspace_id, exc)
        return {"ok": False, "error": f"transcription_request_failed: {exc}"}

    if response.status_code >= 400:
        logger.warning(
            "transcription: OpenAI Whisper returned status=%s for workspace=%s: %s",
            response.status_code, workspace_id, response.text[:300],
        )
        return {"ok": False, "error": f"transcription_http_{response.status_code}"}

    try:
        payload = response.json()
    except Exception:
        return {"ok": False, "error": "transcription_invalid_response"}

    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        return {"ok": False, "error": "empty_transcript"}
    return {"ok": True, "transcript": transcript, "provider": "openai", "model": DEFAULT_WHISPER_MODEL}


def format_voice_message_text(result: Dict[str, Any]) -> str:
    """Render a transcription result as the text injected into the turn.

    Contract: ``[Voice message]: <transcript>`` on success, or the fixed
    ``[Voice message — transcription unavailable]`` placeholder on any
    failure/degraded case — the exact strings the inbound handlers splice
    into the message text before it reaches the agent.
    """
    if isinstance(result, dict) and bool(result.get("ok")) and str(result.get("transcript") or "").strip():
        return f"[Voice message]: {str(result['transcript']).strip()}"
    return UNAVAILABLE_TEXT
