# The Agent Backbone — where we are vs. the best, and the build order

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

The agent's core operating layer: how it manages its context window, its skills, its
tools, whether it can work **continuously** through a task, spawn **sub-agents**, compact
its own context, and **improve itself**. This is what separates a real agent (Claude Code,
Codex, Cursor) from a chatbot. Benchmarked against the authoritative sources.

**Source docs (all `docs/design/`):**
- `backbone-anthropic.md` — Claude Code + Agent SDK + Agent Skills, from live Anthropic docs
- `backbone-codex-cursor.md` — OpenAI Codex + Cursor + Agents SDK, from live official docs
- `backbone-empyralis.md` — our platform's current state, file:line-cited

**The convergence signal:** independently, all three top players do the same four things —
(a) **sub-agents for context hygiene** (disposable exploration threads that return a summary,
not just parallelism), (b) **plans/TODOs persisted as files**, not conversation state, so work
survives a restart, (c) a **cheap reviewer/classifier agent** between "always ask" and "full
autonomy," and (d) **scheduled self-wake** across days. When three independent teams converge,
it's the blueprint.

---

## The 7 dimensions

| # | Dimension | Best-in-class mechanism | What WE have | Gap | Build |
|---|---|---|---|---|---|
| 1 | **Context-window mgmt** | Tools listed by NAME, full schemas deferred/loaded on demand (Claude Code `ENABLE_TOOL_SEARCH`); skill listing capped at 1% of window; live `/context` breakdown | 2-tier: 11 always-on tools + lazy `query_tool_registry` (`tool_registry_service.py:213`); real proactive+reactive token-budget enforcement (`sage_agent_runtime_service.py:4956`) | Budget **never shown** to the user; tool discovery is **keyword, not semantic** | S–M |
| 2 | **Compaction** | 3 tiers: `/compact` summarize → server `compact_20260112` → `clear_tool_uses` context-editing | Real LLM-summarized proactive+reactive compaction + memory-flush-before-compact (`compaction_service.py`) | **Near-parity.** No material gap | — |
| 3 | **Prompt caching** | Exact-prefix, stable/dynamic split, 4 breakpoints, 1h TTL, 0.1× read | **Absent** — zero `cache_control`, only passive billing accounting | No stable/dynamic split (cost, not capability) | M |
| 4 | **Skills** | `SKILL.md` progressive disclosure (desc always-on, body on-demand) + **self-authoring A/B loop** (`skill-creator` blind-tests its own edits) | Real `SKILL.md` infra EXISTS (`skill_registry.py`) **but is DEAD** — no `skills/` dir, `build_active_skill_prompt_append()` only called from a health endpoint, never the chat runtime | Skills don't reach the agent at all; no self-authoring | **M (infra 80% built)** |
| 5 | **Sub-agents** | `Agent`/`Task` tool + `Workflow` (orchestration OUT of context), depth 5, 200/session; used for **context hygiene** | Only `fleet_message_agent`, **fire-and-forget**; `fleet_inbox` is written but **never read** (dead); no spawn/depth/fan-out | No real delegation, no blocking reply, no context-hygiene sub-agents | **L** |
| 6 | **Continuous work / tasks** | Agent loops until done (no hard turn cap); `/loop`, `ScheduleWakeup`, Routines, cloud tasks-to-PR; plan-as-file recipe | `bounded_scheduler_service.py` is strong (+ a native privileged-wakeup approval gate the others lack), BUT the action loop is **capped at 5 tool iterations/turn** (`_SAGE_OPERATOR_LOOP_MAX_ITERATIONS`); long tasks faked via repeated scheduled turns | **No continuous-work mode** — the single biggest "feels like Codex/Claude Code" gap | **M–L** |
| 7 | **Self-improvement** | Auto-memory with ENFORCED self-curation (hard-fail past cap); memory tool; skill self-authoring | `memory_write` now redacts secrets; memory tree works | No consolidation/"dreaming," no self-authored skills, `MEMORY.md` silently truncates (no enforced nudge) | M |

*Freshness note: the P0 correctness bugs from the earlier gap docs (tool-strip, dropped instructions, unredacted memory) were fixed + shipped this session — the inventory reflects the post-fix state.*

---

## Prioritized build order

### TIER A — the "it just keeps working" core (what you admire most about Claude Code / Codex)
1. **Continuous-work loop (#6).** Today the action loop hard-stops at 5 tool iterations and simulates long tasks with repeated scheduled turns. Build a real long-horizon mode: the agent works a multi-step task through **one persistent session** until done — the 5-cap becomes a soft budget guarded by the token-accounting we ALREADY have, with a durable **plan file** (Tier C #5) for restart-survival. This is *the* quality that makes an agent feel alive.
2. **Wire the dead Skills system (#4).** The `SKILL.md` infrastructure is 80% built and never called. Wire progressive disclosure (name+description always in context, body loaded on demand) into the chat runtime, add a `skills/` directory, and the **self-authoring A/B loop** (the agent writes and blind-tests its own skills — Anthropic's diamond). Highest ROI on the board: mostly wiring, not building.

### TIER B — real orchestration + visibility
3. **Real sub-agents (#5).** Replace fire-and-forget `fleet_message_agent` + the dead `fleet_inbox` with spawn / delegate / fan-out / block-for-reply, and specifically **context-hygiene sub-agents** (disposable exploration that returns a summary) — the pattern all three players converged on. Biggest single capability gap (also gap-map Tier 2).
4. **Context-window visibility + semantic tool discovery (#1).** Show the agent's context breakdown in the UI (you screenshotted Claude Code's `/context` — you want to *see* it), and upgrade `query_tool_registry` from keyword to semantic search.

### TIER C — durability + self-improvement
5. **Plans-as-repo-files (convergence).** Persist the agent's plan/TODO as a durable artifact — survives restarts, keeps the working context clean. Feeds Tier A #1.
6. **Self-improvement (#7).** Memory consolidation ("dreaming"), enforced `MEMORY.md` self-curation nudge, and self-authored skills (ties to #2).

### TIER D — cost/latency
7. **Prompt caching (#3).** Stable/dynamic prompt split for provider caching. Lowest priority — cost/latency, not capability.

---

## The "diamond" patterns worth copying (from `backbone-anthropic.md` §9 + `backbone-codex-cursor.md` §4)
- **Deferred tool-schema loading** — list tools by name, load full schemas on demand (we have a keyword version; make it the default + semantic).
- **Skill self-authoring A/B loop** — agent writes a skill, blind-tests old-vs-new, keeps the winner.
- **Sub-agents as context hygiene** — spend a disposable thread, keep only its summary in the main window.
- **Orchestration out of the context window** — a `Workflow`-style harness runs fan-out deterministically without bloating the agent's own context.
- **Plans/TODOs as files, not chat state.**
- **A reviewer/classifier tier** between always-ask and full-autonomy (we already have a native approval gate to build on).

---

## Recommended start
**Tier A first — continuous-work loop + Skills wiring.** They're the two highest-leverage items, they're the exact "just keeps working + extensible" qualities you named, and both are *partially built already* (the budget accounting exists; the skills infra exists). Then Tier B (real sub-agents + context visibility). This is a build wave, run like the reliability wave.
