from __future__ import annotations

import asyncio
import inspect
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import quote_plus

from server_modules import inventory_skill, mcp_registry_service, tools_http, web_tools
from server_modules.execution_router import get_browser_adapter
from server_modules.installed_skills import list_installed_skills


SkillExecutor = Callable[..., Awaitable[dict[str, Any]]]
SkillActionClass = Literal["read", "write", "execute"]
SkillClass = Literal["system", "business", "specialist_local"]

_SUPPORTED_ACTION_CLASSES = {"read", "write", "execute"}
_SUPPORTED_RUNTIME_MODES = {"hosted_secure", "local_secure", "privileged_device"}
_SUPPORTED_SKILL_CLASSES = {"system", "business", "specialist_local"}
_URL_PATTERN = re.compile(r"https?://[^\s)]+", re.IGNORECASE)
_BUILT_IN_SOURCE = "built_in"
_ALL_RUNTIME_MODES = ("hosted_secure", "local_secure", "privileged_device")


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    label: str
    description: str
    permission_label: str
    execution_mode: str
    action_class: SkillActionClass
    connector_scopes: tuple[str, ...]
    trigger_terms: tuple[str, ...]
    allowed_runtime_modes: tuple[str, ...] = _ALL_RUNTIME_MODES
    requires_approval: bool = False
    executor: SkillExecutor | None = None
    skill_class: SkillClass = "system"
    execution_adapter: str | None = None
    source: str = _BUILT_IN_SOURCE
    path: str | None = None
    enabled: bool = True
    available: bool = True
    unavailable_reason: str | None = None


async def _manual_skill_stub(*, goal: str, agent_label: str, skill_label: str, **_: Any) -> dict[str, Any]:
    return {
        "status": "manual",
        "reply": f"Heads up: {skill_label} is not wired to a live execution path yet.",
        "artifact": None,
        "steps": [
            {"label": "Resolving skill requirement", "detail": goal, "status": "done", "kind": "thinking"},
            {"label": "Skill adapter unavailable", "detail": f"{skill_label} is not wired to a live execution path yet", "status": "error", "kind": "connector"},
        ],
    }


def _ordered_unique(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = str(raw or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return tuple(ordered)


def _normalize_skill_class(value: Any, default: SkillClass = "specialist_local") -> SkillClass:
    token = str(value or "").strip().lower()
    if token in _SUPPORTED_SKILL_CLASSES:
        return token  # type: ignore[return-value]
    return default


def _normalize_action_class(value: Any, default: SkillActionClass = "read") -> SkillActionClass:
    token = str(value or "").strip().lower()
    if token in _SUPPORTED_ACTION_CLASSES:
        return token  # type: ignore[return-value]
    return default


def _normalize_runtime_modes(values: Any) -> tuple[str, ...]:
    modes = _ordered_unique(tuple(str(item or "").strip().lower() for item in list(values or [])))
    filtered = tuple(mode for mode in modes if mode in _SUPPORTED_RUNTIME_MODES)
    return filtered or _ALL_RUNTIME_MODES


def _normalize_trigger_terms(values: Any) -> tuple[str, ...]:
    return _ordered_unique(tuple(str(item or "").strip().lower() for item in list(values or [])))


def _normalize_connector_scopes(values: Any) -> tuple[str, ...]:
    return _ordered_unique(tuple(str(item or "").strip().lower() for item in list(values or [])))


def _search_url_from_goal(goal: str) -> str:
    direct_url = _URL_PATTERN.search(str(goal or "").strip())
    if direct_url:
        return direct_url.group(0)
    compact = str(goal or "").strip()
    query = re.sub(r"\s+", " ", compact).strip()
    return f"https://duckduckgo.com/?q={quote_plus(query)}" if query else "https://example.com"


def _build_search_reply(agent_label: str, goal: str, results: list[dict[str, str]]) -> str:
    if not results:
        return f"Heads up: web search for '{goal}' returned no confident results."
    top = results[0]
    title = str(top.get("title") or top.get("url") or "Top result").strip()
    snippet = str(top.get("snippet") or "").strip()
    source = str(top.get("url") or "").strip()
    reply = f"Heads up: found public sources for '{goal}'. Top result: {title}."
    if snippet:
        reply += f" {snippet}"
    if source:
        reply += f" Source: {source}"
    return reply.strip()


def _search_artifact(goal: str, results: list[dict[str, str]]) -> dict[str, Any]:
    lines = ["# Web search results", "", f"Goal: {goal}", ""]
    if not results:
        lines.append("No results found.")
    for index, item in enumerate(results, start=1):
        title = str(item.get("title") or item.get("url") or "Untitled result").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   - URL: {url}")
        if snippet:
            lines.append(f"   - Snippet: {snippet}")
    return {
        "label": "Web search result",
        "kind": "web-search-results",
        "summary": f"{len(results)} public result(s) returned for the current goal.",
        "media_type": "text/markdown",
        "preview_content": "\n".join(lines)[:12000],
    }


async def _live_web_search(
    *,
    goal: str,
    agent_label: str,
    hard_context: str,
    operational_policy: str,
    **_: Any,
) -> dict[str, Any]:
    del hard_context, operational_policy
    query = str(goal or "").strip()
    if not query:
        return {
            "status": "no_query",
            "reply": f"Heads up: a search query is required for Web Search.",
            "artifact": None,
            "steps": [
                {"label": "Resolving public research request", "detail": "Missing query", "status": "error", "kind": "thinking"},
            ],
        }
    response = await tools_http.http_request(
        method="GET",
        url=f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
        headers={"User-Agent": web_tools.DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    )
    html_body = str(response.get("body") or "")
    results = web_tools._parse_html_results(html_body) or web_tools._parse_lite_results(html_body)
    return {
        "status": "ok" if results else "no_match",
        "reply": _build_search_reply(agent_label, query, results),
        "artifact": _search_artifact(query, results),
        "steps": [
            {"label": "Resolving public research request", "detail": query, "status": "done", "kind": "thinking"},
            {"label": "Searching public sources", "detail": f"{len(results)} result(s) parsed from DuckDuckGo HTML", "status": "done", "kind": "connector"},
        ],
        "results": results,
    }


async def _live_browser_skill(
    *,
    goal: str,
    agent_label: str,
    hard_context: str,
    operational_policy: str,
    **_: Any,
) -> dict[str, Any]:
    del hard_context, operational_policy
    target_url = _search_url_from_goal(goal)
    browser = get_browser_adapter(
        {
            "trust_mode": "reviewed",
            "skill_id": "browser",
            "source": "skill_registry",
        },
        target="local_companion",
    )
    navigation = await browser.navigate(target_url)
    observation = await browser.observe()
    preview = str(observation.get("text") or "").strip()
    current_url = str(observation.get("url") or navigation.get("url") or target_url).strip()
    title = str(observation.get("title") or navigation.get("title") or current_url).strip()
    reply = f"Heads up: opened {title or current_url}."
    if preview:
        reply += f" Preview: {preview[:320].strip()}"
    return {
        "status": "ok",
        "reply": reply.strip(),
        "artifact": {
            "label": title or "Browser observation",
            "kind": "browser-observation",
            "summary": f"Observed {current_url or target_url}",
            "media_type": "application/json",
            "preview_content": json.dumps(
                {
                    "url": current_url,
                    "title": title,
                    "text": preview[:2000],
                    "interactive_count": len(list(observation.get("interactive_elements") or [])),
                    "screenshot_path": str(observation.get("screenshot_path") or "").strip() or None,
                },
                ensure_ascii=False,
                indent=2,
            )[:12000],
        },
        "steps": [
            {"label": "Resolving browser goal", "detail": goal, "status": "done", "kind": "thinking"},
            {"label": "Opening browser target", "detail": target_url, "status": "done", "kind": "connector"},
            {"label": "Observing current page", "detail": title or current_url or target_url, "status": "done", "kind": "connector"},
        ],
        "observation": observation,
        "navigation": navigation,
    }


async def _execute_handler_skill(
    definition: SkillDefinition,
    *,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    agent_label: str,
    hard_context: str,
    operational_policy: str,
) -> dict[str, Any]:
    skill_path = Path(str(definition.path or "")).expanduser().resolve()
    handler_path = skill_path / "handler.py"
    if not handler_path.exists():
        handler_path = skill_path / "query_handler.py"
    if not handler_path.exists():
        return await _manual_skill_stub(goal=goal, agent_label=agent_label, skill_label=definition.label)
    payload = {
        "mode": "execute_skill",
        "skill_id": definition.id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "goal": goal,
        "agent_label": agent_label,
        "hard_context": hard_context,
        "operational_policy": operational_policy,
    }

    def _run_handler() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(handler_path)],
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(skill_path),
            check=False,
            timeout=12,
        )

    completed = await asyncio.to_thread(_run_handler)
    raw_stdout = (completed.stdout or b"").decode("utf-8", "ignore").strip()
    raw_stderr = (completed.stderr or b"").decode("utf-8", "ignore").strip()
    if completed.returncode != 0:
        return {
            "status": "error",
            "reply": f"Heads up: could not execute {definition.label} right now.",
            "artifact": None,
            "steps": [
                {"label": "Resolving skill handler", "detail": definition.id, "status": "done", "kind": "thinking"},
                {"label": "Handler execution failed", "detail": raw_stderr or raw_stdout or f"exit_{completed.returncode}", "status": "error", "kind": "connector"},
            ],
        }
    try:
        parsed = json.loads(raw_stdout) if raw_stdout else {}
    except Exception:
        parsed = {"reply": raw_stdout}
    if isinstance(parsed, dict):
        result = dict(parsed)
        result.setdefault("status", "ok")
        result.setdefault("reply", raw_stdout or f"{definition.label} completed.")
        result.setdefault("artifact", None)
        result.setdefault(
            "steps",
            [
                {"label": "Resolving skill handler", "detail": definition.id, "status": "done", "kind": "thinking"},
                {"label": "Handler execution complete", "detail": definition.label, "status": "done", "kind": "connector"},
            ],
        )
        return result
    return {
        "status": "ok",
        "reply": raw_stdout or f"{definition.label} completed.",
        "artifact": None,
        "steps": [
            {"label": "Resolving skill handler", "detail": definition.id, "status": "done", "kind": "thinking"},
            {"label": "Handler execution complete", "detail": definition.label, "status": "done", "kind": "connector"},
        ],
    }


_ADAPTER_EXECUTORS: dict[str, SkillExecutor] = {}

# ── Bundled-skill → tool adapter mapping ───────────────────────────────
# Each bundled skill from /skills/ wraps built-in tools from skills_service.py.
# When invoked, the executor dispatches to the underlying tool handler(s)
# and returns structured output compatible with the agent loop's tool result
# format (sage_agent_runtime_service.py).  Skills without tool equivalents
# inject their SKILL.md prompt content into the agent context.

_BUNDLED_SKILL_DISPATCH: dict[str, str] = {
    "memory-manager": "memory_update",
    "code-runner": "shell__exec",
    "file-manager": "file__read",
    "telegram-bot": "telegram_bot__send_message",
    "web-search": "web__search",
    "browser": "browser__navigate",
}

# ── Canonical enforcement id map ────────────────────────────────────────
# skill_registry ids are hyphenated display ids; _specialist_tool_allowed()
# (sage_agent_runtime_service.py) keys tool gating by the literal LLM
# tool-call name instead. The two id spaces are otherwise disconnected — a
# mandate grant stored under the display id would never match what
# enforcement checks. This maps every built-in skill that has a real,
# callable LLM tool onto that tool's exact name, so the Tools tab
# (fleet_tools.fleet_get_agent_tools) can read/write the id enforcement
# actually consults for Customer Access (Authority Mandate) grants — the
# per-agent enable/disable checklist this map used to also serve is gone
# (2026-08-14, CLAUDE.md, founder decision). Starts from
# _BUNDLED_SKILL_DISPATCH since those 6 mappings already encode the same
# skill -> tool identity.
# Skills with no live LLM tool_call name (connector-scoped manual skills,
# and skills dispatched by keyword/handler rather than tool-calling) are
# intentionally absent — enforcement never checks their id today, so they
# have nothing to be unified onto.
_ENFORCEMENT_TOOL_NAME: dict[str, str] = {
    **_BUNDLED_SKILL_DISPATCH,
    "fleet-create-agent": "fleet__create_agent",
    "fleet-list-agents": "fleet__list_agents",
    "fleet-get-agent-activity": "fleet__get_agent_activity",
    "fleet-configure-agent": "fleet__configure_agent",
    "memory-read": "memory_read",
    "memory-write": "memory_write",
}


def enforcement_tool_name(skill_id: str) -> str:
    """The literal LLM tool-call name enforcement checks for ``skill_id``,
    or ``skill_id`` unchanged when the skill has no live tool-calling
    equivalent (nothing enforces those ids either way)."""
    return _ENFORCEMENT_TOOL_NAME.get(str(skill_id or "").strip(), skill_id)

# Skills that only inject prompt context (no tool execution).
_PROMPT_ONLY_SKILLS: frozenset[str] = frozenset({
    "business-skill-template",
    "vision-monitor",  # has its own handler.py — executed via _execute_handler_skill
})


async def _bundled_skill_executor(
    *,
    skill_id: str,
    goal: str,
    agent_label: str,
    tenant_id: str,
    workspace_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute a bundled skill by dispatching to its mapped tool handler.

    For skills with tool equivalents (memory-manager → memory_update, etc.),
    this calls the underlying tool via the skills_service tool execution path.
    For prompt-only skills, it loads the SKILL.md content and returns it as
    a context-injection artifact.
    """
    tool_name = _BUNDLED_SKILL_DISPATCH.get(skill_id)
    if tool_name:
        # Tool-backed skill — dispatch to the underlying tool handler.
        try:
            from server_modules.direct_tool_execution_service import (
                execute_tool_by_name,
            )
            result = await execute_tool_by_name(
                tool_name=tool_name,
                goal=goal,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
            )
            if result and isinstance(result, dict):
                reply = str(result.get("reply") or result.get("output") or f"Heads up: completed {skill_id}.")
                return {
                    "status": "ok",
                    "reply": reply,
                    "artifact": result.get("artifact"),
                    "steps": [
                        {"label": f"Running {skill_id}", "detail": goal, "status": "done", "kind": "thinking"},
                        {"label": f"Dispatched to {tool_name}", "detail": reply[:200], "status": "done", "kind": "connector"},
                    ],
                }
        except (ImportError, AttributeError):
            pass
        # Fallback: tool dispatch not available — return manual stub with tool hint
        return {
            "status": "manual",
            "reply": f"Heads up: {tool_name} requires tool dispatch which is not available in this environment.",
            "artifact": None,
            "steps": [
                {"label": f"Resolving {skill_id}", "detail": goal, "status": "done", "kind": "thinking"},
                {"label": f"Mapped to {tool_name}", "detail": "Tool execution path unavailable", "status": "error", "kind": "connector"},
            ],
        }

    # Prompt-only skill — inject SKILL.md content into agent context.
    prompt_content = ""
    try:
        from server_modules.installed_skills import _read_text
        from pathlib import Path as _Path
        skill_path = _Path(__file__).resolve().parent.parent / "skills" / skill_id / "SKILL.md"
        if skill_path.exists():
            prompt_content = _read_text(skill_path)
    except Exception:
        prompt_content = ""

    if prompt_content:
        return {
            "status": "ok",
            "reply": f"Heads up: loaded the {skill_id} skill context.",
            "artifact": {
                "label": f"{skill_id} skill prompt",
                "kind": "skill-context",
                "summary": f"Injected {skill_id} skill instructions into agent context.",
                "media_type": "text/markdown",
                "preview_content": prompt_content[:8000],
            },
            "steps": [
                {"label": f"Loading {skill_id}", "detail": "Skill prompt injected into agent context", "status": "done", "kind": "thinking"},
            ],
        }
    return await _manual_skill_stub(goal=goal, agent_label=agent_label, skill_label=skill_id)


async def _live_memory_skill(
    *,
    goal: str,
    agent_label: str,
    workspace_id: str,
    tenant_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for the memory-manager skill.  Loads and returns the current
    memory context so the agent can inspect and edit memory facts."""
    try:
        from server_modules import memory_service
        text = memory_service.get_memory(workspace_id) or "No memory facts stored yet."
        return {
            "status": "ok",
            "reply": f"Heads up: loaded the current memory context.",
            "artifact": {
                "label": "Memory context",
                "kind": "memory-snapshot",
                "summary": f"Current memory facts for workspace {workspace_id}.",
                "media_type": "text/markdown",
                "preview_content": text[:8000],
            },
            "steps": [
                {"label": "Loading memory-manager", "detail": "Memory context retrieved", "status": "done", "kind": "thinking"},
            ],
        }
    except Exception:
        return await _bundled_skill_executor(
            skill_id="memory-manager", goal=goal, agent_label=agent_label,
            tenant_id=tenant_id, workspace_id=workspace_id, **kwargs,
        )


# ── Phase N: per-agent memory tools (memory-read/write/list) ───────────────
# Distinct from memory-manager above: memory-manager returns the legacy
# shared workspace memory-facts snapshot (memory_service.get_memory);
# these three operate on each agent's OWN per-agent memory directory
# (agent_memory_tools.py / workspace_context.agent_workspace_context_dir).
# tool_broker.execute_skill already special-cases these three ids ahead of
# its own executor check and dispatches to agent_memory_tools directly
# (see tool_broker._dispatch_memory_tool) — the executors below give this
# registry's OWN execute_skill (used by skills_service.py's generic
# "skill_invoke" tool dispatch) the same real behavior instead of falling
# through to the SKILL.md/manual-stub fallback, and mirror the same
# goal-parsing so both dispatch paths behave identically.


def _extract_memory_path_from_goal(goal: str) -> str:
    """Extract a memory file path from free-text goal input.

    Mirrors tool_broker._extract_memory_path so a memory-read/memory-write
    invocation behaves the same whether it arrives via the capability-broker
    dispatch (tool_broker.execute_skill) or this registry's generic
    skill_invoke dispatch.
    """
    text = str(goal or "")

    # Quoted path: "SOUL.md" or 'memory/notes.md'
    m = re.search(r"""["']([^"']+\.[a-z]{1,10})["']""", text)
    if m:
        return m.group(1).strip()

    # Bare .md filename: SOUL.md, memory/notes.md
    m = re.search(r"(\S+\.md)\b", text)
    if m:
        return m.group(1).strip()

    # Look for "path:", "file:", "read:" prefixes
    for prefix in ("path:", "file:", "read:", "write to ", "write ", "read "):
        if prefix in text.lower():
            after = text.lower().split(prefix, 1)[-1].strip()
            qm = re.search(r"""["']([^"']+)["']""", after)
            if qm:
                return qm.group(1).strip()
            word = after.split()[0] if after.split() else ""
            return word.strip().rstrip(",.;:")

    return ""


def _extract_memory_content_from_goal(goal: str) -> str:
    """Extract write content from free-text goal input.

    Mirrors tool_broker._extract_memory_content — see note above.
    """
    text = str(goal or "")
    text = re.sub(r"(?i)memory[_ ]?write\b[:\s]*", "", text)

    m = re.search(r"""["']([^"']+\.[a-z]{1,10})["']\s*[:：]\s*(.+)""", text, re.DOTALL)
    if m:
        return m.group(2).strip()

    m = re.search(r"(\S+\.md)\s*[:：]\s*(.+)", text, re.DOTALL)
    if m:
        return m.group(2).strip()

    if ": " in text:
        parts = text.split(": ", 1)
        if ".md" in parts[0] or len(parts[0].split()) <= 2:
            return parts[-1].strip()

    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) > 1:
        return " ".join(sentences[1:]).strip()

    return text.strip()


async def _live_memory_read_skill(
    *,
    workspace_id: str,
    goal: str,
    agent_label: str,
    agent_id: str = "",
    agent_install_id: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for memory-read — reads a file from the CALLING agent's own
    per-agent memory directory (Phase N; see agent_memory_tools.py)."""
    del kwargs
    from server_modules import agent_memory_tools

    # SECURITY: an empty agent scope must never silently fall through to
    # the workspace root's memory (that is exactly how a prior cross-agent
    # memory leak happened — see docs/PLATFORM-MAP.md's memory security
    # audit and workspace_context.agent_workspace_context_dir's docstring).
    # Fail closed instead.
    resolved_agent = str(agent_install_id or agent_id or "").strip()
    if not resolved_agent:
        return {
            "status": "error",
            "reply": "Memory tools need a resolved agent identity and none was available for this call.",
            "artifact": None,
            "steps": [{"label": "Reading memory", "detail": "No agent identity resolved", "status": "error", "kind": "thinking"}],
        }

    path = _extract_memory_path_from_goal(goal)
    if not path:
        return {
            "status": "error",
            "reply": "I need a file path to read from memory, e.g. notes.md or memory/notes.md.",
            "artifact": None,
            "steps": [{"label": "Reading memory", "detail": "No path found in request", "status": "error", "kind": "thinking"}],
        }

    result = await agent_memory_tools.memory_read(
        workspace_id=workspace_id,
        agent_install_id=resolved_agent,
        agent_id=agent_id,
        path=path,
    )
    ok = bool(result.get("ok"))
    return {
        "status": "ok" if ok else "error",
        "reply": (
            str(result.get("content") or "")
            if ok
            else f"Could not read {path}: {result.get('error', 'unknown error')}"
        ),
        "artifact": result if ok else None,
        "steps": [{"label": "Reading memory", "detail": path, "status": "done" if ok else "error", "kind": "thinking"}],
    }


async def _live_memory_write_skill(
    *,
    workspace_id: str,
    goal: str,
    agent_label: str,
    agent_id: str = "",
    agent_install_id: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for memory-write — writes/appends to a file in the CALLING
    agent's own per-agent memory directory (Phase N; see agent_memory_tools.py)."""
    del kwargs
    from server_modules import agent_memory_tools

    resolved_agent = str(agent_install_id or agent_id or "").strip()
    if not resolved_agent:
        return {
            "status": "error",
            "reply": "Memory tools need a resolved agent identity and none was available for this call.",
            "artifact": None,
            "steps": [{"label": "Writing memory", "detail": "No agent identity resolved", "status": "error", "kind": "thinking"}],
        }

    path = _extract_memory_path_from_goal(goal)
    content = _extract_memory_content_from_goal(goal)
    if not path:
        return {
            "status": "error",
            "reply": "I need a file path to write to memory, e.g. notes.md or memory/notes.md.",
            "artifact": None,
            "steps": [{"label": "Writing memory", "detail": "No path found in request", "status": "error", "kind": "thinking"}],
        }
    if not content:
        return {
            "status": "error",
            "reply": "I need content to write to memory.",
            "artifact": None,
            "steps": [{"label": "Writing memory", "detail": "No content found in request", "status": "error", "kind": "thinking"}],
        }

    result = await agent_memory_tools.memory_write(
        workspace_id=workspace_id,
        agent_install_id=resolved_agent,
        agent_id=agent_id,
        path=path,
        content=content,
    )
    ok = bool(result.get("ok"))
    return {
        "status": "ok" if ok else "error",
        "reply": (
            f"Saved to {path} ({result.get('byte_count', 0)} bytes)."
            if ok
            else f"Could not write {path}: {result.get('error', 'unknown error')}"
        ),
        "artifact": result if ok else None,
        "steps": [{"label": "Writing memory", "detail": path, "status": "done" if ok else "error", "kind": "thinking"}],
    }


async def _live_memory_list_skill(
    *,
    workspace_id: str,
    goal: str,
    agent_label: str,
    agent_id: str = "",
    agent_install_id: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for memory-list — lists files in the CALLING agent's own
    per-agent memory directory (Phase N; see agent_memory_tools.py)."""
    del kwargs, goal
    from server_modules import agent_memory_tools

    resolved_agent = str(agent_install_id or agent_id or "").strip()
    if not resolved_agent:
        return {
            "status": "error",
            "reply": "Memory tools need a resolved agent identity and none was available for this call.",
            "artifact": None,
            "steps": [{"label": "Listing memory", "detail": "No agent identity resolved", "status": "error", "kind": "thinking"}],
        }

    result = await agent_memory_tools.memory_list(
        workspace_id=workspace_id,
        agent_install_id=resolved_agent,
        agent_id=agent_id,
    )
    ok = bool(result.get("ok"))
    files = result.get("files", []) if ok else []
    summary = "\n".join(f"- {f.get('path')} ({f.get('size', 0)}B)" for f in files) or "(no memory files yet)"
    return {
        "status": "ok" if ok else "error",
        "reply": summary if ok else f"Could not list memory files: {result.get('error', 'unknown error')}",
        "artifact": result,
        "steps": [{"label": "Listing memory", "detail": f"{len(files)} found", "status": "done" if ok else "error", "kind": "thinking"}],
    }


async def _live_code_runner_skill(
    *,
    goal: str,
    agent_label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for the code-runner skill.  Prepares a sandboxed execution
    environment.  Actual execution is handled by the shell__exec tool."""
    return await _bundled_skill_executor(
        skill_id="code-runner", goal=goal, agent_label=agent_label, **kwargs,
    )


async def _live_file_manager_skill(
    *,
    goal: str,
    agent_label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for the file-manager skill."""
    return await _bundled_skill_executor(
        skill_id="file-manager", goal=goal, agent_label=agent_label, **kwargs,
    )


async def _live_telegram_bot_skill(
    *,
    goal: str,
    agent_label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for the telegram-bot skill."""
    return await _bundled_skill_executor(
        skill_id="telegram-bot", goal=goal, agent_label=agent_label, **kwargs,
    )


async def _live_vision_monitor_skill(
    *,
    goal: str,
    agent_label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for the vision-monitor skill — dispatches to the handler.py
    subprocess.  Falls back to prompt injection if handler unavailable."""
    return await _bundled_skill_executor(
        skill_id="vision-monitor", goal=goal, agent_label=agent_label, **kwargs,
    )


# ── Fleet tool executors ──────────────────────────────────────────────────

async def _live_fleet_create_agent_skill(
    *,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    agent_label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for fleet-create-agent — delegates to fleet_tools."""
    from server_modules.fleet_tools import fleet_create_agent

    # Extract an agent name from the goal text (first line, or up to 80 chars).
    goal_clean = str(goal or "").strip()
    name = goal_clean.split("\n")[0].strip()[:80] or "Fleet Specialist"

    result = await fleet_create_agent(
        actor_id=agent_label or "sage",
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        name=name,
    )
    return {
        "status": "ok" if result.get("ok") else "error",
        "reply": (
            f"Created agent '{result.get('agent_id', '?')}'."
            if result.get("ok")
            else f"Could not create agent: {result.get('error', 'unknown error')}"
        ),
        "artifact": result,
        "steps": [
            {"label": "Creating fleet agent", "detail": name, "status": "done" if result.get("ok") else "error", "kind": "fleet"},
        ],
    }


async def _live_fleet_list_agents_skill(
    *,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    agent_label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for fleet-list-agents — delegates to fleet_tools."""
    from server_modules.fleet_tools import fleet_list_agents

    result = await fleet_list_agents(
        actor_id=agent_label or "sage",
        workspace_id=workspace_id,
        tenant_id=tenant_id,
    )
    agents = result.get("agents", []) if isinstance(result, dict) else []
    summary = "\n".join(
        f"- {a.get('label', a.get('agent_id', '?'))} ({a.get('role', '?')}) [{a.get('status', '?')}]"
        for a in (agents or [])
    ) or "(no agents)"
    return {
        "status": "ok",
        "reply": f"Agents in workspace:\n{summary}",
        "artifact": result,
        "steps": [
            {"label": "Listing fleet agents", "detail": f"{len(agents)} found", "status": "done", "kind": "fleet"},
        ],
    }


async def _live_fleet_get_agent_activity_skill(
    *,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    agent_label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for fleet-get-agent-activity — delegates to fleet_tools.

    The goal text should include an agent install id (ainstall_…). If not found,
    returns a prompt asking for the id.
    """
    from server_modules.fleet_tools import fleet_get_agent_activity
    import re as _re

    match = _re.search(r"ainstall_[0-9a-fA-F]+", str(goal or ""))
    agent_id = match.group(0) if match else ""
    if not agent_id:
        match2 = _re.search(r"[0-9a-fA-F]{8,}", str(goal or ""))
        agent_id = match2.group(0) if match2 else ""

    if not agent_id:
        return {
            "status": "error",
            "reply": "I need an agent install id to look up activity. Which agent?",
            "artifact": None,
            "steps": [
                {"label": "Looking up agent activity", "detail": "No agent id found in request", "status": "error", "kind": "fleet"},
            ],
        }

    result = await fleet_get_agent_activity(
        actor_id=agent_label or "sage",
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
    )
    events = result.get("events", []) if isinstance(result, dict) else []
    return {
        "status": "ok",
        "reply": f"Found {len(events)} activity events for {agent_id}.",
        "artifact": result,
        "steps": [
            {"label": "Reading agent activity", "detail": f"{len(events)} events for {agent_id}", "status": "done", "kind": "fleet"},
        ],
    }


async def _live_fleet_configure_agent_skill(
    *,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    agent_label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Executor for fleet-configure-agent — delegates to fleet_tools.

    Structured configuration is best done via the direct tool calling path
    (fleet__configure_agent with a JSON patch). This executor handles simple
    cases described in natural language.
    """
    from server_modules.fleet_tools import fleet_configure_agent
    import re as _re

    match = _re.search(r"ainstall_[0-9a-fA-F]+", str(goal or ""))
    agent_id = match.group(0) if match else ""
    if not agent_id:
        match2 = _re.search(r"[0-9a-fA-F]{8,}", str(goal or ""))
        agent_id = match2.group(0) if match2 else ""

    if not agent_id:
        return {
            "status": "error",
            "reply": "I need an agent install id to configure it. Which agent?",
            "artifact": None,
            "steps": [
                {"label": "Configuring agent", "detail": "No agent id found in request", "status": "error", "kind": "fleet"},
            ],
        }

    result = await fleet_configure_agent(
        actor_id=agent_label or "sage",
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        patch={"instructions": str(goal or "").strip()},
    )
    return {
        "status": "ok" if result.get("ok") else "error",
        "reply": (
            f"Configured agent {agent_id}."
            if result.get("ok")
            else f"Could not configure agent: {result.get('error', 'unknown error')}"
        ),
        "artifact": result,
        "steps": [
            {"label": "Configuring agent", "detail": agent_id, "status": "done" if result.get("ok") else "error", "kind": "fleet"},
        ],
    }


# ── Populate adapter executors (must be after all executor functions) ──
_ADAPTER_EXECUTORS.update({
    "web_search": _live_web_search,
    "browser": _live_browser_skill,
    "inventory": inventory_skill.execute_inventory_skill,
    "memory_manager": _live_memory_skill,
    "code_runner": _live_code_runner_skill,
    "file_manager": _live_file_manager_skill,
    "telegram_bot": _live_telegram_bot_skill,
    "vision_monitor": _live_vision_monitor_skill,
})


_BUILT_IN_SKILLS: tuple[SkillDefinition, ...] = (
    SkillDefinition(
        id="email-access",
        label="Email Access",
        description="Read, draft, and route customer emails.",
        permission_label="Inbox scope",
        execution_mode="manual",
        action_class="write",
        connector_scopes=("email",),
        trigger_terms=(),
        requires_approval=True,
        skill_class="system",
    ),
    SkillDefinition(
        id="web-search",
        label="Web Search",
        description="Research public facts and retrieve references.",
        permission_label="Public web",
        execution_mode="live",
        action_class="read",
        connector_scopes=("web",),
        trigger_terms=(),
        executor=_live_web_search,
        execution_adapter="web_search",
        skill_class="system",
    ),
    SkillDefinition(
        id="browser",
        label="Browser",
        description="Open a page in the browser runtime and return the current page state.",
        permission_label="Browser runtime",
        execution_mode="live",
        action_class="read",
        connector_scopes=("browser",),
        trigger_terms=(),
        executor=_live_browser_skill,
        execution_adapter="browser",
        skill_class="system",
    ),
    SkillDefinition(
        id="calendar-access",
        label="Calendar Access",
        description="Create, move, or confirm appointments.",
        permission_label="Calendar scope",
        execution_mode="manual",
        action_class="write",
        connector_scopes=("calendar",),
        trigger_terms=(),
        requires_approval=True,
        skill_class="system",
    ),
    SkillDefinition(
        id="task-runner",
        label="Task Runner",
        description="Execute operational tools behind approvals.",
        permission_label="Operational tools",
        execution_mode="manual",
        action_class="execute",
        connector_scopes=("task_runner",),
        allowed_runtime_modes=("local_secure", "privileged_device"),
        requires_approval=True,
        trigger_terms=(),
        skill_class="system",
    ),
    SkillDefinition(
        id="inventory-tool",
        label="Inventory Tool",
        description="Check stock, fitment, and availability from the workspace inventory table.",
        permission_label="Inventory scope",
        execution_mode="live",
        action_class="read",
        connector_scopes=("inventory",),
        trigger_terms=(),
        executor=inventory_skill.execute_inventory_skill,
        execution_adapter="inventory",
        skill_class="business",
    ),
    SkillDefinition(
        id="crm-notes",
        label="CRM Notes",
        description="Write structured conversation notes back to the system of record.",
        permission_label="CRM writeback",
        execution_mode="manual",
        action_class="write",
        connector_scopes=("crm",),
        requires_approval=True,
        trigger_terms=(),
        skill_class="system",
    ),
    # ── Bundled skills (from /skills/) ──────────────────────────────────
    SkillDefinition(
        id="memory-manager",
        label="Memory Manager",
        description="Save and recall facts about the user.",
        permission_label="Memory scope",
        execution_mode="live",
        action_class="write",
        connector_scopes=("memory",),
        trigger_terms=(),
        executor=_live_memory_skill,
        execution_adapter="memory_manager",
        skill_class="system",
    ),
    SkillDefinition(
        id="code-runner",
        label="Code Runner",
        description="Run Python and shell code, show output, handle errors.",
        permission_label="Code execution",
        execution_mode="live",
        action_class="execute",
        connector_scopes=("shell", "code"),
        trigger_terms=(),
        executor=_live_code_runner_skill,
        execution_adapter="code_runner",
        skill_class="system",
    ),
    SkillDefinition(
        id="file-manager",
        label="File Manager",
        description="Read, write, list, and delete files.",
        permission_label="File system",
        execution_mode="live",
        action_class="write",
        connector_scopes=("file",),
        trigger_terms=(),
        executor=_live_file_manager_skill,
        execution_adapter="file_manager",
        skill_class="system",
    ),
    SkillDefinition(
        id="telegram-bot",
        label="Telegram Bot",
        description="Send Telegram messages with approval.",
        permission_label="Telegram messaging",
        execution_mode="live",
        action_class="write",
        connector_scopes=("telegram",),
        trigger_terms=(),
        executor=_live_telegram_bot_skill,
        execution_adapter="telegram_bot",
        skill_class="system",
    ),
    SkillDefinition(
        id="vision-monitor",
        label="Vision Monitor",
        description="Monitor physical spaces from camera snapshots.",
        permission_label="Camera access",
        execution_mode="live",
        action_class="read",
        connector_scopes=("vision",),
        trigger_terms=(),
        executor=_live_vision_monitor_skill,
        execution_adapter="vision_monitor",
        skill_class="system",
    ),
    # ── Phase L: Fleet control tools (operator-only) ──────────────────────
    SkillDefinition(
        id="fleet-create-agent",
        label="Create Agent",
        description="Create a new specialist agent in the workspace. Operator only.",
        permission_label="Fleet management",
        execution_mode="live",
        action_class="write",
        connector_scopes=(),
        trigger_terms=("create agent", "new agent", "add agent"),
        requires_approval=True,
        skill_class="system",
        executor=_live_fleet_create_agent_skill,
    ),
    SkillDefinition(
        id="fleet-list-agents",
        label="List Agents",
        description="List all agents in the workspace with roles and status. Operator only.",
        permission_label="Fleet management",
        execution_mode="live",
        action_class="read",
        connector_scopes=(),
        trigger_terms=("list agents", "show agents", "fleet"),
        skill_class="system",
        executor=_live_fleet_list_agents_skill,
    ),
    SkillDefinition(
        id="fleet-get-agent-activity",
        label="Agent Activity",
        description="Read recent ledger activity for a specific agent. Operator only.",
        permission_label="Fleet management",
        execution_mode="live",
        action_class="read",
        connector_scopes=(),
        trigger_terms=("agent activity", "agent history"),
        skill_class="system",
        executor=_live_fleet_get_agent_activity_skill,
    ),
    SkillDefinition(
        id="fleet-configure-agent",
        label="Configure Agent",
        description="Update an agent's tools, connectors, channels, model, or access. Operator only.",
        permission_label="Fleet management",
        execution_mode="live",
        action_class="write",
        connector_scopes=(),
        trigger_terms=("configure agent", "update agent", "agent settings"),
        requires_approval=True,
        skill_class="system",
        executor=_live_fleet_configure_agent_skill,
    ),
    # ── Phase N: Agent memory tools ─────────────────────────────────────
    SkillDefinition(
        id="memory-read",
        label="Memory Read",
        description="Read a file from your memory directory. Use this to recall durable facts, identity, goals, or past corrections.",
        permission_label="Memory access",
        execution_mode="live",
        action_class="read",
        connector_scopes=(),
        trigger_terms=("remember", "recall", "memory", "read memory"),
        executor=_live_memory_read_skill,
        skill_class="system",
    ),
    SkillDefinition(
        id="memory-write",
        label="Memory Write",
        description="Write or append to a file in your memory directory. Use this to save corrections, facts, or new information for future sessions.",
        permission_label="Memory access",
        execution_mode="live",
        action_class="write",
        connector_scopes=(),
        trigger_terms=("remember this", "save to memory", "write to memory", "note this"),
        executor=_live_memory_write_skill,
        skill_class="system",
    ),
    SkillDefinition(
        id="memory-list",
        label="Memory List",
        description="List all files in your memory directory.",
        permission_label="Memory access",
        execution_mode="live",
        action_class="read",
        connector_scopes=(),
        trigger_terms=("list memory", "memory files", "what do i remember"),
        executor=_live_memory_list_skill,
        skill_class="system",
    ),
)


def _definition_from_installed_skill(item: dict[str, Any]) -> SkillDefinition | None:
    skill_id = str(item.get("id") or "").strip()
    if not skill_id:
        return None
    runtime_metadata = dict(item.get("runtime_metadata") or {}) if isinstance(item.get("runtime_metadata"), dict) else {}
    execution_adapter = str(runtime_metadata.get("execution_adapter") or "").strip().lower()
    has_query_handler = bool(item.get("has_query_handler"))
    source = str(item.get("source") or "").strip().lower()
    is_bundled = source == "bundled"
    has_builtin = skill_id in {d.id for d in _BUILT_IN_SKILLS}
    # Every item here already passed installed_skills.list_installed_skills()'s
    # on-disk existence check + security scan (docs/design/audit-skills.md
    # §3 item 3: "one unified catalog" — no second, stricter gate here that
    # would make the Tools tab and the model's manifest disagree again).
    # A skill with no executor/handler/mcp adapter is still real and
    # dischargeable: skill_registry.execute_skill's final fallback
    # (SKILL.md-body-injection, below) handles exactly this case. So this
    # function no longer rejects adapter-less skills — it only resolves
    # WHICH dispatch path execute_skill should use.
    executor: SkillExecutor | None = _ADAPTER_EXECUTORS.get(execution_adapter) if execution_adapter else None
    # Resolve executor from _BUILT_IN_SKILLS for bundled skills
    if executor is None and has_builtin:
        builtin = {d.id: d for d in _BUILT_IN_SKILLS}.get(skill_id)
        if builtin is not None and builtin.executor is not None:
            executor = builtin.executor
    if executor is None and (execution_adapter == "handler" or has_query_handler):
        execution_adapter = "handler"
    label = str(item.get("name") or skill_id).strip() or skill_id
    description = str(item.get("description") or "").strip() or f"{label} skill."
    connector_scopes = _normalize_connector_scopes(runtime_metadata.get("connector_scopes") or ())
    trigger_terms = _normalize_trigger_terms(runtime_metadata.get("trigger_terms") or ())
    permission_label = str(runtime_metadata.get("permission_label") or label).strip() or label
    execution_mode = str(runtime_metadata.get("execution_mode") or ("live" if execution_adapter else "manual")).strip().lower() or "manual"
    return SkillDefinition(
        id=skill_id,
        label=label,
        description=description,
        permission_label=permission_label,
        execution_mode=execution_mode,
        action_class=_normalize_action_class(runtime_metadata.get("action_class"), "read"),
        connector_scopes=connector_scopes,
        trigger_terms=trigger_terms,
        allowed_runtime_modes=_normalize_runtime_modes(runtime_metadata.get("allowed_runtime_modes") or ()),
        requires_approval=bool(runtime_metadata.get("requires_approval")),
        executor=executor,
        skill_class=_normalize_skill_class(runtime_metadata.get("skill_class"), "specialist_local"),
        execution_adapter=execution_adapter or None,
        source=str(item.get("source") or "workspace").strip() or "workspace",
        path=str(item.get("path") or "").strip() or None,
        enabled=bool(item.get("enabled")),
        available=bool(item.get("available", True)),
        unavailable_reason=(
            "; ".join(str(token).strip() for token in list(item.get("availability_reasons") or []) if str(token).strip())
            or None
        ),
    )


def _definition_from_mcp_skill_entry(item: dict[str, Any]) -> SkillDefinition | None:
    skill_id = str(item.get("id") or "").strip()
    if not skill_id:
        return None
    metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
    return SkillDefinition(
        id=skill_id,
        label=str(item.get("label") or skill_id).strip() or skill_id,
        description=str(item.get("description") or "").strip() or f"{skill_id} MCP tool.",
        permission_label=str(item.get("permission_label") or item.get("server_id") or "MCP server").strip() or "MCP server",
        execution_mode=str(item.get("execution_mode") or "live").strip().lower() or "live",
        action_class=_normalize_action_class(item.get("action_class"), "read"),
        connector_scopes=_normalize_connector_scopes(item.get("connector_scopes") or ()),
        trigger_terms=_normalize_trigger_terms(item.get("trigger_terms") or ()),
        allowed_runtime_modes=_normalize_runtime_modes(item.get("allowed_runtime_modes") or ()),
        requires_approval=bool(item.get("requires_approval")),
        skill_class=_normalize_skill_class(item.get("skill_class"), "specialist_local"),
        execution_adapter=str(item.get("execution_adapter") or "mcp_tool").strip().lower() or "mcp_tool",
        source=str(item.get("source") or "mcp_registry").strip() or "mcp_registry",
        path=str(item.get("path") or metadata.get("endpoint") or "").strip() or None,
        enabled=bool(item.get("enabled", True)),
    )


def _skill_registry_map(*, workspace_id: str | None = None, include_disabled: bool = False) -> dict[str, SkillDefinition]:
    merged: dict[str, SkillDefinition] = {definition.id: definition for definition in _BUILT_IN_SKILLS}
    for item in list_installed_skills(workspace_id=workspace_id):
        definition = _definition_from_installed_skill(item)
        if definition is None:
            continue
        if not include_disabled and (not definition.enabled or not definition.available):
            if definition.id in merged:
                merged.pop(definition.id, None)
            continue
        merged[definition.id] = definition
    normalized_workspace_id = str(workspace_id or "").strip()
    if normalized_workspace_id:
        for item in mcp_registry_service.list_workspace_mcp_skill_entries(normalized_workspace_id):
            definition = _definition_from_mcp_skill_entry(item)
            if definition is None:
                continue
            if not include_disabled and not definition.enabled:
                if definition.id in merged:
                    merged.pop(definition.id, None)
                continue
            merged[definition.id] = definition
    return merged


def get_skill_definition(skill_id: str, *, workspace_id: str | None = None, include_disabled: bool = False) -> SkillDefinition | None:
    normalized = str(skill_id or "").strip()
    if not normalized:
        return None
    return _skill_registry_map(workspace_id=workspace_id, include_disabled=include_disabled).get(normalized)


def list_skill_definitions(*, workspace_id: str | None = None, include_disabled: bool = False) -> list[SkillDefinition]:
    return list(_skill_registry_map(workspace_id=workspace_id, include_disabled=include_disabled).values())


def skill_connector_scopes(skill_ids: list[str] | tuple[str, ...], *, workspace_id: str | None = None) -> list[str]:
    scopes: list[str] = []
    for skill_id in skill_ids:
        definition = get_skill_definition(skill_id, workspace_id=workspace_id)
        if definition is None:
            continue
        for scope in definition.connector_scopes:
            if scope not in scopes:
                scopes.append(scope)
    return scopes


def detect_skill_need(goal: str, *, workspace_id: str | None = None) -> SkillDefinition | None:
    normalized = str(goal or "").strip().lower()
    if not normalized:
        return None
    if any(
        token in normalized
        for token in (
            "in stock",
            "inventory",
            "availability",
            "available",
            "sku",
            "fitment",
            "part",
            "parts",
            "wiper",
            "wipers",
            "brake",
            "brakes",
        )
    ):
        return get_skill_definition("inventory-tool", workspace_id=workspace_id, include_disabled=True)
    return None


async def execute_skill(
    *,
    skill_id: str,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    agent_label: str,
    hard_context: str,
    operational_policy: str,
    agent_id: str = "",
    agent_install_id: str = "",
) -> dict[str, Any]:
    definition = get_skill_definition(skill_id, workspace_id=workspace_id, include_disabled=True)
    if definition is None:
        return {
            "status": "missing",
            "reply": f"The requested skill {skill_id} is not registered in the universal harness.",
            "artifact": None,
            "steps": [
                {"label": "Resolving skill registry", "detail": skill_id, "status": "error", "kind": "connector"},
            ],
        }

    if not definition.enabled:
        return {
            "status": "disabled",
            "reply": f"Heads up: {definition.label} is disabled for this workspace.",
            "artifact": None,
            "steps": [
                {"label": "Resolving skill registry", "detail": definition.id, "status": "done", "kind": "thinking"},
                {"label": "Workspace skill state", "detail": f"{definition.label} is disabled", "status": "error", "kind": "connector"},
            ],
        }

    if not definition.available:
        detail = str(definition.unavailable_reason or f"{definition.label} is not available in this environment.").strip()
        return {
            "status": "unavailable",
            "reply": f"Heads up: {definition.label} is not ready. {detail}",
            "artifact": None,
            "steps": [
                {"label": "Resolving skill registry", "detail": definition.id, "status": "done", "kind": "thinking"},
                {"label": "Skill availability", "detail": detail, "status": "error", "kind": "connector"},
            ],
        }

    if definition.executor is not None:
        executor_kwargs: dict[str, Any] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "goal": goal,
            "agent_label": agent_label,
            "hard_context": hard_context,
            "operational_policy": operational_policy,
        }
        # Forward agent_id/agent_install_id only to executors that can
        # actually accept them (declared explicitly, or via a **kwargs
        # catch-all — every built-in executor except inventory_skill's has
        # one). Older/narrower executor signatures (e.g. execute_inventory_skill,
        # which takes exactly the six kwargs above and nothing else) must be
        # left untouched rather than made to error on an unexpected kwarg.
        try:
            executor_params = inspect.signature(definition.executor).parameters
            accepts_var_keyword = any(
                param.kind is inspect.Parameter.VAR_KEYWORD
                for param in executor_params.values()
            )
            if accepts_var_keyword or "agent_id" in executor_params:
                executor_kwargs["agent_id"] = agent_id
            if accepts_var_keyword or "agent_install_id" in executor_params:
                executor_kwargs["agent_install_id"] = agent_install_id
        except (TypeError, ValueError):
            pass
        return await definition.executor(**executor_kwargs)

    if definition.execution_adapter == "handler":
        return await _execute_handler_skill(
            definition,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            goal=goal,
            agent_label=agent_label,
            hard_context=hard_context,
            operational_policy=operational_policy,
        )

    if definition.execution_adapter == "mcp_tool":
        return await mcp_registry_service.invoke_workspace_mcp_skill_async(
            workspace_id=workspace_id,
            skill_id=definition.id,
            goal=goal,
            agent_label=agent_label,
        )

    # ── Fallback: bundled skills and prompt-only skills ────────────────
    # For skills that don't have an executor or handler, try the bundled
    # skill executor (which loads SKILL.md for prompt injection or
    # dispatches to the mapped tool handler).
    if definition.id in _BUNDLED_SKILL_DISPATCH or definition.id in _PROMPT_ONLY_SKILLS:
        return await _bundled_skill_executor(
            skill_id=definition.id,
            goal=goal,
            agent_label=agent_label,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            hard_context=hard_context,
            operational_policy=operational_policy,
        )

    # ── Workspace/custom skills: inject SKILL.md prompt content ────────
    # Custom skills without executor functions modify agent behavior by
    # injecting their SKILL.md instructions into the agent context.
    if definition.path:
        try:
            from pathlib import Path as _Path
            from server_modules.installed_skills import _read_text
            skill_md = _Path(definition.path) / "SKILL.md"
            if skill_md.exists():
                prompt_content = _read_text(skill_md)
                if prompt_content:
                    return {
                        "status": "ok",
                        "reply": f"Heads up: loaded the {definition.label} skill context.",
                        "artifact": {
                            "label": f"{definition.label} skill prompt",
                            "kind": "skill-context",
                            "summary": f"Injected {definition.id} skill instructions into agent context.",
                            "media_type": "text/markdown",
                            "preview_content": prompt_content[:8000],
                        },
                        "steps": [
                            {"label": f"Loading {definition.id}", "detail": "Skill prompt injected into agent context", "status": "done", "kind": "thinking"},
                        ],
                    }
        except Exception:
            pass

    return await _manual_skill_stub(goal=goal, agent_label=agent_label, skill_label=definition.label)
