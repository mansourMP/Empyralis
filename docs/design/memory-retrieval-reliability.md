# Memory Retrieval Reliability — VPS-Local vs Server-Side, and the Hallucination Vector

Answering the founder's question (2026-07-23): does it matter whether an agent
pulls memory from the VPS's own disk vs. our servers, and will retrieval
failure make a weak-tool-calling model hallucinate? Verdict-first,
file:line-verified against the working tree. Some files were mid-edit by
concurrent agents during this pass; those reads used `git show HEAD:<path>`
and are noted as such (see per-file notes below — none of the files this
report is built on were actually diverged from HEAD at read time).

---

## The direct answer (VPS-local vs server-side: what actually differs, what doesn't)

**There is no VPS-local memory pull today to compare against — it doesn't
exist as a mechanism.** Every memory tool call, for every agent placement
(cloud-only, paired computer, cloud VPS), reads and writes one place: the
Empyralis platform server's own filesystem, under
`.orion-stack/workspace/...` (`server_modules/workspace_context.py:10-11`)
and `.orion-stack/memory/...` (`server_modules/agent_memory.py:21-22`).
Confirmed independently in this pass: `grep -rn "MEMORY.md|memory_read|
memory_write|memory_search|memory_get" empyralis-gateway/src/` returns **zero
hits**. Even `cli_subscription` agents — which run the customer's real
`claude`/`codex` binary on their own box — never
have that binary touch a local memory file: `sage_agent_runtime_service.py`
flattens the entire conversation + system prompt (including the memory
brief) into text and ships it down the wire every turn
(`empyralis-gateway/src/llm/cli-runner.ts:244-260`, `buildInvocation`). The
box-side CLI process is a stateless generation engine, not a Claude-Code-style
local agent reading its own `MEMORY.md`. This matches and independently
re-confirms `docs/design/memory-placement-scope.md`'s bottom line.

**So, today, the founder's two scenarios collapse into one.** There is
exactly one retrieval path, and it is server-side. The question "is there a
difference" is currently moot by construction — not because the two paths
were compared and found equivalent, but because only one of them is built.

**If a VPS-local path were built** (the Gateway already exposes a
`filesystem.read_write` capability that could carry it —
`empyralis-gateway/src/shell/runtime.ts:11-14,216-300` — wired into the live
tool loop for the unrelated `file`/`shell` connectors today), here is exactly
where it would and wouldn't differ from today's server-side path:

- **No difference at the model layer.** Both are a `tool_use` block in,
  a `tool_result` block out — the same OpenAI/Anthropic-shaped wire
  envelope either way (`docs/design/agent-service-doctrine-research.md` A6).
  The model cannot tell, from the shape of the call or the result, whether
  the bytes came from a local file, a database, or a machine across the
  internet. Nothing about tool-calling *reliability* changes based on where
  the bytes physically live.
- **Real difference #1 — latency.** The Gateway's own measured number for
  the same durable-dispatch/WSS-flush mechanism a memory read would reuse
  (`llm.generate`) is **~4.4s per turn on the fast/warm path, with a 40s
  worst-case deadline** before the call fails outright
  (measured by an earlier audit, cited and re-verified in
  `memory-placement-scope.md:201-213`). That is not a network call to a
  colocated service — it is enqueue → wait for a WSS flush cycle → real
  process work on a customer's own, possibly single-vCPU machine. A local
  filesystem/SQLite read today is sub-millisecond by comparison.
- **Real difference #2 — a whole new failure class.** Local disk reads
  fail only if the file is missing/corrupt (see hallucination vectors
  below). A VPS pull adds: the box being offline, the WSS socket being dead
  while showing "Online" (a documented gotcha), and a
  durable-dispatch timeout that looks, from the model's perspective, exactly
  like every other kind of "nothing came back" unless it's deliberately
  built to look different (see Hardening Plan item 7).
- **Real difference #3 — round trips.** Today: zero extra network hops: the
  read happens in-process on the same machine that's already running the
  turn. A box-routed design adds one full gateway round trip per
  cache-miss, mitigated only by the existing per-session cache
  (`sage_instruction_compiler_service.py:307-324`, which already limits
  this to once per session, not every turn).

**Bottom line for the founder's actual worry:** the risk of a weak
tool-calling model mishandling a *remote* memory fetch is not present in the
live system today, because the fetch is not remote today. The hallucination
risk that already exists is a different, orthogonal one — how the *existing
server-side* tools report "not found" — detailed below. Building a
VPS/box-authoritative memory layer, if and when the founder wants it, should
be scoped as a new latency/offline-semantics problem (per
`memory-placement-scope.md`'s "Turn-time read cost" section), not as "the
same tool call, just pointed somewhere else."

---

## How retrieval works today (traced, file:line)

**Wire format:** native structured tool calls — an OpenAI/Anthropic-shaped
`tools=` JSON-schema array, not prose the harness has to regex-parse (with
two explicit *fallback* parsers for weak callers that emit prose anyway —
see Hardening section below).

**Schema definitions** — `server_modules/skills_service.py`,
`_builtin_tool_descriptors()` (starts `:659`):
- `memory_search` — `:764-783` — `{query: string (required), max_results:
  integer (optional)}`
- `memory_write` — `:785-832` — `{path (required), content (required), mode
  (optional enum), description (conditional), attribution_reason
  (conditional)}`
- `memory_read` — `:834-848` — `{path: string (required)}`
- `memory_get` — `:850-866` — `{path (required), from (optional), lines
  (optional)}`
- `memory_update` — `:868-903` — `{filename (required), content (required),
  description (conditional), attribution_reason (conditional)}`
(7 more memory tools exist — `memory_stage_edit`, `memory_apply_edit`,
`memory_append_daily_note`, `memory_stage_consolidation`,
`memory_consolidate_daily_notes`, `memory_list_versions`,
`memory_rollback_version`, `:905-1054` — out of scope for reads, not
detailed here.)

**Dispatch** — `skills_service.py`, `execute_single_direct_tool_call()`
(`:4648`), the `connector_id == "memory"` branches (`:4973-5100`):
- `memory_search` → `:4973-4989` → `callbacks.search_memory_notebook()`
- `memory_get` → `:4990-5002` → `callbacks.get_memory_notebook_excerpt()`
- `memory_read` → `:5050-5059` → `callbacks.memory_read_file()`
- `memory_write` / `memory_update` also dispatch here (`:5003-5049,
  5060-5100`)

**Service layer** — `server_modules/memory_service.py`:
- `search_memory_notebook()` (`:708-720`) →
  `get_memory_notebook_excerpt()` (`:723-737`) →
  `memory_read_file()` (`:887-913`) — all thin wrappers delegating to
  `agent_memory.py` (imported as `_workspace_memory_store`, `:16`).

**Storage layer** — `server_modules/agent_memory.py`:
- `_search_memory_notebook()` (`:1001-1064`) — linear scan + token-overlap
  scoring over every `.md` file under the agent's notebook dir, reading each
  file with `abs_path.read_text()` (`:1026`).
- `_get_memory_notebook_excerpt()` (`:1067-1096`) — resolves the path via
  `_resolve_notebook_path()` (`:350-367`, validates format/traversal, never
  existence) then reads the whole file and slices lines.
- Root: `agent_workspace_context_dir()` → `.orion-stack/workspace/...` on
  the platform server's own disk (see previous section).

**Pre-injection (the zero-tool-call path)** —
`server_modules/sage_instruction_compiler_service.py`,
`build_root_memory_brief_sections()` (`:335-440`): `SOUL.md`, `IDENTITY.md`,
`USER.md`, `GOALS.md`, `AGENTS.md`, `TOOLS.md` are injected **in full, every
turn** (`:354-370`, `ALWAYS_LOAD_INSTRUCTION_FILES`); `MEMORY.md` is
injected as a **capped index** (200 lines / 25KB, `:372-393`, matching
Claude Code's own published discipline per the comment at `:930-938`). A
"Root Memory Index" manifest (`:418-440`) lists the *paths* of any
`memory/files/**.md` topic files that exist, without their content, telling
the model what's available to pull. This whole assembly is cached per
`(workspace_id, session_id)` (`:307-324`) — first turn of a session pays the
read cost, every later turn in that session reuses it until a write
invalidates the cache.

---

## Hallucination vectors found, ranked (every silent/ambiguous failure path)

1. **[HIGH] `memory_search` returns a bare empty array for both "no
   query" and "query given, zero matches" — structurally indistinguishable
   from a broken search.** `agent_memory.py:1001-1064`,
   `_search_memory_notebook()` — line 1009-1010 returns `[]` for an empty
   query; the main loop simply produces zero results if nothing scores above
   0. `skills_service.py:4973-4989` wraps this as `json.dumps({"results":
   results})` — the model-visible payload is exactly `{"results": []}`,
   with no file count, no "confirmed searched N files," nothing to
   distinguish "we checked and there's genuinely nothing" from "your search
   phrasing didn't hit."

2. **[HIGH] Unreadable topic files are silently dropped from search
   results.** `agent_memory.py:1025-1028` — `try: lines =
   abs_path.read_text(...).splitlines() except Exception: continue`. A file
   that exists and contains the answer but can't be read (encoding issue,
   permission race, concurrent-write collision) disappears from the
   candidate set exactly like a file that never existed — zero signal to
   caller or model.

3. **[MEDIUM-HIGH] `memory_read` on a non-existent topic-file path returns
   `is_default: False` — a false negative on the one field designed to
   prevent this exact hallucination.** `workspace_context.py:601-602`
   (`read_workspace_context_file`) — `if not path.exists(): return ""` for
   any path outside the fixed root-file set. `memory_service.py:887-913`
   (`memory_read_file`) wraps this into `{"content": "", "chars": 0,
   "is_default": is_default_context_content(...)}`.
   `is_default_context_content()` (`workspace_context.py:719-728`) only
   recognizes the fixed `ALLOWED_CONTEXT_FILENAMES` root files (its
   `DEFAULT_CONTEXT_FILE_CONTENTS` lookup) — for any `memory/files/**.md`
   topic-file path, `default is None` → the function **always returns
   `False`**. A hallucinated or mistyped topic-file path therefore comes
   back as `{content: "", is_default: False}` — which reads as "this is
   real, curated, non-placeholder content that happens to be empty," the
   opposite of the truth. This is actively misleading, not merely silent.

4. **[MEDIUM] `memory_get` past end-of-file returns empty text, no
   error.** `agent_memory.py:1080-1087` — `if safe_from_line > len(lines):
   return {..., "text": "", "total_lines": len(lines)}`. At least
   `total_lines` is present for a model that checks it, but nothing flags
   this as an out-of-range request rather than "this section is blank."

5. **[LOW, largely mitigated] `memory_get` on a hallucinated/nonexistent
   path raises an uncaught `FileNotFoundError`** — `_resolve_notebook_path`
   (`agent_memory.py:350-367`) validates format and directory traversal but
   never checks existence; the subsequent `path.read_text()`
   (`agent_memory.py:1076`) throws. Listed last because this is **not**
   silent: it propagates as an exception, caught by
   `direct_chat_generation_service.py:2368` (`except Exception as exc`) and
   turned into an explicit tool-role message — `f"Tool execution failed:
   {llm_error}. Use only the provided tools and choose another tool if
   needed."` (`:2412-2416`) — genuinely actionable, just noted for
   completeness against vectors 1-4.

**What is NOT a hallucination vector, verified:** a tool *timeout* is
structurally distinguishable from a genuine empty result. Memory tools get a
dedicated 10s timeout (`direct_chat_generation_service.py:137-150`,
`_tool_timeout_seconds`); on expiry the model receives `{"error": "timeout",
"message": "The tool '...' timed out after 10s. Try a different approach —
..."}` (`:2151-2158`) — a different shape from `{"results": []}`, not
ambiguous with it. The gap is specifically the "tool succeeded, found
nothing" class, not timeouts or hard errors.

**No retries exist at the infrastructure level.** A memory tool that fails
or times out is not automatically retried by any code path found in
`skills_service.py`, `direct_tool_execution_service.py`, or
`direct_chat_generation_service.py` — recovery is delegated entirely to the
model deciding, from the explicit error text, whether to call again.

---

## Tool-call burden (minimum round trips, what's pre-injected vs fetched)

- **Zero calls:** `SOUL.md`/`IDENTITY.md`/`USER.md`/`GOALS.md`/
  `AGENTS.md`/`TOOLS.md` (full content, every turn) + `MEMORY.md` (capped
  index, every turn) — `sage_instruction_compiler_service.py:335-393`,
  cached per session (`:307-324`).
- **Zero calls to discover the tools exist:** all 5 primary memory tools
  (`memory_write`, `memory_read`, `memory_search`, `memory_get`,
  `memory_update`) are in `ALWAYS_ON_TOOL_NAMES`
  (`tool_registry_service.py:33-45`) — their full native JSON schemas ride
  on every turn's `tools=` payload; no `query_tool_registry` discovery call
  is needed first (unlike the long-tail connector/MCP tools, which are
  BM25-discovered on demand).
- **1-2 calls for a topic-file fact:** the documented pattern
  (`direct_chat_prompt_service.py:33-36`) is `memory_search` → `memory_get`
  for the needed lines — 2 round trips. If the model already knows the
  exact path from the "Root Memory Index" manifest
  (`sage_instruction_compiler_service.py:418-440`), it can skip straight to
  `memory_get` — 1 round trip.
- **0 additional calls** if the fact is already captured directly in the
  `MEMORY.md` index line — it's already in context from the pre-injection
  above.

This matches the architecture Empyralis explicitly adopted from Claude Code
(index-always-resident, on-demand-detail-pulled — comment at
`memory_service.py:917-938`), independently confirmed as the convergent
pattern across Anthropic and OpenAI/Codex in
`docs/design/agent-service-doctrine-research.md` §A7.1.

---

## Hardening plan, ranked (concrete changes)

1. **Kill the ambiguous empty search result.** In
   `agent_memory.py:_search_memory_notebook`, return a disambiguating
   envelope instead of a bare list — e.g. `{"results": [...],
   "files_searched": N}`, and when `N > 0` and `results == []`, have
   `skills_service.py`'s dispatch (`:4973-4989`) add an explicit `"note":
   "No matches for this query across N memory file(s). Try broader or
   different search terms before concluding nothing is saved."` This turns
   "empty" into "confirmed-checked-nothing," directly reinforcing the
   existing system-prompt doctrine that already tells the model to "say you
   checked" (`direct_chat_prompt_service.py:35`) and to say "I don't have
   anything saved yet" rather than fabricate
   (`sage_instruction_compiler_service.py:656-658`) — this closes the gap
   between what the prompt *asks* the model to do and what the tool result
   actually *gives it* to work with.
2. **Surface unreadable-file skips instead of silently continuing.**
   `agent_memory.py:1025-1028` — collect an `errors: [{"path":...,
   "reason":...}]` list alongside results rather than swallowing the
   exception. A file that exists but failed to read must never look
   identical, to the model, to a file that never existed.
3. **Fix the `is_default` false-negative for topic files.** Add an
   explicit `"exists": bool` field to `memory_read_file()`'s return
   (`memory_service.py:887-913`), computed from `path.exists()` before the
   read, for any `memory/files/**.md` path — don't rely on
   `is_default_context_content()`, which structurally cannot know about
   topic files (`workspace_context.py:719-728` only checks the fixed
   root-file dict).
4. **Collapse `memory_read`/`memory_get`.** Already independently
   identified in `docs/design/audit-tool-reliability.md` §3.4/recommendation
   5 as near-duplicate purposes — cited here rather than re-derived; this
   report's contribution is that the duplication also compounds hallucination
   risk (two similarly-named tools with two different empty-result shapes)
   on top of the selection-ambiguity risk that audit already flagged.
5. **Make conditional-requirement fields schema-visible.**
   `attribution_reason` (memory_write, memory_update) and `description`
   (for `memory/files/**.md` targets) are genuinely required in some cases
   but only ever appear in the `required` array as absent — the true
   condition lives in prose only (`skills_service.py:819-826, 892-898`). A
   weak model has no structural signal; it only discovers the rule after a
   rejected call. The write-side error itself is good ("This was NOT saved
   — retry with attribution_reason set," `memory_service.py:1008-1012`), so
   this is lower priority than 1-3, but a `source_is_owner: boolean`
   companion field (forcing the judgment into a checkable value rather than
   a memorized prose rule) would remove the ambiguity at the schema level
   instead of the retry level.
6. **Add `strict`/`additionalProperties: false`.** Already flagged
   platform-wide in `docs/design/audit-tool-reliability.md` recommendation
   4; applies equally to the memory tool schemas, which currently have no
   such field on `ToolDescriptor` (`skills_service.py:37-51`).
7. **If/when box-routed memory ships** (per
   `docs/design/memory-placement-scope.md`'s scoped implementation plan):
   do not make it a synchronous per-turn round trip. Reuse the existing
   offline-degrade pattern already proven for the `file`/`shell` connectors
   — `hardware_runtime_target_resolver.py`'s `AGENT_COMPUTER_OFFLINE_ERROR`
   — so a box-offline memory read degrades with an explicit, structured
   result instead of a 40-second timeout that looks, to the model, exactly
   like every other kind of nothing coming back. This preserves the
   "explicit failure, never silent" property that is already load-bearing
   doctrine elsewhere in this codebase (see next section).

---

## What is already right (say so plainly)

- **MEMORY.md pre-injection is real and matches Claude Code's own
  discipline by explicit design decision**, not accident — index always
  resident, capped, cached per session; on-demand detail pulled via tool
  calls only when needed (`sage_instruction_compiler_service.py:335-440`,
  `memory_service.py:917-938`).
- **All 5 primary memory tools are always-on** — zero discovery calls
  needed before a model can retrieve memory (`tool_registry_service.py:33-45`).
- **Write-side failures are exemplary: explicit, actionable, never
  silent.** "This was NOT saved -- retry with attribution_reason set"
  (`memory_service.py:1008-1012`); "This write was NOT saved... consolidate
  or move older/less-active facts" (`:981-988`). No write path in this
  codebase silently truncates or drops data — every cap violation is a
  loud, explained rejection.
- **Timeouts are explicit and structurally distinct from empty results** —
  a dedicated 10s memory-tool timeout that produces `{"error": "timeout",
  ...}`, not a hang or an ambiguous blank (`direct_chat_generation_service.py:137-150,
  2151-2158`).
- **Exceptions from bad tool calls don't crash the turn** — caught one
  layer up and turned into an explicit `"Tool execution failed: {error}"`
  tool-role message the model can reason about and retry from
  (`direct_chat_generation_service.py:2368, 2406-2419`).
- **System-prompt doctrine already targets this exact hallucination
  scenario directly**, in plain language: *"Never say 'I don't have any
  information' without actually checking memory first. If memory is empty
  after checking, say 'I don't have anything saved yet' — not 'I don't
  remember.'"* (`sage_instruction_compiler_service.py:656-658`), backed by
  a turn-level guard that detects any failed/timed-out/errored tool result
  in the recent history and force-injects: *"You MUST NOT invent, guess,
  estimate, or fabricate ANY data... Report ONLY what the tools actually
  returned"* (`direct_chat_generation_service.py:2337-2357`).
- **Two independent weak-tool-caller fallback parsers already exist,
  specifically for memory tools, with DeepSeek explicitly named as the
  motivating case** — `direct_chat_generation_service.py:673-677`'s bracket
  notation parser (`[memory_search: query]`, comment: *"DeepSeek often
  emits [tool: params] in text"*) and `no_provider_service.py:380-511`'s
  regex-based intent extraction for providers with no native tool-calling
  at all. This is exactly the class of hardening the doctrine research
  (`docs/design/agent-service-doctrine-research.md` §A6) identifies as the
  actual reliability lever — not smarter models, but a harness that
  tolerates non-structured output from weaker ones.
- **"Pull from VPS" causing a weak-tool-calling failure cannot happen
  today, because it doesn't exist as a mechanism** — the founder's specific
  fear is pre-empted by the current architecture being simpler (fully
  server-side) than the scenario being worried about, not by any special
  handling of a remote path.
