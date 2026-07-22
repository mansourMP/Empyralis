# Root-file taxonomy (SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS) — removal scope audit

**Date:** 2026-07-23. Audit-only — nothing in this document has been deleted or changed except this
file. Scope: the 10 modules named in the founder's ruling, plus 3 more the grep for the taxonomy
turned up during this audit (`bounded_scheduler_service.py`, `memory_contracts.py`,
`unified_memory_service.py` — flagged because they are genuine load-bearing consumers, not because
anyone asked for them by name).

**Headline finding, stated plainly up front:** the premise that "the machinery is vestigial"
(commit `57ee98d82`'s message) does not survive this audit. What that commit actually removed was
one paragraph of prompt text that *taught the model the taxonomy's names* ("SOUL=persona,
IDENTITY=how you present..."). It did **not** touch, and nothing in this repo has touched, the
machinery that: creates all ten files on disk for every agent, injects six of them in full into the
system prompt **on every turn, on three separate generation surfaces**, lets the model **write**
to them via an always-on native tool, and populates them for real from a **live, user-facing
onboarding flow** ("Sage setup," five questions, no filenames ever shown to the user). This is not
dead code with a few stale references. It is a live, working, in-production feature whose *product
skin* (the literal filenames) was hidden from the user and from the model's own self-description,
while the underlying read/write/inject plumbing kept running unchanged. Removing it is a migration
of live behavior onto the new architecture, not a cleanup.

---

## What each module does (file:line)

### 1. `server_modules/workspace_context.py` — the ground truth
This is the actual filesystem layer everything else calls into.
- `ALLOWED_CONTEXT_FILENAMES` (:13-27): the ten root files — `SOUL.md, AGENTS.md, TOOLS.md,
  IDENTITY.md, HEARTBEAT.md, USER.md, GOALS.md, MEMORY.md, PROCEDURES.md, REFLECTION.md`.
- `DEFAULT_CONTEXT_FILE_CONTENTS` (:50-144): scaffold text for every one of the ten, written to disk
  the first time a file is touched.
- `agent_workspace_context_dir` (:217-235): resolves the on-disk directory (see "on disk" section
  below).
- `ensure_workspace_context_files` (:477-503): **auto-creates every missing file from the default
  scaffold, every single call** — and it's called from both `read_workspace_context_files` (:506-511)
  and `read_workspace_context_file` (:514-532). There is no way to *read* the context files without
  first *writing* any missing ones to disk.
- `write_workspace_context_file` (:558-602): the one write primitive, enforcing per-file (64KB) and
  per-scope (512KB) byte caps.
- `delete_workspace_context_file` (:535-556): explicitly refuses to delete any of the ten root files
  ("core root files (MEMORY.md, SOUL.md, …) are reset via write, never removed," :541-543) — only
  topic files under `memory/files/**` can be deleted.

### 2. `server_modules/sage_instruction_compiler_service.py` — Sage's own prompt compiler
- `OFFICIAL_ROOT_MEMORY_FILES` (:14-22, 7 files) and `ALWAYS_LOAD_INSTRUCTION_FILES` (:44-46, the 7
  minus `MEMORY.md` = 6: SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS).
- `build_root_memory_brief_sections` (:335-453): injects each of those 6 files **in full** every
  turn, one `### <filename>` section per file (:354-370), whenever the file has non-default content.
  `MEMORY.md` alone gets index-only treatment with its own dedicated char budget (:372-393).
- `_kernel_prompt` (:604-681) carries a comment at :632-638 that is now **stale**: it says the
  taxonomy is "surfaced to the model in the root-memory-brief header" — that header text was the
  exact thing commit `57ee98d82` deleted (verified via `git show`; the header is now just `"## Memory"`,
  :862-869). The comment describes prompt text that no longer exists; a two-line comment fix, zero
  behavior change.
- This is the path used for Sage's own (native/master + specialist fleet-agent) turns.

### 3. `server_modules/sage_context_files_api.py` — a REST API, currently orphaned in the frontend
- `GET /api/sage-context-files` (:20-51) lists every `ALLOWED_CONTEXT_FILENAMES` entry with content;
  its own docstring says "List SOUL.md/MEMORY.md/GOALS.md/etc." (:26).
- `PATCH /api/sage-context-files/{filename}` (:53-81) writes one file.
- Registered live at `routes_workflows.py:6,18` — the backend route is real and reachable.
- **But nothing in the frontend calls it.** `workstation-client.ts` defines typed wrappers
  (`listSageContextFiles`/`updateSageContextFile`, :752-753, :2315-2320) and a response type
  (`WorkstationSageContextFileRecord`, :301) — confirmed by grep, these three symbols have **zero**
  call sites anywhere else in `frontend/`. This is a real, callable, but completely unused UI surface.

### 4. `server_modules/sage_profile_service.py` — the one place real content gets written today
- `SAGE_PROFILE_BOOTSTRAP_QUESTIONS` (:13-44): 5 onboarding questions ("What should I call you?",
  "What do you do?", "How should I communicate with you?", recurring responsibility, standing rules)
  — **no filename is ever shown to the user.**
- `SAGE_PROFILE_PROJECTED_FILES` (:46-51) = `USER.md, IDENTITY.md, SOUL.md, HEARTBEAT.md`.
- `projected_context_files`/`_project_user_md`/`_project_identity_md`/`_project_soul_md`/
  `_project_heartbeat_md` (:245-302) render the structured answers into those four files' markdown.
- `sync_profile_context_files` (:305-336) writes the projection into the real workspace context files
  via `workspace_context.write_workspace_context_file`, but only when the existing file content is
  still the default/empty/previous-projection (never clobbers a manual edit).
- `_storage_policy` (:339-345) is explicit that the JSON profile is canonical and
  `"markdown_format": "projection_only"` — i.e. even this module's own internal model treats the
  markdown files as a *derived* artifact, not the source of truth.
- Confirmed live end-to-end: `sage_profile_api.py` registers `GET/PATCH /api/sage-profile` and
  `POST /api/sage-profile/bootstrap/answer`; the frontend's "Sage setup" flow
  (`workstation-client.ts:1143,1151`, `workstation-chat-memory-loaders.ts:80-111`,
  `workstation-chat-pane-model.ts:97`) calls all three.

### 5. `server_modules/workspace_context_memory_adapter.py` — a second, independent prompt compiler
- `_ROOT_CONTEXT_FILE_ORDER` (:10-23): **12** filenames — the same 6 always-load files plus
  `PROCEDURES.md, REFLECTION.md, HEARTBEAT.md`, and two names (`SELF_MODEL.md`, `LIFE_STORY.md`) that
  are **not** in `workspace_context.ALLOWED_CONTEXT_FILENAMES` at all, so they can never actually hold
  root content (any write to those names gets remapped into `memory/files/` by
  `workspace_context._validate_context_path`, :274-281). Dead references inside a live module.
- `build_workspace_context_file_blocks` (:139-192) injects full content the same way
  `sage_instruction_compiler_service.py` does — independently re-implemented, not shared.
- `load_workspace_context_payload` (:195-359) is the entry point.
- Confirmed live callers: `direct_chat_memory_facade_service.py`, `direct_chat_generation_service.py`
  (the BYO-model / API-key chat path), `deployed_agent_memory_service.py` (deployed fleet agents),
  `conversation_memory_facade_service.py`. **This means the taxonomy is injected on at least three
  separate generation surfaces (Sage native, direct/BYO-model chat, deployed fleet agents), by two
  independently-written compilers that don't agree on which files qualify** (6 vs 12) — see Risks.

### 6. `server_modules/memory_service.py` — the real write engine behind the model's tools
- `memory_write_file` (:951-1045ish) docstring: "Used by Sage to update MEMORY.md (append new facts)
  or edit bootstrap files (SOUL.md, AGENTS.md, TOOLS.md, IDENTITY.md) via replace" (:964-965).
- `_SAFE_CONSOLIDATION_TARGET_FILES = {"MEMORY.md", "GOALS.md", "PROCEDURES.md", "REFLECTION.md"}`
  (:56) — daily notes auto-consolidate into these on a schedule.
- `apply_memory_consolidation_staging` (:1271-1339) can write **any** `ALLOWED_CONTEXT_FILENAMES`
  member once a staged edit is approved, with full version history (`_append_memory_file_version_record`).
- This is the module every model-callable memory tool (`memory_write`, `memory_update`,
  `memory_stage_edit`, `memory_apply_edit`, `memory_append_daily_note`, `memory_consolidate_daily_notes`)
  ultimately calls into.

### 7. `server_modules/skills_service.py` — the tool schemas sent to the model
- `memory_get` (:835-852), `memory_update` (:853-880, filename param description literally lists
  "MEMORY.md, USER.md, IDENTITY.md, SOUL.md, GOALS.md, PROCEDURES.md, or REFLECTION.md" at :867),
  `memory_stage_edit` (:881-905, :893 lists "IDENTITY.md, GOALS.md, PROCEDURES.md, TOOLS.md,
  AGENTS.md, REFLECTION.md, or MEMORY.md"), `memory_apply_edit` (:906-926), `memory_append_daily_note`
  (:927-954), `memory_stage_consolidation` (:955-981), `memory_consolidate_daily_notes` (:982-1010ish,
  :989 lists "MEMORY.md, GOALS.md, PROCEDURES.md, REFLECTION.md").
- These are **real JSON tool-parameter schemas**, not prose — the taxonomy names are inside the
  function-calling contract sent to the model's API today.
- `memory_append_daily_note` dispatch (:5150-5170) threads attribution through to root-file
  consolidation ("daily notes are consolidated into MEMORY.md/GOALS.md/etc later," :5163-5167).

### 8. `server_modules/skill_registry.py` — a second, DIFFERENT memory-tool surface
- Registers three built-in "system" skills, `memory-read`/`memory-write`/`memory-list`
  (:1229-1264), competing for the reserved skill-manifest slots
  (`docs/design/context-engineering-plan.md` item 12).
- Their executors (`_live_memory_read_skill` :572-625, `_live_memory_write_skill` :628-685,
  `_live_memory_list_skill` :688-724) dispatch to `agent_memory_tools.py` — **a directory distinct
  from where the real root taxonomy files live** (see module 10 below). Error/help text at :604 and
  :656 tells the model "e.g. SOUL.md" as a valid path — this is misleading: no live write path ever
  puts real SOUL.md content in the directory these three tools actually read/write, so that example
  would 100% return "File not found" in production.

### 9. `server_modules/tool_broker.py` — a near-duplicate of module 8
- `_MEMORY_SKILL_IDS = {"memory-read", "memory-write", "memory-list"}` (:486); `_dispatch_memory_tool`
  (:489-540) is a second, independently-written dispatcher for the *same three skill ids*, also
  calling into `agent_memory_tools.py` directly, also citing "SOUL.md" as an example path in its
  regex-based free-text goal parser (:517, :551, :556) and its own docstring at :497-508 explains
  this duplication is deliberate ("gives... the same real behavior instead of falling through to the
  SKILL.md/manual-stub fallback") — two hand-maintained copies of the same goal-parsing regexes.

### 10. `server_modules/agent_memory_tools.py` — the directory-mismatch root cause
- Module docstring (:1-12): "The agent's memory directory is:
  `<workspace_context>/agents/<install_id>/memory/`" — **a `memory/` subdirectory**, not the agent's
  root directory where `workspace_context.py` actually puts `SOUL.md`/`IDENTITY.md`/etc.
- `memory_read`/`memory_write`/`memory_list` (:112-266) operate exclusively inside that subdirectory;
  `memory_read`'s own docstring gives `"SOUL.md"` as an example path (:122) that, in the real
  directory layout, is never where SOUL.md's real content lives.
- Its own `migrate_existing_memory_to_index` helper (:272-376) correctly understands the *actual*
  root layout ("For every existing Sage install: keep the 12 files as-is," :279, discovering files
  directly in `agent_dir`, not `agent_dir/memory/`) and even ships a `_DESCRIPTIONS` dict (:325-337)
  naming `SOUL.md/IDENTITY.md/USER.md/AGENTS.md/TOOLS.md/GOALS.md/HEARTBEAT.md/PROCEDURES.md/
  REFLECTION.md` — i.e. this one module contains **two internally-inconsistent ideas of its own
  directory layout** side by side.

### Additional live consumers found (not in the original 10, added because they're genuinely load-bearing)
- **`server_modules/bounded_scheduler_service.py:1174-1196`**: reads `USER.md` live
  (`workspace_context.read_workspace_context_file("USER.md", workspace_id=workspace_id)`, :1193-1196)
  as "user_preferences" input to the heartbeat/scheduler wake-request decision, alongside a comment
  explicitly noting `HEARTBEAT.md` is "workspace-level configuration only the owner edits" (:1174-1178).
- **`server_modules/memory_contracts.py:29-43`**: a shared-constants leaf module (no behavior) whose
  `MEMORY_LAYER_SPECS["profile_memory"]` names `"Workspace USER.md context file"` (:32) as the
  documented storage location for the profile-memory layer — this is the canonical architecture
  description other modules cite.
- **`server_modules/unified_memory_service.py:362-429, 704-720`**: `_service_boundaries`/
  `_ingestion_contract`/`_profile_layer` all name `USER.md` as the profile-memory layer's repository
  and writer (:364, :380, :401-429); `build_sage_memory_payload` (:704-720) is called live from
  `agent_registry_api.py:1859` — a real, served memory-transparency endpoint.

---

## Live write paths found

| Writer | Files touched | Trigger | Confirmed live? |
|---|---|---|---|
| `sage_profile_service.sync_profile_context_files` | `USER.md, IDENTITY.md, SOUL.md, HEARTBEAT.md` | Onboarding "Sage setup" Q&A, every answer | **Yes** — frontend calls it (`workstation-client.ts:1143,1151`) |
| Model tool `memory_update` (native, always-on) | any of `MEMORY.md, USER.md, IDENTITY.md, SOUL.md, GOALS.md, PROCEDURES.md, REFLECTION.md` | Model decides to call it (approval-gated) | **Yes** — in `tool_registry_service.ALWAYS_ON_TOOL_NAMES` (`:41`), a real native schema on every turn |
| Model tool `memory_stage_edit` → `memory_apply_edit` | any `ALLOWED_CONTEXT_FILENAMES` member | Model proposes, user/policy approves | **Yes** — full version history via `_append_memory_file_version_record` |
| `memory_service` daily-note auto-consolidation | `MEMORY.md, GOALS.md, PROCEDURES.md, REFLECTION.md` | Scheduled job over daily notes | **Yes** — `_SAFE_CONSOLIDATION_TARGET_FILES` |
| `sage_context_files_api.update_workspace_sage_context_file` | any `ALLOWED_CONTEXT_FILENAMES` member | Direct API call | Route is live/reachable; **zero UI callers** — orphaned |
| `agent_memory_tools.memory_write` (via skill `memory-write`) | files inside `<agent_dir>/memory/` (**not** the root taxonomy location) | Model calls skill `memory-write` | Reachable, but never actually lands in the real SOUL/IDENTITY/etc. files due to the directory mismatch |
| `runtime_heartbeat_service` | `HEARTBEAT.md` | Every heartbeat run | **Yes** (not one of the 10 named modules, found via `write_workspace_context_file` grep — `runtime_heartbeat_service.py:302-350`) |

**Bottom line: this is not a case of "nothing writes here anymore."** Real user-facing onboarding
writes here today, and the model itself can write here today via an always-on native tool.

---

## What the UI exposes

- **`frontend/lib/workspace/fleet/tabs/MemoryTab.tsx`** (the live "Memory" tab in the fleet-agent
  modal) hits `/api/w/{workspace}/fleet/agents/{agent}/memory/tree` and `/file`
  (`server_modules/routes_fleet.py:794-862`, backed by `agent_memory_tree_service.py`). This surface
  is **deliberately scoped to `MEMORY.md` + topic files only** (`agent_memory_tree_service.py:1-19`
  docstring: "MEMORY.md index + memory/files/**.md topic files") — it does **not** show
  SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS/HEARTBEAT/PROCEDURES/REFLECTION at all. Someone already built
  the Phase-6 UI to match the target index-first model and simply never wired the older taxonomy
  files into it.
- **`/api/sage-context-files`** (`sage_context_files_api.py`) is the one surface that *would* show the
  full taxonomy — confirmed **zero live frontend callers** (see module 3 above). Dead UI surface,
  live backend route.
- **"Sage setup"** (`sage_profile_service.py` + `sage_profile_api.py`) is a 5-question onboarding
  wizard. It never shows a filename to the user; it just happens to persist its answers by projecting
  them into four of the ten root files under the hood.

**Net: there is no current UI that names "SOUL.md" or "IDENTITY.md" etc. to a human.** The taxonomy
survives entirely as an internal storage/injection mechanism, invisible in the product surface, but
fully wired into three prompt-compilation pipelines and one always-on model tool.

---

## Removal order (safe sequence, each step independently shippable)

These first three steps are genuinely safe, zero-risk, ship-independently cleanups. Everything after
that is **not a cleanup** — it's a feature migration requiring a founder product decision, sequenced
as a separate track below.

1. **Fix the stale comment in `sage_instruction_compiler_service.py:632-638`.** It describes prompt
   text that commit `57ee98d82` already deleted. Comment-only, zero behavior change.
2. **Delete `/api/sage-context-files`'s frontend stub** (`workstation-client.ts:301, 590, 752-753,
   1157-1160, 2315-2320` — the type + two functions + the two path entries). Zero call sites today;
   removing them changes nothing a user can observe. Leave the backend route alone in this step (see
   step 3).
3. **Retire the backend route** `sage_context_files_api.py` + its registration in
   `routes_workflows.py:6,18`, now that step 2 confirms nothing calls it. Independently verify no
   external/API-key integration hits it directly (check access logs for `/api/sage-context-files`
   before deleting server-side, since unlike the frontend this is a public HTTP surface a
   third-party script could theoretically call).
4. **Fix the misleading "e.g. SOUL.md" examples** in `agent_memory_tools.py:122`,
   `skill_registry.py:604,656`, `tool_broker.py:517,551,556` — replace with an example path that
   actually lives in the directory these functions read/write (e.g. `notes.md`). Documentation-only;
   makes the tool's own help text stop lying to the model about what it can find.
5. **STOP. Everything below this line requires a product decision, not a cleanup PR.** The remaining
   machinery is live, in-production behavior (onboarding writes, the model's own always-on write
   tool, scheduled consolidation, scheduler/heartbeat reads). Deleting any of it without first
   deciding what replaces it (see next section) will regress: onboarding ("Sage setup" would have
   nowhere to persist its answers), the model's own `memory_update`/`memory_stage_edit` tools (would
   need new target filenames or removal from the tool schema), the heartbeat/scheduler's read of
   `USER.md`, and the daily-note auto-consolidation job.
6. Once the founder picks a replacement mapping (next section), the migration itself can still ship
   incrementally, e.g.: (a) point `sage_profile_service.sync_profile_context_files` at a MEMORY.md
   "Summary" section instead of USER.md/IDENTITY.md/SOUL.md/HEARTBEAT.md; (b) update the
   `memory_update`/`memory_stage_edit` tool schemas in `skills_service.py` to drop the taxonomy
   filenames from their parameter descriptions; (c) update both prompt compilers
   (`sage_instruction_compiler_service.py`'s `ALWAYS_LOAD_INSTRUCTION_FILES` and
   `workspace_context_memory_adapter.py`'s `_ROOT_CONTEXT_FILE_ORDER`) to stop injecting the six/twelve
   files in full; (d) only then shrink `workspace_context.ALLOWED_CONTEXT_FILENAMES` and stop
   `ensure_workspace_context_files` auto-creating them. Each of (a)-(d) is independently shippable and
   independently revertible, in that order — reverse order breaks things (e.g. shrinking the allowed
   filename set before the tool schemas stop naming them would make the model's own tool calls start
   failing validation).

---

## What replaces it (mapping each legitimate need onto the target architecture)

| Root file | What it's actually used for today | Where it belongs in the target model |
|---|---|---|
| `SOUL.md` | Generic, mostly-static product-voice copy (the default scaffold, `workspace_context.py:51-61`) plus one onboarding-derived "communication style" line and a "standing rules" list (`sage_profile_service._project_soul_md:261-279`) | The static copy belongs in the **always-in-window kernel prompt** (it's product copy, not per-customer data). The onboarding-derived style/rules line is small, per-user, and durable — belongs in **MEMORY.md's index** (its own scaffold already earmarks a "Summary" section for exactly this, `workspace_context.py:92-93`), not a dedicated always-loaded file. |
| `IDENTITY.md` | One onboarding-derived "role and focus" line (`_project_identity_md:253-258`) | Fold directly into the profile/USER summary in **MEMORY.md's index** — it's one fact, not a section worth its own file. |
| `USER.md` | Real per-user name + preferences; read live by the scheduler/heartbeat path (`bounded_scheduler_service.py:1193-1196`) and named as canonical in `memory_contracts.py:32` / `unified_memory_service.py:364,401-429` | This is genuinely durable, per-user data — the textbook case for **MEMORY.md's "Summary" section** the founder's own target model describes (`workspace_context.py:92-93`: "Summary (identity + owner/customer context + stable facts)"). Every other module that reads `USER.md` today (scheduler, unified-memory-service, memory_contracts) should read the MEMORY.md summary instead. |
| `GOALS.md` | Onboarding "recurring responsibility" seed today; also a live daily-note auto-consolidation target (`memory_service.py:56`) | A **MEMORY.md topic file** (e.g. `memory/files/goals.md`), linked from the index and pulled on demand via `memory_search`/`memory_get` — exactly the "Topic files" mechanism the scaffold already documents. |
| `AGENTS.md` / `TOOLS.md` | Static boilerplate in practice (`workspace_context.py:62-81`) — no live writer populates these with real per-customer content anywhere in this audit | These read as **operating rules**, which is what the **always-in-window kernel/system prompt** already is for. If genuinely nothing customer-specific has ever been written here, there may be no content to migrate at all — worth confirming on a real production workspace sample before assuming otherwise. |
| `PROCEDURES.md` | Daily-note consolidation target (`memory_service.py:56`); already NOT in the 6-file always-load set | Already closer to the target shape — make it a plain **MEMORY.md topic file**, drop it from `workspace_context_memory_adapter.py`'s 12-file full-injection list (where it currently still gets injected in full on the direct-chat path, inconsistent with the Sage-native path that already excludes it). |
| `REFLECTION.md` | Its own scaffold text (`workspace_context.py:123-134`) **already says**: "NOT loaded every turn... Link anything here... under MEMORY.md's Topic files section" | The file's own documented intent already matches the target model. Just needs `workspace_context_memory_adapter.py` (which still full-injects it on the direct-chat path) brought in line with that stated intent. |
| `HEARTBEAT.md` | A real, live, system-written run log (`runtime_heartbeat_service.py`), read by the scheduler as owner-tier config (`bounded_scheduler_service.py:1174-1178`) | This is genuinely a runtime log, not a persona/identity file — leave it as a dedicated file (or move it under `memory/` as a topic file the agent reads on demand) but it was never really part of the SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS taxonomy in spirit; it's closer to a system log than to customer-editable persona/identity data. |

**General principle for the mapping:** anything that is (a) static product copy → kernel/system
prompt; (b) small, durable, per-user fact → MEMORY.md's index/Summary section; (c) large, occasional,
or agent-written → a MEMORY.md topic file pulled on demand. None of the six always-loaded files
actually needs to keep being injected in full every turn once its real content is this small — the
measured defaults (`workspace_context.py:50-144`) are almost entirely boilerplate, and the only real
per-customer content found anywhere in this audit (the 5-question onboarding profile) easily fits in
a few lines of a MEMORY.md summary.

---

## Risks

1. **Two independently-written prompt compilers disagree on the file set.**
   `sage_instruction_compiler_service.py` full-injects 6 files (`ALWAYS_LOAD_INSTRUCTION_FILES`);
   `workspace_context_memory_adapter.py` full-injects 12 names, 2 of which (`SELF_MODEL.md`,
   `LIFE_STORY.md`) can never have real content. Any removal work must update **both**, or the
   direct-chat/BYO-model and deployed-fleet-agent surfaces will keep injecting files the Sage-native
   surface has already stopped injecting — a silent behavior divergence between "the same agent" on
   different channels.
2. **`memory_update`/`memory_stage_edit` are real, native, always-on tool schemas** (not prose) naming
   the taxonomy files in their JSON parameter descriptions (`skills_service.py:867,893,989`). Removing
   the taxonomy without updating these schemas leaves the model able to call
   `memory_update(filename="SOUL.md", ...)` against a filename that either no longer exists or has
   been silently repurposed — a genuine runtime-error / confused-agent risk, not a cosmetic one.
3. **Two independent skill-dispatch implementations for `memory-read/write/list`**
   (`skill_registry.py` and `tool_broker.py`) point at `agent_memory_tools.py`'s `<agent_dir>/memory/`
   subdirectory — a location distinct from where the real root taxonomy files live. This is a
   pre-existing bug/confusion independent of the removal question: right now, a model that tries
   `memory_read("SOUL.md")` via the `memory-read` **skill** (not the `memory_read` **tool**) gets
   "File not found," while the same filename via the native `memory_read`/`memory_get` tools works.
   Worth fixing regardless of what happens to the taxonomy, since it's a live source of agent
   confusion today.
4. **The onboarding flow ("Sage setup") has no other persistence target today.** Any removal of
   `USER.md`/`IDENTITY.md`/`SOUL.md`/`HEARTBEAT.md` as write targets must land a replacement
   destination (MEMORY.md summary, per the mapping above) in the *same* change, or the live onboarding
   flow breaks for every new signup.
5. **The scheduler/heartbeat path reads `USER.md` directly** (`bounded_scheduler_service.py:1193-1196`)
   for wake-request decisions — this is a real behavioral dependency, not just a prompt-injection one.
   It needs to be repointed at whatever replaces `USER.md`, not just have its current read path deleted.
6. **`sage_context_files_api.py`'s backend route, though frontend-orphaned, is still a live public HTTP
   endpoint.** Before deleting it server-side, confirm nothing external (a saved Postman collection, a
   third-party integration script, a stale mobile app build) calls it directly — the frontend grep only
   proves the *current* web frontend doesn't use it.
7. **Confirming "do real production workspaces have these files on disk"**: based on
   `agent_workspace_context_dir`'s layout (`workspace_context.py:217-235` — root:
   `.orion-stack/workspace/workspaces/<normalized-workspace-id>/agents/<normalized-install-id>/`, or
   the bare workspace-scope root when no `agent_install_id` is passed), every workspace that has ever
   had a single turn processed through `read_workspace_context_files`/`ensure_workspace_context_files`
   will have all ten files on disk, because that function runs unconditionally on every read. This
   audit did not (and was instructed not to) inspect actual production data directories to confirm
   file counts/content — but the code path guarantees their existence structurally, not
   probabilistically, for any workspace that has ever taken a live turn. Treat "do they exist in
   prod" as **effectively certain**, not an open question, given this call graph.
