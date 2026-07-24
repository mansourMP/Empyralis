# Memory + Context Delivery: How Claude Code Does It, How Empyralis Does It, and How to Close the Gap

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers (e.g. `"sage-main"`, `sage_agent_runtime_service.py`) are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

Status: design doc, not yet built. No production code was changed to produce this.
Scope: how an agent's durable memory (facts across sessions) and per-turn context (system prompt + history + tools) get assembled and delivered to the model. Goal stated by the founder: make Empyralis agents work "exactly like Claude Code's memory system" — a dense `MEMORY.md` index loaded every turn, individual memory files created/read on demand, links between files.

---

## TL;DR

Empyralis already built most of the *plumbing* for this (topic-file tree, structured `memory_search`/`memory_get` tools, an index-only prompt path). What's missing is **discipline and consistency**, not infrastructure:

1. Two different context-assembly implementations exist side by side for two different chat surfaces, and only one of them behaves like Claude Code's model.
2. Even the correct one has a **silent content blackout**: every root file except `MEMORY.md` (persona, rules, user profile, goals) gets excluded from context entirely — neither injected nor listed for retrieval.
3. The system prompt actively instructs the agent to treat `MEMORY.md` as a flat, ever-growing fact dump, not an index — even though the topic-file infrastructure to do the real thing already exists and is wired up.
4. There's no self-correcting "your index is too big, shorten it" feedback loop like Claude Code has.
5. There's no literal per-message inbound envelope (channel/sender/timestamp prefix in the transcript) — sender identity only steers tool availability and instructions, invisibly.

Part E below has the concrete design. The tight 5-8 item list is at the very end.

---

## Part A — How Claude Code's memory system actually works

Sourced from Anthropic's own docs (primary sources), fetched live for this doc:

- [How Claude remembers your project](https://code.claude.com/docs/en/memory) — official Claude Code docs
- [Explore the context window](https://code.claude.com/docs/en/context-window) — official, includes an annotated token-by-token timeline and a "what survives compaction" table
- [Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) — official Anthropic API docs for the `memory_20250818` tool, the API-level building block this same pattern is built from
- Community grounding (secondary, cited as community — not verified against Anthropic source): [HackerNoon — AI Agent Memory Files](https://hackernoon.com/the-complete-guide-to-ai-agent-memory-files-claudemd-agentsmd-and-beyond), [developertoolkit.ai — Claude Code Memory System](https://developertoolkit.ai/en/claude-code/advanced-techniques/memory-system/), [claude-howto (GitHub)](https://github.com/luongnv89/claude-howto/blob/main/02-memory/README.md)

### A1. Two complementary systems, not one

| | CLAUDE.md files | Auto memory |
|---|---|---|
| Who writes it | You (the human) | Claude itself |
| Contents | Instructions and rules | Learnings and patterns Claude discovered |
| Scope | Project / user / org, walked up the directory tree | Per git repo, shared across worktrees |
| Loaded into context | **Every session, in full** | Every session, but only the **first 200 lines or 25KB of `MEMORY.md`**, whichever hits first |
| Use for | Coding standards, workflows, architecture | Build commands, debugging insights, discovered preferences |

This distinction is the single most important fact for this doc: Claude Code never conflates "always-loaded instructions" with "index of on-demand facts." They are two different mechanisms with two different loading rules, and only the memory one is capped/on-demand. (Empyralis conflates them — see Part C.)

### A2. `CLAUDE.md` — instructions, loaded in full, always

- Discovered by walking up the directory tree from cwd to the repo root; every `CLAUDE.md`/`CLAUDE.local.md` found along the way is concatenated into context at launch (root-most first, closest-to-cwd last, `.local.md` appended after its sibling).
- Files in *subdirectories* below cwd are not loaded at launch — they load lazily the moment Claude reads a file in that subdirectory (this is Claude Code's actual "on-demand" file mechanism for instructions, distinct from auto-memory's on-demand mechanism for facts).
- Can `@path/to/file` import other files (max depth 4); imports are expanded and loaded at launch too — imports don't reduce context, they just organize authoring.
- Recommended size: **under 200 lines** per file; longer files "consume more context and reduce adherence" (the docs are explicit that size hurts instruction-following, not just token cost).
- `CLAUDE.md` content enters the transcript as a **user message after the system prompt**, not inside the system prompt itself — which is why Anthropic's own troubleshooting doc says there's no guarantee of strict compliance.

### A3. Auto memory — the actual "MEMORY.md index + on-demand files" model the founder is asking for

This is the mechanism to replicate. Verbatim from the official docs:

> Each project gets its own memory directory at `~/.claude/projects/<project>/memory/`. ... The directory contains a `MEMORY.md` entrypoint and optional topic files:
> ```
> ~/.claude/projects/<project>/memory/
> ├── MEMORY.md          # Concise index, loaded into every session
> ├── debugging.md       # Detailed notes on debugging patterns
> ├── api-conventions.md # API design decisions
> └── ...                # Any other topic files Claude creates
> ```
> `MEMORY.md` acts as an index of the memory directory. Claude reads and writes files in this directory throughout your session, using `MEMORY.md` to keep track of what's stored where.

Mechanics, precisely:

1. **Hard load cap on the index, not the topic files.** "The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the start of every conversation. Content beyond that threshold is not loaded at session start." Topic files (`debugging.md`, etc.) are **never** auto-loaded — Claude reads them "on demand using its standard file tools when it needs the information," exactly like reading any other file.
2. **A self-correcting feedback loop keeps the index dense.** After Claude writes to `MEMORY.md`, Claude Code measures the file against the limit. If it's *near* the limit, Claude Code reminds Claude to shorten it — "keep one line per entry, move detail into topic files, and merge or drop stale entries." If it's *over* the limit, the write still succeeds but Claude Code returns an explicit error telling Claude to rewrite the index, because "everything past the limit is dropped on the next load." This loop is the mechanism that actually keeps `MEMORY.md` a dense index instead of a growing dump — it's not just a prompt instruction, it's enforced by the harness.
3. **Auto memory is opt-out, not opt-in**, on by default, toggled via `/memory` or `autoMemoryEnabled` in settings.
4. **Compaction interacts deliberately with memory.** Per the "what survives compaction" table on the context-window page:

   | Mechanism | After `/compact` |
   |---|---|
   | System prompt and output style | Unchanged — not part of message history |
   | Project-root CLAUDE.md and unscoped rules | Re-injected from disk |
   | Auto memory (MEMORY.md) | **Re-injected from disk** |
   | Rules with `paths:` frontmatter | Lost until a matching file is read again |
   | Nested CLAUDE.md in subdirectories | Lost until a file in that subdirectory is read again |
   | Invoked skill bodies | Re-injected, capped 5K tokens/skill, 25K total, oldest dropped first |
   | Hooks | N/A — run as code, not context |

   The pattern that makes this coherent: because memory is a **file on disk**, not a message in the transcript, compaction (which summarizes the transcript) simply doesn't touch it — Claude re-reads it fresh every time. This is why Claude Code's own docs describe memory as designed for a harness where "your context window might be reset at any moment" (see A4) — it's the durable layer underneath an ephemeral transcript, by construction, not by convention.
5. **Subagent isolation.** A subagent gets its own system prompt and its own `CLAUDE.md` copy, but the *main session's* auto memory is **not** included — unless the subagent has its own `memory:` field in frontmatter, in which case it gets a wholly separate `MEMORY.md` of its own. A `fork` is the one exception (inherits the parent's everything).
6. **No frontmatter/metadata schema is documented** for individual topic files (name/description/type) — this specific detail the founder mentioned (per-file frontmatter) is **not** part of Claude Code's own auto-memory spec as documented; it more closely resembles the `SKILL.md` frontmatter convention (`name`, `description`) from Claude Code's *skills* system, which is a related-but-different on-demand-loading mechanism (skill descriptions are always in context as one-liners; full skill bodies load on invocation). Worth borrowing the *pattern* (cheap-metadata-line always visible, full-body on demand) even though it's not literally documented for memory files.

### A4. The official building block underneath: the `memory` tool (API level)

Claude Code's auto-memory is a product-level instance of a documented, general Anthropic API primitive: the `memory_20250818` tool (`platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool`). This is the cleanest spec to copy mechanically:

- Six commands: `view` (list dir or read file, with `view_range` for large files), `create`, `str_replace`, `insert`, `delete`, `rename`.
- All operations are scoped under a single root (`/memories`); the tool is client-side — Claude only *requests* operations, the host application executes them against real storage and returns a `tool_result`.
- Security is explicitly the host's job: validate every path resolves inside `/memories`, reject `../` and URL-encoded traversal, cap file/view sizes, and consider periodic expiration of stale files.
- Anthropic auto-injects a standard system-prompt nudge whenever this tool is present: *"ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE... ASSUME INTERRUPTION: your context window might be reset at any moment, so you risk losing any progress not recorded in your memory directory."* This framing — memory as insurance against an unpredictable context reset, not as a nice-to-have — is worth replicating verbatim in spirit.
- Documented "multisession software development pattern": an *initializer session* sets up a progress log + feature checklist + reference script before work starts; every subsequent session opens by reading those files; each session ends by updating the progress log. This is a good template for what Empyralis's own kernel prompt should be teaching agents to do with `MEMORY.md` + topic files, instead of the flat fact-dump it currently teaches (see C6).

### A5. Full per-turn context assembly, in load order

From the interactive context-window breakdown (`code.claude.com/docs/en/context-window`), the components that load **before the user ever types anything**, in order: system prompt → auto memory (`MEMORY.md`, capped) → environment info (cwd/platform/git) → MCP tool *names* only (schemas stay deferred, loaded on demand via tool search) → skill *descriptions* only (one-liners; full body loads on invocation) → `~/.claude/CLAUDE.md` (global) → project `CLAUDE.md`. Then: user prompt → tool calls/file reads (each one adds its actual content) → path-scoped rules (load automatically the moment a matching file is read) → hook output (`additionalContext` from PostToolUse hooks enters context; plain stdout does not) → assistant replies. Subagents get their own nested version of the same stack in an isolated context window; only their final summary text returns to the parent.

The generalizable principle: **everything either (a) is cheap and always-on (system prompt, index, tool/skill names), or (b) loads lazily exactly when something makes it relevant (file reads, path-scoped rules, skill bodies, memory topic files)** — nothing sits in the "always full content, unconditionally" bucket except the two things small enough to afford it (system prompt, capped memory index).

---

## Part B — How OpenClaw does it (read-only reference, `/Users/mansur/openclaw`)

Per the memory index note (`docs/PLATFORM-MAP.md`), OpenClaw is a second live reference the founder wants mined for patterns. Three pieces are directly relevant:

### B1. Root memory files — same `MEMORY.md` convention, resolved defensively

`src/memory/root-memory-files.ts` resolves `MEMORY.md` as the "canonical root memory file," explicitly guards against a legacy lowercase `memory.md` and against symlinks (`resolveCanonicalRootMemoryFile` only accepts a real file, not a symlink), and keeps a `.openclaw-repair/root-memory` migration directory out of scans. Small but telling: OpenClaw treats the root memory file's *identity* (exact name, real file not a symlink) as something worth defending in code, not just convention.

### B2. Two-layer memory delivery: small durable file inline + real search engine for everything else

`src/agents/system-prompt.ts` (`buildProjectContextSection`, `isDynamicContextFile`, `CONTEXT_FILE_ORDER`) loads root context files' **full content** into the system prompt (it explicitly special-cases `SOUL.md` — persona — and `MEMORY.md` — "durable user preferences and behavior guidance, keep following it unless higher-priority instructions override"). Separately, `extensions/memory-core/src/prompt-section.ts` (`buildPromptSection`) adds a **pure tool-guidance section**, not content:

```
"Before answering anything about prior work, decisions, dates, people, preferences, or todos:
 run memory_search on MEMORY.md + memory/*.md + indexed session transcripts;
 then use memory_get to pull only the needed lines. If low confidence after search, say you checked."
```

Behind those two tools sits a genuinely sophisticated local search engine — `extensions/memory-core/src/memory/` has dedicated modules for embeddings, MMR re-ranking, FTS, tokenization, temporal decay, and session-transcript indexing (`manager-search.ts`, `embeddings.ts`, `mmr.ts`, `temporal-decay.ts`). This is meaningfully more capable than Claude Code's plain filesystem `Read`-tool-on-demand model, and considerably more capable than Empyralis's current keyword-substring/optional-sentence-transformer search (Part C). It's also more than this design doc's scope requires copying wholesale — but the *shape* (small durable index inline + real retrieval tool for depth) is the right one to hold onto.

### B3. Stable vs. dynamic context — a prompt-caching-aware detail Empyralis has no analog for

`system-prompt.ts`'s `prepareContextFilesForPrompt` splits context files into `stable` (rarely changes — kept above the prompt-cache boundary so repeated turns reuse the cached prefix) and `dynamic` (frequently changing — kept below the cache boundary so it doesn't invalidate the cache on every turn). This is a cost/latency optimization neither Claude Code's docs nor Empyralis's implementation reflect; flagged here as a stretch idea, not a required parity item.

### B4. A real inbound envelope — exactly the piece Empyralis is missing (Part C7)

`src/auto-reply/envelope.ts`'s `formatInboundEnvelope`/`formatAgentEnvelope` literally prefix every inbound message with a bracketed header before it reaches the model:

```
[WhatsApp +2m 2026-07-21 14:03:10] Mansur: can you check on the deploy?
```

Channel, elapsed time since the previous message, optional host/IP, timestamp (weekday-prefixed, timezone-aware), and — critically — the resolved sender label, with explicit direct-vs-group formatting (`resolveDirectEnvelopeBodyLabel`) and a `(self)` marker when the message is the owner's own echo. This is a literal, in-transcript "who's speaking, on what channel, when" signal the model can see and reason about on every turn — not just a side-channel flag that silently changes which tools are available (which is all Empyralis currently does; see C7). The founder's own memory note (`project_inbound_envelope_and_memory_attribution.md`) already flags wanting exactly this, citing OpenClaw as the model to replicate — this confirms that's the right pattern and gives an exact source file to port from.

---

## Part C — How Empyralis does it today (file:line-verified)

### C1. The file/directory model — already close to Claude Code's shape

`server_modules/workspace_context.py` defines `ALLOWED_CONTEXT_FILENAMES` (workspace_context.py:13-27) — ten root files loaded per install: `SOUL.md, AGENTS.md, TOOLS.md, IDENTITY.md, HEARTBEAT.md, USER.md, GOALS.md, MEMORY.md, PROCEDURES.md, REFLECTION.md`, each with a seeded scaffold in `DEFAULT_CONTEXT_FILE_CONTENTS` (workspace_context.py:50-144) — and a topic-file tree under `memory/files/**.md`, one category subdirectory deep (workspace_context.py:270-328, `USER_MEMORY_FILE_RE`), plus daily notes (`memory/YYYY-MM-DD.md`) and a `.dreams/` staging area. Storage is filesystem-backed, one directory per `(workspace_id, agent_install_id)` (workspace_context.py:217-235) — this is structurally the same idea as Claude Code's `~/.claude/projects/<project>/memory/`.

**Found bug while reading this file**: `MEMORY.md`'s own seeded scaffold (workspace_context.py:88-98) says *"Sections to add as they're earned... Retrieval pulls relevant topic files in on demand"* and `REFLECTION.md`'s scaffold (workspace_context.py:123-135) says *"NOT loaded every turn — only MEMORY.md is."* But the comment directly above `ALLOWED_CONTEXT_FILENAMES` (workspace_context.py:29-31) says *"All core memory files are now loaded every turn"* — and `REFLECTION.md` is literally inside that same `ALLOWED_CONTEXT_FILENAMES` tuple. The scaffold text an agent reads and the code comment describing that same file disagree about whether it's loaded every turn. In practice this doesn't matter much because — see C3 — the file that actually ships to the model doesn't load `REFLECTION.md` either way, but the seeded content is misleading regardless of which code path runs.

### C2. Real, structured, on-demand memory tools already exist — and there are two competing sets of them

Empyralis has **two parallel tool surfaces** for memory, both live:

**Surface 1 — proper JSON-schema tools**, registered as `ToolDescriptor`s in `server_modules/skills_service.py:678-770`: `memory_search` (query → snippets with path+line), `memory_write` (path/content/mode), `memory_read` (path), `memory_get` (path/from/lines — "Read a small excerpt ... after `memory_search` identifies the file and lines," a direct structural match to Claude Code's `view` with `view_range`), and `memory_update` (whole-file rewrite of an official root context file). `memory_search`/`memory_get`/`memory_read` are marked `audience_safe=True`; `memory_write`/`memory_update` are owner-only. Also registered as core tool names in `server_modules/tool_registry_service.py:30-31`.

**Surface 2 — a legacy, fragile dispatch path**: `server_modules/agent_memory_tools.py` implements its own `memory_read`/`memory_write`/`memory_list` against a *different* directory convention (`<workspace_context>/agents/<install_id>/memory/`, agent_memory_tools.py:25-39, vs. the Surface-1 tools which go through `workspace_context.py`'s root/`memory/files/` convention). It's reached via `server_modules/tool_broker.py:485-556`'s `_dispatch_memory_tool`, which — instead of taking structured arguments — **regex-parses a free-text `goal` string** to extract a path and content (`_extract_memory_path`/`_extract_memory_content`, tool_broker.py:539+, matching patterns like `"[^"']+\.[a-z]{1,10}"` or a bare `\S+\.md` in the goal text). This is the "Phase N" prototype mentioned in `agent_memory_tools.py`'s own docstring; it also ships a `migrate_existing_memory_to_index` helper (agent_memory_tools.py:260-364) and a `build_memory_starter_template` (agent_memory_tools.py:367-388) that already describes almost exactly the target design — *"This is the agent's memory index. It is the ONLY file guaranteed to be injected into every turn's context. All other memory files are read on-demand via memory_read"* — but neither of those two helpers has any caller anywhere in the codebase (verified: `migrate_existing_memory_to_index` and `build_memory_starter_template` have zero call sites outside their own file). It's dead code describing the right design, sitting unused next to a fragile, superseded dispatch path.

Net: the model-facing tool surface the system prompt actually advertises (Surface 1) is solid and close to Claude Code's `view`/`create`/`str_replace` shape. Surface 2 is redundant, structurally weaker (regex text-parsing instead of typed arguments), and should be deleted rather than reconciled.

### C3. Two competing context-assembly pipelines for two different chat surfaces — only one matches Claude Code

This is the central architectural finding.

**Pipeline A — the old one, full-file-loading.** `server_modules/workspace_context_memory_adapter.py`'s `build_workspace_context_file_blocks` (workspace_context_memory_adapter.py:139-192) walks a fixed 12-file order (`_ROOT_CONTEXT_FILE_ORDER`, line 10-23: `SOUL.md, USER.md, IDENTITY.md, GOALS.md, PROCEDURES.md, TOOLS.md, AGENTS.md, REFLECTION.md, MEMORY.md, HEARTBEAT.md, SELF_MODEL.md, LIFE_STORY.md`) and injects **every file's full content**, unconditionally, up to 12,000 chars/file and 48,000 chars total (lines 24-25). Topic files (`memory/files/**`) only get a filename manifest, not content (lines 170-192) — that part *is* on-demand, correctly. This whole payload is built by `load_workspace_context_payload` (line 195) and is reached through `conversation_memory_facade_service.load_context` (conversation_memory_facade_service.py:70-108), which is gated to `surface_kind == DIRECT_CHAT_SURFACE` only (line 84) — confirmed live on the `direct_chat_runtime_service.py:1082` path (`services.direct_chat_workspace_context_text`).

**Pipeline B — the new one, index-only, added recently ("Phase N Stage 5").** `server_modules/sage_instruction_compiler_service.py`'s `build_root_memory_brief_sections` (lines 249-346) has an explicit comment: *"inject ONLY MEMORY.md content. Every other file is available on-demand via memory_read. MEMORY.md is the index the agent maintains."* It injects `MEMORY.md` content only, capped at 900 chars/section and **4,800 chars total** (`ROOT_MEMORY_BRIEF_SECTION_CHAR_LIMIT`/`ROOT_MEMORY_BRIEF_TOTAL_CHAR_LIMIT`, lines 36-37) — genuinely comparable in spirit to Claude Code's 25KB/200-line cap on auto-memory, just tighter. This is the path used by `build_sage_instruction_bundle` (sage_instruction_compiler_service.py:554-585), which is master Sage's real system-prompt builder, called from `handle_sage_chat` (sage_agent_runtime_service.py:4199-4218) — and it's *also* the path specialist agents use for their own `MEMORY.md` (sage_agent_runtime_service.py:3942-3961), per a fix dated 2026-07-15 documented right there in the code: before that fix, "every specialist ran with no memory index, every turn" because the old call path read a dead SQLite table (see C4).

**Which surface is "real" production traffic?** `handle_sage_chat` (Pipeline B) is Sage's/specialists' main assistant loop — the primary product surface. `direct_chat_runtime_service` (Pipeline A) is a separate chat facade (BYOK/other-provider path). Both are live, but they now give an agent **structurally different context** depending on which one a given turn routes through — the same workspace's `SOUL.md`/`USER.md`/etc. is either fully present (Pipeline A) or entirely absent except through a tool call the agent has to think to make (Pipeline B, and see C5 below — it isn't even offered for retrieval in some cases).

### C4. Confirmed dead code from the migration, worth deleting outright

- `sage_instruction_compiler_service.build_root_memory_sections` (lines 145-213, using the older 12,000/48,000-char limits) has **zero callers** anywhere in the codebase — it's the full-file predecessor to `build_root_memory_brief_sections`, left in place after the Phase N migration.
- `agent_memory.py`'s SQLite `memory_entries` table (structured-facts side store, agent_memory.py:259-278) is explicitly called out in its own code comment (agent_memory.py:559-573) as "a legacy side system — nothing on any live turn writes to it with a real agent_install_id today," and the specialist-memory fix above exists precisely because an earlier code path read from this dead table and got nothing back.
- Two near-duplicate keyword-marker lists for "does this message need memory context," copy-pasted with drift risk: `message_needs_memory_context` (workspace_context_memory_adapter.py:112-116, ~30 markers) and `_message_needs_memory_context` (sage_instruction_compiler_service.py:459+, same list, separately maintained).

### C5. The silent content blackout in Pipeline B — the highest-severity gap found

`build_root_memory_brief_sections` defines `OFFICIAL_ROOT_MEMORY_FILES = (SOUL.md, IDENTITY.md, USER.md, AGENTS.md, TOOLS.md, MEMORY.md, GOALS.md)` (sage_instruction_compiler_service.py:13-21). Walking the function (lines 249-346):

1. Only `MEMORY.md`'s content is ever appended to `sections` (lines 258-273).
2. The other six official files are checked for "meaningful content" and, if present, added to `consumed_paths` **only** (lines 276-278) — marked as accounted-for so they don't leak into the generic "extra files" bucket, but never surfaced anywhere else.
3. The manifest built afterward (lines 302-320, *"Full root and workspace memory files stay available through memory_search and memory_get..."*) only lists `LEGACY_ROOT_MEMORY_FILES` (just `HEARTBEAT.md`) and ad-hoc `extra_context_files` (arbitrary non-official filenames) plus `memory/files/**` topic paths. `OFFICIAL_ROOT_MEMORY_FILES` members are in `consumed_paths`, which is exactly the set the manifest-builder *skips* (line 291: `if filename in official_set ...: continue`).

Net effect: on the primary production chat path, `SOUL.md` (persona), `AGENTS.md` (operating rules), `TOOLS.md`, `USER.md` (who the user is), and `GOALS.md` are **neither injected into the system prompt nor listed as available for retrieval**. The model has no way to know they exist unless it happens to call `memory_search`/`memory_get` with a query that surfaces them some other way, or the separate `profile_context`/`heartbeat_context` sections (built independently, sage_agent_runtime_service.py:3934-3980, from a different service — `_load_profile_context`, not from `USER.md` itself) happen to carry equivalent information. This is a straightforward regression relative to Claude Code's model, where the `CLAUDE.md`-equivalent instruction layer is *always* fully loaded, separate from and unconditional on the memory-index cap. Empyralis's Phase N migration correctly built the "index-only" half of Claude Code's model but dropped the "instructions always load in full" half for these six files.

### C6. The system prompt itself teaches the wrong pattern

`sage_instruction_compiler_service._kernel_prompt`'s "Durable Memory Rule" (lines 411-434) instructs: *"After every user message, silently check: did the user share anything you should remember? ... call `memory_write` with `path='MEMORY.md'` and `mode='append'`... Write one fact per line: `- key: value`."* There is no instruction anywhere in this rule to move detail into topic files, keep the index dense, or use the `memory/files/**` tree that already exists and is already wired up for retrieval (`agent_memory_tree_service.py`, see C8). Given `MEMORY.md`'s hard 4,800-char injection cap (C3), the actual behavior this produces is: keep appending one-line facts to `MEMORY.md` forever; once it exceeds ~4,800 chars, the oldest content silently falls off the visible window every turn (`_clip_text`, "content truncated due to length limit," sage_instruction_compiler_service.py ~line 270) with **no error, no nudge, no signal to the agent that it needs to reorganize** — unlike Claude Code, where exceeding the cap produces an explicit harness error telling the agent to rewrite the index and move detail out.

### C7. No literal inbound envelope — sender identity is a side-channel, not a visible signal

`handle_sage_chat` does resolve sender identity per turn: cross-channel identity linking (`resolve_canonical_sender`, sage_agent_runtime_service.py:3901-3906) and an owner/audience/unknown classification (`triage_service.resolve_sender_identity`, called at sage_agent_runtime_service.py:3908-3930). But this classification only ever (a) filters which tools are available (`_direct_tool_bundle(..., sender_class=...)`, sage_agent_runtime_service.py:2321-2396) and (b) selects which behavior-instruction block gets appended to the system prompt (`audience_behavior_instructions`, referenced at sage_agent_runtime_service.py:3928-3930). `_build_prompt_envelope` (sage_instruction_compiler_service.py:1578-1591) — the function actually named "envelope" in this codebase — returns only `{system_prompt, user_message, context: {workspace_id, source}}`; the `user_message` string the model sees carries no channel, sender, or timestamp marker at all. Compare OpenClaw's `formatInboundEnvelope` (B4), which literally prefixes `[Channel +elapsed timestamp] Sender: body` onto every inbound message so the model can see, in the transcript itself, who is talking and from where — not just have its tool access silently narrowed. The founder's own memory note already flags this exact gap and names OpenClaw as the reference; this confirms it and gives the precise file to port the pattern from.

### C8. The Phase 6 topic-file tree — built correctly, just under-used

`server_modules/agent_memory_tree_service.py` is a solid, correctly-scoped implementation of exactly the "topic files, read on demand" half of Claude Code's model: `list_tree`/`read_file`/`write_file`/`append_file`/`delete_file` over `memory/files/<name>.md` or one category deep (`memory/files/<category>/<name>.md`, matching `workspace_context.py`'s `USER_MEMORY_FILE_RE`), plus `retrieve_relevant_topics` (lines 132-178) — a keyword-overlap-plus-phrase-bonus scorer over topic file contents, budgeted per call (default 4,000 chars, `max_files` capped), returning `absent-when-irrelevant` (empty query → empty result, by design, per its own docstring). It's wired into `workspace_context_memory_adapter.load_workspace_context_payload` (workspace_context_memory_adapter.py:267-284) — i.e., it's reachable from Pipeline A (the direct-chat surface) — but **not** referenced anywhere in Pipeline B's `build_root_memory_brief_sections`/`build_sage_instruction_bundle` path, i.e., not proactively pulled into master Sage's or specialists' main turn; the agent can still reach individual topic files via the `memory_search`/`memory_get` tools (C2, Surface 1), just not via this specific relevance-scored auto-pull. `semantic_search` in `agent_memory.py` (lines 403-431) additionally supports an optional `sentence-transformers` embedding model with graceful fallback to substring search when the dependency isn't installed — reasonable, but noticeably less capable than OpenClaw's dedicated embeddings/MMR/temporal-decay search stack (B2).

### C9. History windowing and compaction — separate machinery from the memory-file system

Empyralis has real compaction: `_find_cut_point_proactive` for a hard token-budget cut of channel-carried history (sage_agent_runtime_service.py ~5039-5049), and `_run_memory_flush_before_compaction` (sage_agent_runtime_service.py:3941-4010ish, "B3: Memory flush turn before compaction") — an explicit LLM turn that runs *before* compaction specifically to save important facts to `MEMORY.md` via `memory_write` first, so compaction doesn't destroy anything not yet durable. This is a good pattern and conceptually similar to why Claude Code's compaction is safe (memory lives on disk, outside the transcript it's summarizing) — except Empyralis's version does the safety work with an extra LLM call at compaction time, rather than by construction (Claude Code doesn't need a "flush turn" because memory was never *in* the transcript being compacted in the first place — it was already a side file, always re-read fresh). Worth keeping the flush-turn as a safety net but not relying on it as the primary mechanism once C5/C6 are fixed.

---

## Part D — Gap table

| Claude Code mechanism | Empyralis today | Gap |
|---|---|---|
| `CLAUDE.md` (instructions) always loaded in full, separate from memory | `SOUL.md`/`AGENTS.md`/`TOOLS.md`/`USER.md`/`GOALS.md` conflated into `OFFICIAL_ROOT_MEMORY_FILES` alongside `MEMORY.md`, and Pipeline B drops all six from context except `MEMORY.md` (C5) | No "instructions always load" tier exists in Pipeline B at all |
| `MEMORY.md` index, capped (200 lines/25KB), topic files on-demand | Pipeline B does this correctly for `MEMORY.md` itself (4,800-char cap) | Mostly matches — just needs the self-correcting nudge (next row) |
| Over-limit index → explicit harness error telling agent to shorten | Silent truncation, "[content truncated]", no signal to the agent | No feedback loop; index quality degrades invisibly |
| One coherent memory pipeline | Two parallel pipelines (Pipeline A full-file / Pipeline B index-only) gated by chat surface | Same workspace behaves differently depending which surface a turn lands on |
| One coherent memory tool surface (`view`/`create`/`str_replace`/...) | Two tool surfaces: typed `memory_search`/`memory_get`/`memory_write`/`memory_read`/`memory_update` (good) vs. regex-goal-string `memory-read`/`memory-write`/`memory-list` (fragile, C2) | Redundant, and the weaker one is reachable in production |
| Topic files (`debugging.md` etc.), created as facts are earned, read on demand | `memory/files/**.md` tree exists (`agent_memory_tree_service.py`), correctly scoped and on-demand | Prompt never instructs the agent to *use* it this way (C6); not proactively pulled into Pipeline B |
| Literal in-transcript inbound envelope (channel/sender/timestamp) | Sender identity resolved but only shapes tool access + instructions, invisibly (C7) | Model can't see who it's talking to in the transcript itself |
| Compaction is safe by construction (memory lives outside the transcript) | Compaction is made safe by an extra LLM "flush" turn beforehand (C9) | Works, but is a workaround rather than structural |
| Subagent memory isolation (separate `MEMORY.md` unless forked) | Specialist memory is correctly isolated by `agent_install_id` (per `specialist_runtime_context.py`, `memory_scope()`) | No gap — already matches |

---

## Part E — Design: closing the gap

Each item names the exact mechanism to change and the file(s) it touches. Ordered by leverage (highest first), matching the tight list below.

**E1. Restore an always-loaded "instructions" tier in Pipeline B, separate from the memory index.**
Split `OFFICIAL_ROOT_MEMORY_FILES` into two lists: an *instructions* tier (`SOUL.md`, `AGENTS.md`, `TOOLS.md` — rarely change, small, always-relevant, no cap needed beyond a generous ceiling) that gets injected in full every turn like `CLAUDE.md`, and an *index* tier (`MEMORY.md` only) that keeps the current 4,800-char cap treatment. `USER.md`/`GOALS.md` need a decision: either fold their durable content into `MEMORY.md`'s index (simplest, matches Claude Code exactly — there's no separate "user profile file" in Claude Code's model, it's all in the index) or give them the same always-loaded instructions treatment as `SOUL.md`. Recommend the former — it also resolves the `profile_context`-vs-`USER.md` duplication noted in C5. Touches `sage_instruction_compiler_service.py`'s `build_root_memory_brief_sections` and `build_sage_instruction_bundle`. **Build size: small** — it's a re-partitioning of an existing list plus one new always-inject code path modeled directly on the existing `MEMORY.md` injection block.

**E2. Rewrite the kernel prompt's memory rule to teach index-plus-topic-files, not flat-append.**
Replace `_kernel_prompt`'s "Durable Memory Rule" (sage_instruction_compiler_service.py:411-434) with Claude Code's actual pattern: keep `MEMORY.md` to one line per fact/topic pointer; when a topic grows (a customer, a project, a recurring procedure), create or append to `memory/files/<topic>.md` via the existing `memory_write`/`memory_get` tools and link it from `MEMORY.md`; consult `memory_search` before claiming ignorance. This requires no new tools — `memory_write`, `memory_read`, `memory_get`, `memory_search` (C2, Surface 1) already do everything needed. **Build size: small** — prompt text change only.

**E3. Add the self-correcting over-budget nudge.**
When `_export_memory_md`/`memory_write` (or the Pipeline B injection path) detects `MEMORY.md` content near/over its char cap, return guidance in the tool result (for a `memory_write` call) or inject a one-line system note ("MEMORY.md is near its size limit — move detail into a memory/files/*.md topic file and keep the index to one line per entry") instead of silently truncating. Mirrors Claude Code's "near limit → reminder, over limit → hard error" behavior. Touches `agent_memory.py`'s `_export_memory_md`/`_save_memory` and/or the `memory_write` tool implementation in `skills_service.py`/`memory_service.memory_write_file`. **Build size: small-medium** — needs a size check at write time plus a way to surface the nudge back through the tool result.

**E4. Collapse to one context-assembly pipeline.**
Migrate the `direct_chat` surface (`direct_chat_runtime_service.py:1082`, via `conversation_memory_facade_service`/`workspace_context_memory_adapter`) onto the same `build_root_memory_brief_sections`-based path Pipeline B already uses for `sage_chat`, once E1 makes Pipeline B feature-complete. Delete `workspace_context_memory_adapter.build_workspace_context_file_blocks`'s full-file-loading branch (or repoint it to call the Pipeline B builder). Also delete the confirmed-dead `build_root_memory_sections` (C4) and reconcile the duplicate `message_needs_memory_context` functions into one. **Build size: medium** — touches a chat surface that's presumably in active use (BYOK/other-provider chat), so needs a careful behavior-parity check before cutover, but the target function already exists and works.

**E5. Delete the legacy regex-dispatch memory tool surface.**
Remove `agent_memory_tools.py`'s `memory_read`/`memory_write`/`memory_list` and `tool_broker.py`'s `_dispatch_memory_tool`/`_extract_memory_path`/`_extract_memory_content` goal-string-parsing path (C2, Surface 2), along with its distinct `<workspace_context>/agents/<install_id>/memory/` directory convention. Keep only the typed `memory_search`/`memory_get`/`memory_write`/`memory_read`/`memory_update` tools already registered in `skills_service.py`. Also delete the now-pointless `migrate_existing_memory_to_index`/`build_memory_starter_template` helpers (C2) or repurpose `build_memory_starter_template`'s text — it's already well-written — as the seeded `MEMORY.md` scaffold in `workspace_context.py`'s `DEFAULT_CONTEXT_FILE_CONTENTS`. **Build size: small** — pure deletion plus one scaffold-text swap; check for any UI/route caller of `agent_memory_tools.memory_list` first (`routes_fleet.py:607` uses it directly — needs repointing to the Phase 6 tree's `list_tree` instead).

**E6. Add a literal inbound envelope to the transcript.**
Port the shape of OpenClaw's `formatInboundEnvelope`/`formatAgentEnvelope` (B4): prefix the user-visible message that reaches the model with `[<channel> <sender-or-(self)> <elapsed> <timestamp>]` before the body, using the sender-identity resolution `handle_sage_chat` already computes (`resolve_canonical_sender`, `resolve_sender_identity`, C7) — just render it into the message text instead of only using it to gate tools/instructions. Touches `handle_sage_chat`'s message-normalization step and `_build_prompt_envelope` in `sage_instruction_compiler_service.py`. This also directly closes the founder's own previously-flagged gap (`project_inbound_envelope_and_memory_attribution.md`). **Build size: medium** — needs careful handling for existing thread history (retrofitting old messages is optional/skippable; only new turns need the prefix) and for surfaces that pass `channel_prior_messages` directly.

**E7. Fix the `REFLECTION.md`/`ALLOWED_CONTEXT_FILENAMES` contradiction.**
Either move `REFLECTION.md` out of `ALLOWED_CONTEXT_FILENAMES` (workspace_context.py:13-27) so its own scaffold text ("NOT loaded every turn") is true under whichever pipeline remains after E4, or delete the stale claim from its scaffold. **Build size: trivial** — one-line fix once E4 settles which pipeline is authoritative.

**E8 (stretch, optional). Prompt-cache-aware stable/dynamic split.**
Borrow OpenClaw's stable-vs-dynamic context-file partition (B3) once E1/E4 land, to keep the always-loaded instructions tier above the provider's prompt-cache boundary and volatile content (per-turn retrieved memory, heartbeat state) below it — a cost/latency win, not a correctness fix. **Build size: medium**, and provider-dependent (only pays off on providers whose prompt caching keys off a stable prefix). Lowest priority of the eight.

---

## Return-to-founder summary (5-8 highest-leverage changes)

1. **Restore always-loaded instructions in the main chat path** (E1) — `SOUL.md`/`AGENTS.md`/`TOOLS.md` currently vanish from context entirely on the primary `sage_chat` surface; this is the single biggest correctness bug found. Small build.
2. **Rewrite the kernel prompt's memory rule to teach index+topic-files, not flat-append** (E2) — the topic-file tree already exists and works; the agent is just never told to use it. Small build.
3. **Add the self-correcting "MEMORY.md too big" nudge** (E3) — right now it silently truncates and loses facts with no signal; Claude Code's actual enforcement mechanism is what makes its index stay dense. Small-medium build.
4. **Collapse the two context-assembly pipelines into one** (E4) — same workspace currently behaves differently depending which chat surface (`sage_chat` vs `direct_chat`) a turn lands on. Medium build, needs parity testing.
5. **Delete the legacy regex-goal-string memory tool surface** (E5) — a second, fragile, redundant memory_read/write/list path exists next to the real typed one; pure cleanup, removes a footgun. Small build.
6. **Add a literal inbound envelope** (E6) — channel/sender/timestamp currently only gate tools invisibly; making it a visible transcript prefix (OpenClaw's exact pattern) closes a gap the founder already flagged independently. Medium build.
7. **Fix the `REFLECTION.md` self-contradiction** (E7) — trivial, but worth doing alongside E4 since it's caused by the same split-pipeline root cause.
8. *(Optional/stretch)* **Prompt-cache-aware stable/dynamic content split** (E8) — a cost/latency optimization borrowed from OpenClaw, not a correctness fix; do last if at all.
