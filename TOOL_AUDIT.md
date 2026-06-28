# Empyralis Tool Builder Audit — 2026-06-28

## Summary

All tools flow through ONE entry point: `_direct_tool_bundle()` at `sage_agent_runtime_service.py:1153`. It calls three builders from `skills_service.py` (via `direct_chat_tool_catalog_service.py` thin wrappers). No channel adapter bypasses this path. No tool is registered outside this system.

**Total tools:** 48 (14 local + 34 builtin) + dynamic connector tools from workspace capabilities.

---

## PRIMARY ENTRY POINT

### `_direct_tool_bundle()` — sage_agent_runtime_service.py:1153

```
_direct_tool_bundle(workspace_id, provider)
  ├─ resolve_workspace_tool_capabilities(workspace_id)    → tool_capabilities
  ├─ _resolve_direct_chat_availability(workspace_id, provider) → availability
  ├─ _build_direct_chat_tools(tool_capabilities)          → connector tools (dynamic)
  ├─ _build_local_direct_chat_tools(availability)         → local tools (gateway-gated)
  ├─ _build_builtin_direct_chat_tools()                   → builtin tools (always)
  ├─ FILTER: strip computer tools if browser_status != "online"
  └─ _dedupe_tools(tools)
```

**Called from:**
- `sage_agent_runtime_service.py:1559` — in `_run_sage_action_loop_v3()`
- `sage_agent_runtime_service.py:1818` — in the handle_sage_chat → run_sage call path

**Entitlement filtering:** YES — computer tools stripped when gateway offline. No credit/cap check on individual tools.

**Provider formatting:** None. Tools are generic dicts with `name`, `description`, `parameters` (JSON Schema). Formatted for specific providers in the LLM transport layer (`orion_local_worker_llm.py`).

---

## BUILDER 1 — Local Tools (gateway-gated)

**`build_local_direct_chat_tools()`** → `skills_service.py:1168`
→ delegates to `_local_tool_descriptors()` at `skills_service.py:448`
→ gate: returns `[]` if `local_worker_available(availability)` is False

| # | Tool Name | Connector | Description |
|---|-----------|-----------|-------------|
| 1 | `file__read` | file | Read a file from the local machine |
| 2 | `file__write` | file | Write content to a file |
| 3 | `shell__exec` | shell | Execute a shell command |
| 4 | `screenshot__capture` | screenshot | Take a screenshot |
| 5 | `computer__ocr` | computer | Read visible text via OCR |
| 6 | `computer__click` | computer | Click by coordinates or text |
| 7 | `computer__type` | computer | Type text into active app |
| 8 | `computer__applescript` | computer | Execute system script |
| 9 | `computer__clipboard_read` | computer | Read clipboard |
| 10 | `computer__clipboard_write` | computer | Write to clipboard |
| 11 | `computer__notify` | computer | Send system notification |
| 12 | `computer__list_apps` | computer | List running applications |
| 13 | `computer__launch_app` | computer | Launch an application |
| 14 | `computer__speak` | computer | Speak text aloud |

**Total: 14.** All `requires_runtime=True`. Hidden when gateway is offline.

---

## BUILDER 2 — Connector Tools (workspace capabilities)

**`build_direct_chat_tools()`** → `skills_service.py:1178`

Dynamic — reads `tool_capabilities` (workspace connector bindings). For each capability, iterates write actions, resolves `ToolDescriptor` contracts, builds tool payloads with `name`, `description`, `connector_id`, `action_id`, `parameters`, `risk_level`, `requires_approval`.

**Count:** Varies by workspace. Generated from connector bindings (Google Workspace, SMTP, Telegram Bot, Slack, Discord, Dropbox, S3, GitHub, Linear, Notion connectors).

---

## BUILDER 3 — Builtin Tools (always available)

**`build_builtin_direct_chat_tools()`** → `skills_service.py:1244`
→ delegates to `_builtin_tool_descriptors()` at `skills_service.py:613`

| # | Tool Name | Connector | Description |
|---|-----------|-----------|-------------|
| 1 | `hardware__action` | hardware | Run action through runtime target |
| 2 | `memory_search` | memory | Search memory files |
| 3 | `memory_get` | memory | Read memory file excerpt |
| 4 | `memory_update` | memory | Update memory context file |
| 5 | `memory_stage_edit` | memory | Stage proposed memory edit |
| 6 | `memory_apply_edit` | memory | Apply staged memory edit |
| 7 | `memory_append_daily_note` | memory | Append daily note |
| 8 | `memory_stage_consolidation` | memory | Stage memory consolidation |
| 9 | `memory_consolidate_daily_notes` | memory | Consolidate daily notes |
| 10 | `memory_list_versions` | memory | List file version records |
| 11 | `memory_rollback_version` | memory | Rollback to previous version |
| 12 | `web__search` | web | Search the web |
| 13 | `web__fetch` | web | Fetch webpage content |
| 14 | `llm__task` | llm | Run focused sub-task |
| 15 | `http_request` | http | Generic HTTP request |
| 16 | `generate_image` | image | Generate images from prompt |
| 17 | `sage_service__list_state` | sage_service | Read saved service state |
| 18 | `sage_service__update_profile` | sage_service | Update service profile |
| 19 | `sage_service__create_entry` | sage_service | Create service entry |
| 20 | `browser__navigate` | browser | Navigate to URL |
| 21 | `browser__screenshot` | browser | Take page screenshot |
| 22 | `browser__observe` | browser | Observe page state |
| 23 | `browser__click` | browser | Click element |
| 24 | `browser__fill` | browser | Fill form field |
| 25 | `browser__extract_text` | browser | Extract visible text |
| 26 | `browser__get_page_state` | browser | Get page state |
| 27 | `browser__execute_js` | browser | Execute JavaScript |
| 28 | `browser__new_tab` | browser | Open new tab |
| 29 | `browser__switch_tab` | browser | Switch active tab |
| 30 | `browser__download_file` | browser | Download file from page |
| 31 | `browser__start_intercept` | browser | Start network intercept |
| 32 | `browser__stop_intercept` | browser | Stop network intercept |
| 33 | `browser__pdf` | browser | Generate PDF |
| 34 | `send_image` | — | Send an image |

**Total: 34.** Some (`hardware__action`, `memory_update`, `memory_stage_edit`, `memory_apply_edit`) have `requires_approval=True`.

---

## AGENT MACHINE MODE — Special injection

In `build_builtin_direct_chat_tools()` (line 1248-1262): when `AGENT_MACHINE_MODE == "agent"` AND supervisor is allowed, local tools are **prepended** into the builtin list (in addition to being in `build_local_direct_chat_tools`). This means in agent mode, local tools appear in BOTH builders — but `_dedupe_tools()` in `_direct_tool_bundle` collapses them.

---

## COVERAGE ANALYSIS

### Duplicates (same tool in multiple builders)

| Tool | Builders | How resolved |
|------|----------|-------------|
| All 14 local tools | Builder 1 (local) + Builder 3 (builtin, agent mode) | `_dedupe_tools()` in `_direct_tool_bundle` |

No other duplicates. The overlap only occurs in agent mode and is deduplicated.

### Divergence (tools that don't reach both web and channels)

**NONE.** Every tool goes through `_direct_tool_bundle()` → `_run_sage_action_loop_v3()` → `stream_provider_backed_direct_chat()`. This path is identical for web and all channels. No channel gets a different tool set.

### Channel bypass check

**NONE.** No channel adapter calls any tool builder directly. Every path through `execute_sage_turn()` → `handle_sage_chat()` → `_run_sage_action_loop_v3()` → `_direct_tool_bundle()`.

---

## ARCHITECTURE ASSESSMENT

### What's correct
1. Single entry point (`_direct_tool_bundle`) for all channels
2. Builtin tools are always available, local tools gated on gateway, connector tools gated on workspace capabilities
3. Browser status filter correctly strips computer tools when offline
4. Deduplication handles the agent-mode overlap
5. No channel bypass — tool list is identical regardless of surface

### What could be cleaner (deferred, not broken)
1. **Three builders → one catalog:** `_local_tool_descriptors()` + `_builtin_tool_descriptors()` + dynamic connector tools could be one `TOOL_CATALOG` with per-tool metadata (`category`, `requires_gateway`, `requires_capability`). The current three-builder pattern is functionally correct but structurally scattered.
2. **Agent-mode double injection:** Local tools are added in both `build_local_direct_chat_tools` and `build_builtin_direct_chat_tools` (agent mode). Relies on `_dedupe_tools` to clean up. Would be cleaner as a single injection point.
3. **`direct_chat_tool_catalog_service.py` thin wrappers** (lines 129-141) add no value — they just delegate to `skills_service.py`. Could import directly.
4. **`skills_service.py` dual role:** It's both the skill loading system AND the tool descriptor registry. The tool descriptor functions (`_local_tool_descriptors`, `_builtin_tool_descriptors`, `_tool_payload_from_descriptor`) could live in a dedicated `tool_catalog.py` with no behavior change.
