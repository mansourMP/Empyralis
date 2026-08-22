"""
Dynamic tool registry with BM25 search.

Instead of injecting all 48+ tools into every inference request (~6k tokens),
the agent receives the 12 always-on tools (ALWAYS_ON_TOOL_NAMES, below —
includes ``query_tool_registry`` itself). When the agent needs a capability
it does not see, it calls ``query_tool_registry`` with a task description.
The registry returns the 3-5 most relevant tools via real BM25 ranking
(Okapi BM25, term-frequency / inverse-document-frequency over the registry
corpus, with name/label tokens weighted above description tokens) plus a
small synonym-expansion layer for everyday chat vocabulary that doesn't
share a word-stem with a tool's own name/description text (e.g. "mail" ->
"email", "chat" -> "message"/"slack"). The generation loop injects the
results for subsequent iterations within the same turn.

Token savings: ~87% for simple Q&A turns, ~75% for typical tool-using turns
(estimated at the time the two-tier design was introduced; not re-measured
since — see docs/design/audit-tool-reliability.md §3.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from server_modules import skills_service


# ── Always-on tool names ──────────────────────────────────────────────────────
# These are injected every turn (~800 tokens). Everything else lives in the
# registry and is loaded on demand via query_tool_registry.
ALWAYS_ON_TOOL_NAMES: frozenset = frozenset({
    "task_complete",
    "update_plan",
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


# ── Shared vocabulary ─────────────────────────────────────────────────────────
# One stopword list and one tokenizer, used by BOTH keyword extraction
# (indexing a tool) and query tokenization (searching for one). These used
# to be two independently hand-maintained sets that had quietly drifted
# apart — the query-side list was missing "via"/"use"/"can"/"will"/"when"/
# "are"/"was"/"not"/"no"/"yes", so a token like "can" or "will" would score
# as a real match on the query side but never on the indexed side (audit
# docs/design/audit-tool-reliability.md §3.5). There is exactly one list now.
_STOPWORDS: frozenset = frozenset({
    "a", "an", "the", "to", "for", "of", "in", "on", "at", "by", "or",
    "and", "is", "it", "its", "be", "as", "from", "with", "this", "that",
    "via", "use", "can", "will", "when", "are", "was", "not", "no", "yes",
    "me", "my", "you", "your", "i", "we", "us", "do", "does", "did", "if",
})

# Tokens under 3 chars are dropped unless they're a known meaningful
# short-form (an acronym or protocol name that would otherwise be silently
# discarded, e.g. "ocr", "api", "sms").
_MEANINGFUL_SHORT_TOKENS: frozenset = frozenset({
    "ai", "ui", "ux", "os", "db", "api", "url", "id", "ip",
    "js", "css", "ocr", "pdf", "csv", "xml", "json", "ssh",
    "sms", "mcp", "cli", "sdk", "vps",
})

# Small, hand-curated synonym layer for everyday chat vocabulary that has no
# word-stem in common with a tool's own name/description text — this is the
# exact gap that made plain keyword overlap return zero/wrong results for
# realistic paraphrases like "let the team know on chat" or "notify the
# customer by mail" (audit §3.1). Each query token is expanded to itself
# plus these mapped terms before BM25 scoring. Intentionally small: this is
# a targeted patch for the handful of paraphrases that come up in chat, not
# a general-purpose thesaurus.
_QUERY_SYNONYMS: Dict[str, List[str]] = {
    "mail": ["email"],
    "mails": ["email"],
    "emails": ["email"],
    "emailing": ["email"],
    "chat": ["message", "slack"],
    "chatting": ["message"],
    "text": ["message"],
    "texting": ["message"],
    "msg": ["message"],
    "messaging": ["message"],
    "ping": ["message", "notify"],
    "notify": ["message", "send"],
    "know": ["notify", "tell"],
    "tell": ["notify", "message"],
    "remind": ["calendar", "reminder", "schedule"],
    "reminder": ["calendar", "schedule"],
    "meeting": ["calendar", "event", "schedule"],
    "schedule": ["calendar", "event"],
    "lookup": ["search"],
    "look": ["search"],
    "find": ["search"],
    "team": ["slack", "channel"],
    "invoice": ["billing", "payment"],
    "customer": ["contact", "client"],
    "client": ["customer", "contact"],
    "see": ["view", "read"],
    "display": ["screen"],
    "screen": ["display"],
    "picture": ["screenshot", "image"],
    "photo": ["screenshot", "image"],
}


def _tokenize(text: str) -> List[str]:
    """Normalize free text into search tokens: lowercase, split on
    underscores/dots/hyphens *and* commas/semicolons/colons/slashes (the old
    regex only handled the first group, so a description ending "...titles,
    URLs, and snippets" produced un-matchable keywords "titles," / "urls,"
    with the comma still attached — audit §3.8), drop stopwords, drop
    tokens under 3 chars unless they're a known meaningful short acronym.
    """
    normalized = re.sub(r"[_.\-,;:/\\]+", " ", str(text or "").lower())
    tokens = [t for t in normalized.split() if t]
    return [
        t for t in tokens
        if t not in _STOPWORDS and (len(t) >= 3 or t in _MEANINGFUL_SHORT_TOKENS)
    ]


def _expand_query_tokens(tokens: List[str]) -> List[str]:
    """Expand each query token with its chat-vocabulary synonyms (see
    _QUERY_SYNONYMS above). Duplicates are fine — BM25 naturally weighs
    repeated query terms more, which is the desired behavior here too."""
    expanded = list(tokens)
    for token in tokens:
        expanded.extend(_QUERY_SYNONYMS.get(token, []))
    return expanded


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
    # BM25 fields, kept separate so name/label tokens can be weighted above
    # description tokens (a lightweight BM25F-style field boost — see
    # _bm25_term_frequency). Raw token streams (not deduped), because term
    # frequency within a field is part of the score.
    name_field_tokens: List[str] = field(default_factory=list)
    desc_field_tokens: List[str] = field(default_factory=list)


def _extract_keywords(name: str, description: str, connector_id: str) -> List[str]:
    """Deduped, sorted keyword set for a tool — used by the category
    classifier and for external introspection. Search ranking itself uses
    the field-weighted token lists (name_field_tokens/desc_field_tokens),
    not this set."""
    return sorted(set(_tokenize(f"{name} {description} {connector_id}")))


def _categorize_tool(name: str, connector_id: str) -> str:
    """Assign a tool to a category for result grouping."""
    n = str(name or "").strip().lower()
    c = str(connector_id or "").strip().lower()
    comm_connectors = {"smtp", "telegram_bot", "slack", "discord_bot",
                       "google_workspace", "microsoft_365", "whatsapp", "whatsapp_twilio"}
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
    label = str(tool.get("label") or "").strip()
    keywords = _extract_keywords(name, description, connector_id)
    category = _categorize_tool(name, connector_id)
    name_field_tokens = _tokenize(f"{name} {label}")
    desc_field_tokens = _tokenize(f"{description} {connector_id}")

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
        name_field_tokens=name_field_tokens,
        desc_field_tokens=desc_field_tokens,
    )


def _build_registry_entry_from_tool_descriptor(
    descriptor: skills_service.ToolDescriptor,
) -> RegistryEntry:
    """Build a RegistryEntry from a ToolDescriptor."""
    name = descriptor.tool_name
    description = descriptor.description
    connector_id = descriptor.connector_id
    label = str(descriptor.label or "").strip()
    keywords = _extract_keywords(name, description, connector_id)
    category = _categorize_tool(name, connector_id)
    name_field_tokens = _tokenize(f"{name} {label}")
    desc_field_tokens = _tokenize(f"{description} {connector_id}")

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
        name_field_tokens=name_field_tokens,
        desc_field_tokens=desc_field_tokens,
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

    # 4. Workspace-connected MCP tools (Phase A wiring —
    # docs/design/mcp-applications-plan.md). This function intentionally has
    # no workspace_id parameter (see the call site comment in
    # agent_turn_runtime_service.py's _direct_tool_bundle(), which is where
    # workspace_id is actually in scope) — the MCP tool payloads are instead
    # injected by the caller into availability_payload["mcp_tools"], already
    # shaped exactly like the app_tools payloads above
    # (name/description/connector_id/parameters), pre-namespaced
    # mcp__<server_id>__<tool_name> and pre-filtered to enabled servers +
    # enabled+approved tools by mcp_registry_service.
    # list_workspace_mcp_direct_tool_payloads(). Absent key or the MCP kill
    # switch (EMPYRALIS_MCP_TOOLS_ENABLED) off => this is simply a no-op.
    mcp_tools = availability_payload.get("mcp_tools") if isinstance(availability_payload, dict) else None
    if isinstance(mcp_tools, list):
        for tool in mcp_tools:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            entries.append(_build_registry_entry_from_tool_payload(tool))

    return entries


# ── BM25 ranking ──────────────────────────────────────────────────────────────
# Real Okapi BM25 over the registry corpus (replaces the old binary
# token/substring-overlap scorer — audit §3.1: that scorer returned ZERO
# results for 4 of 7 realistic user-intent paraphrases, e.g. "let the team
# know on chat" and "schedule a meeting with the client", because none of
# their words happened to be exact substrings of a tool's own name/
# description text). Field weighting: matches in a tool's name/label count
# 3x toward term frequency versus a match in its description/connector_id —
# a lightweight BM25F-style boost (shared k1/b, not per-field tuning) so a
# query that names the action directly ("slack", "send message") still beats
# a tool that merely mentions the word once in a longer description.
_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_NAME_FIELD_WEIGHT = 3.0
_BM25_DESC_FIELD_WEIGHT = 1.0


def _bm25_term_frequency(term: str, entry: RegistryEntry) -> float:
    return (
        _BM25_NAME_FIELD_WEIGHT * entry.name_field_tokens.count(term)
        + _BM25_DESC_FIELD_WEIGHT * entry.desc_field_tokens.count(term)
    )


def _bm25_document_length(entry: RegistryEntry) -> float:
    return (
        _BM25_NAME_FIELD_WEIGHT * len(entry.name_field_tokens)
        + _BM25_DESC_FIELD_WEIGHT * len(entry.desc_field_tokens)
    )


def _bm25_rank(query_terms: List[str], registry: List[RegistryEntry]) -> List[Tuple[float, RegistryEntry]]:
    """Score every registry entry against query_terms with Okapi BM25,
    return (score, entry) pairs for entries that scored > 0, sorted
    descending. query_terms should already be tokenized + synonym-expanded."""
    n_docs = len(registry)
    if n_docs == 0 or not query_terms:
        return []

    doc_lengths = [_bm25_document_length(entry) for entry in registry]
    avg_doc_length = (sum(doc_lengths) / n_docs) if n_docs else 0.0

    unique_terms = set(query_terms)
    idf: Dict[str, float] = {}
    for term in unique_terms:
        doc_freq = sum(
            1
            for entry in registry
            if term in entry.name_field_tokens or term in entry.desc_field_tokens
        )
        # Standard Okapi BM25 IDF, +1 inside the log so it never goes
        # negative for a term that appears in most/all documents.
        idf[term] = math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))

    scored: List[Tuple[float, RegistryEntry]] = []
    for entry, doc_length in zip(registry, doc_lengths):
        score = 0.0
        length_norm = doc_length / avg_doc_length if avg_doc_length > 0 else 0.0
        denom_base = _BM25_K1 * (1 - _BM25_B + _BM25_B * length_norm)
        for term in query_terms:
            term_idf = idf.get(term, 0.0)
            if term_idf <= 0.0:
                continue
            tf = _bm25_term_frequency(term, entry)
            if tf <= 0.0:
                continue
            score += term_idf * (tf * (_BM25_K1 + 1)) / (tf + denom_base)
        if score > 0.0:
            scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


# ── Search ────────────────────────────────────────────────────────────────────

def search_tool_registry(
    query: str,
    registry: List[RegistryEntry],
    *,
    max_results: int = 5,
    availability_payload: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Search the registry with real BM25 ranking (see _bm25_rank above),
    after tokenizing the query and expanding it with the small chat-
    vocabulary synonym layer (_QUERY_SYNONYMS).

    Returns the top *max_results* tool definitions (OpenAI function-calling
    schema dicts), best match first.

    If *availability_payload* is provided, each result includes a
    ``_credential_note`` key when the tool's connector is not authenticated.
    """
    if not query.strip() or not registry:
        return []

    base_tokens = _tokenize(query)
    if not base_tokens:
        return []
    query_terms = _expand_query_tokens(base_tokens)

    scored = _bm25_rank(query_terms, registry)

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

    Covers 11 of ALWAYS_ON_TOOL_NAMES's 12 entries — every one that is a
    real ToolDescriptor in skills_service._builtin_tool_descriptors(). The
    12th, "query_tool_registry", is hand-built (get_query_tool_registry_definition,
    below) rather than a ToolDescriptor, since it isn't a connector/local
    action — callers append it separately to get the full always-on set.
    Together the two make up the 12 tools (~800 tokens, estimated — not
    re-measured) injected into every inference request.

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
