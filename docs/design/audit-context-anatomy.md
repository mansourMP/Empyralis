# Audit: Context-Window Anatomy — What Actually Enters an Empyralis Agent's Prompt

**Date:** 2026-07-23
**Scope:** Reconstruct, file:line-verified, exactly what enters the model's `system` / `messages` / `tools` on a real turn — web chat and channel (Sage) turns both — in the style of Claude Code's context-window breakdown. Evidence gathered by reading the live code paths, not the docstrings that describe them.

**Note on sourcing:** `direct_chat_generation_service.py` had a 112-line working-tree diff against `HEAD` at audit time (a concurrent edit). Every claim below about that file cites line numbers from `git show HEAD:server_modules/direct_chat_generation_service.py`, not the working tree, so citations stay stable. Everything else cites the working tree directly.

**Headline finding, stated up front:** the founder's fear — "a wall of text the agent has to reason through by itself" — is **largely not what's happening today**. There is a real, deliberate two-tier tool system (12 always-on tools + BM25-searched registry), a genuinely native tool-calling path for every provider including DeepSeek, a token-budgeted system prompt capped at ~3,000 tokens, and a proportional (window-scaled) history budget. The design intent already matches Claude Code's shape in most places. The gaps that remain are narrower and more specific than "wall of text": one truly unbounded injection path (Codex tool-result re-injection), one history channel with no aggregate budget (only a per-message cap), a prose tool-listing that duplicates the native schemas, and a compaction summarizer that's hard-pinned to a single cheap model regardless of what the user is actually paying for.

---

## 1. The window, measured (Claude-Code-style breakdown)

| Category | Est. tokens (typical turn) | Loaded vs deferred | Source (file:line) |
|---|---|---|---|
| **System prompt — kernel rules** (durable-memory rule, tool-honesty rules) | ~300–500 | Always loaded, fixed text | `sage_instruction_compiler_service.py:472-520` (`_kernel_prompt`) |
| **System prompt — policy context** (tier/capabilities/restrictions as prose) | small, budget-shared | Always loaded when present | `sage_instruction_compiler_service.py:691-692` |
| **System prompt — capability manifest** ("## Callable Tools" prose list) | ≤16 lines × ~180 chars ≈ 700–900 | Always loaded, capped | `sage_instruction_compiler_service.py:425-463` (`CAPABILITY_MANIFEST_MAX_ITEMS=16`, `:54`; 6 reserved for skills, `:64`) |
| **System prompt — root memory brief** (SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS.md excerpts) | ≤1,200 (4,800 char cap) | Always loaded, capped | `sage_instruction_compiler_service.py:48-49` (`ROOT_MEMORY_BRIEF_TOTAL_CHAR_LIMIT=4_800`), `:167-246` |
| **System prompt — user profile** | ≤375 (1,500 char cap) | Always loaded, capped | `sage_instruction_compiler_service.py:52` |
| **System prompt — workspace/heartbeat state** | ≤225 (900 char cap) | **Retrieval-gated** — only if message needs it | `sage_instruction_compiler_service.py:53`, `:707-714` (`_message_needs_workspace_state`) |
| **System prompt — retrieved memory** | ≤750 (3,000 char cap) | **Retrieval-gated** — only if message needs it | `sage_instruction_compiler_service.py:51`, `:716-724` (`_message_needs_memory_context`) |
| **System prompt — channel/attribution context** | ~30–60 | Always loaded on channel turns | `sage_instruction_compiler_service.py:726-748` |
| **System prompt TOTAL** | **capped at ~3,000 tokens** (12,000 chars) | Hard budget, every section clips into the same pool | `sage_instruction_compiler_service.py:50` (`SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT=12_000`), enforced by `append_section` at `:663-677` |
| **Tools — always-on tier** (`task_complete`, `update_plan`, 7 memory tools, `web__search`, `web__fetch`, `hardware__action`, `query_tool_registry`) | ~800 (estimated at design time, not re-measured) | Always loaded, native schemas | `tool_registry_service.py:34-47` (`ALWAYS_ON_TOOL_NAMES`, 12 names), `:527-562` |
| **Tools — registry (everything else)** | 0 by default | **Deferred** — name+description not even sent; loaded only via `query_tool_registry`, top 3-5 (max 10) results per call, BM25-ranked, real function schemas | `tool_registry_service.py:1-18` (module docstring), `:276-337` (registry build), `:416-453` (search) |
| **Messages — prior turns (web/direct chat)** | proportional to model window: `conversation_budget` up to 65% of window, recent-turn slice ≤35% of that (≤24,000 tokens hard cap) | Loaded, **window-proportional**, non-LLM heuristic truncation for old turns | `conversation_memory_policy.py:322-347` (`build_model_aware_memory_policy`), `conversation_compaction.py:63-112` |
| **Messages — prior turns (channel/Sage recall)** | up to 16 messages × 4,000 chars ≈ **worst case ~16,000 tokens** | Loaded, **per-message capped, NO aggregate token budget** | `sage_instruction_compiler_service.py:583-607` (`_normalize_recent_messages`, `[-16:]`, `content[:4000]`) |
| **Tool results (this turn, non-Codex providers)** | ≤1,000 tokens per result (4,000 char cap) | Loaded, capped | `direct_chat_generation_service.py:2202-2204` (HEAD) |
| **Tool results (this turn, Codex/`codex_cli` provider)** | **unbounded** | Loaded, **no cap at all** | `direct_tool_execution_service.py:789-795` (`direct_tool_followup_message`), call site `direct_chat_generation_service.py:2191-2200` (HEAD) |
| **Tool result — `web__fetch`** | ≤3,000 (12,000 char cap) | Loaded, capped, no pagination | `web_tools.py:72-82` |
| **Tool result — `web__search`** | small, 5 results max, per-field capped | Loaded, capped | `web_tools.py:113-130` (`if len(results) >= 5: break`) |
| **Compaction reserve** | 16,384 tokens reserved off the top | Structural, not content | `compaction_service.py:39` |
| **Free space** | whatever's left of the model's real context window (per-model, catalog-driven, not guessed) | — | `provider_profiles.py:1830-1870` (`context_window_for_model`) |
| **Deferred, never loaded unless asked** | full skills catalog beyond the 6 reserved slots, full tool registry beyond BM25 top-5, MCP tool schemas beyond enabled+approved | — | `tool_registry_service.py`, `sage_instruction_compiler_service.py:445-448` |

---

## 2. The API request anatomy: native tool-calling vs prose, per provider

The real per-provider request builders live in **`scripts/orion_local_worker_llm.py`** — a hand-rolled HTTP layer (no SDK; `urllib`/`curl` calls), not `model_router.py` (that module has no `tools` parameter at all and is only used for simple non-tool completions like triage — verified by grep, zero `tools=` hits in `model_router.py`).

Every supported provider gets **real, native, structured tool/function schemas** — none get a text-protocol fallback:

| Provider(s) | Wire format | Evidence |
|---|---|---|
| OpenAI-compatible (openai, azure_openai, **deepseek**, groq, mistral, openrouter, qwen, xai, ollama, ollama_cloud) | `{"type":"function","function":{name,description,parameters}}`, `tool_choice:"auto"`, `parallel_tool_calls:true` | `orion_local_worker_llm.py:2064-2078` |
| Anthropic | `{"name","description","input_schema"}` (native Claude tool_use/tool_result blocks) | `orion_local_worker_llm.py:2163-2173`, message shaping at `:477-530` |
| Gemini | `{"functionDeclarations":[...]}`, `toolConfig.functionCallingConfig.mode="AUTO"` | `orion_local_worker_llm.py:2228-2242` |
| OpenAI Codex (Responses API) | `{"type":"function","name","description","parameters"}` on `/responses` | `orion_local_worker_llm.py:2486-2500` |

**DeepSeek specifically gets native tool-calling**, not prose — this directly answers the founder's stated worry. It goes through the same `iter_openai_compatible_chat_events` path as every OpenAI-compatible provider (`orion_local_worker_llm.py:2020-2135`). What DeepSeek gets that's *different* is workaround handling for two live-verified provider bugs:
- A **20-tool hard trim** — connected-app tools first, then Sage tools, then the rest — only for `provider_id == "deepseek"` (`direct_chat_generation_service.py:1113-1144`, HEAD).
- **Tool-definition stripping after the first tool-executing round** — DeepSeek is the sole member of `_TOOL_STRIP_REQUIRED_PROVIDERS` because it returns empty content when tools + prior tool-results coexist in one request; every other provider keeps tools live across iterations for real multi-round tool use (`direct_chat_generation_service.py:226-247`, HEAD).
- A **retry-without-tools fallback** when DeepSeek returns empty content with tools in the payload (`orion_local_worker_llm.py:2109-2119`).

These are honest, narrowly-scoped, comment-documented workarounds for one model's real quirks — not evidence of a degraded text-protocol path.

**Live wiring confirmed, dead wiring found:** `direct_chat_runtime_service.py` carries its own system-prompt builder (`direct_chat_prompt_service.build_system_prompt`, which *does* render tools as flat prose lines — `direct_chat_prompt_service.py:7-17`) — but that whole module is self-documented as **dead**: `direct_chat_runtime_service.py:1-35` states "DORMANT — confirmed unreachable from any live route (2026-07-09 audit)," and a live reference at line 1079 (`tools=provider_chat_tools`) refers to a name that is defined nowhere else in the entire repo (confirmed by grep) — it would `NameError` if ever executed, further proof nothing calls it. The **real** live path for both web chat and channel turns is `sage_turn_adapter.execute_sage_turn()` → `sage_agent_runtime_service.handle_sage_chat()` → `_run_sage_action_loop_v3()` → `direct_chat_generation_service.stream_provider_backed_direct_chat()`, confirmed by `sage_agent_runtime_service.py` never importing `direct_chat_runtime_service` at all (grep, zero hits) while it does build its system prompt via `sage_instruction_compiler_service.build_sage_instruction_bundle()` (`sage_agent_runtime_service.py:3373`, `:4152-4185`) and thread the same `tools`/`tool_registry` machinery through (`sage_agent_runtime_service.py:2415`, `:3103-3151`). **Net effect: the tool-prose-listing code exists in the repo but is not reachable — worth a cleanup pass, not an audit alarm.**

---

## 3. Tool surface per turn

A genuinely Claude-Code-shaped two-tier design, shared identically by web chat and channel/Sage turns:

- **Tier 1 — always-on (12 tools, ~800 tokens estimated):** `task_complete`, `update_plan`, `memory_write/read/search/get/update/append_daily_note` (7 memory tools total incl. `query_tool_registry` counted separately), `web__search`, `web__fetch`, `hardware__action`, `query_tool_registry` — `tool_registry_service.py:34-47`.
- **Tier 2 — registry, loaded on demand:** everything else — ~37 built-in tool descriptors total minus the always-on 11 real ones (`skills_service.py:659-1932`, 37 `tool_name=` entries counted directly), plus local tools, plus every connected-app tool, plus enabled+approved MCP tools (`tool_registry_service.py:276-337`). None of these names or schemas are sent up front.
- **Discovery:** the model calls `query_tool_registry(task_description)`; results come back via **real Okapi BM25** (not a substring/keyword scorer) with name-field weighting 3x over description, plus a small hand-curated synonym layer for chat vocabulary ("mail"→"email", "chat"→"message"/"slack") — `tool_registry_service.py:340-412`, `:74-113`. Top 3-5 results (max 10), full native schemas, get appended to the live `tools` list for the rest of the turn, deduped by name (`direct_chat_generation_service.py:1793-1807`, HEAD).
- **Estimated savings:** the module's own docstring claims ~87% token savings on simple Q&A turns, ~75% on typical tool-using turns vs. sending all 48+ tools every time — "estimated at the time the two-tier design was introduced; not re-measured since" (`tool_registry_service.py:16-18`). Worth an actual re-measurement, but the architecture itself is sound.
- **This is the same design used for both surfaces**, confirmed: web chat builds tools via `build_always_on_direct_chat_tools_fn` + `build_registry_entries_fn` in `direct_chat_entry_service.py:444-451`; channel/Sage turns call the identical `direct_chat_tool_catalog_service.build_registry_entries()` at `sage_agent_runtime_service.py:2415`.
- **Capability-manifest prose (separate from the above):** `sage_instruction_compiler_service.py` also renders up to 16 capability items as a "## Callable Tools" prose list in the system prompt (`:425-463`) — this is a **redundant description of tools already passed as native schemas**. It's small (capped, ~700-900 tokens worst case) and safety-net-motivated (comment: "Only these tools are callable in this turn... do not invent"), but it is real duplication Claude Code doesn't do — Claude Code trusts the native tool list alone.

---

## 4. Compaction: how it actually works today (the verified loop)

There are genuinely **two separate systems**, easy to conflate — neither matches the founder's described fear ("ask the agent to summarize its own context window"), but each has a real gap.

**System A — prior-message prep, every turn, no model call at all.**
Runs on every turn's history before the tool loop even starts, via `compact_conversation_history_fn` (`direct_chat_entry_service.py:388-394`). Budget is **window-proportional**: `build_model_aware_memory_policy` reserves ~8% for output, ~6% for system/tools, ~10% for retrieval, and gives the conversation up to 65% of the real context window (`conversation_memory_policy.py:322-347`). Anything over budget gets a **non-LLM, mechanical** compaction: `memory_summary_service.summarize_messages` turns each older message into a line `"- {Role}: {content, 240 chars max}"`, concatenated up to a char cap, no model call whatsoever (`memory_summary_service.py:24-49`). This is genuinely "compaction" in name only for the old portion — it's lossy line-truncation, not summarization intelligence. The recent tail is kept verbatim, token-budgeted (`conversation_compaction.py:43-60`).

**System B — primary-path (mid-turn) compaction, a real separate LLM call.**
This is what fires when the *current* turn's accumulated tool-loop context threatens to overflow the model's real window (proactive pre-flight, `direct_chat_generation_service.py:285-320` HEAD) or after an actual provider overflow error (reactive retry, `sage_agent_runtime_service.py:5076-5174`). Trigger: `estimate_tokens(...) + COMPACTION_RESERVE_TOKENS(16,384) > context_window` (`compaction_service.py:71-86`). It walks back from the newest turn, keeps ~15% of the window as recent raw turns (capped 30,000 tokens, `compaction_service.py:42-53`), and summarizes the rest via **`compact_turns()`** — a genuinely separate LLM call (`compaction_service.py:189-239`), not the same model reasoning about its own window mid-conversation. The summary is persisted as an `agent_turns` row with `role="compaction_summary"` (`:226-235`) and reloaded as a `system`-role message on the next call (`build_context_from_compaction`, `:244-266`).

**The real gap: the summarizer model is hard-pinned, not model-aware.** Every call site of `compact_turns()` across the codebase (`direct_chat_generation_service.py:394-408` HEAD, `sage_agent_runtime_service.py:3557-3562`/`:5031-5036`/`:5145-5150`/`:5500-5505`, `sage_command_dispatcher.py:364-370`) omits `provider`/`model`. `compact_turns`'s default (`compaction_service.py:197,217`) falls through to **`provider="deepseek"`** unconditionally, using a single **platform-wide** environment-variable key (`get_deepseek_api_key()`, `orion_local_worker_llm.py:914-919`) — not the user's own model, not even the user's own DeepSeek credential if they have one. Consequence: if a user is paying for Opus/GPT-5 for quality, their conversation summary is still produced by whatever the platform's shared DeepSeek key can do. And if that env var isn't set on the server at all, `compact_turns` silently returns `""`, compaction no-ops platform-wide (fails safe, never crashes), and the turn falls back on the reactive-overflow retry's raw-truncation fallback (`sage_agent_runtime_service.py:5164-5171`, keeps last 50 raw turns with no token guarantee) or, for channel turns, a genuine token-budgeted truncation with no summary at all (`:5127-5134`).

**Verdict:** compaction is real, not a same-model self-summarization hack, and fails safe. But it is not model-aware, and its quality is capped by a hardcoded cheap model that every call site forgot to parameterize.

---

## 5. Attribution envelope: what the model sees (verbatim format)

This is the strongest part of the system — structured, deterministic, and code-gated rather than prompt-hoped-for. `inbound_envelope.py` defines a `SurfaceKind` taxonomy (`owner_self_chat`, `dm`, `group`, `broadcast_channel`, `console`, `api`) and a tri-state `is_owner` (`True`/`False`/`None`="unverified, treated as NOT owner" — fails closed, `:87-95`).

**The exact header prepended to every inbound message** (`inbound_envelope.py:192-252`, `render_envelope_header`):

```
[Telegram · your owner Mansur · talking to you directly]
[Slack · DM · from Dana — NOT your owner]
[Telegram · group "Family" · from Aruzhan — NOT your owner · you were not
 addressed — observe; reply only if clearly addressed or truly helpful;
 otherwise reply exactly [SILENT]]
[Console · your owner Mansur]
```

This is injected exactly once, at the single chokepoint every channel routes through — `sage_turn_adapter.py:305` (`prepend_envelope_header`), confirmed live (not just designed) by grepping the import and both call sites.

Separately, the code-level gate `envelope_allows_owner_commands()` (`inbound_envelope.py:167-186`) makes "a family group can carry owner authority" **structurally impossible** — True only for verified owner + private surface (self-chat/DM/console), never for group/broadcast — this doesn't depend on the model reading and obeying the header correctly at all, it's enforced before the model is ever asked.

On top of this, the system prompt separately states which channel the reply must be formatted for (`sage_instruction_compiler_service.py:613-620`, e.g. Telegram → "under ~800 chars unless detail is needed") and, for cross-channel history, tags older messages from a different channel inline (`[via telegram_personal] ...`, `:601-605`, `:763-770`) so the model can distinguish "what I saw here" from "what I know from elsewhere." **This is a genuinely good design — structured, consistent, single injection point, fails closed. No changes recommended here.**

---

## 6. Memory injection

Two distinct memory surfaces enter context, both bounded and mostly retrieval-gated:

- **`sage_memory_service.build_sage_memory_context_block`** (`sage_memory_service.py:591-617`): capped at 3 entries per category, restricted-category content explicitly withheld from the model ("Restricted memory exists but is withheld from model context," `:607-610`). Feeds into the retrieved-memory system-prompt section, itself capped at 3,000 chars **and gated** — only injected when `_message_needs_memory_context(message)` returns true (`sage_instruction_compiler_service.py:527-561`, `:716-724`). Real Q&A turns that don't reference the past skip this section entirely.
- **`MEMORY.md` (root memory file)** is treated as an index, not a dump: it's one of the `ALWAYS_LOAD_INSTRUCTION_FILES` at a 900-char/file, 4,800-char-total brief budget (`sage_instruction_compiler_service.py:43-49`), with `memory_search`/`memory_get` (both in the always-on 12) available for full on-demand detail — the index-first, progressive-disclosure pattern the founder's own prior research doc calls for is actually implemented here, not just aspirational.

No evidence of memory being "dumped" — every path found is capped and most are retrieval-gated.

---

## 7. Wall-of-text offenders, ranked (worst first)

1. **Codex-provider tool results are completely unbounded.** `direct_tool_followup_message()` (`direct_tool_execution_service.py:789-795`) wraps `result_text` with zero truncation and re-injects it as a plain `user` message (`direct_chat_generation_service.py:2191-2200`, HEAD) — the exact same result that every *other* provider caps at 4,000 chars (`:2202-2204`, HEAD) is, for `codex_cli`, sent raw. A large file read, a verbose connector JSON dump, or a big `web__fetch` result (already itself capped at 12,000 chars — so up to ~3,000 tokens) goes straight through uncapped for this one provider. **Highest-priority fix.**

2. **Channel/Sage recent-message history has no aggregate token budget.** `_normalize_recent_messages` (`sage_instruction_compiler_service.py:583-607`) keeps the last 16 messages, each capped at 4,000 chars, but never sums them — worst case (16 genuinely long turns) is ~16,000 tokens with no proportional-to-window scaling, unlike the web-chat path's `build_model_aware_memory_policy` which *is* window-proportional (`conversation_memory_policy.py:322-347`). A small-context model (or a long-running Telegram thread) can absorb a disproportionate history hit here relative to everything else in the budget.

3. **The capability-manifest prose list duplicates the native tool schemas.** `_capability_manifest_text` (`sage_instruction_compiler_service.py:425-463`) renders up to 16 tools as `"- {tool}: {label}. {description} ({runtime}; {approval})."` lines in the system prompt — information the model already has as structured, callable schemas via `tools=`. Bounded (~700-900 tokens) and safety-motivated, but genuinely redundant against the Claude Code pattern of trusting native tool definitions alone.

4. **Compaction's summarizer model is hardcoded, not parameterized.** Every one of ~7 call sites across 3 files omits `provider`/`model`, so `compact_turns` silently always uses a single platform-wide DeepSeek key (§4) — not a "wall of text" itself, but a quiet quality/consistency gap that undermines what compaction is *for* on a high-tier conversation.

5. **Dead code that looks live at a glance.** `direct_chat_prompt_service.build_system_prompt` renders tools as flat prose (`tool_prompt_lines`, `direct_chat_prompt_service.py:7-17`) and is still wired into `direct_chat_operator_binding_service.py:858-866` — but its only caller module (`direct_chat_runtime_service.py`) is confirmed dead by its own header comment and by a `NameError`-in-waiting at line 1079. Not a live risk, but worth deleting so a future engineer doesn't "fix" or extend a path that never runs.

Everything else audited — `web__fetch` (12,000 char cap), `web__search` (5 results, per-field caps), the always-on tool tier, BM25 registry discovery, the attribution envelope, the window-proportional web-chat history budget, `sage_memory_service`'s per-category cap, and the retrieval-gating on workspace-state/memory sections — is genuinely well-bounded and comparable in spirit to Claude Code's own context discipline.

---

## 8. Smallest set of changes for a Claude-Code-grade window

1. **Cap Codex tool-result re-injection** at the same 4,000-char bound (or better, a per-provider-configurable one) every other provider already gets — one-line change at `direct_chat_generation_service.py:2191-2200`, mirroring the existing `_tool_content[:3800] + "...[truncated N chars]"` pattern at `:2202-2204`.
2. **Give channel recent-message history an aggregate token budget**, not just a per-message char cap — reuse `build_model_aware_memory_policy`'s proportional-to-window logic (`conversation_memory_policy.py:322-347`) for `_normalize_recent_messages` instead of a flat `[-16:]` / `[:4000]` pair.
3. **Thread `provider`/`model` through every `compact_turns()` call site** so compaction summarizes with (or at least near) the same tier the user is paying for, with DeepSeek retained only as the true fallback when the primary model's key/route is unavailable.
4. **Delete the redundant capability-manifest prose section**, or fold it down to only the items *not* already visible as native schemas (e.g., approval-required flags and runtime-requirement notes the schema itself can't express) — keep the safety-relevant metadata, drop the tool-by-tool re-description.
5. **Delete `direct_chat_runtime_service.py` and its prose-tool-listing dependency chain** (`direct_chat_prompt_service.build_system_prompt`/`tool_prompt_lines`) now that it's confirmed unreachable, so it can't be mistakenly revived or extended.

None of these are large changes. The architecture underneath — two-tier native tool-calling with BM25 discovery, a proportional system-prompt budget, a structured attribution envelope, and a real (if imperfectly parameterized) separate-call compaction system — is already close to what the founder is asking for. The gaps are narrow, named, and file:line-fixable, not evidence of a fundamentally undisciplined context window.
