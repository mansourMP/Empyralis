# Audit: Context-Window Management & Compaction Currency

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). This document's body does not use "Sage" as prose; every `sage_*`/`sage_chat` occurrence below is a literal code identifier or surface name — an accurate code citation, not live product concept language.

Report-only. No code changed. Verified against `/Users/mansur/empyralis` on branch
`main`, 2026-07-22. Builds on and re-verifies (does not repeat) two prior docs —
read those first for material not re-derived here:

- `docs/design/backbone-anthropic.md` — mechanism reference for Anthropic's
  current context-window/compaction/caching/skills/subagents/memory stack,
  fetched live on 2026-07-22 (today).
- `docs/design/backbone-empyralis.md` — file:line inventory of Empyralis's own
  implementation, same date, framed as "near-parity" with OpenClaw on
  compaction specifically.

**This audit's finding, stated up front**: the "near-parity" framing in
`backbone-empyralis.md` is accurate about the code that *exists*
(`compaction_service.py` is genuine, proactive+reactive, LLM-summarized
compaction) but did not check whether that code is *reachable* from the
primary generation path. It mostly isn't. See §3 verdict on compaction, and
the new finding in §2 that wasn't in either prior doc.

---

## 1. Current official Anthropic stack — what it is, what changed

Fetched live this pass (URLs below); cross-checked against the `claude-api`
skill's cached reference (verified consistent, no drift found).

### The three-tier context-exhaustion defense

| Tier | Primitive | Beta header | Trigger (default) | What happens |
|---|---|---|---|---|
| 1 — cheap clearing | `clear_tool_uses_20250919` | `context-management-2025-06-27` | 100,000 input tokens (`trigger.value`) | Clears oldest tool **results** (optionally inputs, `clear_tool_inputs: true`), keeps most recent 3 tool-use/result pairs (`keep.value`), replaces cleared content with a placeholder. `clear_at_least` sets a minimum clear size to avoid trivial cache-invalidating clears. `exclude_tools` allowlist. |
| 1b — thinking clearing | `clear_thinking_20251015` | same header | N/A (keeps last N thinking turns, or `"all"`) | Model-tiered default retention: Opus 4.5+/Sonnet 4.6+ keep all prior thinking by default; older tiers keep only the last turn. Must be listed **first** in `edits[]` when combined with `clear_tool_uses`. |
| 2 — heavier compaction | `compact_20260112` | `compact-2026-01-12` | 150,000 input tokens, minimum configurable 50,000 | API generates a summary, wraps it in a `compaction` content block, **discards every block prior to it**. Client just appends the full `response.content` (compaction block included) back onto its message array. `usage.iterations[]` breaks out the compaction pass's own tokens separately — sum across `iterations` for true total, not top-level `usage`. |
| 3 — escape valve | Memory tool (`memory_20250818`) | none (GA) | N/A (agent-driven) | Client-executed file tool against a virtual `/memories` root. API auto-injects "ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE... ASSUME INTERRUPTION" into the system prompt whenever the tool is present. Explicitly documented to compose with tiers 1 and 2: clearing warns before it fires so the agent can persist first; compaction "keeps active context small... memory preserves what must survive summarization." |

Context editing (tier 1) is **clearing, not summarizing** — cheaper, keeps
transcript structure. Compaction (tier 2) is **summarizing** — heavier, used
when clearing alone isn't enough. They are independently configurable and
explicitly meant to be composed, not alternatives.

Sources (fetched live this pass):
- [Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)

### What changed recently (per the fetched pages + skill cache)

- **Context editing's own docs now explicitly steer toward server-side
  strategies over any client-side `compaction_control`** — the older
  SDK-side compaction concept is called out as deprecated in favor of the
  two server-side primitives above. Empyralis's `compaction_service.py`
  predates and is architecturally closer to the old client-side model (see
  §2/§3).
- **Compaction's `usage.iterations[]` accounting is the current billing
  model** — top-level `usage.input_tokens`/`output_tokens` reflect only the
  non-compaction iteration; a caller that reads only the top-level fields
  under-reports true spend. Nothing in Empyralis reads or replicates this
  shape (Empyralis doesn't call `compact_20260112` at all — see §3).
- **Prompt caching's 1-hour TTL is now free/automatic on a Claude
  subscription** (usage plan-included) and opt-in via `ttl: "1h"` on API-key
  auth; 5-minute write is 1.25× base, 1-hour write is 2×, reads are 0.1×.
  Minimum cacheable prefix is tiered by model (512 tokens on Fable 5/Mythos 5
  up to 4,096 on Opus 4.6/4.5) — irrelevant to Empyralis today since zero
  `cache_control` is sent anywhere (§3).
- **Token counting** (`POST /v1/messages/count_tokens`) is model-specific and
  explicitly **not** interchangeable with `tiktoken`-style estimators — the
  skill's own guidance flags Claude Fable 5's tokenizer alone shifting counts
  ~1×–1.35× vs. older models for identical text. Empyralis uses a flat
  4-chars/token heuristic everywhere (§2, §3) — exactly the failure mode this
  guidance warns against.
- **Long-context guidance** (Anthropic engineering blog, "Effective context
  engineering for AI agents") frames the governing pattern as: compaction +
  structured note-taking (memory tool) + sub-agent condensed summaries
  (1,000–2,000 tokens) + just-in-time retrieval (lightweight identifiers
  resolved at runtime) rather than pre-loading everything. Empyralis's
  two-tier tool catalog (§2 anatomy table) is a genuine instance of the
  just-in-time pattern; nothing else in the stack matches the rest.

Sources: [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting) (per `shared/token-counting.md` in the `claude-api` skill), [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).

---

## 2. Empyralis per-turn context anatomy

There are **three distinct chat entry surfaces** that assemble context
differently — this itself is a finding (§4 fix list item 7). All figures use
Empyralis's own 4-chars/token heuristic (same one used throughout the
codebase — see `compaction_service.py:21-23`, `sage_instruction_compiler_service.py:78-82`,
`conversation_compaction.py:18-22` — three separate implementations of the
identical formula).

### `sage_chat` surface (deployed channel agents — Telegram/WhatsApp/Discord/web owner chat)

| Component | Source (file:line) | Approx tokens | Notes |
|---|---|---|---|
| System prompt (kernel + policy + capability manifest + root-memory brief + user profile + workspace state + retrieved memory + channel context) | `sage_instruction_compiler_service.build_sage_instruction_bundle`, `sage_instruction_compiler_service.py:596-786` | **Hard-capped at ≤3,000** (12,000-char budget, `SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT = 12_000`, line 50) | Sections fill a shared 12,000-char pool in strict order — `append_section` (line 637-651) consumes `remaining_budget` greedily: kernel → policy_context → capabilities → root_memory_brief → user_profile → workspace_state → retrieved_memory → channel_context. A large kernel+capability-manifest can starve memory/profile sections with no signal beyond an entry in `skipped_sections`. Self-instrumented: `diagnostics.estimated_input_tokens` (line 768) and per-section `section_char_counts` (line 771) are computed on every call. |
| — retrieved_memory sub-budget | `SAGE_RETRIEVED_MEMORY_CHAR_LIMIT`, line 51 | ≤750 | Only injected when `_message_needs_memory_context()` — a relevance gate, not always-on |
| — user_profile sub-budget | `SAGE_PROFILE_CONTEXT_CHAR_LIMIT`, line 52 | ≤375 | |
| — workspace_state (heartbeat) sub-budget | `SAGE_HEARTBEAT_CONTEXT_CHAR_LIMIT`, line 53 | ≤225 | Only when `_message_needs_workspace_state()` |
| Prior turns (`recent_messages` / `channel_prior_messages`) | Loaded `sage_agent_runtime_service.py:3896-3911`, capped at `SAGE_THREAD_MAX_TURNS = 10` (line 128); re-normalized `sage_instruction_compiler_service._normalize_recent_messages`, line 560-581 (re-caps at last 16, redundant given the tighter 10 upstream; each turn clipped to 4,000 chars, line 580) | **≤10,000 worst case** (10 turns × 4,000 chars) | Turn-count-capped, **not token-budget-capped** — a conversation of 10 very long turns is not compacted before this point (see §3 finding). |
| Tools — always-on tier | `tool_registry_service.build_always_on_tool_definitions()`, exposed via `direct_chat_tool_catalog_service.py:145-151` | **~800** (docstring's own estimate, ~8 tools) | `task_complete`, 5 memory tools, `web__search`, `web__fetch`, `hardware__action`, `query_tool_registry` |
| Tools — on-demand registry | `tool_registry_service.search_tool_registry`, `tool_registry_service.py:237-` | **Variable, ~200–2,000** | Top 3-5 (default)/10 (max) keyword-matched schemas appended mid-turn only when `query_tool_registry` is called and matches something |
| Compaction reserve (headroom held back from the resolved context window before the proactive check even runs) | `COMPACTION_RESERVE_TOKENS = 16_384`, `compaction_service.py:39` | 16,384 fixed | Applied in the *fallback-only* proactive preflight — see §3 |

### `direct_chat` surface (web UI / API chat — the other primary user-facing entry point)

Uses a **completely different, non-LLM compaction mechanism** — see §3 for
why this matters.

| Component | Source (file:line) | Approx tokens | Notes |
|---|---|---|---|
| Prior-turn budget | `conversation_memory_policy.py` `DIRECT_CHAT_PROFILE` base: `max_prompt_tokens=8000, preserve_last_messages=10` (lines 109-117); scaled by `build_model_aware_memory_policy` against the model's real context window into one of four tiers: 24K/96K/400K/unbounded (lines 74-90) | 8,000–400,000+ depending on model | Model-aware unlike the `sage_chat` path's fixed 10-turn cap |
| "Summary" of dropped history | `conversation_compaction.compact_conversation_history`, `conversation_compaction.py:63-112`, calling `memory_summary_service.summarize_messages`, `memory_summary_service.py:24-49` | Capped at `max_summary_chars` (policy-driven, ~2,200 chars ≈ 550 tokens base) | **Not an LLM call.** Mechanically renders each dropped message as `"- Role: <line, truncated to 240 chars>"` and joins — a lossy transcript excerpt, not a generated summary. See §3. |
| Tools | Same two-tier mechanism as `sage_chat` (`direct_chat_entry_service.py:447-452`) | Same as above | |

### Shared / provider-agnostic

| Component | Source | Notes |
|---|---|---|
| Continuous-work per-iteration budget gate | `direct_chat_generation_service._continuous_work_budget_allows_more`, `direct_chat_generation_service.py:285-320` | Reuses `compaction_service.resolve_context_window`/`estimate_tokens`/`COMPACTION_RESERVE_TOKENS` as a **stop-extending-the-loop** gate for the continuous-work (`update_plan`) feature past `max_iterations`. This is not compaction — it just refuses to let the loop keep going once the running transcript would leave no room, failing closed on any estimation error (line 317-320). |

---

## 3. Currency verdict per feature

| Feature | Verdict | Basis |
|---|---|---|
| **Compaction** (LLM summarization) | **Current in design, outdated in reach** | `compaction_service.py` (306 lines) is genuinely proactive (preflight before the call, `sage_agent_runtime_service.py:4739-4875`) and reactive (retry-with-compact on overflow, lines 4877-4998), with a real LLM summarization call (`compact_turns`, `compaction_service.py:189-239`, calls `openai_chat_text`) and a memory-flush-before-compact safety step. **New finding this pass**: both the proactive preflight and the reactive retry loop live *only* inside `handle_sage_chat`'s fallback branch — reached exclusively when `_run_sage_action_loop_v3` returns `None` (i.e., the turn produced neither a reply nor any tool activity — `sage_agent_runtime_service.py:3162-3167`). The **primary** tool-loop path — `_run_sage_action_loop_v3` → `direct_chat_generation_service.stream_provider_backed_direct_chat` → `generate_chat_reply_stream_with_provider_fallback` (`scripts/orion_local_worker_llm.py:3980`) — never calls `is_context_overflow_error`, never checks a token budget before its LLM call, and never compacts. A provider overflow there surfaces as an ordinary `event_type == "error"` (`direct_chat_generation_service.py:1080`, `llm_error = str(event.get("error") or "")`), which just fails the loop iteration with a generic error reply (`iteration_failed = True`) — the oversized context is never trimmed, so the same conversation will overflow again on the next turn. Confirmed by grep: `is_context_overflow_error` has exactly two call sites in the whole repo, both inside `sage_agent_runtime_service.py`'s fallback block; zero inside `direct_chat_generation_service.py` or `orion_local_worker_llm.py`. This is the single largest gap this audit found — the mechanism that exists is not the mechanism that runs for the common case. |
| **Context editing** (clear stale tool results/thinking, cheap) | **Missing** (weak static analog only) | No `clear_tool_uses`-shaped trigger/threshold/keep-last-K mechanism anywhere. The closest analog, `prune_tool_result` (`compaction_service.py:117-135`), caps a tool result's *stored* content at `TOOL_RESULT_MAX_CHARS = 2,000` chars unconditionally at write time — it doesn't scale with how full the window actually is, doesn't keep a configurable number of *recent* full results, and produces no placeholder-with-context the way Anthropic's `clear_tool_uses_20250919` does. There is no thinking-block clearing at all (Empyralis doesn't request extended/adaptive thinking from any provider — grepped `thinking=`/`"thinking"`/`adaptive`/`budget_tokens`/`output_config` across `scripts/orion_local_worker_llm.py`: zero hits). |
| **Prompt caching** | **Missing** | Confirmed by repo-wide grep: zero `cache_control`, `ephemeral`, or `anthropic-beta` prompt-caching header anywhere in an outbound-request-building context. The only `cache_control` hit in the tree (`scripts/deepseek_anthropic_proxy.py:95`) *reads* an inbound field off an image block for an unrelated purpose. What exists is purely passive: `usage_accounting_service.py:642-688` parses `cache_read_input_tokens`/`cache_creation_input_tokens` back out of whatever a provider's response reports (for pricing), and `pricing_registry_service.py` has cache-rate fields — evidence some providers cache automatically server-side, not evidence Empyralis requests or shapes prompts for caching. Re-confirmed unchanged from both prior audits. |
| **Token counting** | **Outdated** (heuristic, never the real API) | Every token-budget decision in the stack — `should_compact`, the proactive preflight, the continuous-work gate, the system-prompt budget, the direct_chat prior-message budget — runs on a flat `len(text) // 4` (or `math.ceil(len/4)`) heuristic, implemented independently three times (`compaction_service.py:21-23`, `sage_instruction_compiler_service.py:78-82`, `conversation_compaction.py:18-22`). `POST /v1/messages/count_tokens` is never called anywhere in the repo (grepped `count_tokens` across `server_modules/*.py` and `scripts/*.py`: no hits). The 4-chars/token constant is a reasonable average for English prose but is exactly the kind of estimate the current API guidance says not to rely on — code, JSON tool results, and non-English text all skew it, and it can't track real per-model tokenizer differences (e.g. the Opus 4.7-era tokenizer using ~1×–1.35× the tokens of older models for the same text) since it has no per-model awareness at all. |
| **Model catalog currency** (adjacent, but load-bearing for the above) | **Outdated** | `provider_profiles.py`'s Anthropic model table (`lines 942-1010`) tops out at `claude-opus-4-7` / `claude-sonnet-4-6` / `claude-haiku-4-5-20251001` — no `claude-opus-4-8`, `claude-sonnet-5`, or `claude-fable-5` entries at all, despite these being current as of today (2026-07-22; confirmed against the live-fetched model docs in §1). Because `compaction_service.resolve_context_window()` (`compaction_service.py:56-68`) looks up the context window by exact model string in this same table and falls back to `DEFAULT_CONTEXT_WINDOW = 128_000` (line 41) for any unrecognized model, selecting a current-generation Anthropic model not yet in the catalog would silently compact against a 128K assumption instead of the model's real 1M window — the opposite direction of the "avoid a silently inherited raw window" reasoning the code's own comments elsewhere claim to guard against (`sage_agent_runtime_service.py:3785-3796`, cited in `backbone-empyralis.md` §1). |

---

## 4. Ordered fix list

1. **Wire compaction into the primary generation path, not just the fallback branch.** Inside `direct_chat_generation_service.py`'s main iteration loop (around `direct_chat_generation_service.py:1383-1420`, where `event_type == "result"`/error handling already lives), call `compaction_service.is_context_overflow_error` on `llm_error` and, on a hit, run the same compact-and-retry sequence `sage_agent_runtime_service.py:4877-4998` already implements — but reachable from the path that actually handles nearly every real turn. This is the highest-leverage fix in this audit: it doesn't require new design, only moving/duplicating an existing, working mechanism to where it's actually needed.
2. **Add a proactive token-budget preflight ahead of the primary tool loop.** Today `_run_sage_action_loop_v3`'s only bound on prior turns is a fixed count (`SAGE_THREAD_MAX_TURNS = 10`), not tokens — ten very long turns can overflow a model's window without ever tripping the proactive check that currently exists only in the fallback branch. Move (or duplicate) the `sage_agent_runtime_service.py:4739-4875` preflight to run before `_run_sage_action_loop_v3` is called (`sage_agent_runtime_service.py:4357`), not after it returns `None`.
3. **Keep the Anthropic model catalog current, or stop hardcoding it.** Add `claude-opus-4-8` / `claude-sonnet-5` / `claude-fable-5` to `provider_profiles.py`, and consider replacing the static table's context-window field with a live `GET /v1/models/{id}` lookup (cached) so `resolve_context_window()` never silently downgrades a 1M-window model to the 128K fallback just because the catalog is stale on release day.
4. **Call the real token-counting endpoint at the one highest-value site.** Replace the 4-chars/token heuristic in the proactive-compaction threshold check specifically (not necessarily everywhere) with `POST /v1/messages/count_tokens` (or the equivalent for whichever provider is active) — this is the single check that decides whether compaction fires at all, so it's the one place estimate error directly translates into either premature summarization (wasted LLM calls, lost detail) or missed overflow (turn failure).
5. **Add a cheap clearing tier before full LLM-summarization compaction.** A `clear_tool_uses`-style pass — trigger at N tokens, keep the last K tool results in full, replace older ones with a placeholder — would cut LLM-summarization calls (and their cost/latency) for the common case where old *tool output* (not conversation) is what's bloating the window. `prune_tool_result`'s static 2,000-char write-time cap doesn't adapt to how full the window actually is and isn't a substitute.
6. **Reconcile the three independent compaction implementations.** `compaction_service.py` (real LLM summarization, `sage_chat` fallback only), `conversation_compaction.py` + `memory_summary_service.py` (non-LLM char-truncated pseudo-summary, `direct_chat` web/API surface), and the ad-hoc fixed-turn-count caps in `sage_agent_runtime_service.py` are three different answers to "what happens to old context" depending on which surface a user is on — undocumented as a deliberate split, and not obviously intentional. At minimum, document which surface gets which behavior and why; ideally, give `direct_chat` genuine LLM summarization too rather than a lossy truncated transcript.
7. **Prompt caching — lowest priority, unchanged from both prior audits.** Only worth pursuing once system-prompt size and call volume justify the engineering cost. If pursued, the char-budget-capped, mostly-stable `sage_instruction_compiler_service.py` prefix (kernel + policy + capabilities, filled in a fixed order before the volatile tail) is structurally the right shape for a cache breakpoint — but Empyralis's provider-agnostic completion pipeline (`openai_chat_text` / multi-provider fallback in `orion_local_worker_llm.py`) would need per-provider `cache_control` wiring, since caching is not a generic, provider-neutral primitive.

---

## Source index (fetched live this pass)

- [Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (cited via `docs/design/backbone-anthropic.md`, cross-checked)
- Token counting: `shared/token-counting.md` reference inside the `claude-api` skill (cached, cross-checked against platform docs' documented endpoint shape — `POST /v1/messages/count_tokens`)

## Empyralis files read for this audit (file:line citations inline above)

- `server_modules/compaction_service.py` (full file, 307 lines)
- `server_modules/sage_agent_runtime_service.py` (lines 2755-3195, 3540-3920, 4340-5240 — action loop, `handle_sage_chat`, B2 preflight/retry)
- `server_modules/direct_chat_generation_service.py` (lines 250-340, 950-1560, 2060-2200 — continuous-work budget gate, main iteration loop, tool-error handling)
- `scripts/orion_local_worker_llm.py` (lines 3980-4120 — `generate_chat_reply_stream_with_provider_fallback`, provider retry policy)
- `server_modules/sage_instruction_compiler_service.py` (lines 40-100, 446-787 — system-prompt assembly and budget)
- `server_modules/direct_chat_entry_service.py` (lines 330-480 — `direct_chat` surface request prep and compaction call site)
- `server_modules/conversation_compaction.py` (full file, 113 lines)
- `server_modules/memory_summary_service.py` (lines 1-90 — non-LLM summarizer)
- `server_modules/conversation_memory_policy.py` (lines 40-260 — memory-policy profiles and model-aware scaling)
- `server_modules/direct_chat_tool_catalog_service.py` (lines 140-204 — two-tier tool assembly)
- `server_modules/tool_registry_service.py` (spot-checked: `search_tool_registry` signature/defaults)
- `server_modules/provider_profiles.py` (lines 200-1010 — Anthropic model catalog, context windows)
- `server_modules/usage_accounting_service.py`, `server_modules/pricing_registry_service.py` (spot-checked: passive cache-token accounting)
- Repo-wide grep for `cache_control`, `is_context_overflow_error`, `count_tokens`, `thinking=`/`adaptive`/`budget_tokens` (zero/near-zero hits, cited above)
