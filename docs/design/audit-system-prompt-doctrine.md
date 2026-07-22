# Audit: does the live Sage system prompt meet the "doctrine prompt" standard?

Read-only audit. Standard being audited against (founder's ruling): a great
agent system prompt (1) states the agent's FIRST PRIORITY plainly up front,
(2) maps every subsystem by PURPOSE — why memory, why tasks, why schedules,
why skills, why MCP apps, why channels — with when-to-use triggers, so the
model self-selects tools without the owner ever naming one, (3) avoids
scripting ("when X do Y" recipes) and instruction piles.

Every claim below is file:line-verified against the code as committed at the
time of this audit (`86d1f94c5` skill-catalog unification is already merged;
`bounded_scheduler_service.py`, `direct_chat_generation_service.py`, and a
prospective `project_tasks_service.py` had no in-flight uncommitted changes
when this audit ran, so nothing here was invalidated mid-read).

---

## 0. Two different prompts, not one

The single biggest structural fact this audit turned up: **master/operator
(Sage) turns and specialist-agent turns get two different, differently-built
prompts.** Everything below is organized around that split because scoring
them as one prompt would hide the gap between them.

- Master (Sage, `agent_install_id` empty): `server_modules/sage_agent_runtime_service.py:4338-4343` builds the prompt from `instruction_bundle.system_prompt` (the full compiler output — kernel, policy, capability manifest, memory, channel) plus guardrails.
- Specialist (a fleet agent, `agent_install_id` set, cloud mode): `server_modules/sage_agent_runtime_service.py:4275-4337` builds `_specialist_system_prompt` from the specialist's own persona plus four short rules and a raw memory dump — **the capability manifest is never included** (see §3).
- Specialist in `local` or `cli_subscription` mode short-circuits the whole action loop and never reaches `_run_sage_action_loop_v3` at all (`sage_agent_runtime_service.py:4353-4423`, `4433-4520`).

---

## 1. Assembly path (file:line)

1. **Compiler** — `server_modules/sage_instruction_compiler_service.py:622` `build_sage_instruction_bundle()` is the only function that assembles the budgeted, ordered core of the prompt. It is called from two sites in `sage_agent_runtime_service.py`:
   - `:4152-4170` — the live call inside `handle_sage_chat`, wrapped in a `try/except` that falls back to a two-line stub prompt on any exception (`:4171-4185`, `"You are Sage, a helpful AI assistant."` — no kernel, no memory rule, no capabilities, nothing).
   - `:3363-3410` — a second, older call site inside a function that is documented as **dead** (`:3210-3216`: "`_run_sage_action_loop_v2`... were removed here — dead code with zero live callers"); the surrounding function at `:3363` is not reachable from `handle_sage_chat`. Not part of the live path.
2. **Runtime appends (master/Sage path)** — `sage_agent_runtime_service.py:4338-4343`: `envelope["system_prompt"] = f"{instruction_bundle.system_prompt.rstrip()}{_audience_instructions}{sage_surface_guardrails}{_channel_action_honesty_rule}{attachment_context}{mcp_tool_inventory}"`.
3. **Runtime appends (specialist path)** — `:4332`: `_specialist_system_prompt = f"{_spec_persona}{_spec_scope_rule}{_spec_intro_rule}{_spec_honesty_rule}{_channel_action_honesty_rule}{_spec_memory_block}{_audience_instructions}{attachment_context}{mcp_tool_inventory}"` — no `instruction_bundle.system_prompt` at all, so no kernel/policy/capability-manifest/root-memory content reaches a specialist.
4. **Action-loop appends (both paths, cloud mode only)** — inside `_run_sage_action_loop_v3` (`:2755`): hardware paired/not-paired block (`:2858-2875`, unconditional every turn), specialist bound-connector honesty block (`:2885-2901`, specialist-only), blocked-tool context (`:2910-2925`, conditional).
5. **Dispatch** — the final `system_prompt` string is handed to `direct_chat_generation_service.stream_provider_backed_direct_chat` (`sage_agent_runtime_service.py:3128`, `direct_chat_generation_service.py:1073`), which appends at most one more thing: a reasoning-effort instruction for models with no native reasoning param (`direct_chat_generation_service.py:1181-1184`). No further section assembly happens there.
6. **Native tools are a separate channel from the system-prompt text.** `_direct_tool_bundle` (`sage_agent_runtime_service.py:2346-2429`) builds the actual callable-function list handed to the provider API. This is a two-tier design (`server_modules/tool_registry_service.py:1-19`): 12 "always-on" tools (`ALWAYS_ON_TOOL_NAMES`, `tool_registry_service.py:34-47`: `task_complete, update_plan, memory_write, memory_read, memory_search, memory_get, memory_update, memory_append_daily_note, web__search, web__fetch, hardware__action, query_tool_registry`) are injected as real function schemas every turn; everything else (skill_invoke, browser__*, generate_image, http_request, llm__task, sage_service__*, all connector actions, all MCP tools) lives in a lazy BM25-searched registry (`tool_registry_service.py:276-336`) the model must pull via `query_tool_registry`. Fleet tools (`fleet__*`) are the one exception — unconditionally added to the master/Sage native tool list outside the always-on set (`sage_agent_runtime_service.py:2362-2386`), never given to specialists.
   - The comment at `sage_agent_runtime_service.py:2360` says "8 core tools"; the actual `ALWAYS_ON_TOOL_NAMES` set has 12 entries (`tool_registry_service.py:34-47`) — stale comment, minor but real.

---

## 2. The prompt as the model sees it

### 2a. Master/Sage path — ordered sections + est. tokens

Token estimates use the repo's own convention, `chars/4` (`sage_instruction_compiler_service.py:88-92`). Sections 1-8 share one hard budget, `SAGE_SYSTEM_CONTEXT_CHAR_BUDGET_DEFAULT = 12,000` chars ≈ **3,000 tokens** (`:50`, override via `EMPYRALIS_SAGE_SYSTEM_CONTEXT_CHAR_BUDGET`, `:103-107`), consumed **in this fixed order, first-come-first-served** (`append_section`, `:663-677`) — later sections get whatever's left, not a fair share.

| # | Section | Source (file:line) | Always present? | Typical size |
|---|---|---|---|---|
| 1 | Kernel ("You are operating inside Empyralis..." + Durable Memory Rule) | `:472-524`, appended `:679-687` | Yes | 1,853 chars / ~463 tok (measured) |
| 2 | Policy context (tier + platform rules) | `agent_policy_context.py:108-161`, appended `:691-692` | Yes | ~500-700 chars / ~150 tok |
| 3 | Capability manifest ("## Callable Tools", ≤16 lines) | `:425-463`, appended `:693` | Yes if any capability ready | ~2,500-3,000 chars / ~650-750 tok |
| 4 | Root memory brief (SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS/MEMORY.md index) | `:271-386`, wrapped `:694-700` | If any root file has content | **Shares the remaining ~6,700 chars with #5-8** |
| 5 | User profile | appended `:701-706`, capped `SAGE_PROFILE_CONTEXT_CHAR_LIMIT=1,500` (`:52`) | If profile_context set | ≤1,500 chars / ≤375 tok |
| 6 | Workspace state | appended `:707-714`, capped `SAGE_HEARTBEAT_CONTEXT_CHAR_LIMIT=900` (`:53`) | **Only if message matches `_message_needs_workspace_state` keywords** (`:562-580`) | 0 or ≤900 chars |
| 7 | Retrieved memory (search results) | appended `:715-724`, capped `SAGE_RETRIEVED_MEMORY_CHAR_LIMIT=3,000` (`:51`) | **Only if message matches `_message_needs_memory_context` keywords** (`:527-559`) | 0 or ≤3,000 chars |
| 8 | Channel context + sender identity | `:726-748` | Yes if `channel_origin` set | ~100-250 chars |
| 9 | "Ongoing Conversation" note | `:753-771` | If prior_messages present | ~300-600 chars |
| — | *(budget boundary — everything below is unbounded, appended by the runtime, not the compiler)* | | | |
| 10 | "## Who you are" / "## How you respond" persona+style guardrails | `sage_agent_runtime_service.py:4203-4242` | Yes | 2,539 chars / ~634 tok (measured) |
| 11 | Channel action honesty rule | `:4257-4267` | Yes | 485 chars / ~121 tok (measured) |
| 12 | Attachment context | `:3921` (`_load_attachment_context`) | If attachments | Variable, 0 typical |
| 13 | MCP tool inventory | `:1517-1561` | If enabled MCP tools exist | Variable, 0 typical, else ~80-120 chars/tool |
| 14 | Audience session rules | `audience_tool_filter.py:133-149` | Only for non-owner sender | 0 for owner turns, ~600 chars otherwise |
| 15 | Hardware paired/not-paired block | `_run_sage_action_loop_v3:2858-2875` | **Always, unconditional** | 207 chars (paired) or 508 chars (not paired) — measured |
| 16 | Bound-connector honesty block | `:2885-2901` | Specialist only (master never sees it) | 0 for master |
| 17 | Blocked-tool context | `:2910-2925` | Only if a computer-tool call was blocked this turn | 0 typical |

**Total for a typical master turn:** ~12,000 chars (compiler budget, often under-filled) + ~2,539 + ~485 + ~127 (hardware) ≈ **3,000-3,800 tokens of system-prompt text**, before the native tool schemas (12 always-on + up to 6 fleet tools) and before `prior_messages` (up to 16 messages, `:588`, each capped 4,000 chars, `:606`) are added as actual chat turns.

### 2b. Specialist path — ordered sections

No compiler call, no budget, no cap at all (`:4332`):

| # | Section | Source | Notes |
|---|---|---|---|
| 1 | Specialist persona | operator-authored, length unknown/unbounded | Replaces the kernel entirely |
| 2 | "## Scope" (not-an-operator rule) | `:4281-4287`, ~280 chars | |
| 3 | "## First message" intro rule | `:4291-4296`, ~230 chars | |
| 4 | "## Tool honesty" | `:4314-4327`, 608 chars (measured) | |
| 5 | Channel action honesty rule | `:4257-4267`, 485 chars | shared with master |
| 6 | "## Your memory" | `:4331`: `f"\n\n## Your memory\n{memory_context}"` | **Raw dump, zero rule attached** — no write-trigger, no read-trigger, nothing telling the specialist a `memory_write` tool exists or why to use it |
| 7 | Audience rules / attachment / MCP inventory | conditional, same as master | |
| 8 | Hardware block, bound-connector block | `_run_sage_action_loop_v3`, same as master (cloud mode only) | |

**Missing entirely versus master:** kernel/Durable-Memory-Rule, policy context (tier + platform rules), and — critically — **the entire capability manifest** (`_capability_manifest_text`). A specialist's system prompt never contains a "## Callable Tools" section. Its only knowledge of what it can do comes from (a) its persona text (operator-authored, may or may not mention tools) and (b) whatever native tool schemas happen to be in its filtered toolset (`_specialist_tool_allowed`, `:2394`) — with no narrative "why" or "when" for any of them.

---

## 3. Doctrine scorecard

| Subsystem | WHY present in the prompt? | When-to-use trigger present? | Verdict |
|---|---|---|---|
| **First priority statement** | **No.** No string matching "first priority", "your job is", "primary goal" etc. exists anywhere in `sage_instruction_compiler_service.py`, `sage_agent_runtime_service.py`, or `agent_policy_context.py` (grep confirmed, zero hits). The closest substitute is tone-setting persona copy ("you don't wait to be micromanaged", `:4211-4212`) — not a priority statement. | N/A | **FAIL.** Doctrine item (1) is unmet for both paths. |
| **Memory (master)** | Partial. The kernel's "Durable Memory Rule" (`:479-509`) ends with a real why — "everything the user tells you is lost when the session ends" (`:507-508`) — but it's buried after two pages of procedural steps. The root-memory-brief's one-line framing ("Sage's durable personal behavior layer", `:698`) never explains what SOUL vs IDENTITY vs USER vs GOALS vs AGENTS vs TOOLS *each* mean — that explanation exists only as a **Python comment** (`:34-42`), never surfaced to the model. | Yes, but scripted: "After every user message, silently check..." (`:483-484`), explicit trigger phrase lists for both write and read (`:484-485`, `:492-495`) — a textbook "when X do Y" recipe, the exact anti-pattern named in the doctrine. | **PARTIAL.** Functional but scripted, and the file-purpose taxonomy that exists in code comments never reaches the model. |
| **Memory (specialist)** | **None.** `:4331` is a bare content dump with no rule attached at all. | **None.** | **FAIL** for the specialist path specifically. |
| **Tasks/plan (`update_plan`)** | Decent, self-contained in the tool's own schema description (`skills_service.py:686-696`): explains the lay-out-then-update pattern and explicitly says when *not* to use it ("For a simple single-step request, don't use this tool"). | Yes, in the tool description itself. | **PASS at the tool-schema level**, but this description is invisible in the "## Callable Tools" prompt text for most workspaces — see the 140-char truncation finding below — and there is no system-prompt-level framing of why having a visible plan matters (e.g., to the user, across turns). No `project_tasks_service.py` exists yet in this snapshot to check for cross-turn task memory. |
| **Scheduler / self-wakeups** | **Absent.** The model is never told a scheduler exists, that it persists work across turns, or that it can propose its own future wakeup. | **Absent.** | **FAIL — see §4, this is the sharpest single gap.** |
| **Skills (`skill_invoke`)** | Reasonable at the tool-schema level (`skills_service.py:1459-1466`: explicitly frames it as "the Level-2 step after the skill catalog's name+description listing", names the progressive-disclosure model). | Yes, in schema. But `skill_invoke` is **not** an always-on tool (`tool_registry_service.py:34-47` excludes it) and named skills are **not** indexed in the Tier-2 BM25 registry (`tool_registry_service.py:276-336` iterates only `_builtin_tool_descriptors()`/local tools/connector tools/MCP tools — never `skill_registry.list_skill_definitions()`). The **only** channel through which a named skill's existence ever reaches the model is the capped 6-slot manifest text — see §4. | **PARTIAL, capacity-constrained** — good design intent, bad budget. |
| **MCP tools/connectors** | Good, purpose-built patch: `_build_mcp_tool_inventory` (`:1517-1561`) explicitly states these are "real, callable tools like any other" and tells the model to use `query_tool_registry` to get schemas. | Yes. | **PASS** — but only for the master path; specialists get the same block (`:4332` includes `mcp_tool_inventory`), so this is the one subsystem specialists aren't shortchanged on. |
| **Channels** | Reasonable — states current channel, per-channel formatting hints (`CHANNEL_FORMAT_HINTS`, `:613-620`), sender identity, and cross-channel "[via ...]" tagging convention (`:754-770`). | Yes, functional. | **PASS**, best-covered subsystem in the whole prompt. |
| **Tool discovery (`query_tool_registry`)** | Best-designed piece of the entire prompt. Its own schema description (`skills_service.py:1251-1257`, `tool_registry_service.py:571-578`) states exactly why and when to call it — "Call this when you need to perform an action... but do not see the relevant tool" — and it is always-on, so this WHY is never truncated or crowded out. | Yes. | **PASS**, the one subsystem that actually matches the doctrine's "map by purpose, self-select without being told" standard. |

---

## 4. The gaps, ranked

1. **The scheduler doesn't exist to the model, at all — on either path.** `bounded_scheduler_service.propose_self_wakeup` (`bounded_scheduler_service.py:635-715`) has exactly two callers: `fleet_tools.schedule_task` (`fleet_tools.py:2325-2410`, itself only reachable via the master-only `fleet__schedule_task` native tool, `skills_service.py:1415-1444`) and a human-facing REST endpoint, `POST /agent-registry/scheduler/self-wakeups` (`agent_registry_api.py:1615-1649`) gated behind `member_dependency` auth — not a model tool at all. `fleet__schedule_task`'s own description (`skills_service.py:1420-1424`) and `audience_note` (`:1443`, "Operator-only: schedules future work for an agent") frame it purely as fleet management of *other* agents; nothing in its text suggests the calling agent can schedule *itself*. Specialists never even get this tool (`sage_agent_runtime_service.py:2362-2386` — fleet tools are added only when `specialist_toolset is None`). Net effect: neither the master agent nor any specialist is ever told "you can wake yourself up later" — a real, live, wired capability that is 100% invisible in the prompt.
2. **Named skills are capacity-starved to the point of being mostly invisible, and the starvation is structural, not incidental.** `CAPABILITY_MANIFEST_SKILL_RESERVED_ITEMS = 6` (`sage_instruction_compiler_service.py:64`) out of `CAPABILITY_MANIFEST_MAX_ITEMS = 16` (`:54`). ~20 built-in `SkillDefinition`s exist (`skill_registry.py:780-1030`), and 6 bundled filesystem skills ship with the platform (`skills/` — `business-skill-template`, `code-runner`, `file-manager`, `memory-manager`, `telegram-bot`, `vision-monitor`) that overwrite same-named built-ins and are sorted to win the reserved slots ahead of built-ins (`sage_instruction_compiler_service.py:435-445`, `:446`). `business-skill-template` ships `enabled: false` in its frontmatter (`skills/business-skill-template/SKILL.md:1-8`) so it self-excludes, leaving 5 bundled skills that structurally consume 5 of the 6 reserved slots on every workspace, every turn — before a single workspace-authored skill is considered. The ~14-15 remaining pure built-ins (`crm-notes`, `task-runner`, `inventory-tool`, all five `fleet-*` skill duplicates, `memory-read/write/list`) are crowded into the last 1 slot, alphabetically. And — confirmed by reading `tool_registry_service.py:276-336` directly — named skills have **no fallback discovery path**: they are not in the Tier-2 BM25 registry the way every other tool is, so a skill that misses the 6-slot cut is not just de-prioritized, it is unreachable this turn by any means.
3. **The 140-char manifest truncation quietly destroys most of the "why/when" text the developers actually wrote.** `CAPABILITY_DESCRIPTION_CHAR_LIMIT = 140` (`sage_instruction_compiler_service.py:65`, applied at `:452-456`). `update_plan`'s real description is ~350 chars of genuinely good guidance (`skills_service.py:690-696`); the prompt text shows ~140 of it. This matters less for always-on tools (their full schema still reaches the model natively, `tool_registry_service.py:527-562`) but matters a great deal for the 10 "other" manifest slots and the 6 skill slots, where the truncated one-liner is the *only* text the model ever sees before deciding whether to reach for that capability at all.
4. **No first-priority statement, anywhere, on either path.** Confirmed by direct grep across all three assembly files — zero matches for any phrasing that states what the agent's job actually is before anything else. The prompt opens with a scene-setting sentence ("You are operating inside Empyralis, an environment connecting the user with AI, tools, files, memory, and apps," `:517-519` or `:522-523`) then goes straight into the memory-write recipe. Doctrine item (1) is unmet.
5. **Specialists — the actual customer-facing fleet agents — get a structurally worse prompt than the master.** No kernel, no policy context, no capability manifest at all (`:4332` vs `:4342`), and a bare, ruleless memory dump (`:4331`). Everything scored above as "PARTIAL" for master is closer to "absent" for specialists. Given specialists are the fleet-agent product surface (Telegram/WhatsApp/Discord bots, per `docs/design/project_cross_channel_and_multiagent_requirements` in memory), this is the higher-traffic path, not an edge case.
6. **Heavy reliance on brittle keyword-gating to decide what the model even gets to see**, which is itself a scripted, recipe-driven design smell even though it's in Python rather than prompt text: `_message_needs_memory_context` (`:527-559`) and `_message_needs_workspace_state` (`:562-580`) are hard keyword-match lists that gate whether retrieved memory / workspace state are shown at all — a user who needs recall but doesn't use one of the ~25 magic words gets nothing prepended, even though the memory_search/memory_read *tools* remain callable regardless. This doesn't block the tools, but it does mean the doctrine's "map by purpose so the model self-selects" partially degrades into "the platform pre-decides for the model via string matching."
7. **The shared 12,000-char budget is consumed in a fixed, non-negotiable order that structurally starves the customer's own memory content.** Kernel (fixed boilerplate) + policy (fixed boilerplate) + capability manifest (fixed boilerplate) are appended first and are non-negotiable (`append_section` order, `:679-748`); the customer's actual SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS/MEMORY.md content — the entire point of the "memory" pitch — gets whatever's left, commonly under half the budget on a workspace with several ready capabilities. This is a resource-allocation problem, not strictly a "doctrine" wording problem, but it directly undercuts subsystem #1 (memory) getting a fair hearing.
8. **`memory_update` is a live, always-on, natively callable tool (`ALWAYS_ON_TOOL_NAMES`, `tool_registry_service.py:41`) that the manifest text is deliberately told to hide** (`MODEL_HIDDEN_LEGACY_TOOLS = {"memory_update"}`, `sage_instruction_compiler_service.py:67`, applied `:408-409`). Likely an intentional nudge toward `memory_write`/`memory_stage_edit`, but it means the manifest text and the real tool list disagree by design — worth a one-line note in the compiler so a future reader doesn't mistake it for a bug.

---

## 5. Smallest set of changes to reach doctrine-grade

Ordered by leverage, not by effort:

1. **Tell the model the scheduler exists.** Add one paragraph to the kernel prompt (master) and to the specialist assembly (`:4332`) stating: a scheduler exists, the agent can propose its own future wakeup via `fleet__schedule_task` when `agent_id` is its own install id (or — better — expose a dedicated always-on `propose_self_wakeup` tool wrapping `bounded_scheduler_service.propose_self_wakeup` directly, worded for self-scheduling, not fleet management), and *why* it matters (continuity across turns without the user having to re-prompt). This is the highest-leverage single fix — it's a real, live, already-built capability with zero prompt cost paid so far.
2. **Give specialists the capability manifest.** Thread `_capability_manifest_text(capability_manifest)` (already computed once per turn regardless, `sage_instruction_compiler_service.py:652`) into the specialist branch at `:4332` instead of omitting it. This single change closes gap #5 without touching the compiler at all.
3. **Add one first-priority sentence to the kernel.** One line, before the memory rule: state plainly what the agent's job is this turn (help the user directly, use tools when they help, ask when genuinely blocked) — mirroring what Claude Code states up front. Cheap, high doctrine compliance.
4. **Raise or make dynamic the skill reservation, and/or give named skills a Tier-2 discovery path.** Either (a) index `skill_registry.list_skill_definitions()` entries into `tool_registry_service.build_registry_entries()` so a skill that misses the manifest cut is still BM25-discoverable via `query_tool_registry` (closes the "unreachable, not just deprioritized" half of gap #2 with no prompt-budget cost), or (b) scale `CAPABILITY_MANIFEST_SKILL_RESERVED_ITEMS` with context window the way the code's own comment (`:58-63`) already says is the intended follow-up.
5. **Replace the "silently check every message" memory recipe with a why-first framing**, keeping the mechanical write/read instructions but leading with the reason (durability across sessions) instead of burying it at the very end (`:507-508` moved to the top of the block). Also surface the per-file taxonomy comment (`:34-42`) as one line in the root-memory-brief header (`:698`) instead of leaving it as a Python-only comment.
6. **Stop truncating tool descriptions to 140 chars for the skill and "other" slots specifically** (or raise the limit for those two categories only) — the always-on tools don't need it since their real schema is unaffected, but the manifest text is the *only* text a crowded-out or Tier-2-only capability gets.

---

## Appendix: files read for this audit

`server_modules/sage_instruction_compiler_service.py` (full, 812 lines), `server_modules/sage_agent_runtime_service.py` (~1,700 of 5,533 lines, all prompt-assembly-relevant regions), `server_modules/direct_chat_generation_service.py` (~250 lines, tool-normalization and system-prompt consumption regions), `server_modules/skills_service.py` (~1,300 lines, all `_builtin_tool_descriptors` entries + capability-record builders), `server_modules/sage_skills_api.py` (full, 500 lines), `server_modules/skill_registry.py` (~400 lines, built-in skill catalog + merge logic), `server_modules/installed_skills.py` (~150 lines, bundled-skill scanning), `server_modules/bounded_scheduler_service.py` (~250 lines, `propose_self_wakeup` and callers), `server_modules/fleet_tools.py` (~100 lines, `schedule_task`), `server_modules/agent_registry_api.py` (~60 lines, the self-wakeup REST endpoint), `server_modules/agent_policy_context.py` (full, 218 lines), `server_modules/tool_registry_service.py` (~300 lines, always-on/registry split), `server_modules/audience_tool_filter.py` (~20 lines), `skills/business-skill-template/SKILL.md` frontmatter, plus `git log`/`git status` to confirm the skill-catalog-unification commit (`86d1f94c5`) is already merged and no other in-flight edits from the concurrent session were present at read time.
