# The Anthropic Agent Backbone: How Claude Code and the Claude Agent SDK Actually Work

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

Status: research reference, verified against live Anthropic docs on 2026-07-22.
Scope: the seven backbone dimensions requested — context-window management, compaction, prompt caching, Agent Skills, sub-agents, continuous/long-horizon work, self-improvement (memory). Every claim below is sourced from an official doc fetched during this research pass; URLs are inline.

This is a mechanism reference, not marketing copy: it documents the actual primitives, file formats, thresholds, and defaults so the patterns can be replicated on another platform (e.g. Empyralis's own per-agent harness).

---

## 0. Two backbones, not one

Anthropic ships **two** distinct things that both implement this backbone, and they are not the same product:

| | **Claude Code** (CLI/product) | **Claude Agent SDK** (library) |
|---|---|---|
| What it is | The interactive terminal agent, IDE extensions, Desktop app | `claude-agent-sdk` (Python) / `@anthropic-ai/claude-agent-sdk` (TypeScript) — Claude Code's harness packaged as a library |
| Who hosts it | You run it locally, or Anthropic's cloud/Remote Control environments | **You** — it's a library that runs the agent loop inside your own process, on your own infrastructure |
| Ships | Full interactive UI + all of the below | The *same* agent loop, context management, built-in tools (Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch, Monitor, AskUserQuestion...), hooks, subagents, permissions, MCP — programmable |
| Config surface | Terminal, settings.json, `.claude/` | Same `.claude/` filesystem config (skills, CLAUDE.md, agents) is loadable via `settingSources`/`setting_sources`, or you configure everything programmatically |

Everything documented below (context budgeting, compaction, caching, skills, subagents, memory, scheduling) is **the same mechanism** in both — the Agent SDK is explicitly "the same tools, agent loop, and context management that power Claude Code, programmable in Python and TypeScript." ([Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview))

Important adjacent distinction the user's org should keep straight: the Agent SDK is **not** the Claude API's Tool Runner (`client.beta.messages.tool_runner`, a thinner agentic loop over tools *you* define, no built-in tools) and **not** Managed Agents (Anthropic-hosted sandbox + REST API, session state lives server-side). Anthropic's own comparison table:

| | Agent SDK | Managed Agents |
|---|---|---|
| Runs in | Your process, your infrastructure | Anthropic-managed infrastructure |
| Interface | Python/TypeScript library | REST API |
| Agent works on | Files on your infrastructure | A managed sandbox per session |
| Session state | JSONL on your filesystem | Anthropic-hosted event log |
| Custom tools | In-process functions | Claude triggers the tool; you execute and return results |

([Agent SDK overview — Compare the Agent SDK to other Claude tools](https://code.claude.com/docs/en/agent-sdk/overview))

---

## 1. Context-window management

### The categories, and what's shown

Claude Code exposes a live breakdown via **`/context`**, and a full interactive teaching tool at [Explore the context window](https://code.claude.com/docs/en/context-window). The categories that count against the window, in the order they're composed:

1. **System prompt** — core behavior/tool-use instructions. Always loaded first, never shown to the user (illustrative example: ~4,200 tokens).
2. **Auto memory (`MEMORY.md`)** — Claude's self-written notes. Only the **first 200 lines or 25KB, whichever comes first**, load at session start.
3. **Environment info** — cwd, platform, shell, OS, git-repo flag. Git branch/status/recent commits load as a separate block at the very end of the system prompt.
4. **MCP tools (deferred)** — tool *names* only are listed by default; full JSON schemas are **not** loaded. Claude loads a specific tool's schema on demand via **tool search** when a task needs it. Controlled by `ENABLE_TOOL_SEARCH`: `auto` loads schemas upfront when they fit within **10% of the context window**; `false` loads everything upfront (legacy behavior); default defers.
5. **Skill descriptions** — one-line `description` (+ `when_to_use`) per skill, so Claude knows what it *could* invoke. Full skill body loads only on invocation. Skills marked `disable-model-invocation: true` are **excluded entirely** from this listing (zero context cost until the user types `/name`).
6. **`~/.claude/CLAUDE.md`** (user, global) then **project `CLAUDE.md`** — concatenated in root-to-leaf order down the directory tree.
7. **Everything after that** is the live conversation: user prompts, file reads, tool outputs, path-scoped rules (loaded only when a matching file is touched), hook `additionalContext` output, subagent summaries, etc.

Source: [Explore the context window](https://code.claude.com/docs/en/context-window), [How Claude Code works — The context window](https://code.claude.com/docs/en/how-claude-code-works)

### Deferred/lazy tool schemas — the actual mechanism

This is the single biggest token-budget lever in the stack. By default, MCP tool (and, per the how-it-works doc, some system tool) definitions are **listed by name only**; the full JSON input schema is fetched via a **tool-search call** only when Claude decides it needs that specific tool. This means a session with 40 MCP tools pays ~tens of tokens per tool for the name, not the hundreds-to-thousands of tokens each full schema would cost.

Config knobs (env vars):
- `ENABLE_TOOL_SEARCH=auto` — load schemas upfront **only if the full set fits within 10% of the context window**, otherwise stay deferred.
- `ENABLE_TOOL_SEARCH=false` — disable deferral, load every schema upfront (higher token cost, no round-trip latency for tool search).
- A server/tool can be marked `alwaysLoad` to opt out of deferral individually.
- MCP servers whose tools are deferred are the reason **connecting/disconnecting an MCP server mid-session does not invalidate the prompt cache** — deferred tool listings are appended, not rewritten into the cached prefix. (See §3.)

Source: [Context window doc](https://code.claude.com/docs/en/context-window) (MCP tools deferred event), [MCP — Scale with tool search](https://code.claude.com/docs/en/mcp#scale-with-mcp-tool-search) (referenced), [Prompt caching — Connecting or disconnecting an MCP server](https://code.claude.com/docs/en/prompt-caching)

### Budget visibility and management

- **`/context`** — live per-category token breakdown, with optimization suggestions and which CLAUDE.md/memory files actually loaded.
- **`/mcp`** — per-server token cost.
- **`/doctor`** — estimates the skill-listing's context cost and its biggest contributors; also proposes CLAUDE.md trims (cuts content a session could derive from the codebase — directory layouts, dependency lists — while keeping gotchas/rationale).
- **Skill-listing budget**: scales at **1% of the model's context window** by default (raise with `skillListingBudgetFraction` setting or `SLASH_COMMAND_TOOL_CHAR_BUDGET` env var). Each skill's combined `description` + `when_to_use` is hard-capped at **1,536 characters** (`skillListingMaxDescChars`). When the listing overflows the budget, Claude Code drops full descriptions starting with the **least-invoked** skills first, falling back to name-only.
- **1M-token context**: Fable 5, Sonnet 5, Opus 4.6+, Sonnet 4.6 support a 1M window (Sonnet 5 runs at 1M natively, no `[1m]` variant needed).

Source: [Skills — Skill descriptions are cut short](https://code.claude.com/docs/en/skills#skill-descriptions-are-cut-short), [Context window — Check your own session](https://code.claude.com/docs/en/context-window)

---

## 2. Context compaction

There are **two separate compaction systems** — one in Claude Code (client-orchestrated, summary generated by an LLM call), one newly server-side in the Claude API (beta) — plus a third, narrower mechanism (context editing) that clears rather than summarizes.

### Claude Code: `/compact` and auto-compact

**Trigger**: Claude Code compacts automatically "as you approach the limit" (no single published percentage in the current docs — described as "manages context automatically as you approach the limit"). It first **clears older tool outputs**, then **summarizes the conversation** if that alone isn't enough. Manual trigger: `/compact` (optionally `/compact focus on X` to steer what's preserved).

**Mechanism**: Claude Code sends a one-off request with the *same* system prompt/tools/history as the live conversation plus a summarization instruction appended as a final user message — this **shares the cached prefix**, so most of compaction's cost is generation, not a cache miss.

**What survives compaction** (exact table from the docs):

| Mechanism | After compaction |
|---|---|
| System prompt, output style | Unchanged (not part of message history) |
| Project-root `CLAUDE.md`, unscoped rules | Re-injected from disk |
| Auto memory | Re-injected from disk |
| Rules with `paths:` frontmatter | **Lost** until a matching file is read again |
| Nested `CLAUDE.md` in subdirectories | **Lost** until a file in that subdirectory is read again |
| Invoked skill bodies | Re-injected, capped at **5,000 tokens/skill**, **25,000 tokens total**; oldest dropped first |
| Skill *listing* (descriptions of uninvoked skills) | **Not re-injected** — only skills actually invoked survive |
| Hooks | N/A — hooks run as code, not context |

If a single file/tool-output is so large that context refills immediately after each summary pass, Claude Code **stops auto-compacting after a few attempts** and surfaces a "thrashing" error instead of looping forever.

Source: [Context window — What survives compaction](https://code.claude.com/docs/en/context-window#what-survives-compaction), [How Claude Code works — When context fills up](https://code.claude.com/docs/en/how-claude-code-works#when-context-fills-up)

### Claude API: server-side compaction (beta `compact-2026-01-12`)

This is the primitive under the hood that SDK/API callers can use directly, independent of Claude Code's UI:

- Beta header: `anthropic-beta: compact-2026-01-12`, edit type `compact_20260112`.
- **Trigger**: event-driven on `input_tokens`. Default threshold **150,000**, minimum configurable **50,000**.
- On trigger: the API generates a summary, wraps it in a `compaction` content block, and **discards every content block prior to that block**. The client just appends the *entire* response (compaction block included) back onto its message array to continue — the API does the cleanup.
- `pause_after_compaction: true` lets the caller halt right after the summary is generated (stop_reason `"compaction"`) for fine-grained control before continuing.
- Billing/rate-limit accounting: response `usage.iterations[]` breaks out a `"compaction"` iteration (the summarization pass's own input/output tokens) separately from the `"message"` iteration — **sum across `usage.iterations`** for the true total, since top-level `usage.input_tokens`/`output_tokens` only reflect the non-compaction iteration.
- Supported models: Fable 5, Mythos 5 & Preview, Opus 4.8/4.7/4.6, Sonnet 5/4.6.

Source: [Compaction — Claude Platform docs](https://platform.claude.com/docs/en/build-with-claude/compaction)

### Context editing (clearing, not summarizing) — the third lever

A narrower, cheaper, server-side primitive that pairs with both of the above:

- **`clear_tool_uses_20250919`**: clears the *oldest* tool **results** (optionally also tool call inputs, via `clear_tool_inputs: true`) once a trigger fires. Defaults: trigger at **100,000 input tokens**, keep the most recent **3** tool-use/result pairs, `clear_at_least` (minimum tokens to clear per activation, avoids trivial cache-invalidating clears), `exclude_tools` allowlist. Cleared content is replaced with a placeholder so Claude knows something was removed.
- **`clear_thinking_20251015`**: clears older extended-thinking blocks, keeping the last N thinking *turns* (or `"all"` to maximize cache hits). Default retention varies by model tier (e.g. Opus 4.5+ keeps all, Opus 4.1 and earlier keeps only the last turn).
- Both **invalidate the prompt cache** at the point of clearing (a fresh, shorter prefix gets cached going forward).
- Anthropic's stated integration pattern: **context editing + the memory tool together** — when clearing approaches, Claude receives an automatic warning and is expected to write anything important to a memory file *before* the tool results get cleared out from under it. See §7.

Source: [Context editing — Claude Platform docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)

**Takeaway for a replicable architecture**: Anthropic runs a three-tier defense — (1) cheap server-side *clearing* of stale tool output, (2) heavier server-side *compaction/summarization* when clearing alone isn't enough, (3) an agent-owned *memory tool* as the escape valve so nothing load-bearing is lost when 1 and 2 fire. Claude Code's `/compact` is the client-side analog of tier 2, with its own preservation rules layered on top (CLAUDE.md/auto-memory re-injection, capped skill re-injection).

---

## 3. Prompt caching

### The layer model (why Claude Code orders requests the way it does)

Every Claude Code turn re-sends the *entire* prior request plus what's new (models are stateless). Caching works by **exact-prefix match** — server-side, keyed on an identical prefix of the request. Claude Code deliberately orders content so cache-stable material comes first:

| Layer | Content | Invalidates when |
|---|---|---|
| System prompt | Core instructions, tool definitions, output style | Loaded tool-definition set changes, or Claude Code itself upgrades |
| Project context | `CLAUDE.md`, auto memory, unscoped rules | Session start, or after `/clear`/`/compact` |
| Conversation | Messages, tool results | Every turn (append-only — this is expected and cheap) |

A change anywhere in the prefix recomputes **everything after it** — there is no per-file/per-segment caching, only prefix matching. `/skill` invocations and plan-mode instructions are appended as conversation *messages*, so they never disturb the cached prefix.

Two things are part of the cache key but **not** part of the visible prompt text: **model** and **effort level**. Switching either forces a full recompute (Claude Code warns before an effort-level switch mid-session for exactly this reason).

Source: [Prompt caching — How the cache is organized](https://code.claude.com/docs/en/prompt-caching)

### What invalidates vs. survives

**Invalidates the cache** (partial or full miss on next turn): model switch, effort-level change, fast-mode toggle, connecting/disconnecting an MCP server *whose tools are loaded into the prefix* (deferred-tool servers are exempt — see §1), enabling/disabling a plugin that ships an MCP server, denying an entire built-in tool (`Bash`, `WebFetch`, bare-name deny rules), `/compact`, upgrading Claude Code.

**Keeps the cache**: editing files in the repo (reads append, don't rewrite history), editing `CLAUDE.md` mid-session (doesn't apply until reload — by design, so it can't invalidate), changing output style mid-session (same reason), permission-mode switches, invoking skills/commands (appended as messages), `/recap`, `/rewind` (truncates to an already-cached prefix), **spawning a subagent** (subagent builds its own separate cache; parent's prefix untouched).

### TTL and pricing

- **5-minute TTL** (default, API key / pay-per-token auth): no extra cost beyond the write multiplier.
- **1-hour TTL**: `ENABLE_PROMPT_CACHING_1H=1` on API-key auth, or **automatic** on a Claude subscription (usage is plan-included, so the longer TTL is free to request). Drops back to 5-min automatically if the subscription is over its usage cap and drawing on paid overage credits.
- Pricing multipliers (platform docs): **5-min write = 1.25× base input**, **1-hour write = 2× base input**, **cache read = 0.1× base input** (90% discount).
- Up to **4 explicit cache breakpoints** per request (`cache_control` on tools, system blocks, message content blocks, tool_use/tool_result blocks), or a single top-level `cache_control` that auto-applies to the last cacheable block. **20-block lookback window** per breakpoint for prefix matching.
- Minimum cacheable prefix length varies by model tier: 512 tokens (Fable 5/Mythos 5) up to 4,096 tokens (Opus 4.6/4.5, Haiku 4.5) — shorter prompts silently aren't cached (no error).
- **Cache scope in Claude Code**: effectively one machine + one working directory. The system prompt embeds cwd/platform/shell/OS/auto-memory paths, so different worktrees of the *same* repo build different prefixes and don't share cache. Parallel sessions in the *same* directory do share/read each other's cache.
- **Subagents get their own 5-minute-TTL cache**, always, even on a subscription session running the 1-hour TTL — because the 1-hour auto-TTL only applies to the main conversation.

Source: [Prompt caching (Claude Code)](https://code.claude.com/docs/en/prompt-caching), [Prompt caching (Claude Platform)](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

**Why this matters for a long agent session**: for a 100-turn coding session, each turn resends ~everything before it; without caching that's O(n²) token cost across the session. With a stable prefix (system prompt + CLAUDE.md + memory rarely change) and only the tail growing, effective marginal cost per turn approaches "cost of what's new" at ~10% of standard input pricing for everything re-read from cache.

---

## 4. Agent Skills

### The SKILL.md format and progressive disclosure

A skill is a directory (`<name>/SKILL.md` + optional supporting files) following the open [Agent Skills](https://agentskills.io) standard, extended by Claude Code. Minimal shape:

```yaml
---
description: What this skill does and when to use it. Put the key use case first.
---

## Instructions
...markdown body...
```

**Progressive disclosure is the core mechanism**, and it's precisely two-stage:
1. **Always in context**: the skill's `name` + `description` (+ optional `when_to_use`), listed so Claude knows the skill *exists*. This costs ~tens of tokens per skill (capped at 1,536 chars combined, see §1).
2. **Loaded only on invocation**: the full markdown body. Once loaded (by Claude auto-invoking it, or the user typing `/name`), the rendered content enters the conversation as a message **and stays there for the rest of the session** — Claude Code does *not* re-read the file on later turns, so re-invoking with identical rendered content just gets a "already loaded" note rather than a duplicate copy. Re-invoking with *different* arguments/dynamic output re-appends the full content.

A skill with `disable-model-invocation: true` is **not even in the always-on listing** — it costs zero context until the user types `/name` directly (used for side-effect skills like `/deploy`, `/commit`).

### Frontmatter — the full control surface

| Field | Effect |
|---|---|
| `description` / `when_to_use` | What Claude matches against to auto-invoke |
| `disable-model-invocation` | Only the user can invoke; hidden from Claude's listing entirely |
| `user-invocable: false` | Only Claude can invoke; hidden from the `/` menu |
| `allowed-tools` / `disallowed-tools` | Pre-approve or block tools *for the invoking turn only* (grant clears on next user message) |
| `model` / `effort` | Override for the turn the skill is active |
| `context: fork` + `agent:` | Run the skill's body as the **prompt for a subagent** instead of inline in the main conversation (see §5 crossover) |
| `paths` | Auto-activate only when Claude is working with matching files (same glob syntax as path-scoped CLAUDE.md rules) |
| `arguments` / `$ARGUMENTS` / `$N` / `${CLAUDE_SKILL_DIR}` / `${CLAUDE_PROJECT_DIR}` | Argument substitution, and path variables that resolve correctly regardless of whether the skill is personal/project/plugin-installed |
| `` !`command` `` inline syntax | **Dynamic context injection** — shell commands run *before* Claude ever sees the prompt, and their output is spliced in as static text (this is preprocessing, not something Claude executes) |

### Discovery and location resolution

Skills load, in priority order, from: enterprise/managed settings > `--agents`-equivalent session-scoped > project `.claude/skills/` (walked up to repo root, **and** discovered on-demand in nested subdirectories below cwd — monorepo packages can ship their own) > personal `~/.claude/skills/` > plugin `skills/`. Same-named skills at different scopes: higher-priority wins; nested monorepo skills get a directory-qualified alias (`apps/web:deploy`) so both stay reachable.

### The skill-creator loop — and can an agent author/improve its own skills?

**Yes, directly** — a skill is just a markdown file the agent can `Write`. Two concrete, documented paths:

1. **`/run-skill-generator`** — Claude runs the project from a clean environment, captures what actually worked (install commands, env vars, launch script), and **commits it as a new per-project skill** at `.claude/skills/run-<name>/`. Every subsequent `/run`/`/verify` invocation (by any agent, any session) follows the recorded recipe instead of rediscovering it. This is an agent authoring a skill from its own successful trial-and-error.

2. **The `skill-creator` plugin** (`/plugin install skill-creator@claude-plugins-official`) — a full **eval-and-iterate loop**, not just authoring:
   - Stores test cases (`evals/evals.json`: prompts, input files, expected behavior) inside the skill directory.
   - Runs each test case in an **isolated subagent** (clean context per run), records token count + duration.
   - **Grading**: checks each assertion, writes pass/fail + evidence to `grading.json`.
   - **Benchmark**: aggregates pass-rate/time/tokens for *with-skill vs. without-skill* into `benchmark.json`, so the token/latency overhead can be weighed against the accuracy gain.
   - **Version comparison**: blind A/B between two versions of the *same* skill, to confirm an edit is actually an improvement **before committing it**.
   - **Description tuning**: auto-generates should-trigger / should-not-trigger prompts, measures hit rate, and proposes `description` edits when the skill fires on the wrong requests.
   - **Review viewer**: an HTML report for qualitative human feedback the next iteration reads.

   Source: [Skills — Evaluate and iterate on a skill](https://code.claude.com/docs/en/skills#evaluate-and-iterate-on-a-skill), [skill-creator announcement](https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills)

This is a genuine closed-loop self-improvement mechanism for skills specifically (not memory): write → measure → A/B against the prior version → keep the winner. Worth flagging as a differentiator (see §9).

### Agent SDK skills — the one meaningful difference

The SDK loads skills the same way (filesystem, `SKILL.md`, progressive disclosure) but **cannot register skills programmatically** — they must exist as files. Control is via the `skills` option on `query()`: omit it to match CLI behavior (all discovered skills enabled), pass `"all"`, a name list, or `[]` to disable. `skills` is a **context filter, not a sandbox** — unlisted skills are hidden from the model and rejected by the Skill tool, but their files remain readable via Read/Bash. Also: SKILL.md's `allowed-tools` frontmatter is **CLI-only** — the SDK ignores it; tool access in SDK apps is controlled entirely via the top-level `allowedTools` option.

Source: [Agent Skills in Claude Code](https://code.claude.com/docs/en/skills), [Agent Skills in the SDK](https://code.claude.com/docs/en/agent-sdk/skills)

---

## 5. Sub-agents

### The primitive: the `Agent` tool (formerly `Task`)

Claude delegates via the `Agent` tool (renamed from `Task` in v2.1.63; both names still recognized in tool_use blocks / settings for compat). A subagent is a **separate agent instance with a fresh, isolated context window** — no parent conversation history, no auto memory, own system prompt, own tool/permission set.

### Built-in agent types (Claude Code)

| Agent | Model | Tools | What loads |
|---|---|---|---|
| `Explore` | Inherits main model (capped at Opus on the API) | Read-only (no Write/Edit) | **Skips** CLAUDE.md and git status — smallest/cheapest context |
| `Plan` | Inherits main model | Read-only | Also skips CLAUDE.md/git status; used during plan mode |
| `general-purpose` | Inherits main model | All tools | Full CLAUDE.md + git status |
| `statusline-setup`, `claude-code-guide` | Sonnet/Haiku | — | Narrow helper agents |

### Definition format (filesystem — CLI)

`.claude/agents/<name>.md` (project) or `~/.claude/agents/<name>.md` (personal), YAML frontmatter + markdown system prompt. Key fields beyond `name`/`description`: `tools`/`disallowedTools`, `model` (alias/full-id/`inherit`), `permissionMode`, `skills` (preload full skill *content*, not just description, at subagent startup), `mcpServers` (scope a server to only this subagent, keeping its tool descriptions out of the parent's context entirely), `memory` (`user`/`project`/`local` — persistent cross-session subagent memory, see §7), `isolation: worktree` (spin up a temp git worktree so the subagent edits an isolated copy), `background`, `maxTurns`, `effort`.

### Definition format (programmatic — Agent SDK)

`agents` option on `query()`, keyed by name, value is an `AgentDefinition` (`description`, `prompt`, `tools`, `disallowedTools`, `model`, `skills`, `memory`, `mcpServers`, `maxTurns`, `background`, `effort`, `permissionMode`). Functionally the same schema as the filesystem frontmatter, minus needing a file. Programmatic definitions **take precedence** over a filesystem agent of the same name.

### Isolation — exactly what crosses the boundary

| Subagent receives | Subagent does NOT receive |
|---|---|
| Its own system prompt + env details | Parent's conversation history / tool results |
| The Agent-tool delegation prompt Claude wrote | Parent's auto memory |
| Full CLAUDE.md hierarchy (except Explore/Plan, which skip it) | Parent's output style |
| Preloaded skill content (only if named in `skills`) | Parent's system prompt |
| A snapshot of parent's git status at session start | — |

The isolation is bidirectional and is *the* mechanism for context economy: a subagent can read 6,100 tokens of files and return a 420-token summary — only the summary (plus a small metadata trailer) lands in the parent's window. This is documented with exact illustrative numbers in the [context window walkthrough](https://code.claude.com/docs/en/context-window).

### Parallel fan-out, nesting, and limits

- **Parallel**: "Research the authentication, database, and API modules in parallel using separate subagents" — each gets its own context, results are synthesized by the parent afterward. Best when research paths are independent.
- **Nested** (subagents spawning subagents), since v2.1.172. **Depth limit: 5** levels below the main conversation, fixed, not configurable — a depth-5 subagent doesn't even receive the `Agent` tool. Depth is fixed at spawn time; resuming a background subagent later doesn't let it acquire more depth.
- **Session-wide cap: 200 subagents per session** by default (raise via `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION`, no hard ceiling, but can't be disabled). Every nested/forked/background subagent counts. `/clear` resets the counter.
- **Foreground vs background**: subagents run in the **background by default** (since v2.1.198) — the main session stays responsive; permission prompts from a background subagent surface in the main session naming the subagent. `Ctrl+B` backgrounds a running task manually.
- **Resume**: a completed subagent returns an `agentId`; `SendMessage` with that id continues its full history rather than restarting. Explore/Plan are one-shot (no agentId).
- **At true scale** (dozens-to-hundreds of agents), Claude Code has a *separate* primitive — **dynamic workflows** (`Workflow` tool) — that moves orchestration into a script the runtime executes outside the conversation entirely, so intermediate results live in script variables, not in Claude's context. Up to 1,000 agents/run, 16 concurrent. See §6.

Source: [Create custom subagents](https://code.claude.com/docs/en/sub-agents), [Subagents in the SDK](https://code.claude.com/docs/en/agent-sdk/subagents), [Orchestrate subagents at scale with dynamic workflows](https://code.claude.com/docs/en/workflows)

---

## 6. Continuous / long-horizon work, background tasks, scheduled wake-ups

Anthropic ships **four** distinct mechanisms here, deliberately layered by durability/scope:

| | `/loop` (session) | Routines (cloud) | Desktop scheduled tasks | Dynamic workflows |
|---|---|---|---|---|
| Runs on | Your machine, this session | Anthropic cloud | Your machine | Runtime, in-session but out-of-conversation |
| Needs machine on | Yes | No | Yes | Yes (part of a live session) |
| Needs open session | Yes | No | No | No (resumable within session) |
| Min interval | 1 minute | 1 hour | 1 minute | N/A (event-driven, not scheduled) |
| Survives restart | Restored on `--resume` if unexpired | Yes | Yes | No — fresh on next session |

### `/loop` — session-scoped cron

- **`CronCreate`/`CronList`/`CronDelete`** are the actual tools; `/loop` is a convenience skill wrapping them. Accepts a standard 5-field cron expression (vixie-cron semantics — day-of-month OR day-of-week match if both constrained). Up to **50 scheduled tasks per session**.
- Three modes: `/loop 5m <prompt>` (fixed interval, converted to cron), `/loop <prompt>` (Claude **self-paces** — after each iteration it calls the `ScheduleWakeup` tool to pick a 1-min–1-hour delay based on what it observed, e.g. short waits while CI is running, longer once quiet), bare `/loop` (runs a built-in maintenance prompt, or a project's `.claude/loop.md` override, capped at 25KB).
- **Scheduler mechanics**: checks for due tasks every second, enqueues at low priority, fires **between turns** (never interrupts a live response). **Deterministic jitter** (derived from task ID) spreads fire times so many sessions don't all hit the API at once — up to 30 min late for hourly+ tasks, up to 90s early for :00/:30 one-shots.
- **7-day expiry** on recurring tasks (fires once more, then self-deletes) — bounds how long a forgotten loop can run. `Esc` cancels a pending wakeup; in self-paced mode Claude can end its own loop by calling `ScheduleWakeup(stop: true)`, and if an iteration ends without rescheduling *or* stopping, Claude Code schedules one ~20-min fallback wakeup before giving up.
- Claude can also skip polling entirely and use the **`Monitor` tool** to watch a background script and react to each output line as an *event* — described as "often more token-efficient and responsive than re-running a prompt on an interval."

Source: [Run prompts on a schedule](https://code.claude.com/docs/en/scheduled-tasks)

### Dynamic workflows — the real long-horizon/scale primitive

This is the mechanism that answers "how does the agent keep working through a large multi-step task without stopping and without flooding the conversation." Claude **writes a JavaScript orchestration script** (top-level `await`, `agent()` to spawn one subagent, `pipeline()` to fan one out per item in a list) and a **runtime executes it in the background** while the interactive session stays free. Intermediate results live in **script variables**, not Claude's context — so a 500-file migration or a full-codebase audit doesn't blow the context budget the way turn-by-turn subagent delegation would.

- Trigger: natural language ("use a workflow to...") or the `ultracode` keyword; `/effort ultracode` makes Claude plan a workflow for *every* substantive task automatically.
- Bundled: `/deep-research` (fan out web searches across angles, cross-check sources, vote on claims, filter out anything that didn't survive verification).
- **Limits**: 16 concurrent agents (fewer on constrained machines), **1,000 agents total per run** (hard runaway-loop guard), a "Large workflow" UI warning above 25 agents or 1.5M projected tokens.
- **Resumable within the same session** — completed agents' results are cached; stopping and resuming re-runs only what was in flight. Exits Claude Code entirely = the workflow does *not* survive to the next session (this is the boundary where Routines/cron pick up instead).
- Saved as a reusable `/name` command in `.claude/workflows/` (project, shareable via VCS) or `~/.claude/workflows/` (personal).

Source: [Orchestrate subagents at scale with dynamic workflows](https://code.claude.com/docs/en/workflows)

### The documented long-horizon *pattern* (not a product feature — a recipe)

Anthropic's engineering blog documents a specific, battle-tested pattern for a task that spans **many separate agent sessions** (e.g. building a large app over days), using a two-agent split:

1. **Initializer agent** runs once: sets up a `claude-progress.txt` progress log, a JSON **feature checklist** (each feature has a `passes` boolean the agent may only flip, never delete — preventing accidental scope loss), and an `init.sh` environment-bootstrap script.
2. **Coding agent** runs every subsequent session: reads `claude-progress.txt` + git history + re-runs `init.sh` to reconstruct state from a **fresh context window**, works **one feature at a time** (explicitly to avoid "one-shotting" an entire project in a single context), and before ending: commits to git with a descriptive message, updates the progress file, and runs basic end-to-end verification. A feature is marked complete only after verification passes, not when the code is written.

Source: [Effective harnesses for long-running agents](https://anthropic.com/engineering/effective-harnesses-for-long-running-agents)

---

## 7. Self-improvement: memory

There are, again, **two distinct memory systems** — Claude Code's automatic one, and the Claude API's client-implemented memory tool — plus a documented multi-session pattern that ties memory to long-horizon work (§6).

### Claude Code: auto memory (`MEMORY.md` + topic files)

- Storage: `~/.claude/projects/<project>/memory/` (derived from the git repo — all worktrees/subdirectories of one repo **share one memory directory**; machine-local, never synced across machines).
- `MEMORY.md` is the **index** — the only file loaded automatically, and only its **first 200 lines or 25KB** (whichever comes first). Topic files (`debugging.md`, `api-conventions.md`, etc.) are **not** auto-loaded; Claude reads them on demand with its normal Read tool, using `MEMORY.md` as the map of what's stored where.
- **Self-curation, enforced**: as of v2.1.210, after Claude writes to `MEMORY.md`, Claude Code measures it against the 200-line/25KB cap. Near the limit, it **reminds Claude to shorten it** (one line per entry, move detail to topic files, merge/drop stale entries). Over the limit, the write still succeeds but Claude Code returns an explicit error telling Claude to rewrite the index, because everything past the limit silently drops on next load. This is a genuine self-governing memory-size loop, not just a soft convention.
- Entries get a `modified` ISO-8601 timestamp in YAML frontmatter automatically (since v2.1.214) — freshness is visible to both the user and Claude on read-back.
- Subagents get their **own separate memory** via the `memory: user|project|local` frontmatter field (§5) — `~/.claude/agent-memory/<agent-name>/` etc. — deliberately isolated from the main session's memory, with the same 200-line/25KB index rule.
- Toggle: `/memory` command (also opens/edits files), `autoMemoryEnabled` setting, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.

Source: [How Claude remembers your project](https://code.claude.com/docs/en/memory)

### Claude API: the memory tool (`memory_20250818`)

Generally available on the Messages API (no beta header). This is the *client-side*, protocol-level primitive Claude Code's auto memory is conceptually built on top of, and it's what you'd implement directly in a custom harness:

- Fully client-executed: Claude only ever **requests** a file operation against a virtual `/memories` root; **your application** executes it against storage you control (disk, DB, S3, encrypted store — anything) and returns a `tool_result`.
- Six commands, each with a specified request/response contract: `view` (directory listing or file content, optional `view_range`, images render inline, text >16,000 chars truncated), `create` (overwrites by spec, though returning an "already exists" error is a valid alternative), `str_replace` (errors on non-unique or missing match), `insert` (line-indexed), `delete` (recursive on directories; cannot delete `/memories` root itself), `rename` (errors if destination exists).
- The API **auto-injects** a system-prompt instruction whenever the memory tool is present in `tools`: *"ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE... ASSUME INTERRUPTION: your context window might be reset at any moment."* — you don't write this yourself.
- **Path-traversal is entirely your responsibility** — the docs explicitly warn `/memories/../../secrets.env`-style attacks must be blocked by your handler (canonicalize + verify containment; reject `../`, `..\`, URL-encoded traversal).
- Four SDKs (Python, TypeScript, C#, Java) ship ready-made helper classes (`BetaAbstractMemoryTool` to subclass, or `BetaLocalFilesystemMemoryTool` for an out-of-the-box local-disk backend) plus a `tool_runner` loop so you don't hand-roll the tool-use cycle.
- **Composability, explicitly documented**: memory tool + context editing (`clear_tool_uses_20250919`) together — Claude gets warned as clearing approaches and is expected to persist what matters to `/memories` first; memory tool + compaction together — "compaction keeps the active context small without client-side bookkeeping, and memory preserves the information that must survive summarization."

Source: [Memory tool — Claude Platform docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

### The multisession pattern (memory as recovery mechanism for long-horizon work)

Anthropic's own recommended pattern deliberately sets memory up **before** work starts, not ad hoc: an **initializer session** creates a progress log + feature checklist + reference to a startup script; **every later session** opens by reading those files to reconstruct state without re-exploring the codebase; each session **ends** by updating the progress log. Key discipline: mark a feature complete only after end-to-end verification, not when code is written — keeps the log trustworthy across dozens of sessions. (This is the same pattern as §6's long-horizon recipe — memory *is* the mechanism that makes that recipe work across a fresh context window each session.)

Source: [Memory tool — Multisession software development pattern](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool#multisession-software-development-pattern), case study in [Effective harnesses for long-running agents](https://anthropic.com/engineering/effective-harnesses-for-long-running-agents)

### Does the agent improve its own skills, not just its memory?

Yes — covered fully in §4: the `skill-creator` plugin's version-comparison A/B loop is the closest thing to genuine self-improvement of *behavior* (not just recall) in the stack, because it doesn't just append notes — it tests whether an edited skill actually performs better before the edit is kept.

---

## 8. Foundational design philosophy (why it's built this way)

Two engineering blog posts underpin nearly every mechanism above and are worth citing directly if replicating the architecture rather than just the primitives:

- **[Building Effective AI Agents](https://www.anthropic.com/engineering/building-effective-agents)** — the augmented-LLM baseline (retrieval + tools + memory) and the workflow-vs-agent taxonomy: prompt chaining, routing, parallelization (sectioning/voting), orchestrator-workers, evaluator-optimizer, vs. true agents that "dynamically direct their own processes... maintaining control over how they accomplish tasks." General principle: start with the simplest pattern, add orchestration complexity only when it demonstrably improves outcomes. (Dynamic workflows in §6 are essentially this doc's "orchestrator-workers" pattern, productized.)
- **[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)** — names the "attention budget" / context-rot framing that motivates everything in §1–3: compaction, **structured note-taking** (the conceptual precursor to the memory tool — cites Claude playing Pokémon maintaining tallies across thousands of steps *outside* context), sub-agent architectures returning **1,000–2,000-token condensed summaries** rather than shared state, and **just-in-time context retrieval** (lightweight identifiers — file paths, queries — resolved at runtime via tools, rather than pre-loading everything; explicitly contrasts Claude Code's CLAUDE.md-upfront-plus-glob/grep-on-demand hybrid).
- **[Effective harnesses for long-running agents](https://anthropic.com/engineering/effective-harnesses-for-long-running-agents)** — the two-agent (initializer/coding) + progress-file + git-commit pattern in §6/§7.

---

## 9. Genuine differentiators worth copying into Empyralis's own harness

Ranked by how load-bearing / non-obvious they are:

1. **Deferred MCP/tool-schema loading via tool search** (§1) — most competing agent stacks load every tool's full JSON schema into every request. Anthropic lists tool *names* only and fetches schemas on demand, with an explicit 10%-of-context-window auto-threshold. This is the highest-leverage token-budget primitive in the whole stack and is directly replicable: a name+description registry, plus a "load_schema(tool_name)" meta-tool.
2. **The skill-creator A/B-eval loop** (§4) — an agent that doesn't just write its own procedural knowledge but **measures whether an edit to that knowledge actually helped** (benchmark.json, blind version comparison) before keeping it. This is closed-loop self-improvement, not just accumulation.
3. **Enforced memory-index self-curation** (§7) — Claude Code doesn't just cap `MEMORY.md` at 200 lines/25KB, it actively nags/errors the agent into shrinking it when near the cap, with a hard failure mode ("everything past the limit silently drops") that forces discipline rather than relying on good behavior.
4. **Dynamic workflows moving orchestration into code, out of the context window** (§6) — a documented, explicit boundary: subagents work well for "a few delegated tasks per turn," but at "dozens to hundreds" the *plan itself* moves from Claude's context into a script the runtime executes, with results in script variables. This decouples fan-out scale from context cost — a pattern Empyralis's per-agent architecture doesn't currently have an equivalent for.
5. **The three-tier context-exhaustion defense on the API side** (§2) — cheap clearing (`clear_tool_uses`) → heavier compaction (`compact_20260112`) → agent-owned memory tool as the escape valve, explicitly composable and each with its own trigger/threshold knobs. Worth replicating exactly as three separate, independently-tunable mechanisms rather than one monolithic "summarize when full."
6. **Prompt-cache layer ordering as a first-class design constraint** (§3) — the explicit system-prompt/project-context/conversation three-layer split, with a documented table of exactly what invalidates each layer, is the kind of precision that turns "caching is on" into "cache hit rate is predictable and debuggable." The `cache_read_input_tokens`/`cache_creation_input_tokens` pair Anthropic recommends surfacing on a statusline is a cheap, high-value observability primitive.
7. **Self-paced scheduling via `ScheduleWakeup` + event-driven `Monitor`** (§6) — rather than fixed-interval polling being the only option, Claude can choose its own next-wake delay based on what it observed, and can subscribe to a background process's output stream as *events* instead of polling it. Cheaper and more responsive than cron-only designs.

---

## Source index (all URLs fetched live during this research pass)

**Claude Code docs**
- [Explore the context window](https://code.claude.com/docs/en/context-window)
- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)
- [Prompt caching](https://code.claude.com/docs/en/prompt-caching)
- [Extend Claude with skills](https://code.claude.com/docs/en/skills)
- [Create custom subagents](https://code.claude.com/docs/en/sub-agents)
- [How Claude remembers your project](https://code.claude.com/docs/en/memory)
- [Run prompts on a schedule](https://code.claude.com/docs/en/scheduled-tasks)
- [Orchestrate subagents at scale with dynamic workflows](https://code.claude.com/docs/en/workflows)

**Agent SDK docs**
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Subagents in the SDK](https://code.claude.com/docs/en/agent-sdk/subagents)
- [Agent Skills in the SDK](https://code.claude.com/docs/en/agent-sdk/skills)

**Claude API / Platform docs**
- [Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)
- [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

**Engineering blog**
- [Building Effective AI Agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Effective harnesses for long-running agents](https://anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Improving skill-creator: test, measure, and refine Agent Skills](https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills)
- [Building agents with the Claude Agent SDK](https://claude.com/blog/building-agents-with-the-claude-agent-sdk)
