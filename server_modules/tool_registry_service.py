"""
Dynamic tool registry with keyword search.

Instead of injecting all 48+ tools into every inference request (~6k tokens),
the agent receives 8 always-on tools plus ``query_tool_registry``. When the
agent needs a capability it does not see, it calls ``query_tool_registry`` with
a task description. The registry returns the 3-5 most relevant tools via
keyword matching, and the generation loop injects them for subsequent
iterations within the same turn.

Token savings: ~87% for simple Q&A turns, ~75% for typical tool-using turns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional

from server_modules import skills_service


# ── Always-on tool names ──────────────────────────────────────────────────────
# These are injected every turn (~800 tokens). Everything else lives in the
# registry and is loaded on demand via query_tool_registry.
ALWAYS_ON_TOOL_NAMES: frozenset = frozenset({
    "task_complete",
    "memory_write",
    "memory_read",
    "memory_search",
    "memory_get",
    "memory_update",
    "memory_append_daily_note",
    "web__search",
    "web__fetch",
    "hardware__action",
    "query_tool_registry",
})


# ── Registry entry ────────────────────────────────────────────────────────────

@dataclass(slots=True)
class RegistryEntry:
    """A single tool in the searchable registry."""
    tool_name: str
    description: str
    connector_id: str = ""
    keywords: List[str] = field(default_factory=list)
    tool_definition: Dict[str, Any] = field(default_factory=dict)
    category: str = "other"  # communication, browser, computer, media, data, other


def _extract_keywords(name: str, description: str, connector_id: str) -> List[str]:
    """Extract searchable keywords from a tool name and description."""
    text = f"{name} {description} {connector_id}".lower()
    # Normalize: replace underscores, hyphens, dots with spaces
    text = re.sub(r"[_.\-]+", " ", text)
    tokens = set(text.split())
    # Remove very short/stop words
    stop = {"a", "an", "the", "to", "for", "of", "in", "on", "at", "by", "or",
            "and", "is", "it", "its", "be", "as", "from", "with", "this", "that",
            "via", "use", "can", "will", "when", "are", "was", "not", "no", "yes"}
    tokens -= stop
    # Remove tokens shorter than 3 chars (unless they're meaningful acronyms)
    meaningful_short = {"ai", "ui", "ux", "os", "db", "api", "url", "id", "ip",
                        "js", "css", "ocr", "pdf", "csv", "xml", "json", "ssh",
                        "sms", "mcp", "cli", "sdk", "vps"}
    tokens = {t for t in tokens if len(t) >= 3 or t in meaningful_short}
    return sorted(tokens)


def _categorize_tool(name: str, connector_id: str) -> str:
    """Assign a tool to a category for result grouping."""
    n = str(name or "").strip().lower()
    c = str(connector_id or "").strip().lower()
    comm_connectors = {"smtp", "telegram_bot", "slack", "discord_bot",
                       "google_workspace", "microsoft_365", "whatsapp"}
    if c in comm_connectors or any(t in n for t in ("send_message", "send_email",
        "send_dm", "post_message", "create_email", "draft_email")):
        return "communication"
    if c in {"browser", "browser_automation"} or n.startswith("browser__"):
        return "browser"
    if c in {"file", "shell", "screenshot", "computer"} or any(
        n.startswith(p) for p in ("file__", "shell__", "screenshot__", "computer__")):
        return "computer"
    if c in {"image", "llm"} or n in {"generate_image", "llm__task"}:
        return "media"
    if c in {"memory", "sage_service"} or n.startswith(("memory", "sage_service__")):
        return "data"
    if c in {"web", "http"} or n.startswith(("web__", "http_")):
        return "web"
    if c == "fleet" or n.startswith("fleet__"):
        return "management"
    # App connectors: check if it looks like a connected app tool
    if "__" in n:
        prefix = n.split("__")[0]
        if prefix not in {"memory", "web", "browser", "computer", "file", "shell",
                          "screenshot", "hardware", "llm", "sage_service", "http",
                          "image", "messaging", "generate", "fleet"}:
            return "app"
    return "other"


def _build_registry_entry_from_tool_payload(
    tool: Dict[str, Any],
) -> RegistryEntry:
    """Build a RegistryEntry from a tool payload dict."""
    name = str(tool.get("name") or "").strip()
    description = str(tool.get("description") or "").strip()
    connector_id = str(tool.get("connector_id") or "").strip().lower()
    keywords = _extract_keywords(name, description, connector_id)
    category = _categorize_tool(name, connector_id)

    # Build OpenAI function-calling schema
    params = tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {}
    function_def: Dict[str, Any] = {
        "name": name,
        "description": description,
    }
    if params:
        function_def["parameters"] = params

    return RegistryEntry(
        tool_name=name,
        description=description,
        connector_id=connector_id,
        keywords=keywords,
        tool_definition={
            "type": "function",
            "function": function_def,
        },
        category=category,
    )


def _build_registry_entry_from_tool_descriptor(
    descriptor: skills_service.ToolDescriptor,
) -> RegistryEntry:
    """Build a RegistryEntry from a ToolDescriptor."""
    name = descriptor.tool_name
    description = descriptor.description
    connector_id = descriptor.connector_id
    keywords = _extract_keywords(name, description, connector_id)
    category = _categorize_tool(name, connector_id)

    params = descriptor.parameters if isinstance(descriptor.parameters, dict) else {}
    function_def: Dict[str, Any] = {
        "name": name,
        "description": description,
    }
    if params:
        function_def["parameters"] = params

    return RegistryEntry(
        tool_name=name,
        description=description,
        connector_id=connector_id,
        keywords=keywords,
        tool_definition={
            "type": "function",
            "function": function_def,
        },
        category=category,
    )


# ── Registry construction ─────────────────────────────────────────────────────

def build_registry_entries(
    tool_capabilities: List[Dict[str, Any]],
    availability_payload: Dict[str, Any],
    *,
    local_worker_available: Any = None,
) -> List[RegistryEntry]:
    """Build the full registry from all available tool sources.

    Includes everything NOT in the always-on set: remaining built-in tools,
    local tools, and connected app tools.
    """
    entries: List[RegistryEntry] = []
    seen: set[str] = set(ALWAYS_ON_TOOL_NAMES)

    # 1. Remaining built-in tools (not in always-on set)
    for descriptor in skills_service._builtin_tool_descriptors():
        if descriptor.tool_name in seen:
            continue
        seen.add(descriptor.tool_name)
        entries.append(_build_registry_entry_from_tool_descriptor(descriptor))

    # 2. Local tools (not in always-on set)
    for descriptor in skills_service._local_tool_descriptors():
        if descriptor.tool_name in seen:
            continue
        seen.add(descriptor.tool_name)
        entries.append(_build_registry_entry_from_tool_descriptor(descriptor))

    # 3. Connected app tools (from tool_capabilities)
    # Build through the normal pipeline to get tool payloads
    app_tools = skills_service.build_direct_chat_tools(tool_capabilities)
    for tool in app_tools:
        name = str(tool.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        entries.append(_build_registry_entry_from_tool_payload(tool))

    return entries


# ── Search ────────────────────────────────────────────────────────────────────

def search_tool_registry(
    query: str,
    registry: List[RegistryEntry],
    *,
    max_results: int = 5,
    availability_payload: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Search the registry by keyword matching against tool names and descriptions.

    Returns the top *max_results* tool definitions (OpenAI function-calling
    schema dicts), scored by keyword overlap.

    If *availability_payload* is provided, each result includes a
    ``_credential_note`` key when the tool's connector is not authenticated.
    """
    if not query.strip() or not registry:
        return []

    query_tokens = set(query.lower().split())
    # Remove stop words from query too
    _stop = {"a", "an", "the", "to", "for", "of", "in", "on", "at", "by", "or",
             "and", "is", "it", "its", "be", "as", "from", "with", "this", "that"}
    query_tokens -= _stop

    if not query_tokens:
        return []

    scored: List[tuple] = []  # (score, entry)
    for entry in registry:
        score = 0
        entry_kw_lower = {k.lower() for k in entry.keywords}
        for token in query_tokens:
            if token in entry_kw_lower:
                score += 1
            # Partial match: token is a substring of a keyword or vice versa
            elif any(token in kw or kw in token for kw in entry_kw_lower):
                score += 0.5
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)

    results: List[Dict[str, Any]] = []
    for _, entry in scored[:max_results]:
        result = dict(entry.tool_definition)
        # Add credential note if availability payload shows connector not authenticated
        if availability_payload and entry.connector_id:
            cred_note = _credential_note(entry.connector_id, availability_payload)
            if cred_note:
                result["_credential_note"] = cred_note
        results.append(result)

    return results


def _credential_note(
    connector_id: str,
    availability_payload: Dict[str, Any],
) -> str:
    """Check if a connector has active credentials, return a note if not."""
    connector = str(connector_id or "").strip().lower()
    if not connector:
        return ""

    # Known built-in connectors that don't need auth
    builtin_connectors = {
        "memory", "web", "browser", "http", "image", "llm",
        "sage_service", "messaging", "hardware", "file", "shell",
        "screenshot", "computer",
    }
    if connector in builtin_connectors:
        return ""

    # Check capability state in availability
    tool_capabilities = availability_payload.get("tool_capabilities")
    if not isinstance(tool_capabilities, list):
        return ""

    for cap in tool_capabilities:
        if not isinstance(cap, dict):
            continue
        cap_id = str(cap.get("id") or "").strip().lower()
        if cap_id == connector or cap_id.replace("_", "") == connector.replace("_", ""):
            connected = bool(cap.get("connected"))
            authenticated = cap.get("authenticated")
            runtime_usable = cap.get("runtime_usable")

            if not connected:
                label = str(cap.get("label") or connector).strip()
                return (
                    f"Note: {label} is not connected. "
                    f"Ask the user to connect it in Settings → Connectors first."
                )
            if authenticated is False:
                label = str(cap.get("label") or connector).strip()
                return (
                    f"Note: {label} is connected but credentials are invalid. "
                    f"Ask the user to re-authenticate in Settings → Connectors."
                )
            if runtime_usable is False:
                label = str(cap.get("label") or connector).strip()
                return (
                    f"Note: {label} is connected but not currently available. "
                    f"It may need setup or the service may be unreachable."
                )
            # Connected and authenticated — no note needed
            return ""

    # Connector not found in capabilities at all
    return ""

def _connector_label(connector_id: str, availability_payload: Dict[str, Any]) -> str:
    """Get a human-readable label for a connector from availability."""
    connector = str(connector_id or "").strip().lower()
    tool_capabilities = availability_payload.get("tool_capabilities")
    if isinstance(tool_capabilities, list):
        for cap in tool_capabilities:
            if not isinstance(cap, dict):
                continue
            if str(cap.get("id") or "").strip().lower() == connector:
                return str(cap.get("label") or connector).strip()
    return connector


# ── Always-on tool definitions ─────────────────────────────────────────────────

def build_always_on_tool_definitions() -> List[Dict[str, Any]]:
    """Return the always-on tool definitions in flat format.

    These ~11 tools are injected into every inference request (~800 tokens).
    The OpenAI {type: "function", function: {...}} wrapper is applied at the
    API call site (iter_openai_compatible_chat_events), not here. This keeps
    the format consistent for internal consumers (_dedupe_tools,
    _available_tool_names, etc.) that read tool.get("name") directly.
    """
    tools: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for descriptor in skills_service._builtin_tool_descriptors():
        if descriptor.tool_name in ALWAYS_ON_TOOL_NAMES and descriptor.tool_name not in seen:
            seen.add(descriptor.tool_name)
            payload = skills_service._tool_payload_from_descriptor(descriptor)
            params = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
            tool_def: Dict[str, Any] = {
                "name": payload["name"],
                "description": payload["description"],
            }
            if params:
                tool_def["parameters"] = params
            # Include connector_id for routing
            if payload.get("connector_id"):
                tool_def["connector_id"] = payload["connector_id"]
            tools.append(tool_def)

    return tools


def get_query_tool_registry_definition() -> Dict[str, Any]:
    """Return the query_tool_registry tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "query_tool_registry",
            "description": (
                "Search for available tools and capabilities that are not in your "
                "default tool set. Call this when you need to perform an action "
                "(send email, manage calendar, control browser, edit files, etc.) "
                "but do not see the relevant tool in your available tools. "
                "Returns the 3-5 most relevant tools with their full schemas so "
                "you can call them in subsequent steps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": (
                            "Describe what you need to do in plain language, e.g. "
                            "'send an email to client', 'search Gmail for invoices', "
                            "'create a calendar event', 'take a screenshot', "
                            "'read a file from the computer'. Be specific about the "
                            "service and action."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of tools to return (default 5, max 10).",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["task_description"],
            },
        },
    }


# ── Result formatting ─────────────────────────────────────────────────────────

def format_registry_result(
    matched_tools: List[Dict[str, Any]],
    query: str,
) -> str:
    """Format registry search results as a readable text response for the agent."""
    if not matched_tools:
        return (
            f"No matching tools found for: \"{query}\". "
            "Try a different description or ask the user for guidance."
        )

    lines = [f"Found {len(matched_tools)} tool(s) for: \"{query}\"", ""]

    for i, tool in enumerate(matched_tools, start=1):
        func = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(func.get("name") or tool.get("name") or "").strip()
        desc = str(func.get("description") or tool.get("description") or "").strip()
        cred_note = str(tool.get("_credential_note") or "").strip()

        lines.append(f"{i}. **{name}** — {desc}")
        if cred_note:
            lines.append(f"   ⚠️  {cred_note}")

    lines.append("")
    lines.append("These tools are now available for you to call in subsequent steps.")
    return "\n".join(lines)
