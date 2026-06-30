from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from server_modules import direct_chat_provider_service


def credential_auth_mode(
    provider: str,
    credentials: Optional[Dict[str, Any]],
    *,
    normalize_auth_mode_fn: Callable[..., str],
) -> str:
    return direct_chat_provider_service.credential_auth_mode(
        provider,
        credentials,
        normalize_auth_mode_fn=normalize_auth_mode_fn,
    )


def supports_direct_message_native_chat(
    provider: str,
    credentials: Optional[Dict[str, Any]],
    *,
    credential_auth_mode_fn: Callable[[str, Optional[Dict[str, Any]]], str],
    get_claude_code_session_token_fn: Callable[[], Any],
    provider_has_key_fn: Callable[[str], bool],
) -> bool:
    return direct_chat_provider_service.supports_direct_message_native_chat(
        provider,
        credentials,
        credential_auth_mode_fn=credential_auth_mode_fn,
        get_claude_code_session_token_fn=get_claude_code_session_token_fn,
        provider_has_key_fn=provider_has_key_fn,
    )


def preferred_provider(
    workspace_id: str,
    requested_provider: str = "",
    *,
    supported_providers: list[str] | tuple[str, ...],
    direct_chat_credentials_fn: Callable[[str, str], Dict[str, Any]],
    supports_direct_message_native_chat_fn: Callable[[str, Optional[Dict[str, Any]]], bool],
    credential_auth_mode_fn: Callable[[str, Optional[Dict[str, Any]]], str],
) -> tuple[str, Dict[str, Any]]:
    return direct_chat_provider_service.preferred_provider(
        workspace_id,
        requested_provider,
        supported_providers=supported_providers,
        direct_chat_credentials_fn=direct_chat_credentials_fn,
        supports_direct_message_native_chat_fn=supports_direct_message_native_chat_fn,
        credential_auth_mode_fn=credential_auth_mode_fn,
    )


def provider_display_name(provider: str) -> str:
    return direct_chat_provider_service.provider_display_name(provider)


def provider_unavailable_response(
    provider: str,
    *,
    connect_action_fn: Callable[[str, str], Dict[str, Any]],
    issue_code: Optional[str] = None,
    issue_detail: Optional[str] = None,
) -> Dict[str, Any]:
    return direct_chat_provider_service.provider_unavailable_response(
        provider,
        connect_action=connect_action_fn,
        issue_code=issue_code,
        issue_detail=issue_detail,
    )


def direct_chat_credentials(
    workspace_id: str,
    provider: str,
    *,
    build_provider_credential_candidates_fn: Callable[..., list[dict[str, Any]]],
) -> Dict[str, Any]:
    return direct_chat_provider_service.direct_chat_credentials(
        workspace_id,
        provider,
        build_provider_credential_candidates_fn=build_provider_credential_candidates_fn,
    )


def normalize_reasoning_effort(value: str = "") -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if normalized in {"low", "medium", "high", "max"}:
        return normalized
    return None


def direct_chat_error_reply(
    llm_error: str,
    *,
    chat_iteration_limit_reply_fn: Callable[[int], str],
    safe_positive_int_fn: Callable[[Any, int], int],
    chat_max_iterations_default: int,
) -> str:
    _ = (
        llm_error,
        chat_iteration_limit_reply_fn,
        safe_positive_int_fn,
        chat_max_iterations_default,
    )
    return ""
