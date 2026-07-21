# Gap Analysis — AI/Agent Turn-by-Turn Operation: OpenClaw vs Empyralis

Read-only fact audit, no code changed. Every claim is file:line-cited against
`/Users/mansur/openclaw` (checked-out source tree, real TypeScript under
`src/`, `extensions/`, `packages/` — `package.json` version 2026.6.11) and
`/Users/mansur/empyralis` on branch `fix/hardware-detail-width` (2026-07-21).
Prior art folded in: `docs/OpenClaw.md`'s "Agentic Loop" sketch (now
superseded where it conflicts — see §1 headline finding) and
`docs/design/gap-byo-subscription.md`.

Scope: how a single agent TURN actually runs — the tool-calling loop, tool
execution, memory, inbound context assembly, streaming/interruption,
autonomy/scheduling, and multi-agent coordination. Not provider/billing
plumbing (see `gap-byo-subscription.md`) or channel onboarding (see
`docs/OpenClaw.md`'s iMessage/Signal sections).

**Architecture headline correction**: OpenClaw's tool-calling loop does NOT
run inside the external `@earendil-works/pi-ai` package as `docs/OpenClaw.md`
states — that dependency is gone from the current checkout's `package.json`.
The real loop is a first-party workspace package, `packages/agent-core`,
publicly exposed as `openclaw/plugin-sdk/agent-core`, consumed internally via
`src/agents/runtime/index.ts`.

---

## Headline gap list (ranked by leverage, build size tagged)

| # | Gap | Tier | Build | Empyralis cite | OpenClaw cite |
|---|-----|------|-------|-----------------|-----------------|
| 1 | **Tool loop caps out at ONE round of tool calls, unconditionally, for every provider** — despite a 5-iteration budget | Correctness | **Small–Medium** | `direct_chat_generation_service.py:1183-1193` (unconditional strip), `:978,1502,1681` (flag never reset) | `packages/agent-core/src/agent-loop.ts:262-436` (no turn cap, runs until natural stop) |
| 2 | **`memory_write` has zero secret/credential redaction before persisting** | Correctness/Security | **Small** | `agent_memory_tools.py:149-209` | `packages/memory-host-sdk/src/host/session-files.ts:867` (`redactSensitiveText`) |
| 3 | **No mid-turn steering or hard-interrupt** — a new inbound message during an active turn is fully serialized behind a lock, not injected/steered/aborted | Correctness/UX | **Medium–Large** | `sage_reply_dispatcher.py:40-76` (`_CHANNEL_TURN_LOCKS`) | `packages/agent-core/src/agent.ts` `steer()`/`steeringQueue`; `src/auto-reply/reply/queue-policy.ts:8-29` (`QueueMode`) |
| 4 | **No parallel tool-call execution** — every tool call in a turn runs sequentially even when the LLM requests several in one response | Capability | **Medium** | `direct_chat_generation_service.py:1444-1670` (`for tool_index, tool_call in enumerate(...)`) | `packages/agent-core/src/agent-loop.ts:548-673` (`Promise.all`, parallel by default) |
| 5 | **Channel dispatch is fully buffered — zero progressive delivery** to Telegram/Discord/Slack/personal-bridge channels | Capability/UX | **Medium** | `sage_reply_dispatcher.py:191-221` (waits for the whole turn, then sends) | `src/auto-reply/reply/block-streaming.ts:11-13` (800–1200 char block chunks, ~1s coalesce) |
| 6 | **No genuine ephemeral multi-agent delegation** — only fire-and-forget inbox messaging between persistent fleet agents | Capability | **Large** | `fleet_tools.py:1564-1631` (`fleet_message_agent`, inbox, no wait/reply) | `src/agents/subagent-spawn.ts` (`sessions_spawn`), `src/agents/tools/sessions-send-tool.ts:242-341` (`sessions_send`, can block for reply) |
| 7 | **No reasoning-visibility option** — reasoning/thinking content is unconditionally stripped from every reply | Capability | **Small–Medium** | `direct_chat_generation_service.py:278,2012` (`_strip_reasoning_thinking_blocks`) | `src/agents/embedded-agent-subscribe.ts:162-189` (`ReasoningLevel: off\|on\|stream`) |
| 8 | **Notebook memory search (`MEMORY.md`/`memory/*.md`) has no real index** — line-level substring/token scoring, no embeddings | Capability | **Medium** | `agent_memory.py:641-704` (`_search_memory_notebook`) | `extensions/memory-core/src/memory/hybrid.ts` (SQLite + `sqlite-vec` + FTS/BM25) |
| 9 | **No memory consolidation ("dreaming")** — no recall-frequency-gated promotion of short-term notes into durable memory | Capability | **Large** | not found in `agent_memory.py`/`agent_memory_tools.py` | `extensions/memory-core/src/dreaming.ts` |
| 10 | **Inbound envelope is a thin wrapper, not a structured Facts pipeline** — and provenance framing is inconsistent across channels | Capability | **Medium** | `sage_agent_runtime_service.py:1578-1591` (`_build_prompt_envelope`); rich version only in `personal_channel_sage_bridge_service.py:150-197`, absent from `sage_telegram_hosted_service.py` | `src/channels/turn/types.ts:59-232` (typed Facts) → `src/channels/inbound-event/context.ts:459-546` |
| 11 | **No prompt-cache boundary** — system prompt is rebuilt as flat string concatenation every turn, no stable/dynamic split | Cost | **Medium** | no `cache_control`/boundary marker found in `sage_agent_runtime_service.py` system-prompt assembly (line ranges below) | `src/agents/system-prompt.ts:1268` (`SYSTEM_PROMPT_CACHE_BOUNDARY`) |

**Confirmed "don't bother — OpenClaw doesn't have it either" datapoints**
(from the OpenClaw source dig, cross-checked against Empyralis — both
absent, so building these would put Empyralis AHEAD, not at parity):
- **Per-message memory attribution** (who said it / which channel) — not in
  OpenClaw's `memory_index_chunks` schema (`packages/memory-host-sdk/src/host/memory-schema.ts:308-319`,
  no sender/speaker/channel column) and not in Empyralis's `memory_entries`
  table (`agent_memory.py:266-275`, key/content/timestamps only).
- **No autonomous-action approval gate distinct from a CLI-harness relay** —
  OpenClaw's `approvals.exec`/`approvals.plugin` only forward Claude
  Code's/Codex's own built-in approval prompts into a chat channel; there is
  no OpenClaw-native "does this autonomous cron/subagent action need
  approval" gate (confirmed by exhaustive grep, `src/gateway/server-methods/tools-invoke.ts:19-21`).
  **Empyralis is actually AHEAD here** — see §6.
- **Cost/budget-based loop termination** — OpenClaw defines the hook
  (`AgentLoopConfig.shouldStopAfterTurn`, `packages/agent-core/src/types.ts:218`)
  but never wires it (zero non-test call sites). Empyralis has no equivalent
  hook either. Shared non-issue; not worth building to "catch up."

**Where Empyralis is already at parity or ahead** (see §3, §6 for detail):
compaction design is near-identical (same `16384`-token reserve constant,
independently arrived at or directly modeled — `compaction_service.py:1-11`
says so itself); the slash-command catalog has comparable breadth; tool-call
error handling (JSON error surfaced to the LLM, never framework-auto-retried)
matches; neither side forces `tool_choice`; and Empyralis's bounded
scheduler has a **real, native privileged-wakeup approval gate that OpenClaw
lacks entirely** — a genuine strength, not just parity.

---

## 1. The agent loop

### OpenClaw
Real loop: `packages/agent-core/src/agent-loop.ts:262-436` (`runLoop`).
Structure: outer `while(true)` (follow-ups) wrapping inner
`while(hasMoreToolCalls || pendingMessages.length > 0)`. Each inner pass:
`streamAssistantResponse()` (line 439) → LLM call → filter `toolCall`
blocks → if `stopReason === "toolUse"`, execute tools → append
`ToolResultMessage`s → loop back with results in context.

**No hardcoded max-iteration cap** — grepped exhaustively for
`turnCount`/`maxTurns`/`turnLimit`, zero hits under `src/agents/embedded-agent-runner`
or `src/auto-reply`. Runs until the LLM naturally stops calling tools.

Stop conditions (`StopReason`, `packages/llm-core/src/types.ts:277`):
`"stop"` (natural) exits the loop; `"length"` triggers
`removeNonExecutableToolCalls()` (`agent-loop.ts:82-88`, strips partial tool
calls, treated as final text); `"error"`/`"aborted"` returns immediately
(`agent-loop.ts:314-318`); an explicit per-tool-result `terminate: true` flag
(`shouldTerminateToolBatch()`, lines 784-789) forces the loop to end early
even mid-tool-batch — no dedicated named "done" tool, `terminate` is generic.

Cost/budget is confirmed **not** a stop condition — `shouldStopAfterTurn`
hook exists (`packages/agent-core/src/types.ts:218`) but is never wired
(zero non-test call sites).

`tool_choice` is never forced anywhere in the conversational loop — the only
literal `toolChoice: "required"` is a one-off model-capability probe
(`src/agents/model-scan.ts:317`), unrelated to normal turns.

A **separate outer retry wrapper** (`src/agents/embedded-agent-runner/run.ts:1934`,
`while(true)`) exists around the *entire* attempt for transport failures
(rate limits, auth refresh, context-overflow-triggered
compact-then-retry) — this DOES have a real cap:
`MAX_RUN_LOOP_ITERATIONS` = `24` base + `8`/profile candidate, clamped to
`[32, 160]` (`run/helpers.ts:103-121`). This is not the tool-calling loop;
conflating the two would be a mistake.

Queue policy for a new inbound message during an active run:
`QueueMode = "steer" | "followup" | "collect" | "interrupt"`, default
`"steer"` (`src/auto-reply/reply/queue/settings.ts:36`); `"interrupt"` hard-aborts
the live run (`embeddedAgentRuntime.abortEmbeddedAgentRun`,
`get-reply-run.ts:1023-1041`) before starting the new turn.

### Empyralis
`_run_sage_action_loop_v3` (`sage_agent_runtime_service.py:2755`) calls
`stream_provider_backed_direct_chat` (`direct_chat_generation_service.py:859`),
whose loop is `for iteration in range(max_iterations)`
(`direct_chat_generation_service.py:1178`), with
`max_iterations = _SAGE_OPERATOR_LOOP_MAX_ITERATIONS = 5`
(`sage_agent_runtime_service.py:146`, comment: *"Cap at 5 to prevent
runaway; most tasks finish in 1-3"*).

**Verified, load-bearing finding**: at the top of every loop iteration,
`direct_chat_generation_service.py:1186-1193` runs:

```python
if executed_any_tools:
    if isinstance(metadata, dict) and metadata.get("tools"):
        metadata = {**metadata, "tools": []}
    if isinstance(context, dict) and context.get("tools"):
        context = {**context, "tools": []}
```

This is **unconditional** — the surrounding comment (lines 1183-1185) claims
it's a DeepSeek-only workaround ("DeepSeek does not receive tool
definitions alongside tool_result messages"), but there is no
`provider_id == "deepseek"` check anywhere in this block (confirmed: the
`provider_id` variable, defined at line 905 for an unrelated 20-tool trim,
is never referenced again in the file after line 935). `executed_any_tools`
is set `True` the first time ANY tool call runs — including
`query_tool_registry`, the lazy tool-discovery meta-tool itself (line 1502)
— and is **never reset to `False`** anywhere in the function (confirmed by
exhaustive grep of all 12 occurrences).

Net effect: only **iteration 0** of the loop can ever include tool
definitions in the LLM call. Every iteration after the first tool
execution — up to 4 more, per the 5-iteration budget — runs with `tools: []`
and can only synthesize a final text answer. This means:
- A genuine multi-step chain (search → read a specific result → act on it)
  is impossible if it requires seeing a tool's output before deciding the
  next tool to call — the model gets exactly one shot to emit all tool
  calls it will ever get, upfront, blind to any of their results.
- The lazy-discovery flow is self-defeating: if the model's first move is
  `query_tool_registry` to find the right tool, the matched tools ARE
  appended to the live `tools` list that same iteration
  (`direct_chat_generation_service.py:1488-1501`) — but they are stripped
  again before the very next LLM call, so a tool discovered via
  `query_tool_registry` can never actually be invoked in the same turn.
- The 5-iteration budget is effectively decorative for tool use — after
  iteration 0's tool batch, iterations 1-4 exist only to let the model
  retry/rephrase a text answer, not to take further action.

Sequential-batch tool calls (multiple `tool_calls` in ONE LLM response) DO
still work and execute one after another
(`direct_chat_generation_service.py:1444-1670`, `for tool_index, tool_call
in enumerate(iteration_tool_calls, start=1)`) — so "1-3 iterations" in the
budget comment is not entirely wrong for turns where the model correctly
front-loads every tool call it needs in its first response; it is wrong for
any turn that needs to react to an intermediate result.

Repeat-loop guard (a narrower, different mechanism than OpenClaw's
`terminate` flag): `record_direct_tool_signature()`
(`direct_tool_loop_guard_service.py:29-44`) aborts the turn if the same
`(tool_name, arguments)` signature repeats **3** times
(`DIRECT_CHAT_LOOP_REPEAT_LIMIT = 3`, `direct_chat_runtime_exports.py:292`)
— catches within-iteration repetition, not cross-iteration looping (which
the strip-tools bug prevents from happening anyway, incidentally).

No `tool_choice` forcing anywhere — matches OpenClaw (parity).

Turn concurrency: a new inbound message for the same
`(workspace_id, thread_id)` while a turn is active **waits** on a
per-thread `asyncio.Lock` (`sage_reply_dispatcher.py:40-76`,
`_CHANNEL_TURN_LOCKS`) rather than being steered, interrupted, or dropped —
see §5 for the UX consequence.

A `/stop` command exists (`command_registry.py:449,529-541`) but aborts
"active runs" (`services.active_run_count`) — a **separate durable-run
execution engine** (`runs_execution.py`), not the direct-chat loop above.
There is no equivalent of OpenClaw's `QueueMode:"interrupt"` for the
conversational loop itself.

### GAP
**#1 (top priority, correctness).** The single-round tool-calling limitation
is a real bug, not a design difference — the code's own comment ("most
tasks finish in 1-3") describes behavior the code doesn't deliver, and the
DeepSeek-specific rationale in the adjacent comment doesn't match the
unconditional implementation. **Small–Medium build**: scope the strip to the
actual DeepSeek case (or better, stop stripping and instead avoid sending
`tool_result` messages without a matching `tools` array only for providers
that need it), then verify multi-round chains work end-to-end across the
~15 configured providers. This single fix likely also fixes item on the
list gap #… — the lazy-discovery tool becomes usable again as a side
effect.

**#3 (correctness/UX).** No steer/interrupt — Medium–Large build requiring
a live-attempt registry (something to steer/abort into), not just a lock.

---

## 2. Tool use

### OpenClaw
Schema: TypeBox (`TSchema`/`Static<T>`), JSON-Schema-compatible;
`AgentTool<TParameters,TDetails>` adds `execute()`,
`executionMode?: "sequential"|"parallel"` (`packages/agent-core/src/types.ts:459-485`).
Validation via TypeBox `Compile`/`Value`, cached, 64KB max
string-to-JSON coercion (`packages/llm-core/src/validation.ts:7-11`).

**Parallel by default**: `Agent` class default
`this.toolExecution = options.toolExecution ?? "parallel"`
(`packages/agent-core/src/agent.ts:266`), no override found in production
wiring. Mechanism (`agent-loop.ts:548-594`): preflights all tool calls to
check if ANY resolved tool declares `executionMode:"sequential"` — if so the
**whole batch** falls back to sequential (`executeToolCallsSequential`,
line 608); otherwise all run concurrently via `Promise.all`
(`executeToolCallsParallel`, line 673). `tool_execution_end` events fire in
completion order; `ToolResultMessage`s are still pushed to context in
original assistant-message order. Real forcing example: MCP servers that
don't advertise `supportsParallelToolCalls` get `executionMode:"sequential"`
(`src/agents/agent-bundle-mcp-materialize.ts:302-304`).

Errors: caught in `executePreparedToolCall()`
(`agent-loop.ts:929-965`/1019-1024), surfaced to the LLM as tool-result
content with `isError:true` — never framework-auto-retried, never aborts
the run.

Timeout: no universal loop-level timeout; individual tools opt in
(`buildTimeoutAbortSignal()`, `src/utils/fetch-timeout.ts:105`). `exec`'s
default is `1800s`/30 min (`bash-tools.exec.ts:1314-1316`), plus a
`10,000ms` background-yield threshold after which a long command detaches
into a pollable background process instead of blocking.

A dedicated **tool-call-repair module** (`packages/tool-call-repair/src/*`)
parses plain-text/bracket/XML-ish tool syntax weaker models emit and
promotes it into a real `ToolCall`, capped at 256,000 bytes/payload.

`resolveDeferredTool` (`agent-loop.ts:813-828`) lazily hydrates a
policy-authorized-but-not-yet-advertised tool at call time, for large
catalogs where only a subset is shown to the model upfront (system-prompt
section `"### Deferred Tool Schemas"`, `src/agents/system-prompt.ts:1054-1055`).

### Empyralis
Schema: OpenAI function-calling shape,
`{type:"function", function:{name, description, parameters}}`, normalized
in `direct_chat_generation_service.py` (`_normalize_tool_format`).

**Sequential only, no exceptions.** `direct_chat_generation_service.py:1444`,
`for tool_index, tool_call in enumerate(iteration_tool_calls, start=1)` —
every call in a batch executes one after another; the
`ThreadPoolExecutor(max_workers=1)` wrapping each call
(lines 1656-1670) exists purely to enforce a per-call timeout via
`.result(timeout=...)`, not for concurrency (`max_workers=1`). No
`asyncio.gather`/concurrent path exists anywhere in this call chain
(confirmed by grep across `server_modules/*.py` for `asyncio.gather` —
all hits are in unrelated analytics/gateway code, none in the tool-execution
path).

Per-category timeouts (`skills_service.py:3403-3420`, `_tool_timeout_seconds`):
shell=**120s**, hardware=**120s**, browser/computer=**60s**, web=**30s**,
memory=**10s**, default=**30s**. On timeout, a structured
`{"error":"timeout","message":...}` result is returned
(`_timeout_tool_result`, `skills_service.py:3423-3433`) so the model can
adapt — same philosophy as OpenClaw, much smaller ceiling than OpenClaw's
30-min `exec` default (different product shape: Empyralis's shell tool
targets a paired gateway machine per-turn, not a long-running agentic coding
session).

Errors: JSON error payload becomes the `tool_result` content
(`direct_chat_generation_service.py:1739-1748`, `_looks_like_tool_error`
detection feeds the tool-honesty guard) — same "surface to LLM, don't
auto-retry" design as OpenClaw. **Parity.**

Two-tier catalog: 8 always-on tools
(`direct_chat_tool_catalog_service.build_always_on_direct_chat_tools`,
`tool_registry_service.py`) + everything else in a lazy registry, pulled in
via the `query_tool_registry` meta-tool
(`direct_chat_generation_service.py:1473-1501`). Discovery is **plain
keyword matching** (`tool_registry_service.search_tool_registry`, per its
own docstring at `direct_chat_tool_catalog_service.py:175`: *"Search the
tool registry by keyword matching"*) — not semantic. (Note: the OpenClaw
report does not claim `resolveDeferredTool` itself is semantic either — this
is a description of Empyralis's mechanism, not a head-to-head claim.) As
established in §1, tools matched via `query_tool_registry` are added then
immediately stripped before they can be called — this bug directly
neuters the two-tier design's entire value proposition.

No general tool-call-repair module — only a narrow, shell-specific fallback,
`_extract_assistant_shell_plan_tool_call`
(`direct_chat_generation_service.py:1248-1253`, backed by
`_looks_like_assistant_shell_line`/`_normalize_assistant_shell_line`,
lines 322-339), which extracts a shell command from prose text but doesn't
generalize to arbitrary tools.

### GAP
**#4 (capability).** No parallel execution — Medium build: replace the
sequential for-loop with a bounded-concurrency gather, preserve
tool-result-to-context ordering (OpenClaw's own approach — completion-order
events, source-order context messages — is a reasonable template), decide
what to do about the (currently absent) "sequential-only tool" declaration
OpenClaw uses for non-parallel-capable MCP servers.

Everything else in this axis is either **parity** (error surfacing, no
forced `tool_choice`, per-tool timeouts as a concept) or a **direct
consequence of the §1 bug**, not an independent gap.

---

## 3. Memory

### OpenClaw
**Short-term**: per-session JSONL transcript
(`appendJsonlEntrySync`, `src/config/sessions/transcript-jsonl.ts:44-59`),
tree-structured (`session-manager.ts`, supports forking for
`sessions_spawn context:"fork"`). History limiting
(`limitHistoryTurns`, `src/agents/embedded-agent-runner/history.ts:24-61`)
keeps last N user turns per `channels.<provider>.dmHistoryLimit`/
`historyLimit`; **unbounded by turn count if unconfigured**, gated only by
compaction.

**Compaction is real LLM summarization**, not truncation
(`packages/agent-core/src/harness/compaction/compaction.ts`).
`DEFAULT_COMPACTION_SETTINGS`: `reserveTokens: 16384`,
`keepRecentTokens: 20000` (line 142-146). Trigger:
`contextTokens > contextWindow - reserveTokens` (~91.8% full at a 200K
window). Structured summarization prompt requires exact sections (`## Goal
/ ## Constraints & Preferences / ## Progress / ## Key Decisions / ## Next
Steps / ## Critical Context`); iterative merge-updates instead of
regeneration; explicit UUID/hash/URL preservation instructions.

**Long-term**: `memory_search`/`memory_get`
(`extensions/memory-core/src/tools.ts`) — **genuine hybrid semantic search**:
SQLite + `sqlite-vec` vector similarity + SQLite FTS/BM25 keyword, weighted
merge (`vectorWeight=0.7`/`textWeight=0.3`), optional MMR diversity +
temporal decay (both off by default), chunking 400 tokens/80 overlap,
default embedding provider `openai`
(`src/agents/memory-search.ts:116-136`). Indexes `MEMORY.md` +
`memory/**/*.md` by default; session transcripts NOT indexed unless
`agents.defaults.experimental.sessionMemory=true`. Index schema
(`packages/memory-host-sdk/src/host/memory-schema.ts:308-319`,
`memory_index_chunks`): `id, path, source, start_line, end_line, hash,
model, text, embedding, updated_at` — **no sender/speaker/channel column**.

Both push and pull exist independently: `memory_search`/`memory_get` are
pure on-demand tool calls; separately, `extensions/active-memory/index.ts`
hooks `before_prompt_build` on every turn, builds a query from the last 3
turns, and **auto-prepends** a recalled snippet directly into the prompt.

Consolidation ("dreaming"): `extensions/memory-core/src/dreaming.ts`,
cron-scheduled, promotes short-term notes into durable memory only after a
recall-frequency gate (`DEFAULT_PROMOTION_MIN_SCORE=0.75`,
`MIN_RECALL_COUNT=3`, `MIN_UNIQUE_QUERIES=2`,
`short-term-promotion.ts:47-49`) — spaced-repetition-like.

**Per-message attribution: confirmed absent.** No sender/channel columns
anywhere in the memory index schema or `extensions/memory-core`/
`extensions/active-memory`.

**Save-filter**: secret/credential/payment redaction only
(`redactSensitiveText(text,{mode:"tools"})`,
`packages/memory-host-sdk/src/host/session-files.ts:867`, engine
`src/logging/redact.ts:984+` — targets API keys/tokens/passwords/JWTs/card
numbers/CVCs) applied before session content enters the memory/dreaming
corpus. No general PII (name/email) filter.

### Empyralis
**Short-term**: `compacted_prior_messages` passed into the loop
(`sage_agent_runtime_service.py:3111`). Real compaction exists —
`compaction_service.py`, explicitly modeled on OpenClaw ("Matches OpenClaw's
compaction.ts design" — the module's own docstring, `compaction_service.py:1-11`).
`COMPACTION_RESERVE_TOKENS = 16384` — **identical constant to OpenClaw's
`reserveTokens: 16384`** (`compaction_service.py:39`). `keep_recent_tokens_for_window`
uses ~15% of the context window, capped at `30000` (`_KEEP_RECENT_CAP`,
line 42) vs OpenClaw's flat `20000`. Trigger: `should_compact()`
(line 71-86) — same "total > window - reserve" logic. Wired in for real:
auto-triggered every turn (`sage_agent_runtime_service.py:5482-5510`, calls
`should_compact`/`compact_turns`) plus an explicit `/compact` command
(`sage_command_dispatcher.py:322-364`). **Genuine, verified parity** — this
is the strongest "we already match OpenClaw" finding in the whole audit.

**Long-term**: `agent_memory.py` — two systems. (a) A SQLite key-value store
(`memory_entries`: key/content/created_at/updated_at,
`agent_memory.py:266-275`), searched via `_semantic_search()`
(line 403-431): brute-force cosine similarity over **every** entry, using a
local `sentence-transformers` model (`all-MiniLM-L6-v2`,
line 287-298) if it loads, else falling back to SQL `LIKE` substring search
(line 377-400). No persisted vector index — recomputed at query time. (b) A
Markdown notebook (`MEMORY.md` + `memory/*.md`) via
`memory_write`/`memory_read` tools (`agent_memory_tools.py`), searched via
`_search_memory_notebook()` (`agent_memory.py:641-704`) — **plain
line-level substring/token scoring**, no embeddings at all for this half of
the system, despite indexing the exact same file set OpenClaw's hybrid
index covers.

Both push and pull exist here too: `memory_search`/`memory_get` are always-on
tier-1 tools (`tool_registry_service.py`, on-demand); separately, a
specialist's own `MEMORY.md` brief is **auto-injected** into its system
prompt every turn (`sage_agent_runtime_service.py:4376-4379`,
`_spec_memory_block = f"\n\n## Your memory\n{memory_context}"`) — a push
mechanism functionally parallel to OpenClaw's `active-memory` extension,
independently arrived at.

No consolidation/"dreaming" equivalent found — no recall-frequency-gated
promotion process anywhere in `agent_memory.py`/`agent_memory_tools.py`.

**Per-message attribution: confirmed absent** — same as OpenClaw. The
`memory_entries` schema (`agent_memory.py:266-275`) has no sender/channel
column; nothing in `agent_memory_tools.py` records who said what.

**Save-filter: confirmed absent, and this is a real gap, not parity.**
`memory_write()` (`agent_memory_tools.py:149-209`) does a raw,
path-sandboxed file write — `safe_path.write_text(...)` — with **zero**
content filtering. `secret_redaction_service.redact_text` exists and is
used elsewhere (e.g. `_build_prompt_envelope`,
`sage_agent_runtime_service.py:1585-1586`, redacts the prompt going TO the
model) but is never called on content the model WRITES to memory. Unlike
OpenClaw, which redacts secrets/credentials before they enter the memory
corpus, an Empyralis agent can currently persist a live API key, password,
or card number verbatim into its own long-term memory file if a tool result
or user message contained one and the model chose to save it.

### GAP
**#2 (top priority, correctness/security).** No save-filter on
`memory_write` — **Small build**: call
`secret_redaction_service.redact_text(content)` before the write in
`agent_memory_tools.py:149-209` (and the SQLite path,
`agent_memory.py:334-363`, `_save_memory`), mirroring OpenClaw's
`redactSensitiveText(mode:"tools")` gate.

**#8 (capability).** Notebook search has no real index — Medium build: add
a persisted embedding table for `MEMORY.md`/`memory/*.md` chunks (the
structured-facts table already has brute-force cosine; the notebook half
has nothing). Consider whether OpenClaw's chunking (400 tok/80 overlap) and
hybrid BM25+vector merge are worth porting wholesale versus a lighter
single-signal upgrade.

**#9 (capability, lower urgency).** No dreaming/consolidation — Large
build, genuinely optional; OpenClaw's version is itself a fairly elaborate
subsystem (cron-scheduled, recall-tracked) and the marginal value without
per-message attribution already in place is unclear.

**Don't build**: per-message attribution absent on both sides — building it
in Empyralis would be a genuine capability lead over OpenClaw, not table
stakes. Worth keeping on the roadmap for exactly that reason (matches the
standing founder priority in `project_inbound_envelope_and_memory_attribution.md`),
but it is not "catching up."

---

## 4. Context / envelope

### OpenClaw
Real structured pipeline, not a single "ctxPayload" type as
`docs/OpenClaw.md` implies — actually a chain of typed Facts objects
(`SenderFacts`, `ConversationFacts`, `RouteFacts`, `ReplyPlanFacts`,
`MessageFacts`, `AccessFacts` — `src/channels/turn/types.ts:59-232`) →
`buildChannelInboundEventContext()`
(`src/channels/inbound-event/context.ts:459-546`) →
`BuiltChannelInboundEventContext` → `MsgContext` → `FinalizedMsgContext` →
`TemplateContext` (`src/auto-reply/templating.ts:80,345,358`). Fields
include `ChatType` (`"direct"|"group"|"channel"`), `Provider`, `Surface`,
`SenderId/Name/Username/Tag/IsBot`, `WasMentioned`, `InboundEventKind`
(`"user_request"|"room_event"`), `CommandAuthorized: boolean` — required,
non-optional on the finalized type.

Owner-vs-external is deliberately **not** a field on the envelope itself:
`resolveCommandAuthorization()` (`src/auto-reply/command-auth.ts:605-704`)
computes `CommandAuthorization.senderIsOwner` separately, used for
**tool/command gating**, not asserted to the model as an explicit "this is
the owner" prompt claim. The system prompt's own "Authorized Senders"
section explicitly hedges: *"These senders are allowlisted; do not assume
they are the owner"* (`src/agents/system-prompt.ts:372-386`) — a deliberate
anti-spoofing design choice, not an oversight.

System-prompt assembly has an exact, ordered, two-part structure
(`buildAgentSystemPrompt`, `src/agents/system-prompt.ts:682-1361`) split at
a **prompt-cache boundary**: stable/cacheable prefix (tools, safety,
skills, memory, workspace, sandbox, date/time, bootstrap files, stable
project context) then `SYSTEM_PROMPT_CACHE_BOUNDARY` (line 1268), then a
dynamic per-turn suffix (dynamic project context, exec-approval guidance,
Authorized Senders, group/subagent context, messaging, heartbeat, the
model/channel/reasoning runtime line).

Channel context is deliberately **split into trusted vs untrusted halves**:
`buildInboundMetaSystemPrompt()` (`src/auto-reply/reply/inbound-meta.ts:504-545`)
emits a versioned structured JSON block (`schema:"openclaw.inbound_meta.v2"`,
`account_id/channel/provider/surface/chat_type/response_format`) into the
**system** prompt; `buildInboundUserContextPrefix()` (lines 548-747 —
sender label/id/name, reply chain, forwarded-from, chat history) is
deliberately kept **out of the system prompt** and prefixed onto the **user
message** instead. Explicit code comment: *"Keep system metadata strictly
free of attacker-controlled strings... those belong in user-role untrusted
context blocks"* (`inbound-meta.ts:511-515`) — a real, load-bearing
prompt-injection defense boundary.

### Empyralis
The literal "envelope" object is thin: `_build_prompt_envelope()`
(`sage_agent_runtime_service.py:1578-1591`) returns
`{system_prompt, user_message, context:{workspace_id, source}}` — mainly a
vehicle for `secret_redaction_service.redact_text()`, not a structured
Facts pipeline. This directly confirms the standing gap already flagged by
the founder in `project_inbound_envelope_and_memory_attribution.md`
(2026-07-19) — re-verified fresh against current code, still true.

Sender identity resolves to a coarse three-way class,
`triage_service.resolve_sender_identity()`
(`triage_service.py:83-137`) → `"owner"|"audience"|"unknown"`, used for (a)
tool filtering (`audience_tool_filter.filter_tools_for_audience`,
`audience_tool_filter.py:64-94`) and (b) an authority tier gate
(`authority_mandate_service.py:1-130`, owner/audience/system tiers,
non-owner tiers can only call `audience_safe`-manifested tools). Unlike
OpenClaw's careful avoidance of a positive "you are the owner" prompt
assertion, Empyralis DOES inject an explicit prompt claim, but only the
negative case: `audience_behavior_instructions()`
(`audience_tool_filter.py:133-149`) tells the model plainly *"You are
serving a customer or audience member — NOT the workspace owner."* This is
a different (and arguably lower-risk) framing than OpenClaw is guarding
against — it never asserts trusted-owner status, only ever the restricted
case — so it isn't the same spoofing exposure, but it is a genuinely
different design, not a like-for-like match.

Group/channel provenance exists but is **channel-inconsistent**:
`personal_channel_sage_bridge_service.py:150-197`
(`_owner_provenance_message`) builds a real prose header — `"From: {name}
(owner) · {channel} · message posted in the "{group}" group chat, visible
to other participants who are NOT the workspace owner"` — for the
WhatsApp/Signal/iMessage personal-bridge path, with a parallel
non-owner-branch header
(`_personal_channel_guard_metadata`, lines 237-263, `Chat-Type`/`Group-Name`
metadata fed into `external_content_guard.wrap_external_content`). This is
prose baked into message content, not a structured system-prompt JSON
block like OpenClaw's `inbound_meta.v2`. Verified this treatment is **not
universal**: `sage_telegram_hosted_service.py` (the separate hosted-bot
Telegram path) has `chat_type` available (line 751) and uses it for
group-mention gating (lines 869,1378) but no equivalent "From: X · group
chat Y" prose injection was found anywhere in that file — so a
Telegram-hosted group turn and a WhatsApp-bridge group turn get
meaningfully different provenance treatment today.

No prompt-cache boundary marker or stable/dynamic split was found in the
system-prompt assembly paths traced (`sage_agent_runtime_service.py:4380,4390`,
plain f-string concatenation of persona/rules/memory/audience blocks) — not
exhaustively verified across every provider dispatch path, so this is
reported as "not found in the traced call path" rather than a definitive
platform-wide absence.

### GAP
**#10 (capability).** No structured Facts-style envelope, and provenance
treatment differs by channel family — Medium build: define one canonical
"who/where/how" struct (Platform, Surface, Sender-vs-owner, Group name)
built once per inbound event regardless of channel, matching the founder's
own OpenClaw-reference ask; route ALL channels (not just the
WhatsApp/Signal/iMessage bridge family) through it, including
`sage_telegram_hosted_service.py`. Worth deciding, following OpenClaw's own
precedent, whether sender-controlled fields (display name, group name)
belong in the user-message prefix rather than the system prompt, for the
same prompt-injection reason OpenClaw states explicitly.

**#11 (cost, lower priority).** No prompt-cache boundary — Medium build,
real but secondary; only matters materially once per-turn system-prompt
size and call volume justify the engineering cost of a stable/dynamic
split plus provider `cache_control` wiring (e.g. Anthropic).

---

## 5. Streaming / interruption / thinking

### OpenClaw
Internal LLM streaming is genuinely per-token
(`AssistantMessageEvent.text_delta`/`thinking_delta`,
`packages/llm-core/src/types.ts:369-390`).

Channel dispatch is **block-chunked**, not raw token-by-token: text
accumulates into an `EmbeddedBlockChunker`, drained at `text_end`
boundaries plus a forced flush before tool execution
(`src/agents/embedded-agent-subscribe.handlers.messages.ts:930-954`).
Chunk sizing (`src/auto-reply/reply/block-streaming.ts:11-13`):
`DEFAULT_BLOCK_STREAM_MIN=800`/`MAX=1200` chars, `1000ms` coalesce-idle,
paragraph/sentence break preference — so a Telegram/Discord/etc. user sees
progressive partial output during a long turn, not silence-then-everything.

Typing indicator: two-layer keepalive on a shared primitive
(`createTypingKeepaliveLoop()`, `src/channels/typing-lifecycle.ts:12-59`).
Channel-plugin layer: 3s interval, 60s safety TTL
(`src/channels/typing.ts:41-120`). Reply-run layer: 6s interval, 120s TTL
(`src/auto-reply/reply/typing.ts:11-12`), stopping only when both
"model done" and "delivery queue empty" fire. Start timing is mode-driven
(`TypingMode: "instant"|"message"|"thinking"|"never"`) — groups default to
`"message"` (wait for real text), DMs/mentions default to `"instant"`.

Mid-turn steering is real and traced end-to-end:
`queueEmbeddedAgentMessageWithOutcomeAsync()`
(`src/agents/embedded-agent-runner/runs.ts:306-360`) →
`steerActiveSessionWithOptionalDeliveryWait()` →
`AgentSession.queueSteer()` → `Agent.steer()` → `steeringQueue.enqueue()`,
drained **after the current tool-call batch finishes** (not literal
mid-stream token injection), default `"one-at-a-time"` drain
(`packages/agent-core/src/agent.ts:259-260`). Hard interrupt (full abort)
is the separate `QueueMode:"interrupt"` path (see §1).

Reasoning has **two independent axes**: (1) `ThinkLevel`
(`off|minimal|low|medium|high|xhigh|adaptive|max`,
`src/auto-reply/thinking.shared.ts:13-21`) — the actual reasoning-effort
budget sent to the provider, set via `/think`; (2) `ReasoningLevel`
(`off|on|stream`, line 26) — whether reasoning content is **shown to the
user**, independent of effort, defaulting to **`off`/hidden**
(`src/agents/embedded-agent-subscribe.ts:162-189`), toggled by a *separate*
`/reasoning` command. The system prompt literally states the current
setting to the model: `` `Reasoning: ${reasoningLevel} (hidden unless
on/stream)` `` (`src/agents/system-prompt.ts:1358`).

### Empyralis
Internal streaming is per-token for the **web-chat surface only** —
`direct_chat_generation_service.py`'s `"chunk"`/`delta` events
(lines 1209-1230) are consumed by the SSE web-chat path. **Parity with
OpenClaw's internal streaming granularity, for that one surface.**

Channel dispatch (Telegram/Discord/Slack/personal bridges) is **fully
buffered — zero progressive delivery**: `dispatch_sage_reply()`
(`sage_reply_dispatcher.py:191-221`) is documented as "Execute a full Sage
turn and deliver the reply" — it waits for the entire turn (all loop
iterations, all tool calls) to finish, then sends final text, auto-split at
`transport.max_message_length`. There is no intermediate send during a long
turn — a user watching a Telegram chat sees only the typing indicator until
the whole answer lands. This is a strictly bigger gap than OpenClaw's
block-chunking (which at least ships ~800-1200 char pieces progressively).

Typing: start/stop lifecycle exists per `dispatch_sage_reply`'s own
docstring ("Typing lifecycle (start → AI turn → stop in finally)",
`sage_reply_dispatcher.py:191-221`) — conceptual parity confirmed; exact
interval/TTL constants were not independently re-verified in this pass
(see `docs/OpenClaw.md`'s Signal section for the
`channels/foundation/typing-keepalive.ts` `TypingKeepalive` class already
built and shared across WhatsApp/Telegram/Signal, 3s refresh/60s ceiling —
consistent with OpenClaw's own channel-plugin-layer numbers).

Mid-turn steering: **none**. The per-thread `asyncio.Lock`
(`sage_reply_dispatcher.py:40-76`) means a second inbound message for the
same thread simply **waits** for the lock — it is not steered into the
live turn, not merged, not dropped-with-notice; it silently queues and gets
answered only once the current (possibly now-stale) turn fully completes.
No equivalent of OpenClaw's `Agent.steer()`.

Reasoning: **one axis only**, effort — `/thinking`/`/think`
(`command_registry.py:458-460`, off/minimal/low/medium/high) maps natively
for models detected as reasoning-capable (`o1`/`o3`/`deepseek-r1`/
`deepseek-reasoner`/`gemini`+`thinking`/`claude`/`gpt-5`/`codex` substring
checks, `direct_chat_generation_service.py:961-970`) or degrades to a
system-prompt instruction otherwise. **No visibility axis exists** —
reasoning/thinking content is **unconditionally stripped** from every
reply, always: `_strip_reasoning_thinking_blocks()`
(`direct_chat_generation_service.py:278`, applied at line 2012). The
"Thinking..." UI step users see (`thinking_step_payload`,
`direct_tool_execution_service.py:345-353`) is a **generic, hardcoded
label** ("Planning the response"/"Planning the next step") — never real
model reasoning content, regardless of provider or effort level. There is
no `/reasoning`-equivalent command.

### GAP
**#5 (capability/UX).** Zero progressive channel delivery — Medium build:
port a chunker analogous to OpenClaw's `EmbeddedBlockChunker` (paragraph
break preference, ~800-1200 char pieces) into `sage_reply_dispatcher.py`,
with per-channel-transport incremental-send semantics (message edit vs.
sequential new messages differs by platform — Telegram/Discord support
message editing, WhatsApp/Signal typically don't).

**#7 (capability).** No reasoning-visibility toggle — Small–Medium build:
stop unconditionally stripping reasoning blocks; thread real
`reasoning_content`/thinking deltas through as a distinct field instead of
discarding them, add an on/stream/off toggle analogous to `/reasoning`,
replace the fake "Planning the response" label with real content when
visibility is on.

**#3 (correctness, already counted in §1).** No steering — same underlying
lock architecture as §1; fixing both together is likely more efficient than
sequentially.

---

## 6. Autonomy / scheduling

### OpenClaw
Cron tool (`src/agents/tools/cron-tool.ts`): actions
`status|list|get|add|update|remove|run|runs|wake`; schedule kinds
`"at"` (one-shot ISO), `"every"` (interval ms), `"cron"` (cron expr + IANA
tz + jitter) — **no natural-language time parsing**, the model must emit
ISO/cron syntax. Persisted in **SQLite** (`src/cron/store.ts`, despite the
legacy `jobs.json` path name) with a JSON quarantine sidecar for rejected
rows. Scheduler is a **self-re-arming `setTimeout`**
(`src/cron/service/timer.ts`, `armTimer()`), never sleeping more than 60s,
`MIN_REFIRE_GAP_MS=2000` spin guard, bounded concurrency (default 1).

**Cron re-enters the same agent loop** — confirmed by direct call-chain
trace. `"main"`-target jobs ride the heartbeat/system-event path; detached
jobs go `executeDetachedCronJob → runIsolatedAgentJob →
runCronIsolatedAgentTurn → executeCronRun → createCronPromptExecutor →
runEmbeddedAgent` (`src/agents/embedded-agent-runner/run.ts:605`) — **the
identical function** ordinary chat replies use
(`src/auto-reply/reply/agent-runner-execution.ts:2541`). Cron gets an
isolated session, not a separate execution engine.

Beyond cron: `goal-tools.ts` (single-goal-per-thread tracker — objective +
token budget + status, not a multi-step planner); `src/tasks/` (a
cross-runtime status/delivery registry, doesn't itself execute work);
`src/commitments/` (background LLM extraction of implicit follow-ups from
conversation text, seeding future check-ins via cron/heartbeat). No
standalone long-horizon planner — long-horizon behavior is composed from
these pieces, each wake-up a fresh pass through the same agent-core loop.

**Approval gates: confirmed NOT a native OpenClaw feature.**
`approvals.exec`/`approvals.plugin` (`src/config/types.approvals.ts`) are a
**chat-forwarding relay** of a CLI-native harness's (Claude Code's/Codex's)
own built-in approval prompts into a configured chat channel for remote
human sign-off — not an OpenClaw-native permission gate on autonomous
cron/subagent actions. No distinct "does this autonomous action need
approval" gate exists; cron/subagent actions run autonomously by default,
bounded only by tool-policy allowlists and sandbox mode.

### Empyralis
`bounded_scheduler_service.py` (1314 lines) is a substantially more built-out
system than a bare cron tool: `propose_self_wakeup()` (lines 635-715, LLM
self-schedules a future check-in, rate-limited to
`policy.max_self_proposed_per_hour`, subject to quiet-hours via
`_apply_policy_to_due_at`), `maybe_schedule_event_trigger()`
(lines 565-632, context-engine-driven wakeups, rate-limited to
`policy.max_event_triggers_per_hour`), `claim_due_wake_requests()`/
`finalize_wake_requests()` (lines 718-891, batch claim/resolve), quiet-hours
enforcement (`_is_within_quiet_hours`/`_next_allowed_wakeup_time`,
lines 324-345), and a real retry policy with backoff
(`RetryPolicy`/`compute_retry_delay`/`should_retry`, lines 50-101).

Wake requests re-enter the loop via a periodic scanner
(`_ensure_wake_request_scanner_started`,
`runtime_route_registration_service.py:414`) feeding
`heartbeat_run_callback` — a **system-tier agent turn through the same
runtime** ordinary turns use (per `sage_heartbeat_service.py` and
`PLATFORM-MAP.md`'s own description of this path). Functionally comparable
to OpenClaw's "cron re-enters the same loop" finding — **rough parity** on
this specific point.

**Genuine strength, confirmed ahead of OpenClaw**: a real, native approval
gate for privileged self-proposed wakeups.
`propose_self_wakeup()` (`bounded_scheduler_service.py:658-678`): if
`metadata.requires_privileged_runtime` is set and
`policy.require_owner_approval_for_privileged_wakeups` is on and
`metadata.approval_granted` is not true, the wake request is persisted with
`status="denied"`, `denial_reason="approval_required"` — the autonomous
action simply does not run without owner sign-off. This is a native,
first-party gate; OpenClaw has no equivalent (confirmed absence above — its
`approvals.*` only relays a *different product's* (Claude Code/Codex) own
CLI approval UI, it is not a gate OpenClaw itself enforces on its own
cron/subagent actions).

Caveat on scope: Empyralis's gate covers only whether a **scheduled
wake-up is allowed to happen at all** — it does not gate individual
tool/action calls *within* that turn once it starts (no per-tool-call
human-approval relay inside the live loop, matching OpenClaw's own absence
there). A separate, different execution engine
(`runs_execution.py`, the visual workflow-builder product surface) does
have connector-action-level approval metadata
(`_connector_tool_node_approval_metadata`, lines 1073-1121) but that's
scoped to durable workflow runs, not autonomous cron-triggered chat turns —
so it doesn't extend the wake-up gate's coverage either.

No equivalent found for OpenClaw's `goal-tools.ts` (single-goal-per-thread
tracker) or `src/commitments/` (implicit-follow-up extraction) — reported
as "not found in this pass" rather than a confirmed absence, since these
weren't exhaustively searched for.

### GAP
**No correctness/capability gap ranked here** — this axis is a net
**strength** for Empyralis. The one real, actionable item is cosmetic:
OpenClaw's explicit hourly rate limits + quiet-hours + backoff-retry
policy are all independently present in `bounded_scheduler_service.py`
already; nothing to build to reach parity, and the privileged-wakeup
approval gate is ahead. Lower-priority/optional: consider whether a
`goal-tools`-style per-thread objective tracker or a `commitments`-style
implicit-follow-up extractor would add value — **not urgent**, speculative,
no evidence either is missed today.

---

## 7. Multi-agent

### OpenClaw
`sessions_spawn` (`src/agents/subagent-spawn.ts`) is **asynchronous,
fire-and-forget**: `spawnSubagentDirect()` enforces depth/fan-out guards →
creates child session key `agent:<targetAgentId>:subagent:<uuid>` →
dispatches via the same gateway `agent` RPC ordinary inbound messages use →
registers in `subagent-registry.ts` → **returns immediately** with
`{status:"accepted", childSessionKey, runId}` — does not await completion.
Parent discovers results via `sessions_history`/`session_status`/
`sessions_list`, or an automatic completion-delivery message governed by
`notifyPolicy`.

Depth/fan-out limits (`src/config/agent-limits.ts`):
`DEFAULT_SUBAGENT_MAX_CHILDREN_PER_AGENT=5`,
`DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH=1` — by default a top-level agent can
spawn one subagent, but that subagent cannot itself spawn further
subagents (blocked with `"sessions_spawn is not allowed at this depth"`).

No shared mutable state — each spawned session gets its own session-store
entry; context inheritance is explicit opt-in (`context:"isolated"`
default, or `"fork"` to inherit the parent transcript). Lineage is metadata
linkage only (`spawnedBy`/`parentSessionKey`/`spawnDepth`).

**`sessions_send` CAN be synchronous** — `sessions-send-tool.ts:242-341`:
if the target session has an active run, the message is **steered** into
it; if idle, a fresh turn starts via the same gateway RPC.
`timeoutSeconds` (default 30) controls whether the caller **blocks for the
reply** or gets an immediate fire-and-forget ack (`timeoutSeconds:0`).

### Empyralis
The only cross-agent messaging primitive is `fleet_message_agent()`
(`fleet_tools.py:1564-1631`) — **fire-and-forget only, no blocking option
at all**. It appends `{from_agent_id, message, enqueued_at, message_id}`
to the target install's `install_metadata.fleet_inbox` (capped at the last
**20** entries, line 1605) and returns `{"ok": True, "status": "enqueued"}`
immediately. The docstring itself hedges: *"The target agent's next turn
**may** read and process it"* (lines 1572-1576) — there is no confirmed,
guaranteed inbox-check mechanism verified in this pass, no timeout-to-block
option (unlike OpenClaw's `sessions_send timeoutSeconds`), and **no
completion-notification path back to the caller** — the operator has no
way to learn the target ever saw or acted on the message short of asking
again later.

There is **no `sessions_spawn` equivalent** — no ephemeral, task-scoped
subagent session with depth/fan-out limits that a turn can create on the
fly and later query for a result. `fleet_create_agent`
(via `fleet_tools.py`, the full list of fleet tools confirmed at
`skills_service.py:1200-1332`: `create_agent, list_agents,
get_agent_activity, get_project_activity, configure_agent, message_agent,
schedule_task`) creates **persistent, named, workspace-visible agent
installs** — a product/business object (a new Fleet entry a human sees and
manages), categorically different from OpenClaw's cheap, disposable,
per-task subagent session. There is no lightweight "spin up a scoped
helper, get its answer back, discard it" primitive at all.

Memory isolation between agents IS real and matches OpenClaw's "no shared
mutable state" principle at the storage layer: each specialist's memory is
keyed by `agent_install_id`
(`agent_memory._memory_db_path`/`_memory_notebook_dir`, confirmed via
`specialist_runtime_context.SpecialistRuntimeContext.memory_scope()`,
`specialist_runtime_context.py:75-77`) — **genuine parity** on this one
specific point, independent of the missing spawn/send primitives above.

### GAP
**#6 (capability, the largest single capability gap in this audit).**
Empyralis cannot today do "ask the research specialist to look this up and
report back" inside one operator turn — no synchronous option, no
completion callback, no cheap ephemeral delegation. **Large build**: this
needs (a) an ephemeral, task-scoped subagent/session concept distinct from
the persistent Fleet-agent object model, (b) depth/fan-out guards
(OpenClaw's `5` children / depth `1` defaults are a reasonable starting
point), (c) a blocking-with-timeout variant of `fleet_message_agent`
analogous to `sessions_send`'s `timeoutSeconds`, and (d) a way for the
caller to retrieve a completion result (`sessions_history`/`session_status`-
equivalent, or a return-message convention). This is a genuinely new
subsystem, not a small extension of the existing inbox.

Memory isolation needs no work — already matches.

---

## Summary: build-size-ranked punch list

| Priority | Item | Tier | Build |
|---|---|---|---|
| P0 | Fix unconditional tool-strip-after-first-call (§1) | Correctness | Small–Medium |
| P0 | Add secret redaction to `memory_write` (§3) | Correctness/Security | Small |
| P0 | Mid-turn steer/interrupt instead of hard-serialize (§1, §5) | Correctness/UX | Medium–Large |
| P1 | Parallel tool-call execution (§2) | Capability | Medium |
| P1 | Progressive channel delivery / block-chunking (§5) | Capability/UX | Medium |
| P1 | Ephemeral synchronous multi-agent delegation (§7) | Capability | Large |
| P1 | Reasoning-visibility toggle (§5) | Capability | Small–Medium |
| P2 | Notebook memory: real vector index (§3) | Capability | Medium |
| P2 | Canonical, channel-universal inbound envelope (§4) | Capability | Medium |
| P2 | Memory consolidation/"dreaming" (§3) | Capability | Large |
| P3 | Prompt-cache boundary (§4) | Cost | Medium |

**Do not build to "catch up"**: per-message memory attribution and a
general autonomous-action approval gate are both absent in OpenClaw too —
Empyralis already has a narrower version of the latter (§6) that OpenClaw
lacks entirely. A cost/budget loop-termination hook is defined-but-unused
in OpenClaw's own SDK; not worth porting.

**Already at parity or ahead, no action needed**: conversation compaction
design (§3, near-identical constants), tool-call error surfacing (§2),
no forced `tool_choice` (§1, §2), slash-command catalog breadth, per-agent
memory isolation (§7), and the bounded-scheduler's privileged-wakeup
approval gate (§6) — the last one a genuine lead over OpenClaw.
