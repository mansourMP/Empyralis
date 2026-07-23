# Audit: storage lifecycle — every growing store, what's capped, what isn't

Status: report-only audit, verified against the `main` branch of this repo,
commit `8b559e469`, on **2026-07-23**. Backend files (`agent_memory.py`,
`workspace_context.py`, `skills_service.py`, `gateway_execution_service.py`,
`empyralis-gateway`) are being edited concurrently by other agents while this
was written — line numbers below were correct at write time but will drift;
re-grep the quoted function/constant names rather than trusting a line number
alone. No code was changed to produce this document. `git show HEAD:<path>`
was used wherever a file looked mid-edit.

Triggered by the founder's question (2026-07-23): *"If a customer has 20
agents and each has 100 sessions, that's a massive amount of storage kept on
OUR servers. Where do session threads actually live? ... I think there's MORE
going on under the hood than just sessions."*

He's right on both counts. Session threads are the smallest of the
uncapped stores, not the biggest — and there are roughly **eighteen**
independently-growing stores, not one.

---

## 1. The short answer

- **What dominates (structured data):** `agent_trace_events`
  (`server_modules/control_plane_repository.py:1199-1214`). A single agent
  turn with a few tool calls emits 8-10 persisted trace-event rows —
  `tool.started`/`tool.result` per tool call, plus `assistant.message.completed`
  which re-stores the **entire final reply text a second time**, duplicate of
  what's already in `agent_turns.content`. In the model below it's ~46% of
  all structured per-session bytes — before you even count the fact that
  `agent_action_events` (`control_plane_repository.py:932-976`) logs the
  *same* tool calls again for billing, and up to **four separate tables**
  (`credit_ledger_events`, `usage_events`, `deployed_agent_monthly_cost_ledger`,
  `workspace_hosted_ai_monthly_cost_ledger`) each log the *same* LLM call a
  third, fourth, fifth, and sixth time.
- **What dominates (raw bytes, if used at all):** generated media —
  screenshots, generated images, uploaded files — via `artifact_service.py`.
  A single PNG screenshot is 100-2000x the size of any JSONB row. This store
  has **zero enforced retention** (see §4) and is the one nobody thinks about
  first, because it isn't a "conversation" store at all.
- **What's uncapped:** agent_turns, agent_traces, agent_trace_events,
  agent_action_events, credit_ledger_events, usage_events, two monthly cost
  ledgers, knowledge_chunks/embeddings/retrieval_events,
  personal_context_events, agent_scheduler_wake_requests,
  agent_egress_events, agent_secret_access_events, security_control_events,
  run_archive/run_transitions, gateway_events, personal_channel
  inbound/outbound messages, `memory_entries_history`, and artifacts. That's
  **18 of ~21 identified growing stores** with no deletion path at all
  short of a full manual workspace wipe — and even that wipe misses 12 of
  them (§4).
- **The one-line risk:** a real, automated retention *enforcement* system
  (`retention_enforcement_job.py`) exists, is fully tested, and is **never
  called from anywhere in production** — no route, no scheduler, no systemd
  timer. Of the stores it does know about, it only ever actually deletes
  one (`sage_memory`). Every other declared TTL is decorative.
- **The founder's 20×100 number:** roughly **520 MB/account** in structured
  Postgres rows alone (sessions, turns, traces, trace events, action events,
  4x redundant billing ledgers), plus **40-70 MB/account** in per-agent
  memory/context files, **before counting a single generated image or
  screenshot** — which could add anywhere from 0 to several GB per account,
  uncapped, because that store has no measurement or enforcement at all.
  Full model in §3.

---

## 2. Every growing store

All "server" paths below are on **our** infrastructure (the backend host's
Postgres, or its local filesystem under `.orion-stack/` /
`~/.empyralis/state/`) — never the customer's VPS. This matches the standing
finding in `memory_placement_scope.md`/`MEMORY.md`: all memory is server-side
today.

| Store | Where it lives | Bytes/unit (estimate) | Growth driver | Capped today? |
|---|---|---|---|---|
| `agent_threads` | Postgres, `control_plane_repository.py:453-465` | ~1 KB/row | 1 per conversation/session | No cap; no deletion path |
| `agent_turns` | Postgres, `control_plane_repository.py:481-498` | ~1.5 KB/row (content + actor/approvals/interventions/metadata JSONB) | 1 per user or assistant message; `INSERT`/upsert at `control_plane_repository.py:11319-11358` has no pre-write cap | **No.** No TTL, no row cap, not even covered by full workspace wipe (§4) |
| `agent_traces` | Postgres, `control_plane_repository.py:1182-1197` | ~0.4 KB/row | 1 per turn execution (`agent_trace_service.start_trace`) | No |
| `agent_trace_events` | Postgres, `control_plane_repository.py:1199-1214` | ~0.6 KB/row blended; `assistant.message.completed` carries the full reply text again | 8-10 **persisted** events per turn (13 of 16 event types persist — see `PERSISTED_TRACE_EVENT_TYPES`, `agent_trace_service.py:15-44`; only `reasoning.summary.delta`/`tool.progress`/`assistant.message.delta` stay ephemeral) | **No.** Not in the retention catalog at all |
| `agent_action_events` | Postgres, `control_plane_repository.py:932-976` | ~0.7 KB/row (`usage_ref` JSONB + input/output summaries) | 1 per tool/connector/skill/MCP-tool call, written by `agent_action_metering_service.py` — duplicates `tool.started`/`tool.result` trace events | No |
| `credit_ledger_events` | Postgres, `control_plane_repository.py:904-930` | ~0.5 KB/row | 1 per billed LLM call | No |
| `usage_events` | Postgres, `usage_events_repository.py:88-106` | ~0.45 KB/row | 1 per LLM call — its own docstring calls it "the normalized ... table the existing monthly ledgers never provided," i.e. an admitted 4th copy of the same metering event | No |
| `deployed_agent_monthly_cost_ledger` | Postgres, `control_plane_repository.py:862-882` | ~0.5 KB/row | 1 per LLM call for marketplace-deployed agents | No |
| `workspace_hosted_ai_monthly_cost_ledger` | Postgres, `control_plane_repository.py:883-902` | ~0.5 KB/row | 1 per LLM call for hosted direct-chat | No |
| `memory_entries` | Per-agent SQLite, `.orion-stack/memory/<workspace>/agents/<agent>/memory.db` (`agent_memory.py:302-309,465-472`) | ≤6 KB/row (`ORION_MEMORY_MAX_TEXT_CHARS`, `memory_service.py:431`) | 1 per fact key, upserted | Content length capped; **row count uncapped** |
| `memory_entries_history` | Same SQLite file (`agent_memory.py:487-506`) | ~similar to entry size ×2 (old+new content) | **Every** create/update to a fact appends a row here, by design ("audit trail," never overwritten) | **No.** Never pruned, anywhere |
| `workspace_context` files (MEMORY.md, topic files, daily notes, dreams) | Server filesystem, `.orion-stack/workspace/<workspace>/agents/<agent>/` (`workspace_context.py:10-11`) | 64 KB/root file × 12; 25 KB/topic file × 40 (or ×5 cloud-only); up to 64 KB/daily note × 365 | Per memory write / per day | **Yes**, as of the same-day commit `5d982fb66` — `MAX_CONTEXT_ROOT_FILES=12`, `MEMORY_TOPIC_FILE_MAX_COUNT=40`/`_CLOUD_ONLY=5`, `MEMORY_TOPIC_FILE_MAX_LINES/BYTES=200/25_000`, `MAX_CONTEXT_DAILY_NOTES=365` (`workspace_context.py:33-100`). Ceiling ≈ 23 MB/agent just from daily notes if every day is used at max size |
| `agent_channel_events` | Postgres, `control_plane_repository.py:1257-1278` | ~1 KB/row (full `text` + payload/metadata JSONB) | 1 per inbound/outbound channel message (WhatsApp/Telegram/etc.) | Declared `active_conversation` (30-day TTL) in the retention catalog, but **never enforced** (§4) |
| `personal_channel_inbound_messages` / `_outbound_messages` | Server SQLite, `~/.empyralis/state/personal_channels/personal-channels.sqlite3` (`personal_channels_repository.py:12-105`) | ~0.5-1 KB/row | 1 per personal-channel message — **a second copy** of the same text as `agent_channel_events` | **No.** Grepped the whole file: no DELETE, no trim, anywhere |
| `agent_conversation_memory` JSONL | Server filesystem, `~/.empyralis/state/conversations/<workspace>/<agent>/<conversation>.jsonl` (`agent_conversation_memory.py:20,42-47`) | line-per-turn, small | 1 line per turn — **a third copy** of the same conversational content | **Yes** — the only conversational store that actually self-prunes: `MAX_TURNS_RETAINED=400` lines, trimmed on every append (`agent_conversation_memory.py:55,235,272-291`) |
| `knowledge_sources`/`knowledge_chunks`/`knowledge_embeddings`/`knowledge_retrieval_events` | Postgres, `control_plane_repository.py:978-1049` | chunk text uncapped; embeddings stored as JSONB float array (not `pgvector`), currently 96-dim hash embeddings (`knowledge_rag_service.py:216`, `HASH_EMBEDDING_MODEL`) ≈1 KB/chunk; grows to real trouble the day a real embedding model (1536+ dims) replaces the hash one | Per uploaded file / per RAG query | No |
| `personal_context_events` | Postgres, `control_plane_repository.py:1216-1232` | ~0.6 KB/row | Per cross-app context event | No |
| `agent_scheduler_wake_requests` | Postgres, `control_plane_repository.py:1234-1255` | ~0.7 KB/row | Per scheduled/triggered wake | No |
| `agent_egress_events` / `agent_secret_access_events` / `security_control_events` | Postgres, `control_plane_repository.py:1280-1298, 1300-1319, 1358-1376` | ~0.5 KB/row each | Per outbound HTTP call / per secret read / per kill-switch action | No |
| `activity_ledger_events` | Postgres, `control_plane_repository.py:1378-1404` | ~0.7 KB/row (`artifacts`/`payload`/`metadata` JSONB) | Per notable action, feeds the owner's Activity feed | Declared `audit_record` (10-yr TTL) in the catalog; never enforced |
| `run_archive` / `run_transitions` | Postgres, `run_state_repository.py:530-576` | archive row: full JSONB payload; transitions: small, BIGSERIAL | 1 archive row + N transition rows per specialist-agent delegation run | No `DELETE` found on either table |
| `gateway_events` | Server SQLite, `~/.empyralis/state/gateway/gateway-state.sqlite3` (`gateway_state_repository.py:20-21,125-139`) | small, but every WS frame | Every gateway WebSocket frame (including heartbeats/acks) — 4 separate call sites INSERT into it, none delete | **No.** Literally unbounded |
| Artifacts (generated images, browser screenshots, uploaded files) | `.orion-object-store/` on the server by default, or an S3-compatible bucket if `EMPYRALIS_OBJECT_STORAGE_*` is configured (`artifact_service.py:32,99-121,251-296`) | 100 KB - 2+ MB/file (real binary media, not JSONB) | Per artifact-generating tool call (screenshot, image gen, file save) | **No.** `retention_days` is a per-record field that defaults to `None` (never expires) unless a caller passes it — grep found **no caller that ever does** (`artifact_service.py:168-172`). `plan_artifact_retention()` exists and computes what *would* be expired (`artifact_service.py:1001-1023`) but is called by nothing except its own tests — it's referenced only as a documentation string inside `build_artifact_backup_manifest()` (line 955) |
| `vault_credentials`, `mcp_oauth_clients/authorization_codes/access_tokens/refresh_tokens` | Postgres/SQLite | small | Per connector/OAuth client, not per message | Bounded by nature — not a growth risk |
| CLI subscription session transcripts (`~/.claude`, `~/.codex`) | **On the customer's VPS**, under the gateway service user's home (`scripts/install-agent-computer.sh:141-142,214`) | whatever Claude Code/Codex's own local retention does | Per hardware-backed CLI turn | Not ours to cap — we neither observe nor manage it. The one store in this whole audit that actually *is* on the customer's box |

---

## 3. The arithmetic — founder's 20×100 scenario

**Assumptions** (stated so they can be challenged/replaced):

- 1 "session" = 1 `agent_threads` row.
- 40 turns/session (20 user + 20 assistant) — a mid-length conversation.
- Each assistant turn fires ~3 tool calls (typical agentic turn).
- Trace events/turn: `trace.started` + `trace.routed` + (`tool.started`+`tool.result`)×3 + `assistant.message.completed` + `trace.completed` = **10 persisted events per assistant turn**, 0 for user turns → 200 events/session.
- 1 LLM call per assistant turn → 20 `credit_ledger_events` + 20 `usage_events` + 20 rows in whichever single cost ledger applies (direct chat → `workspace_hosted_ai_monthly_cost_ledger`) → 60 billing rows/session, not 80 (assumes only one of the two per-surface cost ledgers fires per session, which is generous — a marketplace-deployed agent would add the second).
- Row sizes per the table in §2.
- This model covers **structured Postgres rows only** — it excludes memory files, knowledge base, and artifacts, which are modeled separately below because they're per-agent or per-tool-call, not per-session.

**Per session** (structured Postgres):

| Store | Rows | KB | Share |
|---|---:|---:|---:|
| `agent_threads` | 1 | 1 | 0.4% |
| `agent_turns` | 40 | 60 | 23% |
| `agent_traces` | 20 | 8 | 3% |
| `agent_trace_events` | 200 | 120 | **46%** |
| `agent_action_events` | 60 | 42 | 16% |
| billing ledgers (3 tables) | 60 | 29 | 11% |
| **Total** | 381 | **≈260 KB** | 100% |

- **Per agent** (100 sessions): 260 KB × 100 ≈ **26 MB**
- **Per account** (20 agents): 26 MB × 20 ≈ **520 MB** of structured rows

**Plus per-agent (not per-session) stores**, at realistic (not worst-case-cap) usage:

- `memory_entries` + `memory_entries_history`: ~200 facts, a few revisions each ≈ 0.5 MB/agent
- `workspace_context` files: well under the 23 MB daily-note ceiling in practice — realistic estimate ≈ 2-3 MB/agent

→ ~2.5-3.5 MB/agent × 20 agents ≈ **50-70 MB/account**

**Running total, structured + memory: ≈ 570-600 MB/account** — before a single image, screenshot, or uploaded file.

**The wildcard — artifacts:** if these 20 agents use any browser automation or image generation, a single session with 5 screenshots at ~200 KB each is 1 MB; across 100 sessions that's 100 MB/agent; across 20 agents (if all use it) that's **+2 GB/account**, entirely uncapped and untracked by any quota system (§5). This is not in the 570-600 MB floor above because there's no code to measure it — that absence is itself the finding.

**Extrapolating the floor (structured + memory, excludes the artifacts wildcard):**

| Accounts | Structured + memory floor | + artifacts (0 to heavy use) |
|---:|---:|---|
| 1 | ~0.6 GB | +0 to a few GB |
| 100 | ~57-60 GB | +0 to hundreds of GB |
| 1,000 | ~570-600 GB | +0 to low-TB |

The floor alone is not catastrophic at 100 accounts. It becomes a real
problem at 1,000+ accounts, and it **never goes down** — every table in the
"No" column of §2 only accumulates, forever, regardless of how many of those
sessions are still relevant. The dominant, most avoidable piece is
`agent_trace_events` + `agent_action_events` together — 62% of the structured
per-session total — because the same tool call is being logged twice for two
different purposes (replay/observability vs. billing) with no attempt to
compact or expire either copy.

---

## 4. Retention today — what exists, what's wired, what runs, what it misses

**What exists:** `server_modules/data_retention_service.py` declares:
- `RETENTION_CLASSES` (lines 9-30): `ephemeral` (1d), `active_conversation`
  (30d), `compact_summary` (90d), `business_aggregate` (365d), `audit_record`
  (3650d).
- `DATA_STORE_CATALOG` (lines 33-94): maps **10 stores** to those classes —
  `deployed_agent_conversation_memory`, `deployed_agent_daily_message_usage`,
  `channel_user_acquisition_touches`, `deployed_agent_business_insights`,
  `external_user_privacy_requests`, `external_user_privacy_delete_audits`,
  `agent_channel_events`, `activity_ledger_events`, `mini_apps_state`,
  `sage_memory`. Every single one of these is about **marketplace-deployed
  agent / external-customer** data. None of `agent_turns`, `agent_traces`,
  `agent_trace_events`, `agent_action_events`, `credit_ledger_events`,
  `usage_events`, `knowledge_*`, `memory_entries`, `workspace_context` files,
  `run_archive`/`run_transitions`, `gateway_events`, personal-channel
  messages, or artifacts is in the catalog **at all**.

**What's wired:** `server_modules/retention_enforcement_job.py` walks the
catalog above via `evaluate_retention()` (lines 82-176). For every store
except `sage_memory`, it reads `eligible_count` off the inventory row — but
`data_retention_service.build_workspace_retention_inventory()` (lines
154-187) and `control_plane_repository.build_workspace_data_retention_inventory()`
(`control_plane_repository.py:8569-8610`) only ever compute a plain
`COUNT(*)` per store; **`eligible_count`/`expired_count` is never set for
any of them**, so it defaults to 0 and nothing is ever flagged as expired.
The only store with real logic is `sage_memory`, via
`_evaluate_sage_memory_store()` (`retention_enforcement_job.py:46-79`),
which actually calls `sage_memory_service.delete_memory_entry()` on rows
past their TTL.

**What runs:** nothing, in production. Grepping the whole repo (outside
`.claude/worktrees` per-agent snapshots and the module's own test file) for
callers of `evaluate_retention`, `run_retention_apply`, or
`run_retention_dry_run` returns **zero results**. There is no route, no
scheduler entry (`bounded_scheduler_service.py` has no reference to
"retention"), and no systemd timer — `deploy/` contains `.service` unit
files but zero `.timer` files. The job is fully tested
(`server_modules/tests/test_retention_enforcement_job.py`, 10 passing tests)
and never invoked outside those tests. It is dead code with a green test
suite.

**What it misses even when manually invoked:** `delete_workspace_scope_data()`
(`control_plane_repository.py:8745-8856`), the one real bulk-delete function
that exists (used for full workspace teardown), covers 9 stores: `agent_channel_events`,
`deployed_agent_conversation_memory`, `deployed_agent_daily_message_usage`,
`channel_user_acquisition_touches`, `deployed_agent_upgrade_click_events`,
`deployed_agent_business_insights`, `external_user_privacy_requests`,
`external_user_privacy_delete_audits`, `activity_ledger_events`. It does
**not** touch `agent_turns`, `agent_threads`, `agent_traces`,
`agent_trace_events`, `agent_action_events`, `credit_ledger_events`,
`usage_events`, either monthly cost ledger, `knowledge_*`, `memory_entries`
(+history), `workspace_context` files, `run_archive`/`run_transitions`, or
`gateway_events`. **A customer could explicitly delete their entire
workspace and 12 of the 21 stores in this audit would still hold their
data.**

**Declared-but-decorative policy:** `MemoryRetentionContract`
(`conversation_memory_policy.py:28-34`) declares
`raw_transcript_days`/`summary_days`/`semantic_days`/`event_memory_days` =
365 for every profile (`_BASE_RETENTION`, lines 59-64). Grep confirms these
fields are read nowhere outside `conversation_memory_policy.py` itself —
never consumed by any deletion path. It's a labeled constant, not a TTL.

**What genuinely does cap something (context/read-time, not storage):**
- `agent_conversation_memory.py`'s JSONL files: hard 400-turn cap, actively
  trimmed on write — the one real storage-time cap in the whole audit.
- `workspace_context.py`'s memory files: real, shipped-same-day caps (§2).
- `conversation_memory_policy.py`/`conversation_compaction.py`: bound how
  many messages/tokens get pulled **into a prompt** (`preserve_last_messages`,
  `max_transcript_items`) — this controls LLM context cost, not stored
  bytes. The underlying `agent_turns` rows are untouched either way.
- `list_agent_turns()` defaults to `LIMIT 200` per **read**
  (`control_plane_repository.py:11391`) — again a read cap; row 201 onward
  still sits in Postgres forever.

---

## 5. The VPS disk — what's actually on it, and is the mismatch real

**Where the 25-50GB number comes from:** disk size is never independently
chosen — it's whatever the selected CPU/RAM tier bundles from the
provider's own plan catalog. `_normalize_digitalocean_plans()`,
`_normalize_hetzner_plans()`, `_normalize_vultr_plans()`
(`vps_provisioning_service.py:3796-3910`) all pull `disk_gb` straight off
the provider API's per-size field with no independent filtering. Google and
AWS use fixed defaults: `GOOGLE_DEFAULT_BOOT_DISK_GB = 40`
(`vps_provisioning_service.py:132`), `_AWS_DEFAULT_ROOT_VOLUME_GB = 40`
(line 3459). A cheap DO/Hetzner/Vultr droplet at the CPU/RAM tier we'd
actually pick for an agent box just happens to come with 25-80GB of SSD —
it's a side effect of the compute purchase, not a storage decision.

**What the installer actually puts on it:**
`scripts/install-agent-computer.sh` (541 lines) installs: apt base packages
(~200 MB), Node 20 LTS (~150 MB), the `empyralis-gateway` built from a git
clone — `npm install && npm run build` inside a staged directory (lines
246-260) — plus CLI tool installs (`npm install -g @openai/codex`, Claude
Code CLI) into `${INSTALL_ROOT}/cli` (lines 133-150). Realistic total
runtime + tooling footprint: on the order of **2-5 GB**, not 25-50 GB.

**What does grow on the box over time:** the CLIs' own local state —
`~/.claude`, `~/.codex` — under the gateway service user's home (lines
141-142, 214 explicitly call this out: *"npm's cache (~/.npm) and the
installed CLIs' own config (~/.claude, ~/.codex) resolve off $HOME"*). This
is the **one** store in this entire audit that genuinely lives on the
customer's VPS rather than our servers — and we neither cap nor observe it;
whatever Claude Code/Codex's own local transcript policy is, is what
happens.

**Is the mismatch real? Yes.** Every store the founder is actually worried
about — sessions, turns, traces, memory, billing ledgers, artifacts — lives
in **our** Postgres or **our** backend filesystem
(`.orion-stack/`, `~/.empyralis/state/`), confirmed by every path cited in
§2. None of it is on the customer's box. The customer is paying for
25-50GB of disk that holds ~2-5GB of runtime plus whatever the CLI's own
local history accumulates, while the data that actually scales with usage
sits on our infrastructure — unbilled to them, and unbounded by us. The
founder's instinct ("what's the point of them having 25-50GB there") is
correct: today, that disk is mostly bought for CPU/RAM and carried along
for free, not sized for or used by anything of ours.

---

## 6. Multi-tenancy risk

**Isolation:** every hot table carries `tenant_id`/`workspace_id` (and RLS
is enabled — `migrations/enable_rls.sql`), so per-account billing/limiting
is technically possible without a schema change. Isolation is not the gap.

**Quota enforcement:** grepping `storage_quota|disk_quota|storage_limit|
disk_limit|storage_cap|max_storage|storage_bytes|bytes_used|storage_used`
across every `server_modules/*.py` and the frontend returns exactly 5 hits,
all in two files:
- `entitlements_service.py:802` — `storage_memory_size_mb_max`, a plan
  entitlement field.
- `deployed_agent_service.py:1489-1494,1617-1623` — gates that check
  `storage_limit_mb <= 0` and reject enabling memory if so.

Both are **boolean plan gates** ("is cloud memory storage a feature on this
plan, yes/no") scoped narrowly to `deployed_agent_conversation_memory` (a
marketplace-deployed agent's *external customer* memory). Neither computes
actual bytes/rows consumed and compares it to the configured limit — there
is no metering loop anywhere that measures what an account is actually
using. **No per-account storage quota exists for the owner's own agents' —
`agent_turns`, `agent_trace_events`, `memory_entries`, artifacts — which are
exactly the stores in §2 with no cap.** Today this is one shared,
unmetered pool across every tenant, isolated by row ownership but not
bounded by it.

---

## 7. Options for the founder

Ranked by leverage-to-effort, each with its tradeoff. **Recommendation: #1
first (it's nearly free — the enforcement job already exists), then #2.**

1. **Wire up the retention job that already exists, and extend its catalog.**
   `retention_enforcement_job.py` is fully built and tested — it just needs
   (a) a scheduler entry (`bounded_scheduler_service.py` or a systemd
   timer) actually calling `run_retention_apply()` per workspace, and (b)
   `data_retention_service.DATA_STORE_CATALOG` extended to cover the 11
   stores it currently ignores (`agent_turns`, `agent_traces`,
   `agent_trace_events`, `agent_action_events`, `credit_ledger_events`,
   `usage_events`, both cost ledgers, `knowledge_*`,
   `run_archive`/`run_transitions`, `gateway_events`), plus real
   `eligible_count` computation (currently hardcoded to a `COUNT(*)` with
   no age filter). *Tradeoff: cheapest option, but only solves the "growth
   forever" problem for OLD data — a very active account still grows
   between sweeps, and this alone doesn't address the 4x-redundant billing
   ledgers or the 2x-redundant trace/action events.*

2. **De-duplicate the redundant event logs at the write site.** Collapse
   `agent_action_events` into `agent_trace_events` (or vice versa) — they
   log the same tool call twice for two audiences that could share one
   table with two read views. Collapse `credit_ledger_events` +
   `usage_events` + the two monthly cost ledgers into one canonical
   per-call metering table (already halfway argued for in
   `usage_events_repository.py`'s own docstring). *Tradeoff: this is the
   single biggest structural win (§3 shows these account for ~62% + all of
   the billing rows in the per-session model) but it's a real migration —
   four write call-sites and whatever reads each table today.*

3. **Hard session/turn caps, mirroring what `agent_conversation_memory.py`
   already does.** Cap `agent_turns` per thread (e.g. keep the newest N,
   fold the rest into a `compact_summary`-class row) the same way the JSONL
   conversation store already self-trims at 400 lines. *Tradeoff: simple
   and proven pattern already in the codebase; risks losing raw transcript
   fidelity for old turns unless paired with a real compaction summary
   (see §8 on what needs the old data first).*

4. **Tiered retention by age**, using the `RETENTION_CLASSES` TTLs that
   already exist as *labels* — actually enforce them: `ephemeral` (1d) for
   trace-event ephemera that somehow got persisted, `active_conversation`
   (30d) for raw turns/trace events, `compact_summary` (90d) for
   post-compaction summaries only, `audit_record` (10yr) for the genuinely
   compliance-relevant tables (`security_control_events`,
   `agent_secret_access_events`, privacy-request audits). *Tradeoff: this
   is the "correct" long-term shape and reuses work already done
   (`RETENTION_CLASSES` is well-designed) — but it's more design work
   up front than #1's blunt sweep, and needs product sign-off on what
   "expired" should mean for something like `agent_turns` that a customer
   might still want to scroll back to.*

5. **Artifacts: enforce the retention plan that already exists, or move
   large media to object storage with a lifecycle rule.** `artifact_service.py`
   already supports an S3-compatible backend
   (`configured_artifact_storage_backend()`) — for accounts not already on
   it, moving there and setting a bucket lifecycle policy (e.g. Glacier/cold
   tier after 90 days) is closer to "flip a config" than new code, once
   `plan_artifact_retention()` is actually wired to something that deletes.
   *Tradeoff: artifacts are the single largest wildcard in raw bytes (§3),
   so this has the highest ceiling on savings, but it's also the store
   where "did we just delete something the owner wanted" is most visible
   and least reversible — needs a real UI-level "keep this" affordance
   before aggressive deletion, not just a silent TTL.*

6. **Per-account storage quotas tied to plan**, the same shape
   `storage_memory_size_mb_max` already uses for deployed-agent memory —
   extend that pattern to the owner's own agents' aggregate footprint
   (structured rows + artifacts + memory files) and surface it in the UI
   (per the standing UI ruling in `feedback_ui_normal_width_and_look_closely.md`-
   adjacent product principles: show it, don't hide it). *Tradeoff: highest
   product/billing-model impact (ties storage to the unified-credit business
   model already in flight per `project_unified_credit_business_model.md`),
   but needs a real metering job first (nothing today computes actual bytes
   used per account) — this is the end state, not the first move.*

7. **Cold-storage/archive instead of delete**, for accounts or agents that
   want a permanent record without live-table cost — move
   `expired`-by-age rows to a cheap columnar/object-store export
   (`export_workspace_data()` in `data_retention_service.py:190-223`
   already produces a JSON export shape that could target this) rather than
   deleting outright. *Tradeoff: solves the "will this break something the
   agent needs" problem (§8) more safely than hard deletion, but is
   strictly more engineering than any option above and doesn't reduce live
   Postgres size on its own — it's a complement to #1/#3, not a
   replacement.*

---

## 8. What would break if we pruned naively

- **Compaction/memory does NOT depend on old `agent_turns` rows staying in
  Postgres.** `conversation_compaction.py`'s `compact_conversation_history()`
  operates on an in-memory list of messages passed in by the caller at
  prompt-build time — it doesn't re-read history from the database itself.
  Pruning old `agent_turns` rows is safe **as long as** whatever already
  produced a `compact_summary`-class record (e.g.
  `deployed_agent_conversation_memory.summary_text`) has actually been
  written first — right now that only happens for deployed/marketplace
  agents, not for the owner's own `agent_turns` thread history. **Pruning
  `agent_turns` today, before a summary-writing path exists for it, would
  be a real data loss, not just a storage saving** — there's currently no
  mechanism that converts old owner-agent turns into a durable summary
  before the raw rows would be deleted.
- **`agent_trace_events` replay is the Work-tab / trace-replay feature.**
  `get_trace_replay()` (`agent_trace_service.py:365-390`) reads persisted
  events back for a given `trace_id` — this is what lets the owner scroll
  back through what an agent did on a past turn (tool calls, approvals,
  artifacts). Pruning these means that UI silently goes blank for old
  turns; that's a legitimate product tradeoff (§7 option 4/7), not a bug,
  but it must be a **decision**, not a side effect of a blunt sweep.
- **`credit_ledger_events`/`usage_events`/cost ledgers are billing history.**
  These likely need to survive at least as long as a customer could dispute
  a charge or we need to reconcile a Stripe invoice — pruning these on the
  same short TTL as raw chat turns would be a compliance/billing mistake.
  They belong in the `audit_record`-class tier (10yr), not
  `active_conversation` (30d), if the tiered-retention option (§7.4) is
  built.
- **`memory_entries_history` is the provenance/audit trail for Decision B**
  (update-don't-duplicate memory writes, `agent_memory.py:474-484`) — it
  exists specifically so "what did this fact used to say, who said the new
  version, and why" is reconstructable. Pruning it defeats the reason it
  was built; if it needs a cap, it should be a count-based cap per key
  (keep last N revisions), not a blind age-based delete.
- **`personal_channel_inbound_messages`/`_outbound_messages` and the
  `agent_conversation_memory` JSONL files are two of three copies of the
  same channel conversation** (the third being `agent_channel_events`).
  Deleting only one of the three without checking which one a given
  read-path actually depends on risks the exact bug
  `agent_conversation_memory.py`'s own module docstring describes fixing
  (silently-dead channel memory after a restart) — de-duplicate (§7.2)
  before pruning any of the three, or a naive sweep of "the wrong one"
  reintroduces that bug.
- **Artifacts referenced from a still-visible trace event or chat message**
  (e.g. a screenshot inline in a past conversation) would 404 if deleted
  out from under a message that still links to it. Any artifact TTL needs
  to check `agent_trace_events`/`agent_turns` for live references first, or
  be paired with pruning those referencing rows in the same pass.

---

## 9. De-duplication follow-up (2026-07-23) — what was verified, what changed, what wasn't touched

Scope: option #2 from §7 ("de-duplicate the redundant event logs at the
write site"), restricted to changes with **zero deletion risk** — no row,
table, or ledger removed, only a duplicated large field trimmed at the one
write site that creates it. No files owned by the concurrent
SOUL/IDENTITY-taxonomy removal (`workspace_context.py`,
`sage_instruction_compiler_service.py`, `memory_service.py`,
`skills_service.py`, `sage_profile_service.py`) were touched.

### 9.1 `assistant.message.completed` vs `agent_turns.content` — CONFIRMED duplicate, fixed

**Verification (file:line):** `direct_chat_generation_service.py:2569-2578`
emits the `assistant.message.completed` trace event with
`data={"text": final_reply, ...}` inside `stream_provider_backed_direct_chat()`.
The same request then returns `final_reply` as `result["reply"]`, and
`agent_turn.py:1788-1798` calls
`thread_service.record_assistant_turn(reply=str(result.get("reply") or ""), ...)`
→ `control_plane_repository.upsert_agent_turn(content=reply, ...)` — the
exact same string, written into `agent_turns.content`, in the same turn.
`agent_turn.py:317-326` (`_should_persist_direct_chat_result`) guarantees
`record_assistant_turn` always runs whenever `reply` is non-empty, so there
is no code path where the trace event carries real text but `agent_turns`
doesn't — the fallback below is safe in every case, not just the common
one.

**Consumer check (the part that could have blocked this):**
`frontend/lib/workspace/fleet/tabs/WorkTab.tsx:884-885` is the *only*
reader of this event type anywhere in the frontend (confirmed by grepping
both `frontend/app` and `frontend/lib` for the literal event name) — and it
already does
```
const repliedEvent = effectiveEvents.find(e => e.event_type === "assistant.message.completed");
const replyText = String(repliedEvent?.data?.text || assistantTurn?.content || "").trim();
```
`assistantTurn` is resolved independently from `selectedThread.turns` (i.e.
straight from `agent_turns` via `/api/threads`, not from the trace event),
so the fallback is already live code, not something added for this change.
Emptying `data.text` on the stored row makes this fallback the only path,
which was already correct.

**Fix — `server_modules/agent_trace_service.py`:** added
`TEXT_DEDUPED_TRACE_EVENT_TYPES = {"assistant.message.completed"}` and
`_persisted_trace_event_payload()`, applied inside `emit_with_envelope()`
**only** to the row handed to
`control_plane_repository.append_agent_trace_event()`. The in-memory
envelope returned to the caller — which is what the live in-request
response stream actually consumes while the turn is running — is
untouched, so nothing about the live chat experience changed. The stored
row keeps `message_id`, `citation_refs`, `artifact_ids`, and ordering
(`seq`/`ts`/`id`) exactly as before; only `text` is emptied, with
`text_ref: "agent_turns.content"` and `text_bytes_deduped: <int>` added so
the row is still self-describing about *why* the field is empty.

**What was deliberately left alone:** the never-called helper
`agent_trace_service.emit_assistant_message_completed()` (grepped — zero
callers outside its own test) was not touched or removed; it routes through
the same `emit()` → `emit_with_envelope()` path, so it inherits the fix for
free if something starts calling it later.

**Before/after bytes (same per-session model as §3 — 40 turns, 20
assistant turns/session):** `assistant.message.completed` fires once per
assistant turn (20/session). Assuming a ~1 KB average reply body (consistent
with §2's `agent_turns` row-size estimate net of that row's own JSONB
overhead), removing the duplicated text and replacing it with the ~45-byte
marker (`text: ""`, `text_ref`, `text_bytes_deduped`) saves roughly:

| | Before | After | Saved |
|---|---:|---:|---:|
| `agent_trace_events` bytes/session | 120 KB | ~100 KB | ~20 KB (~17%) |
| `agent_trace_events` share of per-session total | 46% | ~42% | — |
| Per-session total (all structured stores) | 260 KB | ~240 KB | ~20 KB (~8%) |
| Per agent (100 sessions) | 26 MB | ~24 MB | ~2 MB |
| Per account (20 agents) | 520 MB | ~480 MB | ~40 MB |
| 1,000 accounts | ~570-600 GB | ~530-560 GB | ~40 GB |

This is a real, permanent per-turn saving, not a one-time cleanup — every
future assistant turn now writes the reply body once instead of twice. It
is deliberately modest relative to the full §3 floor: `assistant.message.
completed` is 1 of the 10 modeled event types per turn, and the other 9
(trace.started/routed, 3×tool.started/result, trace.completed) were never
duplicated data to begin with, so they're untouched. It does not address
`agent_action_events` or the billing ledgers — see below for why.

**Tests:** added
`test_assistant_message_completed_dedupes_stored_text_only` to
`server_modules/tests/test_agent_trace_service.py`, asserting (a) the
stored payload has `text=""`, `text_ref="agent_turns.content"`,
`text_bytes_deduped>0`, with `message_id`/`citation_refs`/`artifact_ids`
unchanged, and (b) the envelope returned to the caller still has the full
`text`. Ran clean: `test_agent_trace_service.py` (10 passed),
`test_agent_trace_routes.py` + `test_agent_trace_repository.py` +
`test_agent_trace_control_plane_rust_gate.py` +
`test_personal_channel_trace_id.py` (20 passed),
`test_direct_chat_generation_service.py` (24 passed, 3 subtests),
`test_agent_turn.py` (44 passed / 3 pre-existing failures, see below).
Ordering is untouched — `get_agent_trace_events()`
(`control_plane_repository.py:10283-10319`) still sorts by
`seq ASC, ts ASC, id ASC`, neither of which this change touches — so trace
replay ordering and non-text metadata resolvability are unaffected.

### 9.2 `agent_action_events` vs `agent_trace_events` (`tool.started`/`tool.result`) — REAL overlap, NOT a safe merge target, not changed

**Verification:** for a direct-chat tool call, `direct_chat_generation_
service.py:2068-2075` emits a persisted `tool.started` trace event with
`args_preview` (full sanitized-args dict), then invokes
`services.execute_single_direct_tool_call` — backed by
`direct_tool_execution_service.py`, which separately calls
`agent_action_metering_service.record_started_sync()` /
`record_completed_sync()` (`direct_tool_execution_service.py:1076,1189`)
with `input_summary`/`output_summary` (bounded to 1000 chars via
`_bounded()`, `agent_action_metering_service.py:42-43,157-158`) for the
*same* tool call. So yes — the same tool call really does get logged from
two different subsystems, and some fields overlap conceptually
(tool name, connector id, an args/result summary).

**Why it isn't a safe merge/removal in this task:** `agent_action_events`
(`control_plane_repository.py:932-976`) carries columns
`agent_trace_events` has no equivalent for at all — `payer`,
`billing_mode`, `credit_type`, `credits_debited`, `platform_cost_usd`,
`approval_status`, `policy_decision`, `risk_level`, `source_table`/
`source_event_id`, `idempotency_key`. Writing it isn't a passive log call:
`direct_tool_execution_service.py` threads it through
`_enforce_direct_tool_execution_transition()` (a governance state-machine
gate) and `security_audit_service.emit_security_audit_event()` — i.e. this
table's writes are load-bearing for tool-execution policy enforcement, not
just observability. Collapsing it into `agent_trace_events` (or vice versa)
would mean rebuilding that governance/billing plumbing on a
differently-shaped table — exactly the "real migration, four write
call-sites and whatever reads each table today" risk §7 option 2 already
flagged, and squarely inside the founder's fear (a): a system that could
break while a customer's agent is mid-run. Left unchanged, as instructed —
reported here rather than forced.

### 9.3 The four billing ledgers — reader-by-reader, not merged (per explicit instruction)

Confirmed **real, distinct readers for all four** — none qualifies as "a
pure duplicate with zero readers":

| Ledger | Real reader (file:line) | What it uniquely provides |
|---|---|---|
| `usage_events` | `usage_events_repository.summarize_usage()` (`usage_events_repository.py:231-261`), called from `routes_fleet.py` — this is the literal backend of WorkTab's "Cost today" (`/api/w/{ws}/fleet/usage?scope=agent...`) | Per-call rows with `agent_install_id`/`project_id` attribution the monthly ledgers don't carry (own docstring, `usage_events_repository.py:1-11`) |
| `credit_ledger_events` | `billing_service.unified_credit_usage_for_workspace()` (`billing_service.py:1257-1290`) | The single cross-surface (sage + studio + mini_app, AI + non-AI) canonical credit-debit history; upserted by `source_table`/`source_event_id` (`uq_credit_ledger_events_source`, `control_plane_repository.py:1542-1543`) — i.e. it's deliberately *fed from* the other tables via an idempotency key, not an independent duplicate write |
| `workspace_hosted_ai_monthly_cost_ledger` | Also read inside `billing_service.unified_credit_usage_for_workspace()` (`billing_service.py:1291-1294`) and `billing_service.py:1072` | Hosted-direct-chat-specific monthly rollup joined alongside the unified ledger in the same response |
| `deployed_agent_monthly_cost_ledger` | `deployed_agent_cost_cap_service.summarize_deployed_agent_monthly_cost_ledger()`, read inside `settle_deployed_agent_monthly_cost_cap()` (`deployed_agent_cost_cap_service.py:553`) | The actual monthly-cost-cap enforcement mechanism for marketplace-deployed agents — this is a live governance decision, not just a report |

One real LLM call in the hosted-direct-chat path does write all three of
`usage_events`, `workspace_hosted_ai_monthly_cost_ledger`, and
`credit_ledger_events` (`direct_chat_hosted_usage_service.py:576,
610-639, 645-653`) — confirmed 3x, not the 4x the intro estimated, because
`deployed_agent_monthly_cost_ledger` is scoped to a different, mutually
exclusive surface (marketplace-deployed agents) and never fires alongside
the other three for the same call. Per instruction, **no ledger was
merged, removed, or had a column dropped** — this is a report, not a
change. Recommendation stands as written in §7 option 2: this is real,
provable redundancy (the same provider/model/token/cost tuple, 3 times),
but de-duplicating it means picking one canonical writer and re-pointing
`unified_credit_usage_for_workspace()` and the cost-cap settlement job at
it — a real migration with billing correctness on the line, appropriate
for a dedicated task with its own test plan, not a bytes-per-session
storage pass.
