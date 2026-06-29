from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence

from server_modules import skills_service
from server_modules import tool_registry_service


@dataclass(frozen=True)
class DirectChatToolPolicyCallbacks:
    compact_text: Callable[[Any], str]
    question_like: Callable[[str], bool]
    mentions_any: Callable[[str, Sequence[str]], bool]
    extract_first_path_reference: Callable[[str], str]
    extract_first_url: Callable[[str], str]
    provider_supports_direct_tool_calls: Callable[[str], bool]
    is_obvious_smtp_write_request: Callable[[str], bool]
    google_workspace_keywords: Sequence[str]
    smtp_keywords: Sequence[str]
    telegram_keywords: Sequence[str]
    slack_keywords: Sequence[str]
    discord_keywords: Sequence[str]
    dropbox_keywords: Sequence[str]
    s3_keywords: Sequence[str]
    browser_keywords: Sequence[str]
    local_file_keywords: Sequence[str]
    local_shell_keywords: Sequence[str]
    local_screenshot_keywords: Sequence[str]
    local_computer_control_keywords: Sequence[str]
    web_lookup_keywords: Sequence[str]
    http_request_keywords: Sequence[str]
    image_generation_keywords: Sequence[str]
    llm_task_keywords: Sequence[str]


def provider_supports_direct_tool_calls(provider: str) -> bool:
    return bool(str(provider or "").strip())


def _compact_intent_text(message: str) -> str:
    return " ".join(str(message or "").strip().lower().split())


def message_has_web_lookup_intent(message: str) -> bool:
    compact = _compact_intent_text(message)
    if not compact:
        return False
    if any(
        token in compact
        for token in (
            "current directory",
            "current folder",
            "current working directory",
            "current workspace",
            "cwd",
        )
    ):
        return False
    if re.search(r"\b(?:web\s+search|search\s+(?:the\s+)?web|search\s+online|internet\s+search)\b", compact):
        return True
    if re.search(r"\b(?:look\s+up|lookup|research)\b", compact):
        return True
    if re.search(r"\b(?:latest|news)\b", compact):
        return True
    if re.search(r"\b(?:current|today'?s?)\b", compact) and any(
        token in compact
        for token in ("about", "news", "on ", "price", "release", "status", "update", "version", "weather")
    ):
        return True
    if re.search(r"\b(?:find|check|search)\b.+\b(?:online|on\s+the\s+web|on\s+the\s+internet)\b", compact):
        return True
    if re.search(r"https?://", str(message or ""), flags=re.IGNORECASE) and any(
        token in compact
        for token in (
            "summarize",
            "fetch",
            "read",
            "what's on",
            "whats on",
            "look up",
            "check",
        )
    ):
        return True
    return False


def message_has_browser_automation_intent(message: str) -> bool:
    compact = _compact_intent_text(message)
    if not compact:
        return False
    if re.search(r"https?://", str(message or ""), flags=re.IGNORECASE) and any(
        token in compact
        for token in (
            "go to",
            "open",
            "visit",
            "click",
            "fill",
            "page title",
            "main heading",
            "screenshot",
            "screen shot",
        )
    ):
        return True
    if re.search(
        r"\b(?:(?:use|open|launch|control|drive|automate)\s+(?:the\s+)?browser|browser\s+(?:automation|profile|session|tab|window|control))\b",
        compact,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"\b(?:open|go\s+to|visit)\s+(?:https?://|www\.|[a-z0-9][a-z0-9.-]*\.[a-z]{2,})(?:\b|/)",
        compact,
        flags=re.IGNORECASE,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:open\s+(?:a\s+)?(?:website|site|webpage|page)|click|fill\s+form|page\s+title|main\s+heading|screenshot|screen\s+shot)\b",
            compact,
            flags=re.IGNORECASE,
        )
    )


def build_local_direct_chat_tools(availability: Dict[str, Any], *, local_worker_available: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
    return skills_service.build_local_direct_chat_tools(
        availability,
        local_worker_available=local_worker_available,
    )


def build_direct_chat_tools(tool_capabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return skills_service.build_direct_chat_tools(tool_capabilities)


def build_builtin_direct_chat_tools() -> List[Dict[str, Any]]:
    return skills_service.build_builtin_direct_chat_tools()


def build_always_on_direct_chat_tools() -> List[Dict[str, Any]]:
    """Return only the always-on tier tools (~8 tools, ~800 tokens).

    These are injected every turn. All other tools live in the registry
    and are loaded on demand via query_tool_registry.
    """
    return tool_registry_service.build_always_on_tool_definitions()


def build_registry_entries(
    tool_capabilities: List[Dict[str, Any]],
    availability_payload: Dict[str, Any],
    *,
    local_worker_available: Any = None,
) -> List[tool_registry_service.RegistryEntry]:
    """Build the full tool registry for lazy-loading via query_tool_registry."""
    return tool_registry_service.build_registry_entries(
        tool_capabilities,
        availability_payload,
        local_worker_available=local_worker_available,
    )


def search_tool_registry(
    query: str,
    registry: List[tool_registry_service.RegistryEntry],
    *,
    max_results: int = 5,
    availability_payload: Any = None,
) -> List[Dict[str, Any]]:
    """Search the tool registry by keyword matching."""
    return tool_registry_service.search_tool_registry(
        query,
        registry,
        max_results=max_results,
        availability_payload=availability_payload,
    )


def format_registry_result(
    matched_tools: List[Dict[str, Any]],
    query: str,
) -> str:
    """Format registry search results for the agent."""
    return tool_registry_service.format_registry_result(matched_tools, query)


def registered_direct_chat_tool_names_for_logging() -> List[str]:
    return skills_service.registered_direct_chat_tool_names_for_logging()


def message_requests_http_request_tool(message: str, callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact or callbacks.question_like(compact):
        return False
    if bool(re.search(r"https?://", str(message or ""), flags=re.IGNORECASE)):
        return True
    return callbacks.mentions_any(compact, callbacks.http_request_keywords)


def message_requests_image_generation_tool(message: str, callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact or callbacks.question_like(compact):
        return False
    return callbacks.mentions_any(compact, callbacks.image_generation_keywords)


def message_requests_browser_tool(message: str, callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact or callbacks.question_like(compact):
        return False
    if callbacks.extract_first_path_reference(message) and any(token in compact for token in ("read", "open", "show", "what's in", "whats in", "count", "how many")):
        return False
    if message_has_browser_automation_intent(message):
        return True
    if callbacks.extract_first_url(message) and (
        any(token in compact for token in ("go to", "open", "visit", "click", "fill", "title", "heading", "screenshot", "screen shot"))
    ):
        return True
    return False


def message_requests_web_lookup_tool(message: str, callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact:
        return False
    if message_requests_browser_tool(message, callbacks):
        return False
    explicit_keywords = tuple(
        keyword
        for keyword in callbacks.web_lookup_keywords
        if str(keyword or "").strip().lower() not in {"current", "today", "web", "website", "online"}
    )
    return message_has_web_lookup_intent(message) or callbacks.mentions_any(compact, explicit_keywords)


def message_can_use_direct_connector_tools(message: str, *, provider: str, tools: List[Dict[str, Any]], callbacks: DirectChatToolPolicyCallbacks) -> bool:
    if not tools:
        return False
    compact = callbacks.compact_text(message)
    if callbacks.mentions_any(compact, callbacks.google_workspace_keywords):
        if any(str(item.get("name") or "").startswith("google_workspace__") for item in tools):
            return True
        if callbacks.is_obvious_smtp_write_request(compact):
            return any(str(item.get("name") or "").startswith("smtp__") for item in tools)
        return False
    if callbacks.mentions_any(compact, callbacks.smtp_keywords) or callbacks.is_obvious_smtp_write_request(compact):
        return any(str(item.get("name") or "").startswith("smtp__") for item in tools)
    if callbacks.mentions_any(compact, callbacks.telegram_keywords):
        return any(str(item.get("name") or "").startswith("telegram_bot__") for item in tools)
    if callbacks.mentions_any(compact, callbacks.slack_keywords):
        return any(str(item.get("name") or "").startswith("slack__") for item in tools)
    if callbacks.mentions_any(compact, callbacks.discord_keywords):
        return any(str(item.get("name") or "").startswith("discord_bot__") for item in tools)
    if callbacks.mentions_any(compact, callbacks.dropbox_keywords):
        return any(str(item.get("name") or "").startswith("dropbox__") for item in tools)
    if callbacks.mentions_any(compact, callbacks.s3_keywords):
        return any(str(item.get("name") or "").startswith("s3__") for item in tools)
    return False


def looks_like_local_path_request(compact_message: str) -> bool:
    if not compact_message:
        return False
    return bool(re.search(r"(^|\s)(/|~/|\./|\.\./|[a-z]:[/\\])", compact_message, flags=re.IGNORECASE))


_LOCAL_TOOL_REQUEST_OPENERS = (
    "append",
    "capture",
    "check",
    "click",
    "close",
    "copy",
    "delete",
    "execute",
    "find",
    "inspect",
    "launch",
    "list",
    "open",
    "paste",
    "press",
    "read",
    "run",
    "save",
    "search",
    "show",
    "take",
    "type",
    "write",
)

_GENERIC_LOCAL_FILE_KEYWORDS = {
    "directory",
    "file",
    "folder",
    "path",
    "repo",
    "repository",
}

_LOCAL_FILE_ACTION_TOKENS = (
    "append",
    "check",
    "count",
    "delete",
    "find",
    "inspect",
    "list",
    "open",
    "read",
    "save",
    "search",
    "show",
    "write",
)

_LOCAL_SYSTEM_INFO_ACTION_TOKENS = (
    "check",
    "detect",
    "get",
    "inspect",
    "list",
    "see",
    "show",
    "summarize",
    "tell me",
    "what do i have",
    "what hardware",
    "what i have",
    "what is in",
    "what specs",
    "what system",
    "what's in",
)

_LOCAL_SYSTEM_INFO_TARGET_PATTERNS = (
    r"\b(?:my|this|local|current|the)\s+(?:hardware|machine|computer|device|mac|macbook|laptop|system)\b",
    r"\b(?:device|machine|system)\s+(?:info|information|specs|specifications|details|profile)\b",
    r"\b(?:hardware|device|machine|system)\s+(?:info|information|specs|specifications|details|profile)\b.*\b(?:my|this|local|current)\s+(?:machine|computer|device|mac|macbook|laptop|system)\b",
    r"\b(?:cpu|ram|memory|disk|storage|os)\s+(?:info|information|specs|details|usage|size)\b",
    r"\b(?:hardware\s+)?(?:specs|specifications)\b.*\b(?:my|this|local|current)\s+(?:machine|computer|device|mac|macbook|laptop|system)\b",
    r"\b(?:specs|specifications)\s+(?:of|for|on)\s+(?:my|this|local|current)\s+(?:machine|computer|device|mac|macbook|laptop)\b",
)

_LOCAL_WORKING_DIRECTORY_PATTERNS = (
    r"\b(?:what|which)\s+(?:folder|directory)\s+(?:are\s+you|am\s+i|is\s+(?:sage|the\s+agent|the\s+runtime))\s+(?:in|using|running\s+from)\b",
    r"\bwhere\s+(?:are\s+you|am\s+i|is\s+(?:sage|the\s+agent|the\s+runtime))\s+(?:running|running\s+from|located)\b",
    r"\b(?:current\s+working\s+directory|current\s+directory|current\s+folder)\b",
    r"\b(?:show|check|get|tell\s+me|print)\s+(?:the\s+)?(?:cwd|current\s+working\s+directory|current\s+directory|current\s+folder)\b",
    r"\b(?:pwd|present\s+working\s+directory)\b",
)


def starts_like_local_tool_request(compact_message: str) -> bool:
    compact = str(compact_message or "").strip().lower()
    if not compact:
        return False
    opener_pattern = "|".join(re.escape(item) for item in _LOCAL_TOOL_REQUEST_OPENERS)
    return bool(
        re.match(
            rf"^(?:please\s+|can you\s+|could you\s+|would you\s+)?(?:{opener_pattern})\b",
            compact,
            flags=re.IGNORECASE,
        )
    )


def looks_like_pasted_chat_context(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if text.count("\n") >= 5:
        return True
    if len(text) >= 900:
        return True
    paste_markers = (
        "@path/to/file",
        "architectural layer audit",
        "listed directory",
        "ran command:",
        "readfile",
        "type your message",
        "viewed ",
        "workspace (/directory)",
        "│",
        "┌",
        "└",
        "▀",
        "▄",
        "✓",
    )
    return any(marker in lowered or marker in text for marker in paste_markers)


def should_skip_local_tool_prefetch(message: str, compact_message: str) -> bool:
    return looks_like_pasted_chat_context(message) and not starts_like_local_tool_request(compact_message)


def looks_like_local_system_info_request(compact_message: str) -> bool:
    compact = str(compact_message or "").strip().lower()
    if not compact:
        return False
    has_action = starts_like_local_tool_request(compact) or any(token in compact for token in _LOCAL_SYSTEM_INFO_ACTION_TOKENS)
    if not has_action:
        return False
    return any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in _LOCAL_SYSTEM_INFO_TARGET_PATTERNS)


def looks_like_local_working_directory_request(compact_message: str) -> bool:
    compact = str(compact_message or "").strip().lower()
    if not compact:
        return False
    return any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in _LOCAL_WORKING_DIRECTORY_PATTERNS)


def _mentions_explicit_local_file_keyword(compact_message: str, keywords: Sequence[str]) -> bool:
    for raw_keyword in keywords:
        keyword = str(raw_keyword or "").strip().lower()
        if not keyword or keyword in _GENERIC_LOCAL_FILE_KEYWORDS:
            continue
        if keyword in compact_message:
            return True
    return False


def _path_action_requested_near_reference(compact_message: str, path_reference: str) -> bool:
    path = str(path_reference or "").strip().lower()
    if not path:
        return False
    path_index = compact_message.find(path)
    if path_index < 0:
        return starts_like_local_tool_request(compact_message)
    window_start = max(0, path_index - 80)
    window_end = min(len(compact_message), path_index + len(path) + 80)
    window = compact_message[window_start:window_end]
    return any(re.search(rf"\b{re.escape(token)}\b", window) for token in _LOCAL_FILE_ACTION_TOKENS)


def message_requests_local_file_tool(message: str, callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact or callbacks.question_like(compact):
        return False
    if should_skip_local_tool_prefetch(message, compact):
        return False
    if _mentions_explicit_local_file_keyword(compact, callbacks.local_file_keywords):
        return True
    listing_tokens = (
        "list files",
        "show files",
        "what files",
        "which files",
        "files on my",
        "files in my",
        "files in the",
        "contents of",
        "folder contents",
        "directory contents",
        "directory listing",
        "list the contents",
    )
    location_tokens = (
        "desktop",
        "downloads",
        "documents",
        "folder",
        "directory",
        "repo",
        "repository",
        "project root",
        "current folder",
        "current directory",
    )
    if any(token in compact for token in listing_tokens) and any(token in compact for token in location_tokens):
        return True
    path_reference = callbacks.extract_first_path_reference(message)
    if path_reference and _path_action_requested_near_reference(compact, path_reference):
        return True
    return looks_like_local_path_request(compact) and starts_like_local_tool_request(compact)


def message_requests_local_shell_tool(message: str, callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact:
        return False
    if should_skip_local_tool_prefetch(message, compact):
        return False
    if looks_like_local_system_info_request(compact) or looks_like_local_working_directory_request(compact):
        return True
    if callbacks.question_like(compact):
        return False
    text = str(message or "")
    if re.search(r"\b(?:run|execute)\s+(?:this\s+)?(?:shell\s+|terminal\s+)?command\s*[:：]", text, flags=re.IGNORECASE):
        return True
    return bool(re.search(r"`[^`]+`", text)) and any(token in compact for token in ("run", "exec", "execute", "shell", "terminal", "command"))


def message_requests_local_screenshot_tool(message: str, callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact:
        return False
    if should_skip_local_tool_prefetch(message, compact):
        return False
    if callbacks.question_like(compact):
        return False
    if callbacks.mentions_any(compact, callbacks.local_screenshot_keywords):
        return starts_like_local_tool_request(compact) or bool(
            re.search(r"\b(?:capture|take|grab|show|get)\b.*\b(?:screenshot|screen\s+shot|screen)\b", compact)
        )
    return False


def message_requests_local_computer_tool(message: str, callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact:
        return False
    if should_skip_local_tool_prefetch(message, compact):
        return False
    if callbacks.question_like(compact):
        return False
    if callbacks.mentions_any(compact, callbacks.local_computer_control_keywords):
        return True
    return bool(
        re.search(
            r"\b(?:click|press|type|move|drag|ocr|read)\b.+\b(?:screen|window|button|mouse|keyboard|active\s+window)\b",
            compact,
            flags=re.IGNORECASE,
        )
    )


def message_can_use_direct_local_tools(message: str, *, provider: str, tools: List[Dict[str, Any]], callbacks: DirectChatToolPolicyCallbacks) -> bool:
    if not tools:
        return False
    tool_names = {str(item.get("name") or "").strip() for item in tools if isinstance(item, dict)}
    hardware_tool_available = "hardware__action" in tool_names
    if message_requests_local_computer_tool(message, callbacks) and (
        any(name.startswith("computer__") for name in tool_names) or hardware_tool_available
    ):
        return True
    if message_requests_local_file_tool(message, callbacks) and (bool({"file__read", "file__write"} & tool_names) or hardware_tool_available):
        return True
    if message_requests_local_shell_tool(message, callbacks) and ("shell__exec" in tool_names or hardware_tool_available):
        return True
    if message_requests_local_screenshot_tool(message, callbacks) and ("screenshot__capture" in tool_names or hardware_tool_available):
        return True
    return False


def message_can_use_builtin_direct_tools(message: str, *, tools: List[Dict[str, Any]], callbacks: DirectChatToolPolicyCallbacks) -> bool:
    compact = callbacks.compact_text(message)
    if not compact or not tools:
        return False
    tool_names = {str(item.get("name") or "").strip() for item in tools if isinstance(item, dict)}
    if {"web__search", "web__fetch"} & tool_names:
        if message_requests_web_lookup_tool(message, callbacks):
            return True
    if "http_request" in tool_names and message_requests_http_request_tool(message, callbacks):
        return True
    if "generate_image" in tool_names and message_requests_image_generation_tool(message, callbacks):
        return True
    if any(name.startswith("browser__") for name in tool_names) and message_requests_browser_tool(message, callbacks):
        return True
    if "hardware__action" in tool_names and (
        message_requests_local_file_tool(message, callbacks)
        or message_requests_local_shell_tool(message, callbacks)
        or message_requests_local_screenshot_tool(message, callbacks)
        or message_requests_local_computer_tool(message, callbacks)
    ):
        return True
    if "llm__task" in tool_names and callbacks.mentions_any(compact, callbacks.llm_task_keywords):
        return True
    return False


def message_requests_tool_inventory(message: str) -> bool:
    compact = " ".join(str(message or "").lower().split())
    if not compact:
        return False
    explicit_commands = {
        "/tools",
        "/tool inventory",
        "/capabilities",
        "tool inventory",
        "show tool inventory",
        "list tool inventory",
        "open tool inventory",
        "what can you do in this environment",
        "what can you do in this environment?",
    }
    if compact in explicit_commands:
        return True
    if compact.endswith("?"):
        compact = compact[:-1].strip()
    capability_questions = (
        "what capabilities do you have",
        "what tools do you have",
    )
    return any(compact.startswith(question) for question in capability_questions)


def _tool_group_label(name: str) -> str:
    normalized = str(name or "").strip()
    connector = normalized.split("__", 1)[0] if "__" in normalized else normalized
    if connector in {"file", "shell", "screenshot", "computer", "browser", "hardware"}:
        return "Local machine"
    if connector in {"web", "http"}:
        return "Web"
    if connector in {"image"} or normalized == "generate_image":
        return "Media"
    if connector in {"telegram_bot", "smtp", "google_workspace", "microsoft_365", "slack", "discord_bot"}:
        return "Communication"
    if connector in {"memory", "llm", "sage_service"}:
        return "Data"
    return "Other"


def _tool_label(tool: Dict[str, Any]) -> str:
    name = str(tool.get("name") or "").strip()
    description = str(tool.get("description") or "").strip()
    if not name:
        return ""
    if description:
        return description
    public_name = name.split("__", 1)[-1]
    return public_name.replace("_", " ").strip().capitalize()


def _status_summary_line(title: str, detail: str) -> str:
    text = str(detail or "").strip()
    return f"{title}: {text or 'none'}"


def _state_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "unknown"
    return normalized.replace("_", " ")


def _collect_state_labels(items: Any, *, limit: int = 4) -> str:
    labels: List[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("id") or "").strip()
        if not label:
            continue
        state = item.get("runtime_usable")
        if state is True:
            summary = label
        elif state is False:
            summary = f"{label} (not ready)"
        elif item.get("connected"):
            summary = f"{label} (connected)"
        else:
            summary = f"{label} (not connected)"
        if summary in labels:
            continue
        labels.append(summary)
        if len(labels) >= limit:
            break
    return ", ".join(labels) if labels else "none"


def _public_setup_label(label: str) -> str:
    text = str(label or "").strip()
    return text.replace("My Computer", "Agent Computer").replace("gateway", "Agent Computer")


def direct_chat_tool_inventory_reply(tools: List[Dict[str, Any]], availability_payload: Dict[str, Any]) -> str:
    grouped: Dict[str, List[str]] = {}
    for item in tools:
        if not isinstance(item, dict):
            continue
        label = _tool_label(item)
        if not label:
            continue
        grouped.setdefault(_tool_group_label(str(item.get("name") or "")), []).append(label)

    gateway_online = bool(
        availability_payload.get("local_gateway_online")
        or availability_payload.get("gateway_online")
        or availability_payload.get("local_worker_online")
    )
    cloud_computer_online = bool(
        availability_payload.get("cloud_computer_online")
        or availability_payload.get("cloud_computer_available")
    )
    capability_truth = (
        availability_payload.get("capability_truth")
        if isinstance(availability_payload.get("capability_truth"), dict)
        else {}
    )
    provider_truth = capability_truth.get("provider") if isinstance(capability_truth.get("provider"), dict) else {}
    credits_truth = capability_truth.get("credits") if isinstance(capability_truth.get("credits"), dict) else {}
    my_computer = capability_truth.get("my_computer") if isinstance(capability_truth.get("my_computer"), dict) else {}
    connected_apps = capability_truth.get("connected_apps") if isinstance(capability_truth.get("connected_apps"), list) else []
    channels = capability_truth.get("channels") if isinstance(capability_truth.get("channels"), list) else []
    byok_providers = capability_truth.get("byok_providers") if isinstance(capability_truth.get("byok_providers"), list) else []
    setup_actions = (
        capability_truth.get("required_setup_actions")
        if isinstance(capability_truth.get("required_setup_actions"), list)
        else []
    )
    provider_ready = bool(provider_truth.get("ai_ready"))
    credits_enabled = bool(credits_truth.get("hosted_enabled"))
    credits_reason = _state_label(credits_truth.get("reason"))
    my_computer_state = _state_label(my_computer.get("state"))
    byok_labels = [
        str(item.get("label") or item.get("provider_id") or "").strip()
        for item in byok_providers
        if isinstance(item, dict) and str(item.get("label") or item.get("provider_id") or "").strip()
    ]

    lines = ["Here is what Sage can do in this workspace right now:"]
    lines.append("")
    lines.append(
        _status_summary_line(
            "Sage AI",
            "ready" if provider_ready else "setup required",
        )
    )
    lines.append(
        _status_summary_line(
            "Workspace AI",
            "available" if credits_enabled else f"blocked ({credits_reason})",
        )
    )
    lines.append(_status_summary_line("Agent Computer", my_computer_state))
    lines.append(_status_summary_line("Connected apps", _collect_state_labels(connected_apps)))
    lines.append(_status_summary_line("Messaging channels", _collect_state_labels(channels)))
    if byok_labels:
        lines.append(_status_summary_line("Additional AI connections", f"{min(len(byok_labels), 5)} configured"))
    lines.append("")
    lines.append("Available capabilities:")
    if not grouped:
        lines.append("")
        lines.append("- No capabilities are currently available.")
    for group in ("Local machine", "Web", "Media", "Communication", "Data", "Other"):
        entries = sorted(grouped.get(group, []))
        if not entries:
            continue
        lines.append("")
        group_label = "Agent Computer" if group == "Local machine" else group
        lines.append(f"{group_label}:")
        lines.extend(f"- {entry}" for entry in entries)
    lines.append("")
    if gateway_online:
        lines.append("This Device capabilities are available through the paired Agent Computer.")
    elif cloud_computer_online:
        lines.append(
            "Computer capabilities are available through Sage Cloud Computer. This Device still requires a paired Agent Computer."
        )
    else:
        lines.append("This Device capabilities require Agent Computer to be online. Sage Cloud Computer can run cloud-side computer tasks only when enabled.")
    if setup_actions:
        labels = [
            _public_setup_label(str(item.get("label") or "").strip())
            for item in setup_actions
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ]
        if labels:
            lines.append(f"Setup available now: {', '.join(labels[:4])}.")
    lines.append("Capability availability is based on Agent Computer status, connector state, and workspace policy, not the selected model provider.")
    return "\n".join(lines).strip()
