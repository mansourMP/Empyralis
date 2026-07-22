# Context Engineering — Synthesized Build Plan

**Date:** 2026-07-23. Synthesized from five completed reports, cited by filename throughout:
`agent-service-doctrine-research.md` (masters research), `audit-system-prompt-doctrine.md` (our
prompt scorecard), `audit-context-anatomy.md` (our measured window), `memory-architecture-research.md`
(industry memory patterns), `context-safety-research.md` (structural vs. behavioral defenses). No
new code was read for this document — it is synthesis, not a new audit.

Every item below is tagged **[INDUSTRY-PROVEN]** (big players already ship this; who's cited) or
**[OURS-TO-INVENT]** (no reference implementation exists anywhere in the five reports). Owner law is
respected throughout: no approve/deny UI, no approval-pending agent states, anywhere in this plan.

Three items below are marked **IN FLIGHT**, not proposed — they're being fixed now under the
founder's clear-defects-get-fixed-immediately mandate, ahead of this document landing. One structural
safety item is marked **DECIDED**, not a decision point — the founder ruled on it mid-flight; it is
recorded here as the implementation of that ruling, not an open question.

---

## What the five reports agree on

1. **Index-first, pull-on-demand is the universal shape for anything large** — tools, skills, and
   memory alike. Claude Code, Codex, Manus, and Anthropic's own multi-agent research system
   independently converge on "small resident index, full detail only on demand"
   (`agent-service-doctrine-research.md` §A7.1; `memory-architecture-research.md` verdict (a)). Our
   own BM25 tool-discovery work is already this shape — a genuine three-way independent convergence
   (Anthropic, OpenAI, Empyralis), not something to build from scratch.
2. **Deferral measurably improves accuracy, not just cost.** Anthropic's own tool-search benchmarks
   (49%→74%, 79.5%→88.1% accuracy with deferral on) directly falsify "less context is a pure quality
   tradeoff" (`agent-service-doctrine-research.md` §A4, §A7.2).
3. **Structural gates — enforced by code/OS regardless of what the model decided — are the only
   real defense against a confused or poisoned context window.** Prompts, instructions, and
   classifiers only ever *reduce* risk, never eliminate it, in every source that discusses safety
   (`context-safety-research.md` §a; verbatim from both Claude Code's and Codex's own docs).
4. **Compaction and memory are first-class, hookable, lifecycle events in every serious
   implementation, not "truncate when full."** But provenance/attribution specifically is a
   documented gap industry-wide (`memory-architecture-research.md` §(e)) — our own attribution
   requirement is already ahead of any reference implementation found in this research.
5. **Our own architecture is already close to the target shape in most places measured.** Native
   tool-calling for every provider including DeepSeek, a genuine two-tier always-on/BM25-registry
   tool system, a token-budgeted system prompt, a structured attribution envelope, and
   window-proportional web-chat history all already exist and already match the industry pattern
   (`audit-context-anatomy.md` headline finding). The gaps found are narrow, named, and
   file:line-fixable — not evidence of a fundamentally undisciplined system.
6. **No source anywhere treats an instruction or prompt as enforcement.** Claude Code's own docs say
   it outright: prompts "shape what Claude tries to do" but don't change "what Claude Code allows" —
   real enforcement is always a hook, a permission rule, or an OS boundary
   (`context-safety-research.md` §1).

---

## The plan, ranked

Ranked by (impact on the founder's stated pains: mid-task hallucination, walls of text, VPS safety,
memory correctness) × (smallness/safety of the change). Surgical fixes first, structural work after.

### 1. Cap `codex_cli` tool-result re-injection — **IN FLIGHT**
- **What:** bound the Codex provider's tool-result re-injection to the same ~4,000-char cap every
  other provider already gets.
- **Why:** the single genuinely *unbounded* injection path found in the whole audit — a big file
  read, a verbose connector JSON dump, or a `web__fetch` result flows straight into context uncapped
  for this one provider only. Direct cause of the "wall of text" pain and a plausible contributor to
  mid-task hallucination from context stuffing.
- **Evidence:** [INDUSTRY-PROVEN pattern, applied to a self-inflicted gap] `audit-context-anatomy.md`
  §7.1 — `direct_tool_execution_service.py:789-795` (uncapped), vs. every other provider capped at
  `direct_chat_generation_service.py:2202-2204` (HEAD).
- **Files touched:** `direct_chat_generation_service.py`, `direct_tool_execution_service.py`.
- **Risk:** very low — mirrors a pattern already proven on every other provider.
- **Verify:** feed `codex_cli` a >20K-char tool result (large file read or `web__fetch` payload),
  confirm it truncates identically to the OpenAI-compatible path; confirm Codex tool loops still
  complete correctly post-truncation.

### 2. Give channel/Sage history an aggregate token budget — **IN FLIGHT**
- **What:** replace the flat `[-16:]`/`[:4000-char]` pair in `_normalize_recent_messages` with the
  same window-proportional logic web chat already uses.
- **Why:** worst case today is ~16,000 tokens of channel history with zero scaling to the model's
  actual window — the "wall of text" pain, worst on small-context/BYO models and long-running
  Telegram threads.
- **Evidence:** [INDUSTRY-PROVEN — Claude Code's history is window/percentage-scaled, not a flat
  count] `audit-context-anatomy.md` §1 table, §7.2 — `sage_instruction_compiler_service.py:583-607`
  vs. the already-working `conversation_memory_policy.py:322-347`.
- **Files touched:** `sage_instruction_compiler_service.py`, `conversation_memory_policy.py`.
- **Risk:** low — reusing an already-proven internal function.
- **Verify:** simulate a Telegram/Slack thread with 16 near-4,000-char messages against a
  small-context model; confirm assembled history respects the proportional cap, not the flat
  16k-token worst case.

### 3. Thread `provider`/`model` through every `compact_turns()` call site, kill the silent no-op — **IN FLIGHT**
- **What:** parameterize all ~7 call sites so compaction summarizes with the tier the user is
  actually paying for; keep the platform DeepSeek key strictly as last-resort fallback; make an
  unset/unavailable summarizer key fail *visibly* (fall back to raw-truncation with a logged event),
  never silently return `""` and no-op.
- **Why:** a direct, named cause of the founder's "mid-task hallucination" complaint — every call
  site today silently falls through to one shared DeepSeek key regardless of the user's actual model,
  so the step that's supposed to preserve continuity can quietly degrade a paid GPT-5/Opus
  conversation's memory of itself. If the env var is unset, compaction silently no-ops platform-wide
  today.
- **Evidence:** `audit-context-anatomy.md` §4, §7.4 — `compaction_service.py:197,217` (hardcoded
  `provider="deepseek"`), 7 named call sites across `direct_chat_generation_service.py`,
  `sage_agent_runtime_service.py`, `sage_command_dispatcher.py`; `get_deepseek_api_key()` platform-wide
  key (`orion_local_worker_llm.py:914-919`).
- **Files touched:** `compaction_service.py`, `direct_chat_generation_service.py`,
  `sage_agent_runtime_service.py`, `sage_command_dispatcher.py`.
- **Risk:** moderate — touches multiple call sites; must confirm the true-fallback path (key unset)
  fails safe and visibly, not silently.
- **Verify:** run a long conversation on a paid non-DeepSeek model past the compaction trigger,
  confirm the summarization call logs that provider/model; separately unset the DeepSeek key in a
  test env and confirm a visible, logged fallback, not a silent empty-string no-op. Default routing
  policy across tiers is **Decision Point C** below — this item is the plumbing and the no-silent-
  failure guarantee, not the tier-matching policy choice.

### 4. Reconcile the capability-manifest: dedup native-schema overlap, un-truncate skill-only slots
- **What:** two paired fixes to `_capability_manifest_text`: (a) drop the lines that duplicate a
  tool's already-visible native schema; (b) for the slots where the manifest is the *only* channel a
  capability has (named skills beyond the 6-slot cut, connector actions not yet pulled via
  `query_tool_registry`), stop truncating at 140 chars.
- **Why:** `audit-context-anatomy.md` calls the whole manifest "redundant duplication" of native
  schemas; `audit-system-prompt-doctrine.md` separately shows the 140-char cap "quietly destroys most
  of the why/when text" for the specific slots with no other channel to the model. These two audits
  pull in opposite directions on the same code (delete vs. un-truncate) — the honest reconciliation
  is: delete the duplicate parts, keep and lengthen the parts nothing else covers.
- **Evidence:** [INDUSTRY-PROVEN direction: Claude Code "trusts the native tool list alone," doesn't
  re-describe] `audit-context-anatomy.md` §3, §7.3; `audit-system-prompt-doctrine.md` §4.2-3 —
  `sage_instruction_compiler_service.py:425-463`, `:65`, `:452-456`.
- **Files touched:** `sage_instruction_compiler_service.py`.
- **Risk:** low-moderate — must keep safety-relevant metadata (approval/runtime flags) the native
  schema can't express; check tool-selection accuracy doesn't regress once duplicate lines drop.
- **Verify:** before/after prompt-token diff on a representative workspace; confirm a
  cap-de-prioritized named skill's full description now reaches the model and gets correctly
  selected on a matching task.

### 5. Dead-code and stale-comment cleanup
- **What:** delete `direct_chat_runtime_service.py` and its prose-tool-listing dependency chain
  (`direct_chat_prompt_service.build_system_prompt`/`tool_prompt_lines`); fix the stale "8 core
  tools" comment (actual count is 12).
- **Why:** not a live risk today (confirmed unreachable, would `NameError` if executed) but exactly
  the kind of dead path a future engineer "fixes" or extends by accident, reviving a prose-tool-
  listing pattern the rest of the codebase already correctly moved away from.
- **Evidence:** `audit-context-anatomy.md` §2, §7.5 — `direct_chat_runtime_service.py:1-35`
  self-documented DORMANT, `NameError`-in-waiting at line 1079; `audit-system-prompt-doctrine.md`
  §1 footnote — `sage_agent_runtime_service.py:2360` comment vs. `tool_registry_service.py:34-47`.
- **⚠️ SCOPE CORRECTED 2026-07-23 (independent re-verification before deletion):** the module is
  functionally dead (nothing live ever CALLS its functions — call-path trace confirmed through
  `turn_runtime.execute_agent_turn_request` → `direct_chat_service.execute_direct_chat_turn_request`)
  but the original "zero remaining importers" claim is FALSE. It is load-bearing for imports:
  `direct_chat_runtime_facade_service.py:9` (constructs `DirectChatRuntimeServices` at :206),
  `direct_chat_runtime_entry_facade_service.py:5`, transitively `direct_chat_composition_service.py`,
  `direct_chat_callback_facade_service.py`, `direct_chat_runtime_exports.py` (module-level import in
  `sage_agent_runtime_service.py:20,54`, `direct_chat_generation_service.py`, `mcp_server.py`), and a
  dynamic `import_module("server_modules.direct_chat_runtime_exports", ...)` that runs on EVERY live
  `/turn` request (`direct_chat_stream_runtime_service.py` via `runtime_runs_api.py:826,1023`).
  Standalone deletion = ModuleNotFoundError on server import or on every chat turn.
- **Status:** the safe half is DONE (stale "8 core tools"→"12 core tools" comment fixed, landed with
  `996b8fdbe`). The module itself is tracked as **known-dead-but-load-bearing-for-imports**; deletion
  requires a properly-scoped facade-chain refactor (6-8 files: both facade services, composition,
  stream-runtime `fromlist` import, `DirectChatExecutionServices` in `direct_chat_service.py`) —
  re-filed as a deliberate refactor item, NOT quick cleanup. Do not delete on the old evidence.
- **Risk (corrected):** standalone deletion would crash production; refactor-scoped removal is medium.
- **Verify (for the eventual refactor):** grep for importers (not just callers) across server_modules/
  + scripts/ + mcp_server.py; server imports cleanly; a live /turn round-trip succeeds.

### 6. Memory provenance + trust-weighted injection + write filters — elevated priority, differentiator
- **What:** every memory write carries who/what/which-channel it came from and a trust tier (owner
  direct instruction / verified-owner-other-surface / non-owner sender / agent inference / tool
  output); at injection time, low-trust-provenance memories are visibly marked as such in the
  assembled context (e.g. "recorded from a non-owner message, unverified"), not silently promoted to
  owner-grade fact; write filters prevent a non-owner message from ever being saved as an unqualified
  fact about the owner in the first place.
- **Why — the founder's own canonical scenario:** his brother messages the agent. The chat-time
  attribution envelope already handles this correctly *in the moment* — `is_owner` is a fail-closed
  tri-state and `envelope_allows_owner_commands()` makes it structurally impossible for a group/DM
  from a non-owner to carry owner authority during that turn (`audit-context-anatomy.md` §5, scored
  PASS, "no changes recommended here"). But nothing today stops a fact volunteered by the brother in
  that same message from being written to memory and later recalled — in a future conversation, with
  the original envelope long gone — as if it were owner-verified truth. This is precisely the
  provenance gap, and it is a genuine industry blind spot, not something to import from a reference
  implementation.
- **Evidence:** [OURS-TO-INVENT — confirmed blind spot across all seven references checked: Anthropic
  memory tool, Claude Code, Manus, Letta/MemGPT, Mem0, OpenAI ChatGPT memory, Codex AGENTS.md]
  `memory-architecture-research.md` §(e) — "None of the seven sources document input-provenance
  tagging... as a first-class part of their memory schema." Claude Code's closest analogue is a
  `modified` write-timestamp only, not a source field. Empyralis's attribution requirement is
  explicitly named as already ahead of the documented state of the art.
- **Files touched:** the memory write path (`sage_memory_service.py` and wherever `memory_write`/
  `memory_update`/`memory_append_daily_note` persist entries), the retrieval/injection path that
  builds the retrieved-memory system-prompt section.
- **Risk:** moderate — schema change to memory storage; existing untagged entries need an explicit
  "unknown provenance" default, not a guess; write filters must not silently drop information the
  owner would actually want captured (e.g., "flag as unverified" not "discard").
- **Verify:** the brother scenario, literally — have a non-owner sender assert a fact in a group/DM,
  confirm it is either not written as an unqualified fact or is written tagged low-trust; in a later,
  separate conversation, confirm the retrieved memory block surfaces that entry with its trust tier
  visible, and that the model does not act on it with owner-level authority.

### 7. Memory index hard cap — adopt Claude Code's exact discipline (200 lines / 25KB), restorable compression, proactive plan externalization
- **What:** adopt Claude Code's exact numbers for our own agent-written index file (`MEMORY.md`):
  hard-capped at the first 200 lines OR first 25KB, whichever is hit first; content beyond that is
  never loaded at session start. Topic files (already pull-on-demand today via `memory_search`/
  `memory_get`, both always-on tools) stay exactly as they are — that half already matches. On
  overflow, the write returns an explicit "shorten this" error instead of silently truncating the
  tail on next load. Additionally: apply restorable compression to large ephemeral tool outputs
  (keep a pointer, drop the body, rather than deleting outright) and prompt the agent to externalize
  critical state (a plan) to memory proactively as it *approaches* a compaction boundary, not only
  after.
- **Why:** this is the founder's adopted standard, not a proposal to weigh — Claude Code's own
  numbers are the reference. One real implementation note: our current
  `ROOT_MEMORY_BRIEF_TOTAL_CHAR_LIMIT` is 4,800 chars, shared across *all* root files (SOUL/IDENTITY/
  USER/GOALS/AGENTS/TOOLS/MEMORY.md combined) — 25KB for `MEMORY.md` alone is roughly 5x that entire
  shared budget today. Adopting the exact number means carving `MEMORY.md` out into its own dedicated
  200-line/25KB allowance, separate from the other root files' shared budget, not just raising the
  shared total.
- **Evidence:** [INDUSTRY-PROVEN: Claude Code] `memory-architecture-research.md` §2 — "The first 200
  lines of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the start of every
  conversation... Topic files are never loaded at startup"; §(d) size-budget table; "pattern to
  adopt" items 1-5. Plan externalization: Anthropic's multi-agent research system, same doc §3.
  Restorable compression: Manus, same doc §4.
- **Files touched:** `sage_instruction_compiler_service.py` (root memory brief budget/enforcement,
  `ALWAYS_LOAD_INSTRUCTION_FILES` handling), the memory write path (overflow error).
- **Risk:** moderate — changes both a budget constant and write-path behavior; must confirm the
  overflow error is surfaced usefully (to the model, so it can actually shorten) rather than just
  failing the write.
- **Verify:** construct a `MEMORY.md` exceeding 200 lines/25KB, confirm the write returns an explicit
  shorten-this error rather than silently succeeding and dropping the tail on next load; confirm a
  200-line/25KB-compliant `MEMORY.md` loads in full every session regardless of the other root files'
  budget state.

### 8. Dedup vs. ADD-only memory write policy (implementation)
- **What:** implement the write policy decided in **Decision Point B** below — update-don't-duplicate
  (merge/overwrite, consolidate near the index cap) vs. ADD-only (keep all historical versions, rank
  at query time).
- **Why:** currently implicit/undecided in our own code; `memory-architecture-research.md` names this
  "the sharpest documented disagreement" in the whole research and says our attribution work
  "presumably has to pick a side." Now that provenance (#6) exists, a merge/overwrite can carry an
  audit trail of what changed and why, without needing Mem0's full ADD-only duplication.
- **Evidence:** `memory-architecture-research.md` §(c), "pattern to adopt" item 7.
- **Files touched:** memory write/consolidation handlers (`memory_write`, `memory_update`).
- **Risk:** low to implement once decided — the open question is the policy, not the code.
- **Verify:** write two contradicting facts about the same entity in sequence; confirm the chosen
  behavior (merge-and-replace, or both-survive-ranked) is what was decided, not accidental.

### 9. Doctrine rewrite, part 1 — first-priority statement + why-first memory framing
- **What:** add one first-priority sentence to the kernel (what the agent's job is, before anything
  else); reorder the memory rule to lead with *why* (durability across sessions) instead of burying
  it after the mechanical recipe; surface the SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS taxonomy
  (currently only a Python comment) as one line in the root-memory-brief header.
- **Why:** a zero-hit grep for any first-priority phrasing on either prompt path is the doctrine
  audit's #1 finding — every industry doctrine source reviewed (Claude Code's "Match features to
  your goal," Codex's identity+mandate paragraph, OpenAI's Model+Tools+Instructions) leads with
  exactly this.
- **Evidence:** [INDUSTRY-PROVEN pattern, ours to apply] `agent-service-doctrine-research.md`
  §B1/B4 (template); `audit-system-prompt-doctrine.md` §3 row 1, §4.4, §5.3&5 —
  `sage_instruction_compiler_service.py:472-524`, `:479-509`, `:34-42`.
- **Files touched:** `sage_instruction_compiler_service.py`.
- **Risk:** low — additive text inside an already-budgeted section; must re-measure token cost
  against the 3,000-token/12,000-char budget.
- **Verify:** the "dad scenario" test below; token-count the kernel before/after to confirm it stays
  inside budget.

### 10. Doctrine rewrite, part 2 — give specialists the capability manifest
- **What:** thread the already-computed `_capability_manifest_text` (and ideally kernel + policy
  context) into the specialist branch (`sage_agent_runtime_service.py:4332`), which omits it
  entirely today.
- **Why:** specialists are the higher-traffic, customer-facing fleet-agent surface
  (Telegram/WhatsApp/Discord bots) and today get a structurally *worse* prompt than the master/Sage
  path — no kernel, no policy context, no capability manifest, and a bare memory dump with zero rule
  attached.
- **Evidence:** `audit-system-prompt-doctrine.md` §0, §2b, §4.1&5, §5.2 —
  `sage_agent_runtime_service.py:4275-4337` (specialist) vs. `:4338-4343` (master); `:4331` (bare
  memory dump); `:4332` (manifest omitted despite being computed every turn regardless).
- **Files touched:** `sage_agent_runtime_service.py`.
- **Risk:** low-moderate — the value is already computed every turn; needs the same budget cap the
  master path already has, since no compiler budget currently applies to the specialist branch.
- **Verify:** capture a specialist system prompt before/after for a live fleet agent, confirm
  "## Callable Tools" now appears; run a specialist-path task requiring an infrequently-used tool and
  confirm it no longer depends solely on persona text.

### 11. Model-callable self-wakeup path
- **What:** expose a dedicated, always-on, self-scheduling tool wrapping
  `bounded_scheduler_service.propose_self_wakeup`, worded explicitly for "you can schedule your own
  future wakeup" — not routed only through the operator-only, fleet-management-framed
  `fleet__schedule_task` (which specialists don't even have).
- **Why:** a real, live, already-built capability (`propose_self_wakeup` exists and works) that is
  100% invisible to the model on both paths today — the doctrine audit's single sharpest gap.
  Continuity across turns without the owner re-prompting is exactly what the founder's "standing
  order" use case depends on.
- **Evidence:** `audit-system-prompt-doctrine.md` §3 row "Scheduler," §4.1, §5.1 —
  `bounded_scheduler_service.py:635-715` (only two callers today: master-only `fleet_tools.py:2325-2410`
  and a human-facing, `member_dependency`-gated REST endpoint); `sage_agent_runtime_service.py:2362-2386`
  (fleet tools never given to specialists).
- **Files touched:** `bounded_scheduler_service.py`, `tool_registry_service.py`,
  `sage_instruction_compiler_service.py` / specialist assembly.
- **Risk:** moderate — new model-callable surface onto an existing internal function; needs the same
  bounding/rate-limiting the REST endpoint presumably already has.
- **Verify:** the "dad scenario" test below directly exercises this — confirm via scheduler logs
  that a wakeup was proposed by the agent turn itself, not the REST endpoint or a human.

### 12. Skills BM25 fallback + reserved-slot scaling
- **What:** index `skill_registry.list_skill_definitions()` into
  `tool_registry_service.build_registry_entries()` so a skill that misses the 6-of-16 manifest cut is
  still discoverable via `query_tool_registry`; and/or scale
  `CAPABILITY_MANIFEST_SKILL_RESERVED_ITEMS` with context window as the code's own comment already
  flags as intended.
- **Why:** ~20 built-in skills + 6 bundled filesystem skills structurally consume nearly all 6
  reserved slots before a single workspace-authored skill is considered — and a skill that misses the
  cut isn't just deprioritized, it's unreachable this turn by any means. A direct cause of
  capability-blindness ("the agent doesn't know it has a skill for this").
- **Evidence:** [INDUSTRY-PROVEN mechanism, same shape we already ship for tools — independent 3-way
  convergence per `agent-service-doctrine-research.md` §A3] `audit-system-prompt-doctrine.md` §3 row
  "Skills," §4.2, §5.4 — `sage_instruction_compiler_service.py:64`, `skill_registry.py:780-1030`,
  `tool_registry_service.py:276-336` (never iterates `skill_registry`).
- **Files touched:** `tool_registry_service.py`, `sage_instruction_compiler_service.py`.
- **Risk:** low-moderate — additive to an existing, working registry; confirm `skill_invoke` dispatch
  handles a registry-discovered skill the same as a manifest-listed one.
- **Verify:** pick a skill confirmed to miss the 6-slot cut today, issue a task matching its
  description with no name given, confirm `query_tool_registry` surfaces it and `skill_invoke`
  executes it correctly.

### 13. Untrusted-content quarantine
- **What:** tag any inbound content the agent didn't get directly from the owner's own instruction —
  fetched web pages, connector API responses, inbound messages from non-owner senders — by
  provenance, the same structural way the attribution envelope already tags sender identity (and the
  same tagging built for #6). State explicitly in the system prompt that tool/fetch/connector content
  is data, not command. JSON-encode it so it can't typographically break out. Consider a screening
  pass on high-risk sources.
- **Why:** the industry's direct answer to "a polluted context window causes a bad action." Today
  nothing in our stack addresses "what is a fetched web page / connector response allowed to make the
  agent do" — the Authority Mandate solves *who is speaking*, not *what fetched content is allowed to
  trigger*.
- **Evidence:** [INDUSTRY-PROVEN — Anthropic's `tool_result`-only + JSON-encoding + classifier-
  screening pattern; MCP's "provenance must be structural" principle] `context-safety-research.md`
  §3, taxonomy (c), "Mapping onto Empyralis" gap #3.
- **Files touched:** `web_tools.py`, the connector/MCP tool-result assembly path, system-prompt
  policy text (`sage_instruction_compiler_service.py`).
- **Risk:** moderate — touches the tool-result assembly path broadly; must not break legitimate use
  of fetched content.
- **Verify:** fetch a test page/document containing an embedded instruction ("ignore previous
  instructions and...") and confirm the agent treats it as inert data across at least `web__fetch`
  and one connector path.

### 14. Remove destructive-infrastructure capability structurally — **DECIDED** (not a decision point)
- **Ruling:** the founder closed this mid-flight. Mechanism is **capability removal**, not gating.
  The agent must not have any tool, connector action, or shell/`hardware__action` allowance that can
  destroy the VPS, its software, or its infrastructure — ever. Not approval-gated, not
  reasoning-gated: not provided, at any authority tier, including owner.
- **What:** an explicit, standing denylist of destructive operation classes (wipe the box, drop the
  database, disable billing, delete all agents en masse, etc.) enforced *outside the model*, at the
  kill-switch / Rust-kernel / Authority Mandate seams — meaning these operations are structurally
  absent from what any tool call can ever be constructed to request, not merely blocked by a check
  after being requested. This is one level stronger than a runtime circuit breaker: there is no
  tool-call shape that reaches the destructive operation in the first place.
- **Why:** the most direct possible answer to "this agent ran some command and broke the entire VPS"
  — removing the capability is strictly stronger than gating it, and needs no approval mechanism to
  work, satisfying owner law by construction rather than by compensating control.
- **Evidence:** [INDUSTRY-PROVEN mechanism, taken one step further] Claude Code's `rm -rf /`/`~`
  circuit breaker and Codex's workspace-boundary check are runtime hard-blocks on a capability that
  still technically exists in the tool surface; this ruling removes the capability from the surface
  entirely — the strongest version of "structural, not behavioral" found in `context-safety-research.md`
  §1-2, §a table, "Mapping onto Empyralis" gap #2, pushed further than any single cited vendor does.
- **Files touched:** `authority_mandate_service.py`, `rust_runtime_kernel_client.py`,
  `tool_registry_service.py` and wherever `hardware__action`/shell-executing tools, connector actions,
  or skills are registered — every registration path needs an inventory pass confirming no destructive
  operation class is exposed as a callable schema anywhere, not just gated post-call.
- **Risk:** high relative to other items — requires an exhaustive inventory of every tool, connector
  action, and skill that could plausibly construct a destructive request, and a standing process to
  re-check that inventory whenever a new tool/connector/skill is added.
- **Verify:** attempt each named destructive operation class as the owner tier via every available
  surface (native tools, skills, connectors, MCP); confirm no code path can even construct the
  request — the correct outcome is "no such tool exists to call," not "call attempted, then blocked."
  Maintain the denylist inventory as a standing checklist reviewed on every new capability addition.

### 15. OS-level sandbox under agent shell/VPS execution
- **What:** put an OS-enforced boundary (Seatbelt/bubblewrap/Landlock-equivalent, or a
  restricted-user + cgroup/seccomp profile fitted to our per-customer VPS model) underneath any
  remaining, non-destructive agent-initiated shell/VPS command, so containment holds regardless of
  what the model decided to run — matching Claude Code's and Codex's own stated architecture. This is
  the general containment floor; item #14 already removes the specifically catastrophic class from
  the tool surface, so this item is about bounding everything else (reads/writes/network reach for
  legitimate, non-destructive operations).
- **Why:** every hard gate we have today (Authority Mandate, kill switch, and the new denylist in
  #14) is a Python-service-layer pre-dispatch decision, not a running-process containment boundary —
  there is still no OS-enforced floor under an allowed command the way there is under a Claude Code
  Bash call, for the (non-destructive) operations that remain legitimately available.
- **Evidence:** [INDUSTRY-PROVEN — both Claude Code (Seatbelt/bubblewrap) and Codex
  (Seatbelt/bubblewrap+seccomp/Landlock) put this under every shell command] `context-safety-research.md`
  §1-2, §a table, "Mapping onto Empyralis" gap #1.
- **Files touched:** wherever agent shell/`hardware__action` execution dispatches to the customer
  VPS — not itself audited in these five reports, needs a follow-up read before scoping.
- **Risk:** highest in this list — real infrastructure work on customer-owned VPS boxes, interacts
  with the locked "multi-agent-per-box, per-agent memory isolation" architecture ruling.
- **Verify:** from an agent shell session, attempt to read/write outside the declared working
  boundary and reach non-allowlisted network destinations; confirm the OS layer blocks both
  regardless of what the model "decided," independent of the Authority Mandate check.

---

## Decision points for the founder

> **ALL FOUR DECIDED by the founder, 2026-07-23, on the recommendations:**
> **A** → (2)+(3): structural compensations as permanent architecture + owner-alert extension (informational, never a gate). Trade-off documented deliberately.
> **B** → update-don't-duplicate, paired with the provenance audit trail (#6).
> **C** → compaction matches the user's active provider/model by default; platform DeepSeek key is true last-resort fallback only.
> **D** → per-process OS sandbox (bubblewrap-equivalent) now, as the containment floor; container/VM-per-agent re-evaluated after observing (2)'s gaps.

Note: the catastrophic/destructive-infrastructure question is **not** listed here — it's decided
(see plan item #14, capability removal). The tension below is the narrower, still-open one: genuinely
novel, irreversible, high-stakes *business-logic* actions that aren't infrastructure destruction (a
wrong payment, a bad commitment made on the owner's behalf, an irreversible send) — where removing the
capability outright isn't an option, because the capability itself is exactly what makes the agent
useful.

### A. The no-approval law vs. the industry's human-confirmation moment
- **The question:** every safety source's answer for a genuinely novel, irreversible, high-stakes
  situation *that isn't a capability you'd remove entirely* is a human noticing something is off *in
  the moment* — a popup. Owner law forecloses that mechanism categorically, permanently, for every
  action. What compensates?
- **Options:** (1) status quo — rely only on existing tier/credential scoping, accept the gap as-is;
  (2) ship the remaining structural compensations in this plan (quarantine #13, the destructive-
  capability removal #14, eventual OS sandbox #15) and state plainly this is *not* equivalent to
  human-in-the-loop for novel business-logic situations; (3) extend the existing post-hoc owner-alert
  pattern (already shipped per commit `cb4a8189c`, "owner alert" for unattended-agent auth issues) to
  fire after any quarantine flag or denylist-veto event — informational only, never a gate, never a
  pending state.
- **What industry does:** OpenAI's guide frames human-in-the-loop as scaffolding "until confidence in
  the agent's reliability grows" — a temporary state, not permanent (`context-safety-research.md` §4).
  Every vendor treats classifiers/prompts as risk-reduction, never elimination (§a).
- **Recommendation:** (2) + (3), combined. Ship the structural compensations as the permanent
  architecture, and extend the already-existing owner-alert pattern to cover quarantine flags and any
  denylist-veto event (item #14 firing) — after the fact, informational, never blocking. State
  explicitly, in whatever founder-facing security material exists, that this is a deliberate, accepted
  trade-off: zero approval friction and a bounded blast radius, in exchange for genuinely giving up
  the industry's answer for the novel-instance business-logic case nobody enumerated in advance. Do
  not build anything that looks like a hold-for-review state, even disguised as a
  notification-with-pause — that violates the law by proxy.

### B. Memory dedup vs. ADD-only
- **The question:** when a new fact contradicts or duplicates an old one, do we merge/overwrite
  (Claude Code) or keep all historical versions and rank at query time (Mem0)?
- **Options:** Claude Code's update-don't-duplicate (nudge consolidation near the index cap) vs.
  Mem0's ADD-only (preserve all versions, accept surfaced duplicates/contradictions as a known
  tradeoff).
- **What industry does:** genuinely split — `memory-architecture-research.md` §(c) calls this "the
  sharpest documented disagreement" found in the research.
- **Recommendation:** Claude Code's stance (update-don't-duplicate) at our current scale — per-agent,
  per-repo working notes, not a cross-user consumer memory product. Pair it with the provenance field
  (#6) so a merge/overwrite still leaves an audit trail of what changed and why, getting most of
  Mem0's forensic benefit without its duplicate-surfacing cost.

### C. Which model summarizes on compaction
- **The question:** now that #3 makes compaction routable and fail-visible, what's the *default*
  policy — always match the user's active provider/model, always use a fixed cheap model (status
  quo), or tier-gate it?
- **Options:** (1) always match the user's paying tier; (2) keep the current fixed-cheap-model
  default; (3) cheap default on low tiers, matched model on paid tiers.
- **What industry does:** no strict precedent found — Codex's own compaction prompt explicitly frames
  the continuing turn as possibly a *different* model doing the earlier work
  (`agent-service-doctrine-research.md` §A5), so there's no rule that summarization must match the
  primary model. This is genuinely an Empyralis cost/quality call, not an industry-settled question.
- **Recommendation:** (1) — match the user's active provider/model by default. A degraded summary
  directly undermines the paid tier's quality promise and is a plausible root cause of the founder's
  own mid-task-hallucination complaint; the platform DeepSeek key stays as true last-resort fallback
  only (key/route genuinely unavailable), never the default.

### D. How far to go on OS-level containment for agent VPS commands (the non-destructive remainder)
- **The question:** given agents run on customer-provisioned VPS boxes, and item #14 already removes
  the destructive-operation class entirely, what's the right containment floor for everything else
  the agent legitimately does (file reads/writes, network calls, package installs, etc.)?
- **Options:** (1) status quo — service-layer gates only; (2) OS user/cgroup/seccomp-level
  restriction (bubblewrap-equivalent) per agent process on the existing box; (3) full per-agent
  container/VM isolation.
- **What industry does:** both Claude Code and Codex run an OS-level sandbox underneath every shell
  command regardless of permission-rule outcome (`context-safety-research.md` §1-2).
- **Recommendation:** (2), now, as the minimum bar for everything item #14 doesn't already remove. It
  fits naturally on top of the already-locked "multi-agent-per-box, per-agent memory isolation"
  architecture (per project memory) — a bubblewrap-style per-process restriction is an incremental
  step on that same box, not a new deployment model. Treat full container/VM-per-agent (3) as a later
  hardening step, re-evaluated once (2) is shipped and its gaps are actually observed.

---

## What we deliberately do NOT build

1. **No approve/deny UI or approval-pending state, anywhere.** Owner law, not a debate. Compensated
   by structural controls (#13-15) and the existing owner-alert pattern (Decision Point A), never by
   a human clicking a button.
2. **No reasoning-gated permission check for destructive infrastructure operations.** Per the
   founder's ruling (item #14), the capability itself is structurally absent from the tool surface —
   there is nothing left for a runtime check to gate, and building one anyway would imply the
   capability still exists somewhere it shouldn't.
3. **No embeddings/vector DB for our own agent-authored working memory.**
   `memory-architecture-research.md`'s own verdict: plain-file index-first reads are correct for our
   shape of problem (small, agent-authored, high-trust corpus); embedding/hybrid retrieval (Letta's
   archival tier, Mem0) only pays off once the corpus is large, unstructured, and multi-tenant — a
   different regime than an agent's own notes about its environment, tasks, and users.
4. **No idle-time "dreaming"/consolidation agent** (Letta's sleep-time pattern). Our write-time
   forcing function (#7) already gives synchronous consolidation the same way Anthropic's memory tool
   and Claude Code do; no evidence we're at a scale where async idle-time reorganization pays for its
   own complexity.
5. **No prose/text-protocol fallback for third-party models lacking native tool-calling.** Codex
   explicitly deprecated exactly this path (removed the Chat Completions wire API) because it
   degrades reliability. Every provider we support, including DeepSeek, already gets real native
   tool-calling (`audit-context-anatomy.md` §2) — building a prose fallback "just in case" would
   reintroduce a pattern the industry actively moved away from, not fill a gap
   (`agent-service-doctrine-research.md` §A6).
6. **No general "ask when unsure" runtime policy layered on top of pre-declared boundaries.** Every
   safety source names this as the direct cause of approval fatigue; our answer stays "the approval
   already happened, upstream, at configuration time" (capability tiers, credential scoping, spend/
   credit ceilings) — never a runtime judgment call, which would also violate the owner law by the
   back door (`context-safety-research.md` taxonomy (d)).
7. **No detection-only/classifier-first safety architecture.** Every source is explicit that
   classifiers and prompts only ever reduce risk, never eliminate it, and none treats a probabilistic
   layer as sufficient alone. Structural gates (#13-15) come first; any screening classifier is a
   secondary layer on top, never the primary defense (`context-safety-research.md` §a).

---

## Verification plan

Every plan item above carries its own verification method. Two overarching, repeatable regression
tests close the loop on the founder's two headline fears.

### The "dad scenario" test — standing order, zero tool-naming, agent self-selects
Set up a real daily standing order phrased the way a non-technical owner actually would — no tool
named, e.g. *"Every morning, check if anything needs my attention and text me a short summary."* Run
it for several consecutive days on a fresh build with items #9-11 shipped (doctrine rewrite +
model-callable self-wakeup), on both a master agent and a specialist/fleet agent.

**Success criteria:**
1. The agent itself proposes and executes its own future wakeup via the model-callable self-wakeup
   path (#11) — never a human or fleet-admin scheduling it.
2. It selects the correct tools/connectors purely from purpose/trigger descriptions — the standing
   order never names a tool.
3. The reply is short and correctly channel-formatted — no wall of text.
4. Run on a specialist path specifically to confirm #10 (specialist capability manifest) actually
   closes the master/specialist gap, not just the master path.

Repeatable: script it as a real scheduled standing order in a test workspace; log which subsystem
(memory/scheduler/skills/tools) fired each day and whether a tool name ever leaked into the
instruction text.

### The DeepSeek-brain regression test — mid-task hallucination
Reconstruct the original failure mode directly. Run an identical, sufficiently long real task (a
multi-step tool-using conversation that crosses the compaction trigger) once on the primary paid
model and once with the underlying model swapped to DeepSeek, both before and after items #1-4 ship
(three of which are already in flight).

**Success criteria:**
1. `codex_cli`-path tool results (if exercised) are capped identically to other providers (#1) — no
   wall-of-text feed to the model.
2. Compaction triggers correctly and the summary is produced by the correct provider/model per the
   routing decision (#3 + Decision Point C), not silently downgraded, and a missing key produces a
   visible fallback, never a silent no-op.
3. A scripted "plant a fact early, ask for it after compaction" probe confirms the fact survives —
   directly modeled on the multi-agent research system's "the plan must survive truncation" lesson
   (`memory-architecture-research.md` §3).
4. DeepSeek-specific workarounds already in place (20-tool trim, tool-definition stripping after the
   first tool round, retry-without-tools fallback — `audit-context-anatomy.md` §2) still function
   correctly and are undisturbed by any of the above changes.

Repeatable: fix this as a regression script — same seed conversation, same planted fact, same
compaction-triggering padding — re-run after every future prompt or compaction change, not just once.
