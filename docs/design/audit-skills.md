# Audit: the Skills pillar — official spec vs. Empyralis's live state

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). This document's body does not use "Sage" as prose; the remaining `sage_*`/`"sage-skills"`/`"sage-capabilities"` occurrences below are all literal code identifiers, endpoint paths, or telemetry token names — accurate code citations, not live product concept language.

Status: report-only audit, verified against live Anthropic docs and the `main`
branch of this repo on **2026-07-22** (later in the day than
`backbone-anthropic.md`/`backbone-empyralis.md`/`backbone-plan.md`, which were
all written earlier the same day). Backend files are being edited concurrently
by other agents while this audit was written — line numbers below were
re-verified at write time but will drift; re-grep the anchor function/comment
text quoted alongside each citation rather than trusting the number alone.

This audit does not repeat `backbone-anthropic.md` §4/§9 or
`backbone-empyralis.md` §1/§4 — it corrects one now-stale finding in the
latter, adds the parts of the official spec neither doc covered (the Messages
API Skills API, the current self-authoring guidance), and traces the *live*
prompt-assembly call graph one level deeper than either prior pass did,
because that's where the real picture changes.

---

## 1. What the official spec says today (fresh, with URLs)

### 1.1 The open standard — `SKILL.md` format

Source of truth: **[agentskills.io/specification](https://agentskills.io/specification)** (the vendor-neutral spec Claude Code explicitly builds on) and Anthropic's own restatement at **[platform.claude.com/docs/en/agents-and-tools/agent-skills/overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)**.

A skill is a directory; only `SKILL.md` is required:

```
skill-name/
├── SKILL.md          # required: YAML frontmatter + Markdown body
├── scripts/           # optional: executable code, run via bash, output-only in context
├── references/        # optional: docs loaded on demand
└── assets/             # optional: templates, images, data files
```

**Frontmatter fields** (agentskills.io is the superset; Anthropic's API enforces the same two required fields plus its own extras):

| Field | Required | Rule |
|---|---|---|
| `name` | Yes | 1–64 chars, lowercase letters/numbers/hyphens only, no leading/trailing/consecutive hyphens, **must exactly match the parent directory name**, no XML tags, cannot be a reserved word (`anthropic`, `claude`) |
| `description` | Yes | 1–1024 chars, non-empty, no XML tags, must state both *what* the skill does and *when* to use it, must be written in **third person** (this is what gets injected into the system prompt — "I can help you..." phrasing causes discovery problems) |
| `license` | No | license name or reference to a bundled file |
| `compatibility` | No | ≤500 chars; environment requirements (target product, required packages, network access) |
| `metadata` | No | arbitrary string→string map for client-specific extensions |
| `allowed-tools` | No | space-separated pre-approved tool list; experimental, support varies by client |
| `display_title` | No (API-only) | must be unique among a workspace's custom skills — Claude API Skills API extra, not part of the open spec |

**Progressive disclosure is exactly three levels**, same framing in both the open spec and Anthropic's docs:

| Level | When loaded | Token cost | Content |
|---|---|---|---|
| 1. Metadata | Always, at startup | ~100 tokens/skill | `name` + `description` only |
| 2. Instructions | When the skill is triggered | recommended <5,000 tokens (spec says keep the whole `SKILL.md` under 500 lines) | the Markdown body, read via bash `cat`/Read — enters context in full, once |
| 3. Resources/code | Only as referenced | ~0 until touched | `scripts/` run via bash (only stdout enters context, never the script source); `references/*.md` read only if `SKILL.md` links to them |

Mechanically, on Claude Code / the Claude API's code-execution sandbox: the model uses **bash** to `cat SKILL.md` when it decides the description matches the task — this is a live filesystem read triggered by the model itself, not a framework-side injection. Keep file references **one level deep** from `SKILL.md` (spec explicitly warns Claude may only partially read doubly-nested references, e.g. via `head -100`).

Source: [Specification — agentskills.io](https://agentskills.io/specification), [Agent Skills overview — Claude Platform docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview), [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)

### 1.2 Claude Code's specific extensions

Already fully documented in `backbone-anthropic.md` §4 (frontmatter control-field table, discovery/location resolution order, the 1%-of-context-window listing budget, 1,536-char description cap, `disable-model-invocation`/`user-invocable` gating). Not repeated here. One number worth restating because it's directly relevant to the fix list below: Claude Code caps re-injected skill bodies at **5,000 tokens/skill, 25,000 tokens total** on compaction, oldest-invoked-first — i.e. even Level 2 has a budget, it isn't "inject everything active."

### 1.3 The Messages API Skills API — not covered by either prior backbone doc

This is new ground. Skills are also a first-class **Claude API** primitive, separate from Claude Code's filesystem-based skills, with its own REST surface:

- Beta headers: `skills-2025-10-02` (Skills), `code-execution-2025-08-25` (the sandbox skills run in — **required**, Skills only work inside the code-execution container), `files-api-2025-04-14` (if uploading/downloading files).
- Skills are attached per-request via `container.skills[]`, up to **8 skills per request**: `{"type": "anthropic"|"custom", "skill_id": "...", "version": "latest"|<date>|<epoch>}`.
- **Pre-built skills**: `pptx`, `xlsx`, `docx`, `pdf` — Anthropic-maintained, available to all API users.
- **Custom skills**: uploaded via `POST /v1/skills` (zip or individual files, ≤30MB total, common root directory required), managed via `client.beta.skills.create/list/retrieve/delete` and `client.beta.skills.versions.*`. Custom skills are **workspace-wide** on the API (unlike claude.ai, where they're per-user and don't sync to the API).
- **Runtime constraint that matters for Empyralis**: on the Claude API, skills run in a **sandboxed container with no network access and no runtime package installation** — only pre-installed packages. This is Anthropic's own hosted VM, not the caller's infrastructure.
- Generated files come back as `file_id`s resolved through the Files API.

Source: [Using Agent Skills with the API](https://platform.claude.com/docs/en/build-with-claude/skills-guide), [Agent Skills overview — Claude API section](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview#claude-api)

**Why this matters for Empyralis and why we should NOT adopt it directly:** Empyralis's agent runtime is a custom multi-tenant, multi-provider harness (`sage_agent_runtime_service.py`) that calls the Anthropic/OpenAI/etc. Messages APIs directly — it is not Claude Code and does not run inside Anthropic's hosted code-execution sandbox. The `/v1/skills` + `container.skills` mechanism only works when the *tool call* is Anthropic's own `code_execution_20250825` tool running in *Anthropic's* VM — it cannot wrap Empyralis's own tool-calling loop, our own filesystem, or non-Anthropic models. The right target to replicate is the **open standard's mechanism** (filesystem-based `SKILL.md`, progressive disclosure implemented in our own prompt-assembly code) — which is exactly the shape `skill_registry.py`/`installed_skills.py` already half-built — not the hosted API product. Section 3 below is written against that target.

### 1.4 Self-authoring — current official guidance (this is human-assisted today, not autonomous)

Two distinct, currently-documented mechanisms, neither of which is "the agent silently writes its own skills mid-conversation with no human in the loop":

1. **The "Claude A / Claude B" iterative pattern** — [Skill authoring best practices § Develop Skills iteratively with Claude](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices#develop-skills-iteratively-with-claude). One Claude instance ("Claude A") is asked directly — *"Create a Skill that captures this analysis pattern we just used"* — and writes a properly-structured `SKILL.md` natively (no special meta-skill needed to teach it the format). A second instance ("Claude B", fresh context, skill loaded) is given real tasks; a human observes where it struggles and brings specifics back to Claude A ("it forgot to filter test accounts even though the skill mentions it — reorganize so that rule is more prominent"). This repeats. **Evaluation-driven development is the recommended starting point, before writing extensive docs**: run the task without a skill first, capture 3+ concrete failure scenarios as JSON evals (`{"skills": [...], "query": "...", "files": [...], "expected_behavior": [...]}`), establish a baseline, write minimal instructions that close the gap, iterate.
2. **The `skill-creator` plugin's automated eval/A-B harness** — already fully documented in `backbone-anthropic.md` §4/§9 (isolated-subagent test runs, `grading.json`, `benchmark.json` with-vs-without-skill token/pass-rate comparison, blind version-A/B comparison before keeping an edit, auto-generated should/should-not-trigger prompts for description tuning). This is the more automated half of the loop, but it still starts from a human (or an orchestrating session) invoking `/plugin install skill-creator` and deciding when to run it — it is not a standing background loop the agent runs unprompted.

Anthropic is explicit that full autonomy is a stated *future* goal, not current behavior: *"looking further ahead, we hope to enable agents to create, edit, and evaluate Skills on their own, letting them codify their own patterns of behavior into reusable capabilities"* — [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills). Design Empyralis's self-authoring fix (§3, item 7) against *today's* documented pattern (a callable write-and-validate tool, human/owner reviews before it's trusted) — not against a fully unsupervised loop Anthropic itself hasn't shipped.

---

## 2. Empyralis's exact state today — traced call graph, file:line

The honest one-line version: **Empyralis has more skill-shaped infrastructure than either prior audit credited it for, spread across at least five different subsystems that do not share a catalog with each other, and the specific mechanism that would inject a real `SKILL.md` body into a live model turn has *zero* reachable call sites from the main chat runtime as of today** — one narrower than what `backbone-empyralis.md` found this morning, because the one remaining legacy call site it cited has since been physically deleted.

### 2.1 The live chat runtime — where a real turn's prompt actually gets built

Both live chat surfaces funnel into one prompt builder:

- `sage_chat` → `sage_agent_runtime_service._run_sage_action_loop_v3` (`server_modules/sage_agent_runtime_service.py:2755`)
- `direct_chat` → `direct_chat_generation_service.stream_provider_backed_direct_chat`
- System prompt assembly for both: `sage_instruction_compiler_service.build_sage_instruction_bundle` (`server_modules/sage_instruction_compiler_service.py:596`)

`_run_sage_action_loop_v2`/`_run_sage_action_loop_v1` were **physically deleted** — the comment left in their place is explicit: *"removed here — dead code with zero live callers (handle_sage_chat only ever invokes `_run_sage_action_loop_v3` above). It housed the old keyword-matched MCP NL-routing (`_matching_mcp_skill`, also removed) that v3 never used."* (`sage_agent_runtime_service.py:3195-3202`). This is the code that, per `backbone-empyralis.md`'s morning pass, held the *one* remaining call site into `skill_registry.execute_skill` from inside the turn loop — that citation (`sage_agent_runtime_service.py:3338-3359`) **no longer points at that code**; lines 3330-3365 today are `_run_memory_flush_before_compaction`, unrelated to skills. **`grep -n "execute_skill(" server_modules/sage_agent_runtime_service.py` returns zero matches.** Two comment blocks confirm this was deliberate, not an accident: `sage_agent_runtime_service.py:33-38` ("the old keyword-matched MCP bridge that called `skill_registry.execute_skill` was removed... `skill_registry.execute_skill` remains the real, live executor for the goal-based `/skills mcp:server:tool` slash command") and `:2929-2933` ("The old keyword-matching bridge... has been removed; it was dead code — v2 had no live caller").

### 2.2 Five disconnected "skill" subsystems

**(A) `skill_registry.py` (1,307 lines) — the richest catalog, but only reaches the frontend, never the model.**
`SkillDefinition` dataclass (`:31-49`); 20 built-in entries in `_BUILT_IN_SKILLS` (`:779-1031`: `email-access`, `web-search`, `browser`, `calendar-access`, `task-runner`, `inventory-tool`, `crm-notes`, the 6 bundled skills `memory-manager`/`code-runner`/`file-manager`/`telegram-bot`/`vision-monitor`, 5 fleet-management skills, `memory-read`/`memory-write`/`memory-list`); `list_skill_definitions()` (`:1147-1148`); dispatch `execute_skill()` (`:1188-1306`) tries, in order: a real Python `executor` callback → `handler.py` subprocess adapter → MCP-tool adapter → `_BUNDLED_SKILL_DISPATCH` map (6 entries, `:332-339`) → **the actual progressive-disclosure Level-2 mechanic**: if `definition.path` is set, load `<path>/SKILL.md` and inject its raw text as a `"skill-context"` artifact (`:1278-1306`).

The consumer of `list_skill_definitions()` in the live turn is `_load_safe_skill_catalog()` (`sage_agent_runtime_service.py:1498-1514`), called at `:3770`. Its output, `safe_skills`, is used for exactly one thing downstream: it's returned as the `available_tools` field in the chat API response (`:4707`, `:5330`) — **API/frontend metadata for the Tools tab UI, not system-prompt text.** `used_context.append("sage_skills")` (`:3772`) is a telemetry marker in the same response payload (`used_context` is returned to the caller at `:4229`/`:4326`/`:4705`/etc.), not something that feeds `build_sage_instruction_bundle`. **Confirmed by reading `build_sage_instruction_bundle`'s full parameter list (`sage_instruction_compiler_service.py:596-616`): it does not accept `safe_skills` or anything derived from `skill_registry.list_skill_definitions()` at all.**

`execute_skill()` — the function that actually does Level-2 body-loading — has exactly two live (non-test) callers in the whole repo:
1. `command_registry.py:997-1001`, inside `_handle_skills()` (`:983-1017`), registered as the literal slash command `/skills` (aliases `/skill`) at `command_registry.py:483`. `command_registry`'s dispatcher only fires on messages that literally start with `/` (`parse()`/`dispatch()`, `:328-388`) — this is a pre-LLM string match a user has to type verbatim, never something the model decides to invoke on its own.
2. `universal_operator.py:440`, inside `execute_customer_turn`/`execute_customer_turn_in_process` (`:249`, `:317`) — a **separate agent engine** with its own manifest-based `build_system_prompt()` (`universal_operator.py:14`), reachable only from `agent_registry_api.py:1460` (route `/agents/customer-preview/respond`) and `hosted_secure_worker.py:100` (a sandboxed subprocess worker for that same feature). Both are member-authenticated dashboard **preview/testing** endpoints — not the path real deployed-agent traffic runs through. `detect_skill_need()` (`skill_registry.py:1163-1184`, keyword router — matches exactly one skill, `inventory-tool`, on tokens like "in stock"/"sku"/"fitment") is also only called from this same preview engine (`universal_operator.py:64,433`), never from the main runtime.

**(B) `installed_skills.py` (949 lines) — the directory-based, marketplace-shaped system closest to the open standard, and the one with zero content anywhere.**
`skill_roots()` (`:49-54`) defines three tiers: `workspace_skills_root()` = `<repo_root>/.orion-stack/skills/` (`:34-35`), `global_skills_root()` = `~/.orion-stack/skills/` or `$ORION_INSTALLED_SKILLS_DIR` (`:38-42`), `bundled_skills_root()` = `<repo_root>/skills/` (`:30-31`). `list_installed_skills()` (`:697-815`) scans all three, parses `SKILL.md` frontmatter via `_parse_skill_frontmatter()` (`:169-180` — **a bare `yaml.safe_load()` with zero validation**: no length check, no lowercase/hyphen enforcement, no name-matches-directory check, no reserved-word check — none of §1.1's rules are enforced today), runs `skill_scanner.scan_skill_dir()` (a real security scanner — line-by-line pattern matching for suspicious shell/network calls, called at `:767`) for a critical-finding gate, and returns each skill's full `skill_body`.

**Verified empty, all three roots, right now:** `find /Users/mansur/empyralis -maxdepth 2 -iname skills` returns nothing at repo root; `ls .orion-stack/` shows `agent-computer, browser-profile, downloads, logs, memory, pdf, pids, runtime_sessions, screenshots, transcripts, workspace` — no `skills` subdirectory; `~/.orion-stack/skills/` does not exist either. **`marketplace/registry.json` — the marketplace pipeline's storage — is `{"version": 1, "skills": []}`, empty.** So `list_installed_skills()` returns `[]` in this environment today, full stop — this is true independent of the wiring question in (A); even a perfectly-wired prompt path would have nothing to list.

`build_active_skill_prompt_append()` (`:837-857`) is the function most directly matching `backbone-plan.md`'s original claim. Confirmed still true, one caller: `health_diagnostics.py:299` (a diagnostics endpoint). Imported but never called at `runtime_config.py:183`. **Worth flagging as a design bug independent of reachability**: this function is not actually progressive disclosure — it concatenates **every active skill's full body** unconditionally (capped at 12,000 chars total via `merge_skill_prompt_append`, `:826-834`), with no name+description-only listing tier and no per-skill trigger gating. Wiring this exact function into the live path would not reproduce Claude Code's behavior; it would reproduce "always paste every skill's whole instructions into every turn," which defeats the token-economy point of the pattern.

A second, more real-looking function in the same file, `query_active_installed_skills()` (`:897-949`), actually **runs** each active skill's `handler.py`/`query_handler.py` subprocess with a structured query payload and returns `{"handled", "response", "prompt_append", "active_skill_ids", "errors"}` — this is genuinely closer to an invocation model. It has zero live callers into the main chat runtime, but it is not orphaned — see (D).

**(C) `sage_skills_api.py` (505 lines) — a *third*, independent catalog, and this is the one that actually reaches the live system prompt.**
`build_sage_capabilities_payload()` (`:400-415`) merges `_builtin_capability_records()` (infra entries: model route, memory retrieval, local companion, billing metering — `:255-286`) + `_skill_capability_records()` (from `_build_sage_skills_payload()`, `:418-468`, which itself merges the **hardcoded 4-item `_CURATED_SKILL_PACK`** — 1Password/Apple Notes/Apple Reminders/tmux, `:26-…` — with whatever `list_installed_skills()` returns, i.e. currently nothing) + `_mcp_capability_records()` (approved MCP tools). This payload flows into `build_model_capability_manifest()` (`sage_instruction_compiler_service.py:383-412`) → `_capability_manifest_text()` (`:415-437`) → rendered as a **`## Callable Tools`** section, capped at **16 items** (`CAPABILITY_MANIFEST_MAX_ITEMS = 16`, `:54`) with a **140-char** per-item description clip (`CAPABILITY_DESCRIPTION_CHAR_LIMIT = 140`, `:55`) → spliced into the live prompt via `append_section("capabilities", ...)` at `build_sage_instruction_bundle:667`.

**This is the closest thing Empyralis has to real Level-1 progressive disclosure today, and it is completely disconnected from `skill_registry.py`'s 20-item catalog** — different source list, different fields, different cap mechanism (fixed 16-item/140-char vs. Claude Code's dynamic 1%-of-context-window/1,536-char). Two consequences worth naming precisely: (1) the Tools-tab-visible catalog (A) and the model-visible catalog (C) are not the same set — a skill enabled in the Tools tab is not guaranteed to appear in what the model is told it can call, and vice versa; (2) per `docs/PLATFORM-MAP.md` Part 14 #3, the 4 curated-pack items that make up most of "type=skill" entries here (1Password/Apple Notes/Apple Reminders/tmux) have **zero execution implementation anywhere** — the model is told these are "ready, callable" tools (Level 1 fires), and calling one falls through to `skill_registry.py:52-62`'s `_manual_skill_stub`, which literally replies *"Heads up: {skill_label} is not wired to a live execution path yet."*

**(D) The Telegram/WhatsApp "autopilot" polling path — live by default, structurally parallel to everything above, and the one place `query_active_installed_skills()` is actually wired in.**
`ORION_TELEGRAM_AUTOPILOT_ENABLED` and `ORION_WHATSAPP_AUTOPILOT_ENABLED` both default to `True` (`runtime_config.py:600,632`); delivery mode defaults to `"polling"` everywhere it's read (`connectors_actions.py:850`, `connector_runtime.py:75`, `telegram_connector_services.py:164`, all `or "polling"`). Two divergent paths exist for an inbound Telegram message: **webhook mode** routes through `agent_channel_router` → `sage_turn_adapter.execute_sage_turn` → the same main runtime as §2.1 (never touches skills at all here); **polling/"autopilot" mode** (the unset default) builds a `run_goal` string that **literally string-concatenates** `TelegramConnectorContextService.installed_skill_query()`'s (`server_modules/connectors/telegram_connector_context_service.py:152-185`, calling `installed_skills.query_active_installed_skills`) `prompt_append` text into the goal (`telegram_run_action_service.py:157-174`, `telegram_terminal_service.py:227-240`) before dispatching to a **third** execution engine (`create_run`), bypassing `sage_instruction_compiler_service.build_sage_instruction_bundle` entirely. `docs/PLATFORM-MAP.md:454-461` lists ~18 `autopilot_*` files as plain inventory with no legacy/deprecated marker (the only explicit "dormant" language in that doc, at `:1183`, is scoped narrowly to WhatsApp-via-Twilio, not autopilot generally) — this path should be treated as live production code, not dead scaffolding. In today's environment it has nothing to inject (§2.2.B — `list_installed_skills()` is empty everywhere), so the pipe is live but currently carries zero content; that will change silently the moment anyone drops a skill into `.orion-stack/skills/`, with no corresponding change on the main-runtime side.

### 2.3 Self-authoring: confirmed not found, one real building block exists unused

No tool an agent can call writes a new `SKILL.md`. `skills_registry.py`'s `install_marketplace_skill()`/`publish_marketplace_skill()`/`list_marketplace_skills()` (`:494`, `:575`, `:433`) is a genuinely complete git-clone/zip-install pipeline with the security scanner already wired (`skill_scanner.scan_skill_dir` called at `skills_registry.py:501`) — but its only callers are admin HTTP routes (`routes_health.py:31,37,60,81`), never any agent-facing tool. `agent_manifest.py`'s `reflection_enabled` policy flag (default `True`) is dead scaffolding — grepped for consultation beyond its own definition/serialization, zero hits (`docs/PLATFORM-MAP.md` Part 15, independently re-confirmed here).

### 2.4 Corrections to prior docs, stated plainly

- **`backbone-plan.md`'s claim** ("the SKILL.md infra... is DEAD — never called from the chat runtime") — **still true, and understated.** The single function it names (`build_active_skill_prompt_append`) is indeed dead outside a health endpoint, but the more consequential function (`skill_registry.execute_skill`, the one that actually loads a `SKILL.md` body) has gone from "one narrow legacy call site" this morning to **zero** call sites from the live turn loop as of this afternoon's edits.
- **`backbone-empyralis.md` §4** — accurate as written for its timestamp; its one call-site citation (`sage_agent_runtime_service.py:3338-3359`) is now stale because that code was deleted in the intervening commits. Everything else in that section (the two-layer description, the empty-directories finding, the no-self-authoring finding) still holds and is not re-derived here.
- **New ground neither prior doc covered**: the existence of a *third* catalog (§2.2.C, `sage_skills_api.py`) that is the actual live Level-1 mechanism today, and the *fourth* parallel mechanism (§2.2.D, autopilot polling) that is the only place any real skill body-content reaches a real customer conversation, via raw string concatenation into a goal rather than a proper system-prompt section.

---

## 3. Ordered fix list

Each item names exact functions/files to change. Written so a build agent can execute in order without re-researching the spec or re-tracing the call graph above.

### Tier 1 — make skills exist at all

**1. Create real `SKILL.md` files for the 6 skills the dispatch code already expects.**
`skill_registry.py:332-339`'s `_BUNDLED_SKILL_DISPATCH` and `:438` already look for `<repo_root>/skills/<skill_id>/SKILL.md` for `memory-manager`, `code-runner`, `file-manager`, `telegram-bot`, `vision-monitor`, and `_PROMPT_ONLY_SKILLS` (`:374-377`) also expects one for `business-skill-template`. Create `skills/<id>/SKILL.md` for each, following §1.1's rules exactly: `name` matching the directory, third-person `description` stating what+when, body under 500 lines. This alone makes `bundled_skills_root()` non-empty for the first time and gives `list_installed_skills()` real content to return.

**2. Add frontmatter validation to `installed_skills._parse_skill_frontmatter()` (`installed_skills.py:169-180`).**
Currently a bare `yaml.safe_load` with no checks. Add: `name` 1–64 chars, `^[a-z0-9]+(-[a-z0-9]+)*$`, must equal `skill_dir.name`; `description` 1–1024 chars, non-empty, no `<`/`>` chars, not identical to a generic template string. Surface violations as a new finding class in `skill_scanner.scan_skill_dir()` (already called at `installed_skills.py:767`) rather than a second bolt-on check, so there's one place skills get rejected.

### Tier 2 — make skills reachable by the model (the core ask)

**3. Reconcile the three disconnected catalogs into one.** Today: `skill_registry.list_skill_definitions()` (20 items, richest schema, feeds only `available_tools` API metadata) vs. `sage_skills_api._build_sage_skills_payload()` (curated pack + `list_installed_skills()`, feeds the actual live prompt) vs. `installed_skills.list_installed_skills()` (filesystem scan, currently empty). Make `sage_skills_api._skill_capability_records()` (`sage_skills_api.py:288-330`) source from `skill_registry.list_skill_definitions(workspace_id=...)` instead of/in addition to `_CURATED_SKILL_PACK`, so the Tools tab and the model's `## Callable Tools` section describe the same set. This is a prerequisite for everything below — without it, "wiring skills into the prompt" just adds a fourth disconnected list.

**4. Give the model a real tool call for Level-2 invocation, and wire it into `_run_sage_action_loop_v3`.** Add a tool (e.g. `skill_invoke(skill_id: str, goal: str)`) to the always-on or lazily-discovered tool set (`direct_chat_tool_catalog_service.py` / `tool_registry_service.py`, per the two-tier mechanism `backbone-empyralis.md` §1 already documents). On call, dispatch to `skill_registry.execute_skill()` (`skill_registry.py:1188-1306`) — the function already does the right thing (executor → handler → MCP → bundled-dispatch → SKILL.md-body-injection fallback at `:1278-1306`) — and splice its `reply`/`artifact.preview_content` back into the turn as a tool result, the same way any other tool result re-enters context today. This is the single missing edge: `execute_skill` already works correctly in isolation (proven by the `/skills` slash command and the preview engine both using it successfully); it just has no caller inside the live turn loop.

**5. Fix `build_active_skill_prompt_append()` (`installed_skills.py:837-857`) to actually be Level 1, not "inject everything."** Split it: keep a cheap name+description-only summary (this becomes redundant with item 3's unified catalog — prefer deleting this function and routing `installed_skills`-sourced skills through the same `_skill_capability_records()` path instead of maintaining a second listing mechanism). Do **not** wire the current all-bodies-unconditionally version into any live path — that would regress token economy, not fix it.

**6. Route the `## Callable Tools` cap dynamically instead of the fixed 16-item/140-char limit** (`sage_instruction_compiler_service.py:54-55`). Not blocking for correctness, but note it explicitly diverges from the open standard's "no hard cap, degrade gracefully" framing and Claude Code's 1%-of-context-window scaling (`backbone-anthropic.md` §1) — worth a follow-up once item 3 potentially grows the list past 16 real entries.

### Tier 3 — make it correct and scoped

**7. Reconcile the autopilot polling path (§2.2.D) instead of leaving it as a silent fourth mechanism.** Once items 3–4 land, `telegram_run_action_service.py:157-174`'s raw string-concatenation of `query_active_installed_skills()`'s `prompt_append` into a goal string produces a *different* skill experience than the main chat runtime for the same workspace's skills. Preferred fix, consistent with the "one agent = one continuous conversation across all channels" ruling already on record for this project: route autopilot/polling traffic through the same `sage_instruction_compiler_service.build_sage_instruction_bundle` + `_run_sage_action_loop_v3` path webhook mode already uses, retiring the bespoke `installed_skill_query`/goal-concatenation mechanism rather than fixing it in place.

**8. Add per-install skill scoping.** `capability_presets.py` already has the pattern (`_SAFE_DEFAULT_TOOLS`, `enabled_tools` on `workspace_agent_installs`) but its own comment (`:36-45`) documents that the display id-space (`skill_registry.py`'s hyphenated ids) and the enforcement id-space (underscore tool-call names, `sage_agent_runtime_service._specialist_tool_allowed`) are "presently disconnected." Extend `_skill_capability_records()` (item 3's unified function) to filter by the invoking install's `tool_toggles`/`enabled_tools`, using `skill_registry.enforcement_tool_name()` (`skill_registry.py:367-371`) — which already exists specifically to bridge these two id-spaces — as the lookup key.

### Tier 4 — self-improvement

**9. Ship a `skill_write` tool before anything resembling autonomous authoring.** Give an agent a callable tool that: takes `name`/`description`/`body`, validates against item 2's rules, writes to `installed_skills.workspace_skills_root()` (`installed_skills.py:34-35`), runs it through `skill_scanner.scan_skill_dir()` (already exists, already wired for the marketplace path at `skills_registry.py:501`), and registers it so `list_installed_skills()` picks it up immediately — this reuses `skills_registry.install_marketplace_skill`'s already-built, already-scanned pipeline (`skills_registry.py:494-561`), which today is backend-complete but has zero agent-facing callers (only `routes_health.py`'s admin routes). Model the human-in-the-loop shape on §1.4's documented pattern: the agent proposes/writes, an owner reviews via the existing Tools-tab-equivalent UI before the skill is trusted with `enabled=True`, rather than auto-enabling on write.

**10. Only after 9 is live and used**, consider the heavier `skill-creator` A/B-eval loop (`backbone-anthropic.md` §4/§9: isolated-subagent test runs, with/without-skill benchmark, blind version comparison before keeping an edit). This is real automation infrastructure worth copying, but it presupposes a working write-and-invoke loop to test against — building it first would have nothing to evaluate.

### Tier 5 — observability

**11. Extend the existing `used_context` telemetry** (`sage_agent_runtime_service.py`, `"sage_skills"`/`"sage_capabilities"` tokens already appended at `:3772`/`:4032`) to record which specific skill ids were Level-1-listed vs. Level-2-invoked per turn, rather than a single boolean-ish flag. Surface this on the existing `/api/sage-skills`/`/api/sage-capabilities` endpoints (`sage_skills_api.py:481-505`) or a new debug field on the chat response — this is the cheapest version of Claude Code's `/context` breakdown and would have made this audit's Tier-1/2/3 findings visible without a multi-hour trace.

---

## Source index

**Official spec / Anthropic docs (fetched live, 2026-07-22):**
- [Specification — agentskills.io](https://agentskills.io/specification)
- [Agent Skills overview — Claude Platform docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [Using Agent Skills with the API](https://platform.claude.com/docs/en/build-with-claude/skills-guide)
- [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- Already cited in full in `backbone-anthropic.md` §4/§9: [Extend Claude with skills (Claude Code)](https://code.claude.com/docs/en/skills), [Agent Skills in the SDK](https://code.claude.com/docs/en/agent-sdk/skills)

**Empyralis repo (this pass):**
- `server_modules/skill_registry.py` (catalog A, 1,307 lines)
- `server_modules/installed_skills.py` (catalog B / filesystem scanner, 949 lines)
- `server_modules/sage_skills_api.py` (catalog C / live prompt source, 505 lines)
- `server_modules/sage_instruction_compiler_service.py` (prompt assembly, 786 lines)
- `server_modules/sage_agent_runtime_service.py` (live turn loop, 5,344 lines)
- `server_modules/skills_registry.py` (marketplace pipeline, 642 lines)
- `server_modules/skill_scanner.py` (security scanner, 661 lines)
- `server_modules/command_registry.py` (`/skills` slash command, 1,365 lines)
- `server_modules/universal_operator.py` (customer-preview engine, separate from live runtime)
- `server_modules/connectors/telegram_connector_context_service.py`, `telegram_run_action_service.py`, `telegram_terminal_service.py` (autopilot polling path)
- `server_modules/capability_presets.py` (agent-creation tool defaults, unrelated but referenced for the two-id-space bridge pattern)
- `docs/PLATFORM-MAP.md` Part 14 (Skills), Part 15 (Self-Improving Agents) — three-subsystem framing this audit extends with catalog C and the autopilot path, which Part 14 does not yet mention
- `docs/design/backbone-anthropic.md` §4, §9 — official-spec reference, not re-derived here
- `docs/design/backbone-empyralis.md` §1, §4 — prior-state reference; §4's one call-site citation corrected in §2.4 above
- `docs/design/backbone-plan.md` — original claim this audit was asked to verify; confirmed true and updated
