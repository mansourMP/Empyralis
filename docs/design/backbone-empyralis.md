# Agent Backbone Inventory — Empyralis vs. Claude Code / Codex / Cursor

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers (e.g. `"sage-main"`, `sage_agent_runtime_service.py`) are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

Read-only inventory, file:line-cited against `/Users/mansur/empyralis` on
branch `main` (2026-07-22). Builds on and extends, does not repeat, two prior
docs — read those first for the material this doc doesn't re-derive:

- `docs/design/gap-ai-operation.md` (2026-07-21) — the agent-loop, tool-use,
  memory, envelope, streaming, autonomy, and multi-agent gap audit vs
  OpenClaw. Most-cited source below.
- `docs/design/memory-context-design.md` — memory/context assembly vs Claude
  Code's own documented model. Second-most-cited source below.

**Important freshness note, verified against `git log` during this pass**:
three of the P0 findings in `gap-ai-operation.md` / `memory-context-design.md`
were fixed in commits that landed at the *exact same timestamp*
(`2026-07-22 01:30:18 +0800`), i.e. after those docs were written and before
this one:

| Commit | Fixes |
|---|---|
| `63f8bc4c6` "fix(agent-core): restore multi-round tool use for non-deepseek providers" | gap-ai-operation.md gap #1 — the unconditional tool-strip bug |
| `6628546ee` "fix(agent-context): always inject SOUL/AGENTS/TOOLS on the main chat path" | memory-context-design.md C5 / E1 — the Pipeline-B content blackout |
| `01b5bcb55` "fix(memory): redact secrets before memory_write persists them" | gap-ai-operation.md gap #2 — no save-filter on `memory_write` |

This doc verifies and cites the **current, post-fix** state of those three
items directly rather than repeating the now-stale "still broken" framing.
Everything below was re-checked against the live file contents during this
pass, not copied from the prior docs.

---

## 1. Context-window management

**How a turn's context gets assembled, in order**, both chat surfaces
(`sage_chat` → `sage_agent_runtime_service._run_sage_action_loop_v3`,
`sage_agent_runtime_service.py:2755`; `direct_chat` →
`direct_chat_generation_service.stream_provider_backed_direct_chat`):

1. System prompt built by `sage_instruction_compiler_service.build_sage_instruction_bundle`
   (`sage_instruction_compiler_service.py:596`) — kernel prompt + always-load
   instruction tier + `MEMORY.md` index + capability manifest + audience
   instructions, all f-string concatenation (see §3 below — no cache split).
2. Prior turns (`prior_messages`/`channel_prior_messages`), pre-clipped by
   compaction if the proactive check trips (§2).
3. Tools — **presented lazily, two-tier, on both surfaces** (same underlying
   code): `direct_chat_tool_catalog_service.build_always_on_direct_chat_tools()`
   (`direct_chat_tool_catalog_service.py:145-151`) returns 11 always-on tools
   (`tool_registry_service.py:26-38`: `task_complete`, 5 memory tools,
   `web__search`, `web__fetch`, `hardware__action`, `query_tool_registry`,
   ~800 tokens per the module's own docstring). Everything else — 40+ built-in
   tools, local tools, and every connected-app tool — lives in a registry
   (`tool_registry_service.build_registry_entries`, `tool_registry_service.py:170-208`)
   searched only on demand.
4. Master Sage additionally always gets fleet-management tools injected
   directly (not through the lazy path) when running as itself, not a
   specialist — `sage_agent_runtime_service.py:2338-2361` (comment: a
   specialist "must never see these at all," enforced by simply not adding
   them for `specialist_toolset is not None`, plus a second runtime-side gate).

**`query_tool_registry` mechanism** (`tool_registry_service.py:370-407`,
handler at `direct_chat_generation_service.py:1486-1561`): the model calls it
with a free-text `task_description`; `search_tool_registry`
(`tool_registry_service.py:213-265`) does **plain keyword-overlap scoring**
(token membership in each entry's extracted keyword set, partial-substring
credit at 0.5) — not semantic/embedding search — and returns the top 3-5
(default)/10 (max) matched tool schemas, appended to the live `tools` list
for **subsequent iterations within the same turn**. A `_credential_note` is
attached per-result if the matched tool's connector isn't authenticated
(`tool_registry_service.py:268-322`).

**Verified current (post-`63f8bc4c6`) — the lazy-discovery loop now actually
works** for every provider except DeepSeek. The strip-after-first-tool-call
logic that used to erase the `tools` array unconditionally after iteration 0
(making a `query_tool_registry`-discovered tool un-callable in the same turn)
is now scoped to `_TOOL_STRIP_REQUIRED_PROVIDERS = {"deepseek"}`
(`direct_chat_generation_service.py:245`, gate check at
`direct_chat_generation_service.py:1216`). For every other provider, tools
stay live across iterations, so `search → observe result → call the tool the
search surfaced` genuinely works within the 5-iteration budget. This closes
what the prior audit called "the two-tier design's entire value proposition
neutered" — that's no longer true as of this commit.

**Token budget: enforced, not just estimated-and-ignored.** Two enforcement
points, both real:
- **Proactive preflight**, `sage_agent_runtime_service.py:4956-5049`: before
  the LLM call, `compaction_service.estimate_tokens()` (4-chars/token
  heuristic, `compaction_service.py:21-23`) sums system prompt + user message
  + all prior-message content, adds `COMPACTION_RESERVE_TOKENS=16384`, and
  compares against the resolved context window. If over, it compacts (or, for
  a `context_policy.on_context_full == "fresh_session"` agent, opens a fresh
  session instead — `_apply_fresh_session_context_policy`) **before** the call
  ever goes out.
- **Reactive retry**, `sage_agent_runtime_service.py:5089-5233`: if the
  provider itself throws a context-overflow error
  (`compaction_service.is_context_overflow_error`, keyword-matched against
  the exception message, `compaction_service.py:293-306`), compact and retry,
  capped at `_MAX_COMPACTION_RETRIES = 2` (`sage_agent_runtime_service.py:4952`).

**Per-agent context ceiling**: `context_policy.max_context_tokens`, read per
specialist/master install (`sage_agent_runtime_service.py:3785-3822`),
clamped against `compaction_service.DEFAULT_CONTEXT_WINDOW` (128K) as a sane
default when unset — explicitly reasoned in the code comment as a
reliability guard against silently inheriting a raw 1M+ window
(`sage_agent_runtime_service.py:3785-3796`).

**Gap vs. Claude Code / Codex / Cursor:**
- No budget is ever **shown** to the user — confirmed no frontend surface
  references a context-window/token-budget indicator (`grep` across
  `frontend/src` for `context window`/`tokens remaining`/`contextWindow`
  returned nothing). Claude Code's own context-window page gives the user an
  explicit token-by-token breakdown; Empyralis's accounting
  (`usage_accounting_service.py`) is billing-facing only, not context-facing.
- Tool discovery is keyword substring/overlap, not semantic — same caveat
  the prior docs already flagged for memory search applies here too; a task
  description that doesn't share literal tokens with a tool's
  name/description/connector_id won't surface it.
- The 5-iteration hard cap (`_SAGE_OPERATOR_LOOP_MAX_ITERATIONS = 5`,
  `sage_agent_runtime_service.py:146`) is a much smaller ceiling than Claude
  Code's or OpenClaw's effectively-unbounded-until-natural-stop loop (see
  gap-ai-operation.md §1) — the fix above makes what happens *inside* those 5
  iterations honest, it doesn't raise the ceiling itself.

---

## 2. Context compaction

Real LLM summarization exists and is wired into both the proactive and
reactive paths above — this is **already at genuine parity with OpenClaw**
per the prior audit's own finding (identical `16384`-token reserve constant,
independently arrived at), reconfirmed here against current code:

- `compaction_service.py` (306 lines): `estimate_tokens()` (4 chars/token,
  line 21-23), `should_compact()` (line 71-86, `total > window - reserve`),
  `find_cut_point()` (line 89-112, walks backward from newest turn until
  `keep_recent_tokens` is covered, returns the index to keep from), tool-result
  pruning at write time (`prune_tool_result`, line 117-135, caps at
  `TOOL_RESULT_MAX_CHARS = 2000`), `serialize_turns_for_compaction()` (line
  140-170, structured `[User]:`/`[Assistant]:`/`[Tool result: name]:` text —
  explicitly *not* chat-message format, "prevents the model from treating it
  as a chat to continue," matching OpenClaw's `serializeConversation()`), and
  `compact_turns()` (line 189-239) — calls `openai_chat_text()` with a fixed
  `COMPACTION_PROMPT` (line 173-186: preserve decisions/preferences/active
  tasks/pending items/facts, "do not invent, do not speculate"), persists the
  result as a `role="compaction_summary"` row in `agent_turns`.
- `keep_recent_tokens_for_window()` (line 46-53): ~15% of context window,
  capped at `_KEEP_RECENT_CAP = 30000` — proportional, unlike OpenClaw's flat
  `20000`.
- **Fires proactively, before the call is even attempted** — not just
  reactively after a provider error — via the preflight described in §1
  (`sage_agent_runtime_service.py:4956-5049`). A separate reactive path
  (`sage_agent_runtime_service.py:5089-5233`) is a backstop for whatever the
  proactive estimate missed, capped at 2 retries.
- A dedicated **memory-flush turn runs before compaction**,
  `_run_memory_flush_before_compaction` (referenced at
  `sage_agent_runtime_service.py:5007-5013` and `5075-5081`) — an explicit LLM
  pass whose job is to call `memory_write` on anything worth keeping before
  the turns get summarized/discarded, so compaction can't silently destroy an
  unrecorded fact. If the flush fails, proactive compaction is skipped
  entirely and the raw turns are left alone (`sage_agent_runtime_service.py:5078-5081`)
  rather than risk data loss.
- `/compact` exists as an explicit user-invoked command too (per prior audit,
  `sage_command_dispatcher.py:322-364` — not re-verified line-for-line in
  this pass, cited from `gap-ai-operation.md` §3).

**Channel-turn-specific correctness detail** (worth flagging as a real
subtlety, not a gap): for a turn carrying `channel_prior_messages` (i.e., not
routed through the control-plane `thread_service`), compaction operates
directly on the in-memory list via `find_cut_point` rather than round-tripping
through `thread_service.get_thread` — the code comments
(`sage_agent_runtime_service.py:5014-5039`) explain this is deliberate: that
thread-service path is dead for these turns under SQLite-fallback prod and
would either silently wipe or cross-contaminate `prior_messages` if used. This
is plain truncation for that path (no LLM summary), not full compaction —
correctly scoped as "strictly safer than a wipe," per the comment, given
`agent_conversation_memory`'s own separate retention already bounds it.

**Gap vs. Claude Code:** Claude Code's compaction is "safe by construction"
— memory lives on disk outside the transcript being summarized, so
compaction never needs a pre-flush step. Empyralis's memory-flush-turn
achieves the same safety property but with an extra LLM call at compaction
time rather than by architecture (this exact framing is already in
`memory-context-design.md` C9 — still accurate, re-confirmed).

---

## 3. Prompt caching

**Confirmed absent — no explicit provider prompt-cache boundary anywhere.**
Re-verified fresh this pass:

- No `cache_control` field, `ephemeral` cache marker, or `anthropic-beta`
  prompt-caching header anywhere in `server_modules/*.py` or `scripts/*.py`
  in an outbound-request-building context — the only `cache_control` hit in
  the whole tree is `scripts/deepseek_anthropic_proxy.py:95`, which *reads*
  an inbound `cache_control` field off an image block to decide a `detail`
  level, unrelated to prompt caching.
- What *does* exist is purely **passive accounting** of cache tokens a
  provider reports back: `usage_accounting_service.py:642-665` parses
  `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` out of a provider's
  usage payload (for pricing), and `pricing_registry_service.py:17-19` has
  `cached_input_usd_per_million`/`cache_write_usd_per_million`/
  `cache_read_usd_per_million` rate fields. This reflects some providers'
  *automatic* server-side caching (e.g., a provider that caches identical
  prefixes without any client-side opt-in) — it is not evidence Empyralis
  requests or shapes prompts for caching.
- System-prompt assembly is plain f-string concatenation, confirmed at
  `sage_instruction_compiler_service.build_sage_instruction_bundle`
  (`sage_instruction_compiler_service.py:596`) — persona/kernel-prompt/
  instruction-tier/memory-index/capability-manifest/audience-instructions all
  merged into one string with no stable/dynamic split or boundary marker.

This directly confirms `memory-context-design.md` B3/E8's flag (stretch,
lowest-priority item) and `gap-ai-operation.md` gap #11 — both already
correctly identified this as absent; nothing has changed here since those
docs were written. **Single biggest gap on this axis**: zero cost/latency
benefit from repeated-prefix reuse on any provider, on any surface, though
this is explicitly the lowest-leverage item across both prior audits (only
pays off once system-prompt size and call volume justify the engineering
cost).

---

## 4. Skills

Real infrastructure exists — a genuine `SKILL.md`-with-frontmatter,
progressive-disclosure-*shaped* system — but it is effectively **inert on the
live agent-turn path today**. Two layers:

**Layer 1 — `skill_registry.py` (1306 lines): a curated catalog of
built-in/business skills**, each a `SkillDefinition` (`skill_registry.py:31-49`)
with `id`, `label`, `description`, `execution_mode`
(`hosted_secure`/`local_secure`/`privileged_device`), `action_class`
(`read`/`write`/`execute`), `skill_class` (`system`/`business`/
`specialist_local`), and an optional `executor` callback. Dispatch
(`execute_skill`, `skill_registry.py:1188-1300`) tries, in order: a real
`executor` callback → a `handler.py`-backed adapter
(`_execute_handler_skill`) → an MCP-tool adapter
(`mcp_registry_service.invoke_workspace_mcp_skill_async`) → the
`_BUNDLED_SKILL_DISPATCH` map (6 skills, `skill_registry.py:332-339`, e.g.
`memory-manager` → `memory_update`, `code-runner` → `shell__exec`) → for
anything left with a real `definition.path`, load `<path>/SKILL.md` and
inject its raw text as a `"skill-context"` artifact
(`skill_registry.py:1278-1300`). This last branch is the actual
progressive-disclosure mechanic (name+description always resolvable via
`list_skill_definitions()`; full `SKILL.md` body loaded only when
`execute_skill` is actually invoked for that id) — structurally the right
shape.
- **How a live turn reaches `execute_skill` at all**: only one call site,
  `sage_agent_runtime_service.py:3338-3359`, and only for an
  `mcp_skill is not None` branch inside the MCP-compatibility path of
  `_run_sage_action_loop_v3` — a narrow legacy/compat lane, not the primary
  way tools are invoked (the primary way is the direct
  `tools`/`tool_calls` JSON-schema loop described in §1). `detect_skill_need()`
  (`skill_registry.py:1163-1184`) — the keyword-trigger router into this
  path — matches exactly **one** built-in skill today
  (`inventory-tool`, on tokens like "in stock"/"sku"/"fitment"/"wiper"), so
  in practice almost nothing routes through this lane.

**Layer 2 — `installed_skills.py` (949 lines): a directory-based, marketplace-shaped
skill-install system**, structurally the closest thing to Claude Code's
skills model. `skill_roots()` (`installed_skills.py:49-54`) defines three
tiers — `workspace` (`.orion-stack/skills/`), `global` (`~/.orion-stack/skills/`
or `$ORION_INSTALLED_SKILLS_DIR`), `bundled` (`<repo_root>/skills/`).
`list_installed_skills()` (`installed_skills.py:697-815`) scans all three,
parses `SKILL.md` frontmatter + optional `skill.json` manifest, runs a
security scanner (`skill_scanner.scan_skill_dir`) that can block a skill
outright on critical findings, resolves availability (missing binaries/env
vars/Python packages/unsupported OS), and returns each skill's full
`skill_body` (stripped-frontmatter `SKILL.md` content) alongside its
metadata. `build_active_skill_prompt_append()`
(`installed_skills.py:837-...`, capped at 12,000 chars via
`merge_skill_prompt_append`, line 826-834) would inject **every active
skill's full body** into the prompt — note: **not** progressive disclosure at
this layer (no name/description-only summary tier with lazy full-body load
gated on relevance/invocation; it's all-active-bodies-or-nothing, up to the
char cap).

**Confirmed dead on the live path — the single biggest finding on this
axis**: `bundled_skills_root()` resolves to `<repo_root>/skills/`, which
**does not exist anywhere in the checked-out tree** (`ls`/`find` both
confirm — no `skills/` directory at repo root). `workspace_skills_root()`
(`.orion-stack/skills/`) also does not exist (`.orion-stack/` has
`agent-computer`, `browser-profile`, `downloads`, `logs`, `memory`, `pdf`,
`pids` — no `skills`). So `list_installed_skills()` returns an empty list in
this environment today — **zero installed skills, bundled or workspace,
currently ship or run.** And even setting that aside,
`build_active_skill_prompt_append()` — the only function that would inject
skill content into a live prompt — has exactly one call site in the entire
codebase, `health_diagnostics.py:299`, a diagnostics/health-check context.
It is imported at `runtime_config.py:183` but never called there, and is
**never called anywhere in `sage_instruction_compiler_service.py` or
`sage_agent_runtime_service.py`** (confirmed by direct grep) — meaning even
if skills *were* installed on disk, nothing in the live turn path would ever
surface them to the model.

**No agent-authored skills.** Grepped for `create_skill`/`generate_skill`/
`skill_creator`/`save_skill`/`write_skill` across `server_modules/*.py` —
every hit is unrelated (a `skills_registry.py` schema field named
`skill.json.author`, comments referencing `_authority_mandate_gate`). There
is no tool an agent can call to write a new `SKILL.md` for itself, and no
scanning/promotion path that would turn a repeated task pattern into a
reusable skill file.

**Gap vs. Claude Code:** Claude Code's skills system is live, in-context
(name+description always visible, full body loads on invocation, capped at
5K tokens/skill / 25K total on compaction re-injection per
`memory-context-design.md` A3.6). Empyralis has the *pieces* of an
equivalent system — frontmatter parsing, security scanning, availability
resolution, a marketplace-shaped install registry — but as shipped today it
is **completely disconnected from the live agent turn**: no bundled content,
no workspace content, and the one function that would wire it into a prompt
is dead code outside a health-check endpoint. This is a bigger gap than
either prior doc flagged (neither doc audited this system at all — it's new
ground covered by this pass).

---

## 5. Sub-agents

**Only primitive: `fleet_message_agent`, and it is fire-and-forget with no
confirmed delivery — verified strictly weaker than the prior audit's own
hedged framing.**

`fleet_tools.fleet_message_agent()` (`fleet_tools.py:1564-1631`) appends
`{from_agent_id, message, enqueued_at, message_id}` to the target install's
`install_metadata.fleet_inbox`, capped at the last 20 entries
(`fleet_tools.py:1605`), and returns `{"ok": True, "status": "enqueued"}`
immediately — no blocking option, no `timeoutSeconds`, no completion
callback. `triage_service._enqueue_owner_notification()`
(`triage_service.py:311-359`) is the only other writer, using the identical
`fleet_inbox` pattern to escalate an out-of-scope message to the workspace
owner's Sage.

**New finding this pass, stronger than what either prior doc stated**: grepped
every `.py` file under `server_modules/` for `fleet_inbox` — there are
exactly **four hits total, all in the two writer functions above** (two in
`fleet_tools.py`, two in `triage_service.py`). **Nothing anywhere in the
codebase ever reads `install_metadata.fleet_inbox` back out.** The
`fleet_message_agent` docstring's own hedge — "The target agent's next turn
**may** read and process it" — is not just unconfirmed, it is currently
**false**: there is no code path, in `sage_agent_runtime_service.py` or
anywhere else, that loads a target install's `fleet_inbox` and surfaces it
into that agent's next turn's context. A message enqueued via
`fleet_message_agent` today is written to a JSON metadata field and never
read by anything. This is a stronger, more concrete version of gap #6 in
`gap-ai-operation.md` (which said "no confirmed... inbox-check mechanism
verified in this pass" — this pass confirms the negative outright).

**No ephemeral/spawned subagent concept.** `fleet_create_agent` (via
`fleet_tools.py`, confirmed tool list at `skills_service.py:1200-1332`)
creates persistent, named, workspace-visible Fleet agent installs — a
product object a human manages in the UI — not a disposable, task-scoped
session an agent can spin up mid-turn and later collect a result from.
`resolve_subagents_enabled()` (`fleet_tools.py:147-152`) is a permission
gate on whether `fleet_message_agent`/`fleet_create_agent` are available at
all (default: operator role can, specialist role cannot) — a coarse
role-based one-hop restriction, not a numeric depth/fan-out counter like
OpenClaw's `DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH=1`/`MAX_CHILDREN_PER_AGENT=5`.
No numeric fleet-size or spawn-depth limit was found anywhere in
`fleet_tools.py` (grepped `max_agents`/`agent_limit`/`max_fleet`/`MAX_FLEET`
— no hits).

**What does work — memory isolation** (unchanged from prior audit, re-confirmed
relevant, not re-derived): each install's memory is scoped by
`agent_install_id` (`specialist_runtime_context.SpecialistRuntimeContext.memory_scope()`,
per `gap-ai-operation.md` §7) — genuine parity with OpenClaw's "no shared
mutable state between agents" principle, independent of the missing
spawn/send primitives above.

**Gap vs. Claude Code/Codex sub-agent (Task-tool-style) delegation and vs.
OpenClaw's `sessions_spawn`/`sessions_send`:** this remains the largest
capability gap in the whole inventory. There is no way for one agent turn to
say "go find out X and tell me" and get an answer back inside that same
turn, synchronously or with any bounded wait — not because the wait is
missing (that alone would be gap-ai-operation.md's framing), but because the
one delivery mechanism that exists doesn't deliver at all.

---

## 6. Tasks / scheduled wake-ups / continuous work

**The action loop itself is turn-scoped and iteration-capped, not a
continuously-running session.** `_run_sage_action_loop_v3`
(`sage_agent_runtime_service.py:2755`) runs once per inbound turn (a message,
or a scheduler wake-up — see below), calling
`stream_provider_backed_direct_chat` (`direct_chat_generation_service.py`),
whose `for iteration in range(max_iterations)` loop (`direct_chat_generation_service.py:1201`)
is capped at `max_iterations = _SAGE_OPERATOR_LOOP_MAX_ITERATIONS = 5`
(`sage_agent_runtime_service.py:146`, comment: "Cap at 5 to prevent runaway;
most tasks finish in 1-3") — same constant, same cap, on both `sage_chat` and
`direct_chat` (`direct_chat_generation_service.py:1032` receives it as
`resolved_chat_max_iterations`, threaded from
`sage_agent_runtime_service.py:3124`). As of the §1 fix, those 5 iterations
now support genuine multi-round tool chains (call → observe → call again) for
every provider but DeepSeek — but 5 is still a hard ceiling, materially
smaller than Claude Code's or OpenClaw's run-until-natural-stop loop.

**"Continuous work" is composed from repeated, separately-scheduled turns,
not one long session.** `bounded_scheduler_service.py` (1314 lines) is the
mechanism — substantially more built-out than a bare cron tool:
- `propose_self_wakeup()` (`bounded_scheduler_service.py:635-715`) — the LLM
  self-schedules a future check-in, rate-limited to
  `policy.max_self_proposed_per_hour` (default `DEFAULT_MAX_SELF_PROPOSED_PER_HOUR = 2`,
  `bounded_scheduler_service.py:16`), gated by quiet-hours.
- `maybe_schedule_event_trigger()` (`bounded_scheduler_service.py:565-632`) —
  context-engine-driven wakeups, rate-limited to
  `policy.max_event_triggers_per_hour` (default
  `DEFAULT_MAX_EVENT_TRIGGERS_PER_HOUR = 4`, `bounded_scheduler_service.py:15`).
- `claim_due_wake_requests()`/`finalize_wake_requests()`
  (`bounded_scheduler_service.py:718-899`) — batch claim/resolve.
- Quiet-hours enforcement (`_is_within_quiet_hours`/`_next_allowed_wakeup_time`,
  `bounded_scheduler_service.py:324-345`) and a real retry policy with backoff
  (`RetryPolicy`/`compute_retry_delay`/`should_retry`, lines 50-101).
- **A native, first-party approval gate for privileged self-proposed
  wakeups** — `propose_self_wakeup()`
  (`bounded_scheduler_service.py:658-678`... exact condition at line 660): if
  `metadata.requires_privileged_runtime` is set and
  `policy.require_owner_approval_for_privileged_wakeups` is on and
  `metadata.approval_granted` is not true, the wake request is persisted as
  `status="denied"` — the autonomous action simply never runs without owner
  sign-off. Per `gap-ai-operation.md` §6, this has **no equivalent in
  OpenClaw** (OpenClaw's `approvals.*` only relays a *different* CLI
  product's own approval UI into a chat channel — not a gate OpenClaw
  itself enforces on its own cron/subagent actions) — a genuine, confirmed
  Empyralis lead, re-verified current in this pass.
- Each wake-up re-enters the same runtime ordinary chat turns use, via a
  periodic scanner feeding `heartbeat_run_callback` — a system-tier agent
  turn through `_run_sage_action_loop_v3`, not a separate execution engine.

**A structurally different engine exists for declarative multi-step work**:
`runs_execution.py` (6292 lines) is the visual workflow-builder product
surface — DAG-shaped nodes with their own bounded loop constructs
(`for_each`/`repeat`/`while`, each capped via `_workflow_loop_cap()`,
`runs_execution.py:1765,1869,1901-1907`) and connector-action-level approval
metadata (`_connector_tool_node_approval_metadata`,
`runs_execution.py:1073-1145`). This is explicit, human-authored workflow
automation, not an agent reasoning freely across many tool calls toward an
open-ended goal — categorically different from what Claude Code/Codex/Cursor
mean by "the agent works continuously on a task."

**No goal-tracker or implicit-follow-up extractor found** — same as the
prior audit's own hedge (`gap-ai-operation.md` §6: "not found in this pass,"
not exhaustively searched for) — not re-verified further in this pass either.

**Gap vs. Claude Code/Codex/Cursor:** the single biggest structural
difference is that Empyralis has **no mode where an agent works
continuously, within one context, until a task is actually done** — every
real unit of autonomous work is a discrete, ≤5-tool-iteration turn, and
"continuing" a multi-step task across more than that requires either the
model finishing early and a human/scheduler kicking off a fresh turn, or the
turn simply running out of budget mid-task with no resumption mechanic
beyond ordinary conversation continuity. This is the same shape gap as gap
#1 restated at the scheduling layer rather than the tool-loop layer — the
5-iteration cap, not the wake-up system (which is genuinely well-built and,
on the approval-gate dimension, ahead of OpenClaw), is the actual ceiling on
"how long can one push toward a goal."

---

## 7. Self-improvement

**Memory-write self-improvement exists and, as of today, is safe.**
`agent_memory_tools.memory_write()` (`agent_memory_tools.py:151-209`) now
runs `secret_redaction_service.redact_text()` unconditionally on every write
before persisting (`agent_memory_tools.py:189`, landed in `01b5bcb55`, see
top-of-doc freshness note) — closing the gap the prior audit flagged as its
#1 security finding. The kernel prompt's "Durable Memory Rule" instructs the
model to silently check after every message whether something's worth
remembering and call `memory_write` (per `memory-context-design.md` C6,
`sage_instruction_compiler_service.py:411-434` — not re-verified line-for-line
this pass, cited from prior doc). The topic-file tree
(`agent_memory_tree_service.py`, per `memory-context-design.md` C8) exists
and is correctly on-demand, but per that doc's own finding the kernel prompt
still doesn't teach the model to use it as an index-plus-topic-files system —
that instruction gap (E2 in the prior doc) was **not** part of today's fix
batch (only C5/E1, the always-load-instructions fix, landed) — re-confirmed
by reading the current kernel-prompt memory-rule section, which still reads
as a flat-append instruction rather than an index-with-pointers one.

**No mechanism for an agent to improve its own tools or skills.** Confirmed
by direct grep (§4 above) — no `create_skill`/`generate_skill`-shaped tool
exists; an agent cannot author a new `SKILL.md`, register a new tool, or
promote a repeated task pattern into reusable capability. This is a
structural absence, not a partially-built path.

**No consolidation ("dreaming") process.** No recall-frequency-gated
promotion of short-term notes into durable memory anywhere in
`agent_memory.py`/`agent_memory_tools.py` — confirmed absent by the prior
audit (`gap-ai-operation.md` §3, OpenClaw's `extensions/memory-core/src/dreaming.ts`
has no Empyralis analog) and re-confirmed here: `REFLECTION.md` is a
seeded-but-purely-manual scaffold (`workspace_context.py:123-135`, "An
optional place for Sage's own reflections") with **zero automation behind
it** — grepped for `reflection_service`/`nightly_reflection`/
`reflection_job`/`run_reflection`/`reflect_on` across `server_modules/*.py`:
no hits. Nothing schedules a reflection pass, nothing reads recall frequency,
nothing promotes anything. The file exists only if and when the model
chooses to write to it in the course of an ordinary turn.

**Self-tuning of instructions**: none found. `SOUL.md`/`AGENTS.md`/`TOOLS.md`
are now always injected in full (the E1 fix, §1/§3 above), and are writable
via the same `memory_write`/`memory_update` tool surface as any other file —
so an agent *can* technically edit its own operating rules mid-conversation
if instructed to — but there is no dedicated, gated, or reviewed pathway for
this (no diff/approval step, no versioning, no distinct tool), and the
kernel prompt does not direct the model to treat this as a deliberate
self-improvement action. This is a capability that exists as a side effect
of the file-write tool surface being general, not a designed mechanism.

**Gap vs. Claude Code/Codex/Cursor:** Claude Code's `memory_20250818` tool
comes with an explicit, harness-enforced self-correction loop (near-limit
nudge, over-limit hard error forcing the model to reorganize its own index —
`memory-context-design.md` A3.2/E3, still not built in Empyralis — confirmed
not part of today's fix batch, only the content-blackout fix landed). Beyond
that specific mechanic, the deeper gap is that Empyralis's "self-improvement"
surface is entirely the memory file tree — there is no path at all toward an
agent improving its own tool access, skill library, or operating
instructions in any structured, reviewable way. **Single biggest gap**: no
self-correcting feedback loop on `MEMORY.md` size (still silently truncates,
per `memory-context-design.md` C6/E3, unconfirmed as fixed — the fix batch
that landed today touched the content-blackout and secret-redaction findings
specifically, not this one).

---

## Summary — what changed since the last two audits, and what's still open

**Fixed since `gap-ai-operation.md`/`memory-context-design.md` were written**
(all three in the same commit batch, `2026-07-22 01:30:18`):
1. Multi-round tool use now works for every provider but DeepSeek
   (`63f8bc4c6`) — makes §1's `query_tool_registry` lazy-discovery loop
   actually deliver on its design.
2. `SOUL.md`/`AGENTS.md`/`TOOLS.md`/`USER.md`/`GOALS.md` are back in the
   main `sage_chat` system prompt every turn (`6628546ee`).
3. `memory_write` redacts secrets before persisting (`01b5bcb55`).

**Still open, ranked by leverage for an "agent backbone" comparison**:
1. **Sub-agent delegation is a dead letter, not just fire-and-forget** —
   `fleet_inbox` is written to but never read anywhere in the codebase (§5,
   new finding this pass, stronger than the prior hedge).
2. **Skills infrastructure is fully built but fully disconnected** — no
   bundled/workspace skill content ships, and the one function that would
   inject installed-skill content into a live prompt has its only call site
   in a health-check endpoint, not the chat runtime (§4, new finding this
   pass — neither prior doc audited this system).
3. **No continuous-work mode** — every autonomous unit of work is a
   ≤5-tool-iteration turn; "long-running" is simulated by repeated scheduled
   wake-ups, not one persistent working session (§6).
4. **No self-correcting memory-index feedback loop** — `MEMORY.md` still
   silently truncates past its char cap with no nudge/error back to the
   model, unlike Claude Code's harness-enforced version (§7,
   `memory-context-design.md` E3, unresolved).
5. **No prompt-cache boundary** — lowest priority per both prior docs, still
   true, re-confirmed absent (§3).

**Genuine strengths, reconfirmed current**: compaction (§2) is real,
proactive, and safety-flushed — near-parity-or-better vs. OpenClaw. The
bounded scheduler's privileged-wakeup owner-approval gate (§6) has no
OpenClaw equivalent. Memory isolation between agents (§5) is solid. All
three fresh fixes above closed real correctness/security gaps same-day.
