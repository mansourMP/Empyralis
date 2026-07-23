# Memory Architecture Research — How the Industry's Best Agent Products Build Memory

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

Research date: 2026-07-23
Scope: live fetches this session from official sources only (platform docs, engineering blogs, help centers, primary papers). No secondary blog paraphrase used as a source of fact where the primary doc was reachable. Empyralis's own code is out of scope — this is the industry side; a separate audit (`docs/design/memory-context-design.md` and commit `80f9a2e31`) covers our implementation.

---

## Per-product memory architectures (cited)

### 1. Anthropic's memory tool (Claude Developer Platform)

Source: [Memory tool — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool), [Managing context on the Claude Developer Platform](https://claude.com/blog/context-management)

Mechanics, verified from the live doc:

- **Directory contract**: a single `/memories` directory. Claude gets one Anthropic-defined tool (`memory_20250818`) with six commands: `view`, `create`, `str_replace`, `insert`, `delete`, `rename`. The tool is **client-side** — Claude only *requests* file operations; the calling application executes them against storage it controls (disk, DB, cloud, encrypted store). "Memory lives entirely in your application."
- **Session-start instruction (verbatim, auto-injected by the API when the tool is present)**:
  > "IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE. MEMORY PROTOCOL: 1. Use the `view` command of your `memory` tool to check for earlier progress. 2. ... (work on the task) ... As you make progress, record status / progress / thoughts etc in your memory. ASSUME INTERRUPTION: Your context window might be reset at any moment, so you risk losing any progress that is not recorded in your memory directory."
- **Read/write policy**: Claude is told to keep the directory "organized," may rename/delete stale files, and is told not to create new files unless necessary. Developers can further scope what gets written, e.g. "Only write down information relevant to \<topic> in your memory system."
- **Retrieval is 100% model-driven plain-file reads — no embeddings, no vector DB.** `view` on a directory lists filenames + sizes (2 levels deep, tab-separated); `view` on a file returns line-numbered content (6-char right-aligned line numbers, 1-indexed), truncating text over 16,000 characters and supporting `view_range` for paging. This is architecturally identical to the founder's target: **index-first (directory listing) → pull individual files on demand.**
- **The doc explicitly frames this as "just-in-time context retrieval"**: "Rather than loading all relevant information up front, an agent records what it learns in memory files and reads them back on demand. This keeps the active context focused on the current task." It cites Anthropic's own "Effective context engineering" post as "the broader pattern" — i.e. Anthropic names index-first/pull-on-demand as *the* pattern, not an implementation detail.
- **Security is explicitly the developer's job, not the model's**: the doc calls out path-traversal (`/memories/../../secrets.env`) as a named attack, requires canonicalizing/validating every path server-side, recommends stripping sensitive data before writes (noting Claude "usually refuses" but that's not a guarantee), recommends capping file size, and recommends **memory expiration** — "periodically delete memory files that haven't been accessed in a long time."
- **Pairs with context editing + compaction**: context editing (auto-clears stale tool_use/tool_result blocks) keeps the live window small; compaction (server-side summarization near the token limit) handles the rest; memory is what survives both because it lives outside context entirely. Anthropic's own guidance: "consider using both: compaction keeps the active context small without client-side bookkeeping, and memory preserves the information that must survive summarization."
- **Numbers** (from [claude.com/blog/context-management](https://claude.com/blog/context-management), a 100-turn web-search agent eval): context editing alone → **84% token reduction**, **29% performance improvement**; **memory tool + context editing combined → 39% performance improvement** over baseline.
- **Multisession dev pattern** (same doc): an "initializer session" sets up memory files *before* real work starts — a progress log, a feature checklist, a reference to any init script — and every later session opens by reading those files first, updating the progress log before it ends. This is a deliberate index-file discipline, not ad hoc note-taking. The doc points to a fuller case study, see #3 below.

### 2. Claude Code's two memory systems

Source: [How Claude remembers your project — code.claude.com/docs/en/memory](https://code.claude.com/docs/en/memory)

This is the exact founder-cited pattern, confirmed live, contract-level:

**CLAUDE.md (project memory — human-written, always loaded):**
- Four scopes, load order broadest→narrowest: managed policy → user (`~/.claude/CLAUDE.md`) → project (`./CLAUDE.md` or `./.claude/CLAUDE.md`) → local (`./CLAUDE.local.md`).
- All CLAUDE.md/CLAUDE.local.md files from the working-directory chain **up to the filesystem root are loaded in full at launch**; files in *subdirectories* below cwd load lazily, only when Claude reads a file in that subdirectory.
- **Size guidance: target under 200 lines per CLAUDE.md file.** "Longer files consume more context and reduce adherence." No hard cap enforced — it's a written recommendation, not a rejection.
- `@path` imports expand into context at launch too (imports don't reduce context, only organize authorship); external imports (e.g. `~/.claude/...` from a project file) require a one-time approval dialog.
- CLAUDE.md content is delivered as a **user message after the system prompt**, not inside the system prompt — explicitly named as why compliance isn't guaranteed ("Claude reads it and tries to follow it, but there's no guarantee of strict compliance").
- Project-root CLAUDE.md **survives `/compact`**: re-read from disk and re-injected. Nested CLAUDE.md files are not auto-reinjected.

**Auto memory (agent-written, index-first + pull-on-demand — this is the exact target pattern):**
- Storage: `~/.claude/projects/<project>/memory/` — one directory per git repo (shared across worktrees), containing `MEMORY.md` (the index) plus any number of topic files Claude creates (e.g. `debugging.md`, `api-conventions.md`).
- **Exact load rule**: "The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the start of every conversation. Content beyond that threshold is not loaded at session start." — a hard, quantified budget, not a guideline.
- **Topic files are never loaded at startup.** "Claude reads them on demand using its standard file tools when it needs the information." This is the literal mechanism the founder described: small index always in context, 20+ topic files never enter the window wholesale, pulled only when needed.
- **Self-enforcing curation loop**: after every write to `MEMORY.md`, Claude Code measures it against the 200-line/25KB limit. Near the limit, Claude gets a reminder to shorten it — "keep one line per entry, move detail into topic files, and merge or drop stale entries." Over the limit, the write still succeeds but Claude Code returns an explicit error telling Claude to rewrite the index, "because everything past the limit is dropped on the next load." This is a built-in consolidation forcing-function, not a suggestion Claude can ignore.
- **Provenance field**: as of v2.1.214, any memory file with YAML frontmatter gets a `modified: <ISO 8601 timestamp>` field auto-stamped by Claude Code (not the model) on every write — "shows how current the fact is, both to you and to Claude when it reads the memory back." This is a built-in staleness signal, though it is a write-timestamp, not a source/attribution field (see Trust section below — this is a real gap even in the reference implementation).
- **Isolation is per-repo, not per-agent-conversation-thread**: "Auto memory is machine-local. All worktrees and subdirectories within the same git repository share one auto memory directory." Subagents do **not** inherit the parent's auto memory (except when forked); a subagent's own memory (if enabled) is a fully separate directory.
- Auto memory is **on by default**, toggleable via `/memory`, `autoMemoryEnabled` in settings, or `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.
- Auto memory is described as opportunistic, not exhaustive: "Claude doesn't save something every session. It decides what's worth remembering based on whether the information would be useful in a future conversation." Triggers named: corrections, preferences, build commands, debugging insights, architecture notes, workflow habits.

### 3. Anthropic engineering: context engineering + long-running-harness + multi-agent research posts

Sources: [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), [How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)

- Framing: context is "a finite resource with diminishing marginal returns" ("context rot") — the goal is "the smallest set of high-signal tokens that maximize the likelihood of your desired outcome," not maximum context stuffing.
- **Note-taking pattern named explicitly**: "structured note-taking (or agentic memory) is a technique where the agent regularly writes notes persisted to memory outside of the context window" — cited examples: Claude Code's own to-do-list mechanism, and custom agents maintaining a `NOTES.md`. The Pokémon-playing-agent case study is offered as proof this generalizes without any special memory prompting: the agent built tallies, maps, and combat strategy notes on its own.
- **Long-running-harness case study** (a concrete build, not just a idea): a first "initializer" session creates an `init.sh`, a `claude-progress.txt` log, and an initial git commit; every subsequent session reads `claude-progress.txt` first instead of re-deriving state; a JSON feature checklist with a `passes` boolean is the source of truth for scope, and agents are strongly instructed to edit only the `passes` field, never delete/rewrite tests; every session ends in a git commit so bad states are revertible. Recovery = git + progress file, not context replay.
- **Multi-agent research system**: memory is used specifically as a *truncation-survival* mechanism, not a knowledge base. Quote: the LeadResearcher agent "sav[es] its plan to Memory to persist the context, since if the context window exceeds 200,000 tokens it will be truncated and it is important to retain the plan." Subagents don't hand raw results back up the chain — they "call tools to store their work in external systems, then pass lightweight references back to the coordinator," explicitly to avoid "information loss during multi-stage processing" (i.e., avoid repeated summarize-of-a-summary degradation). This is index-first at the multi-agent level: pass pointers, not payloads.

### 4. Manus — file system as context

Source: [Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)

- Core argument: even 128K+ windows are insufficient for real agentic tasks — raw web/PDF content blows past them, model quality degrades on very long inputs regardless of nominal window size, and long inputs are expensive even with caching. Manus's answer: **"the file system [is] the ultimate context in Manus: unlimited in size, persistent by nature, and directly operable by the agent itself."**
- **Restorable compression** is the key design rule: never throw away information irreversibly — drop the *body* from context but keep a *reference* (e.g., a URL) that lets the agent restore the content on demand later. This is functionally the same "index/pointer in context, full content pulled on demand" shape as Claude Code's MEMORY.md → topic files, just implemented via URLs/file paths instead of a markdown index.
- **KV-cache economics are the other half of the argument, and this is Manus's most quantified point**: average input:output token ratio in agent loops is ~100:1, and (citing Claude pricing) cached input tokens cost $0.30/MTok vs $3/MTok uncached — a **10x cost difference**. This is why Manus avoids mutating early context (e.g. avoids dynamically adding/removing tool definitions mid-session — it masks token logits instead of altering the tool list, to keep the cacheable prefix stable). Not identical to memory-write policy, but it explains *why* index-first designs (small, stable index; large content pulled separately) are also a latency/cost optimization, not just a context-budget one.

### 5. Letta / MemGPT — the academic-to-product memory-hierarchy reference

Sources: [MemGPT: Towards LLMs as Operating Systems (arXiv:2310.08560)](https://arxiv.org/abs/2310.08560), [Letta docs — MemGPT](https://docs.letta.com/letta-memgpt), [Agent Memory: How to Build Agents That Learn and Remember](https://www.letta.com/blog/agent-memory/)

- **Paper abstract (verified)**: MemGPT proposes "virtual context management," explicitly modeled on OS-style hierarchical memory ("providing the appearance of large memory resources through data movement between fast and slow memory"), and uses interrupts to manage control flow between the agent and the user. Evaluated on document analysis beyond context-window size and multi-session chat.
- **Three-tier hierarchy** (OS analogy made explicit in both the paper and Letta's own materials):
  - **Core memory** = RAM equivalent — in-context, editable blocks (canonical example: a `persona` block and a `human` block) pinned into every prompt. Small, fixed-size, agent-writable via dedicated function calls (`core_memory_append`, `core_memory_replace`).
  - **Recall memory** = disk equivalent — the full conversation/interaction history, searchable but not actively loaded.
  - **Archival memory** = external, out-of-line storage, backed by a **vector database** (Chroma/pgvector by default) — this is the one tier of the "big five" reference architectures that leans on embedding retrieval rather than plain-file reads.
- **Self-editing memory**: the agent calls its own tools to rewrite its memory blocks mid-conversation — memory is not a passive log the harness updates, it's a first-class action space for the model.
- **"Sleep-time" / dreaming pattern (Letta's own extension beyond the original MemGPT paper)**: asynchronous, idle-period reorganization of memory by a separate process ("sleep-time agents"), explicitly contrasted with "lazy, incremental updates during conversations" — i.e. consolidation happens off the hot path.
- **Sharp RAG-vs-memory distinction, stated as a thesis in Letta's own blog**: "While retrieval (or RAG) is a tool for agent memory, it is not 'memory' in of itself." Their position: RAG retrieves static information; memory requires the system to actively curate what's *in* the context window and to let the agent revise its own understanding over time. This is the strongest explicit statement found anywhere in this research of "retrieval ≠ memory."

### 6. OpenAI — ChatGPT memory and Codex AGENTS.md

Sources: [Memory FAQ — OpenAI Help Center](https://help.openai.com/en/articles/8590148-memory-faq), [How does "Reference saved memories" work — OpenAI Help Center](https://help.openai.com/en/articles/11146739-how-does-reference-saved-memories-work), [AGENTS.md — learn.chatgpt.com/docs/agent-configuration/agents-md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) (redirect target of `developers.openai.com/codex/guides/agents-md`)

- **ChatGPT memory is two separate mechanisms, per OpenAI's own docs**: (1) "saved memories" — explicit user-told facts ("remember I'm vegetarian"); (2) "reference chat history" — an automatically-maintained rolling summary drawn from past conversations, distinct from the explicit list, shown to the user as "a memory summary" with a last-updated indicator, and directly editable by the user (type into the summary text box, or highlight text to correct it).
- User controls: delete individual memories, clear all, turn memory off entirely; temporary chats never read or write memory and aren't used for training.
- Stated safety stance: OpenAI says it's "taking steps to steer ChatGPT away from proactively remembering sensitive information, like your health details — unless you explicitly ask it to." This is a policy/training-based mitigation, not an architectural guardrail like Anthropic's path-restriction — notably softer than Anthropic's approach (see Trust section).
- No exact token/size budget is published for the ChatGPT memory summary in the reachable help docs (unlike Claude Code's explicit 200-line/25KB figure).
- **Codex AGENTS.md** functions as the CLAUDE.md analogue for Codex, not the MEMORY.md analogue — it is a human/team-authored "durable guidance" file, not agent-written memory:
  - Layered discovery: global scope (`~/.codex`, prefers `AGENTS.override.md` over `AGENTS.md`) → project scope, walked from git root down to cwd, same override/base file check at each level → files are concatenated root-down, "joining them with blank lines," with files closer to cwd appearing *later* in the merged prompt and therefore overriding earlier guidance.
  - **Explicit size cap: 32 KiB total** (`project_doc_max_bytes`), configurable; Codex stops adding files once the budget is spent — a hard ceiling analogous in spirit to Claude Code's 200-line/25KB MEMORY.md cap, but applied to the always-loaded instruction layer rather than an index-and-pull-on-demand layer. Codex, per the reachable docs, has **no documented equivalent of Claude Code's separate agent-written, pull-on-demand memory tier** — AGENTS.md is the whole story on the OpenAI/Codex side found in official docs this session.

### 7. Mem0 — dedicated memory-layer product

Source: [Memory Evaluation — Mem0 Docs](https://docs.mem0.ai/core-concepts/memory-evaluation)

- Six-stage pipeline: store → context lookup (find related existing memories, to avoid duplicating) → single-pass LLM "distill" extraction of facts → hash-based dedup + embed → graph/entity linking → temporal-metadata extraction.
- **Update policy is an explicit LLM-adjudicated decision per fact**: ADD (new) / UPDATE (augment existing) / DELETE (contradicted/obsolete) / NOOP (nothing changes) — but Mem0's own docs describe the net behavior as **ADD-only in practice**: "new facts are stored alongside old ones... nothing is overwritten or deleted... both the old and new facts survive," explicitly trading dedup cleanliness for preserved temporal history. This is a real, named alternative to "update-don't-duplicate" — Mem0 chose "keep both, let retrieval/ranking sort it out" over hard overwrite.
- **Retrieval is embedding-first, hybrid**: vector similarity (dominant signal) + BM25 keyword + entity-graph matching + temporal-intent scoring, fused by a rank score where "semantic relevance always dominates" and temporal signals nudge rather than filter.
- **Numbers**: LoCoMo benchmark 92.5%, LongMemEval 94.4% accuracy; **under 7,000 tokens per retrieval on average vs 25,000+ tokens for full-context/no-memory-layer baselines** on comparable long-conversation benchmarks (top-200 retrieval budget).

---

## The industry-default verdict (a–f)

### (a) Is index-first + pull-on-demand the industry default among agentic leaders?

**Verdict: yes, for the two industry leaders in developer-facing coding/agent tooling that this research could verify directly — Anthropic (both the platform memory tool and Claude Code) and, structurally, Manus — index-first + pull-on-demand is the explicitly named, documented default. It is not universal across the whole field: Letta/MemGPT and Mem0, the two dedicated "memory product" architectures, default to embedding-backed retrieval for their bottom tier (archival memory / vector search) rather than plain-file model-driven reads, and OpenAI's two products split the difference (ChatGPT memory = auto-summarized always-injected layer with no on-demand pull mechanism documented; Codex AGENTS.md = always-loaded only, no agent-written pull-on-demand tier found in official docs).**

Evidence for the "yes" side:
- Anthropic's memory-tool docs state the mechanism in so many words: "Memory supports just-in-time context retrieval. Rather than loading all relevant information up front, an agent records what it learns in memory files and reads them back on demand." It calls this "the broader pattern," pointing to its own context-engineering post as the canonical reference — i.e., Anthropic treats this as *the* doctrine, not one option among several.
- Claude Code's auto-memory is a literal, shipped implementation of exactly the founder's described shape: a small index (`MEMORY.md`, hard-capped at 200 lines/25KB) loads every session; topic files (unbounded count) are read only "on demand using its standard file tools when it needs the information" — never loaded at startup.
- Manus's "file system as ultimate context" + "restorable compression" (keep the pointer, drop the body, restore on demand) is the same shape applied to tool outputs/observations rather than long-term facts.
- Anthropic's multi-agent research system applies the same principle one level up: subagents pass "lightweight references," not raw results, to the coordinator.

Evidence for the caveat:
- Letta/MemGPT's archival tier is vector-retrieval-first by design (`archival_memory_search`) — the paper's whole contribution is treating context tiers like OS memory paging, and archival memory is explicitly "backed by a vector database." That's RAG-shaped, not index-file-shaped, for anything beyond the small pinned core-memory blocks.
- Mem0 is embedding-first for retrieval, full stop; its ADD-only architecture is a deliberate rejection of "one clean fact per topic file" in favor of "many overlapping timestamped facts, ranked at query time."
- OpenAI's ChatGPT memory has no documented pull-on-demand tier at all — it's a single always-injected rolling summary. Its Codex AGENTS.md is always-loaded-only (with a hard 32 KiB ceiling) and has no documented sibling "auto memory" layer in the official docs reachable this session.

**Net read**: index-first/pull-on-demand is the dominant, explicitly-doctrinal pattern specifically among the harness/agent-loop builders (Anthropic, Manus) — i.e., where the problem is "keep a long-running agent's live context small." It is *not* the default in the dedicated memory-layer / personalization-memory space (Letta, Mem0, OpenAI ChatGPT), where the problem is "recall the right fact out of a large, unstructured personal history" and embedding retrieval remains the default tool for that job. Empyralis's target use case (an agent's own working notes/facts about its environment, tasks, and users, loaded across sessions) maps far more closely to the Anthropic/Claude-Code/Manus problem than to the ChatGPT-personalization or Mem0-consumer-memory problem, which is why index-first is the right call for it — but it's worth being precise that this is "correct for our shape of problem," not "everyone does it this way."

### (b) Retrieval mechanics: plain files + model-driven reads vs embedding RAG vs hybrid

| Product | Mechanism | Why |
|---|---|---|
| Anthropic memory tool | Plain files, `view`/`str_replace`/etc., **no embeddings** | Just-in-time retrieval of an agent's *own* working notes — small, curated, high-trust corpus where the agent already knows roughly what it wrote and where |
| Claude Code auto memory | Plain files (`MEMORY.md` index + topic `.md` files), model reads via standard file tools | Same reasoning — repo-scoped, agent-authored, small corpus (tens of files, not millions of facts) |
| Manus | Plain files/URLs as externalized memory, agent decides what to read back | Same reasoning + KV-cache economics: a stable small in-context reference beats large, changing payloads |
| Letta/MemGPT archival memory | Vector DB (Chroma/pgvector), `archival_memory_search` | Archival tier is explicitly meant to hold data beyond what fits/matters for direct recall — an unbounded corpus where the agent doesn't know a priori what's relevant, so semantic search substitutes for the agent's own recall |
| Mem0 | Hybrid: vector similarity (dominant) + BM25 + entity graph + temporal scoring | Consumer/product memory layer serving many end-users' unstructured histories at scale — needs a ranking function, not a directory listing, because the corpus is large, un-curated, and queried by many different downstream apps |
| ChatGPT memory | Neither, really — a single auto-maintained rolling text summary, always injected | Optimized for simplicity/one summary per user, not for a large corpus; there is no "many files" problem to solve |
| Mem0 / Letta shared thesis | — | Both explicitly argue plain retrieval (RAG) is necessary but insufficient for "memory" — Letta's blog states it outright: RAG is a *tool*, not memory itself; memory requires active curation of what occupies the context window |

**When each wins**: plain-file/model-driven wins when the corpus is small, agent-authored, and high-trust (the agent wrote the index, so it can navigate it) — this is Anthropic's and Manus's regime. Embedding/hybrid retrieval wins when the corpus is large, arrives from many different sources/users, and the agent has no way to know in advance which of thousands of facts is relevant to the current query — this is Letta's archival tier and Mem0's whole reason for existing.

### (c) Write policies: auto vs explicit, dedup, consolidation/"dreaming", staleness

- **Auto vs explicit**: Claude Code auto-memory writes opportunistically and silently ("Claude doesn't save something every session... decides what's worth remembering"); ChatGPT writes both automatically (rolling summary) and on explicit user command ("remember that..."); Anthropic's platform memory tool is fully developer-directed — you can constrain what Claude is even allowed to write ("Only write down information relevant to \<topic>").
- **Dedup / update-don't-duplicate**: Claude Code's `MEMORY.md` self-discipline is closest to "update-don't-duplicate" — Claude Code actively nudges Claude to "merge or drop stale entries" when the index nears its size limit. Mem0 explicitly does the *opposite* by design (ADD-only, old and new facts both survive) and names the tradeoff itself: preserves temporal history at the cost of potential duplicate/contradictory facts surfacing together. This is the sharpest documented disagreement found in this research — worth flagging directly to the founder since Empyralis's "attributed index-first memory" work presumably has to pick a side.
- **Consolidation / "dreaming"**: only Letta names this explicitly — "sleep-time agents" reorganize/rewrite memory blocks asynchronously during idle periods, contrasted with "lazy, incremental updates during conversations." Neither Anthropic's memory tool nor Claude Code auto-memory documents an idle-time consolidation pass; consolidation in the Anthropic world happens synchronously, forced by the write-time size check (shorten now or the write silently truncates on next load).
- **Staleness handling**: Anthropic's memory-tool security guidance explicitly recommends "memory expiration — periodically delete memory files that haven't been accessed in a long time" (a developer responsibility, not automatic). Claude Code auto-memory (v2.1.214+) stamps a `modified: <ISO8601>` frontmatter field on every write so the *write time* is visible to both user and model — but this is not an automatic expiry/deletion mechanism, just a visible signal a human or the model can act on.

### (d) Size budgets — exact numbers documented

- **Claude Code MEMORY.md**: first 200 lines OR first 25KB, whichever is smaller — hard cutoff, enforced with a write-time warning-then-error mechanism.
- **Claude Code CLAUDE.md**: soft target of under 200 lines per file (not hard-enforced; `/doctor` in 2.1.206+ proposes trims).
- **Codex AGENTS.md**: hard 32 KiB (`project_doc_max_bytes`) total across the concatenated instruction chain.
- **Anthropic memory tool `view`**: truncates text file views over 16,000 characters (paged via `view_range`); directory listings go 2 levels deep.
- **Mem0 retrieval**: under ~7,000 tokens per query on average (top-200 retrieval budget) vs 25,000+ tokens for full-context/no-memory baselines on the same benchmarks — the only "tokens actually entering the window" figure found across all seven sources with an explicit head-to-head comparison.
- **Manus KV-cache economics** (not a memory-content budget, but the input-token-shape argument underneath why small stable indices matter): ~100:1 average input:output token ratio in agent loops; cached vs uncached input token pricing differs 10x (Claude Sonnet pricing cited: $0.30/MTok cached vs $3/MTok uncached).
- **Anthropic context-editing + memory combined**: 84% token reduction (context editing alone, 100-turn eval), 39% performance improvement (memory + context editing combined) — these are *session-length* savings, not a static "how much memory content enters the window" figure, but they're the strongest quantified evidence that index-first/pull-on-demand materially outperforms load-everything in Anthropic's own internal benchmark.
- **ChatGPT memory / OpenAI**: no published token or size budget found in the reachable help-center docs.
- **Letta core memory blocks**: docs reference per-block character limits as a concept (label/description/value/limit fields) but the specific reachable page did not surface an exact default number this session.

### (e) Trust & attribution — provenance, poisoning guards

This is the area where the industry is **least mature**, and it's worth stating plainly to the founder: none of the seven sources describe a robust, structural defense against untrusted inbound content becoming a trusted "memory fact." What exists:

- **Anthropic's platform memory tool**: the only source with an explicit security section, but its two safeguards are narrow — (1) path-traversal protection (a data-integrity/security concern, not a truth/provenance concern), and (2) "Claude usually refuses to write sensitive information to memory files" plus a recommendation that the *developer* add validation to strip sensitive data before writes. Nothing in the doc addresses an adversarial *inbound* message ("the user said X, so remember X as fact") being written to memory as if verified — the model is trusted to use judgment, full stop.
- **Claude Code auto-memory**: the `modified` timestamp field (v2.1.214+) is the closest thing to provenance in any source reviewed, and it records *when* Claude wrote the memory, not *who/what said it* or *how confident/verified it is*. There is no source-attribution field, no distinction between "the repo owner told me this" and "a file I read claimed this" in the documented schema.
- **OpenAI ChatGPT memory**: the stated mitigation is behavioral/training-based — "steer ChatGPT away from proactively remembering sensitive information... unless you explicitly ask" — which is a privacy control, not a provenance/poisoning control, and is explicitly softer than Anthropic's approach (Anthropic restricts by directory/path at the infrastructure layer; OpenAI relies on model judgment/training alone per its own docs).
- **Letta/Mem0**: neither source surfaced any attribution or provenance field in their memory schemas during this session's fetches. Mem0's ADD-only design at least preserves *all* historical versions of a fact (so a later audit could see contradictory entries and when each arrived, since temporal metadata is extracted per-fact) — an indirect, forensic form of provenance, but not a source-trust field at write time.
- **None of the seven sources document input-provenance tagging (e.g., "this fact originated from an untrusted inbound channel vs. the owner's direct instruction") as a first-class part of their memory schema.** This appears to be a genuine industry gap, not something this research simply failed to find — it's consistent with the fact that Anthropic's own security section for the memory tool is framed entirely around *storage-layer* security (path traversal, size, expiration, sensitive-data stripping) rather than *epistemic* security (is this fact even true, and who said so). Empyralis's stated standing requirement — attribution-aware memory with save filters — is ahead of what any of these seven official sources document as a shipped mechanism, and is closer in spirit to Mem0's per-fact temporal/provenance metadata than to anything Anthropic or Letta document.

### (f) Failure modes the sources warn about

- **Context rot** (Anthropic, "Effective context engineering"): performance degrades with token count even within nominal window limits — more context is not free, it actively costs attention/accuracy. This is the root justification for index-first design generally.
- **Truncation without a persisted plan** (Anthropic multi-agent research post): a lead agent that doesn't externalize its plan to memory before hitting the 200K-token truncation point loses its own plan — memory-as-plan-insurance is framed as a hard lesson, not a nice-to-have.
- **Information loss through repeated summarization** (same post): passing raw results up a multi-agent chain compounds degradation; the fix is references/pointers, not re-summarized payloads at every hop.
- **Cluttered / un-curated memory directories** (Anthropic memory-tool doc): "If Claude still creates cluttered memory files," the doc gives a specific reinforcing prompt to hand the model — this is presented as an expected failure mode requiring active developer intervention, not a solved problem.
- **Silent index truncation** (Claude Code docs): if `MEMORY.md` grows past 200 lines/25KB, "everything past the limit is dropped on the next load" — Claude Code turns this into a write-time error specifically because the failure mode (silently losing the tail of your own index) is bad enough to need a forced correction loop.
- **KV-cache invalidation from dynamic context edits** (Manus): removing/adding tools or otherwise mutating the early part of the prompt mid-session invalidates the cached prefix for every token after the edit point — a latency/cost failure mode, addressed by masking rather than removing.
- **ADD-only memory duplication/contradiction surfacing** (Mem0, self-disclosed): the docs explicitly flag that ADD-only "means semantically similar prior facts can surface alongside newer ones — particularly challenging for knowledge update tasks," i.e. Mem0 names its own known weak spot rather than papering over it.
- **Sensitive-data leakage into memory** (Anthropic + OpenAI, both flagged, both incompletely solved per their own docs): Anthropic notes the model "usually" refuses but that's not a guarantee, requiring developer-side validation; OpenAI relies on model steering/training with an explicit user override ("unless you explicitly ask").
- **Compliance is not enforcement** (Claude Code docs, stated directly): "CLAUDE.md instructions shape Claude's behavior but are not a hard enforcement layer... To block an action regardless of what Claude decides, use a PreToolUse hook instead." This applies to all memory content across every source reviewed — none of the seven treat memory as a guarantee, only as a strong prior the model can still ignore or misapply.

---

## The pattern to adopt, concretely

Given the above, the concrete shape that matches both the founder's stated target and what the strongest sources (Anthropic's memory tool + Claude Code + Manus) actually ship:

1. **One small index file per agent/session-scope**, hard-capped (Claude Code's own number: 200 lines or 25KB, whichever hits first) — loaded in full, every session, no exceptions.
2. **N topic/fact files, never loaded at session start** — read only via a model-driven `view`/read call when the agent decides it lacks context. No embeddings required at this scale; this only breaks down once the fact corpus grows large/unstructured/multi-tenant enough that the agent can't guess which file to open (that's the point at which Letta-style archival vector search or Mem0-style hybrid retrieval becomes the right tool, not before).
3. **Self-enforced index hygiene**: don't just cap the index size — actively nudge/force consolidation (merge duplicate entries, prune stale ones, move detail to topic files) the moment the index approaches its cap, the way Claude Code turns an over-limit write into an explicit error rather than a silent truncation.
4. **Externalize the plan before truncation risk, not after** — the multi-agent research system's lesson: persist critical state to memory proactively when context is *approaching* a limit, don't wait for an interrupt to force it.
5. **Restorable compression for large ephemeral content** (Manus's pattern): when trimming large tool outputs/observations from live context, keep a pointer/reference in the index rather than deleting the content outright, so it's recoverable on demand.
6. **Treat storage-layer security and epistemic/provenance security as two separate problems, and don't assume the industry has solved the second one for you.** Every source reviewed does the former (path validation, size caps, expiration) far better than the latter (source attribution, trust scoring, poisoning resistance). Empyralis's attribution-aware memory + save-filter requirement is genuinely ahead of the documented state of the art here — it is not something to import from a reference implementation, because none of the seven sources ship one. Suggested minimum bar, synthesized from the closest partial analogues found (Claude Code's `modified` timestamp + Mem0's per-fact temporal metadata): every memory entry should carry, at minimum, *when* it was written and *what turned it into a fact* (explicit owner instruction vs. agent inference from inbound content vs. tool output) — a provenance tag, not just a timestamp.
7. **Pick a deliberate stance on dedup vs. ADD-only**, because the two leading approaches genuinely disagree: Claude Code's index-hygiene model assumes update-don't-duplicate is achievable and desirable at small scale; Mem0's ADD-only model assumes it isn't worth the risk at large scale and opts for preserving all historical versions instead. At Empyralis's current scale (per-agent, per-repo notes, not a cross-user consumer memory product), the Claude Code stance is the correct fit — but this should be a stated choice, not a default.

---

## Sources

- [Memory tool — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- [Managing context on the Claude Developer Platform — Claude blog](https://claude.com/blog/context-management) (redirect target of anthropic.com/news/context-management)
- [How Claude remembers your project — Claude Code Docs](https://code.claude.com/docs/en/memory)
- [Effective context engineering for AI agents — Anthropic Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Effective harnesses for long-running agents — Anthropic Engineering](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [How we built our multi-agent research system — Anthropic Engineering](https://www.anthropic.com/engineering/built-multi-agent-research-system)
- [Context Engineering for AI Agents: Lessons from Building Manus — Manus blog](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [MemGPT: Towards LLMs as Operating Systems — arXiv:2310.08560](https://arxiv.org/abs/2310.08560)
- [MemGPT — Letta Docs](https://docs.letta.com/letta-memgpt)
- [Agent Memory: How to Build Agents That Learn and Remember — Letta blog](https://www.letta.com/blog/agent-memory/)
- [Memory FAQ — OpenAI Help Center](https://help.openai.com/en/articles/8590148-memory-faq)
- [How does "Reference saved memories" work — OpenAI Help Center](https://help.openai.com/en/articles/11146739-how-does-reference-saved-memories-work)
- [AGENTS.md — learn.chatgpt.com (Codex docs)](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Memory Evaluation — Mem0 Docs](https://docs.mem0.ai/core-concepts/memory-evaluation)

Not independently re-fetched (403/paywall on direct URL, relied on WebSearch snippet of the same official source only where noted above): OpenAI Memory FAQ pages returned HTTP 403 on direct WebFetch twice; content above is drawn from the WebSearch-tool's snippet extraction of the same official help.openai.com URLs, not from a third-party paraphrase.
