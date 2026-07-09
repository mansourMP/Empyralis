# Deployed/Studio Agent → Fleet Consolidation Map

**Date:** 2026-07-09
**Branch:** `verify`
**Scope:** Read-only research. No code changes made in producing this report.
**Purpose:** Inventory everything hanging off `deployed_agents` (the "Studio" system), classify each piece, and produce an ordered plan for finishing the consolidation onto Fleet (`workspace_agent_installs`) as the one agent class.

Every claim below is **file:line-cited** and was verified against live file content by five independent research passes (not against old docs — one stale doc citation was caught in the process, see §6).

---

## 0. Headline finding

**This is not a hypothetical "should we consolidate" question. It's Phase 7B of a consolidation the product already started and partially shipped.**

`frontend/next.config.ts:3-6` (comment) + `:8-31` (the redirect table itself):

> "Phase 7A — legacy workstation routes redirect to their new home under the single fleet shell... the old page components become unreferenced and are removed in the **7B deletion sweep**."

It 307-redirects `/w/:workspaceId/studio`, `/studio-integrations`, `/marketplace`, `/applications`, `/applications/:appId`, `/channels`, `/integrations`, `/artifacts`, `/deploy`, `/fleet`, `/chat` → `/w/:workspaceId/agents` (Fleet). There is no `studio/` directory anywhere under `frontend/app/(account)/w/[workspaceId]/` — confirmed by directory listing.

The consequence, confirmed independently by all five research passes: **virtually the entire deployed-agent/Studio product surface — frontend and, transitively, backend — is unreachable from any live product surface today.** Not just the channel-routing piece (which was the given, already-confirmed fact this research started from) — everything.

**Structural fact that changes the migration-cost calculus:** `deployed_agents` is not an independent data model. `control_plane_repository.py:784`:
```sql
backing_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE ... UNIQUE(backing_install_id)
```
Every lifecycle function (`create_draft_deployed_agent` `deployed_agent_service.py:3617`, `update_deployed_agent:4476`, `deploy_deployed_agent:4736`) calls `mirror_deployed_agent_to_backing_specialist` (`:3497`), which writes tool_toggles/channel_bindings/connector_bindings/runtime_profile_id/manifest data directly into the backing `workspace_agent_installs` row via `agent_specialist_repository.update_workspace_specialist_manifest(...)`. **It's a 1:1 overlay on a Fleet row, not a second universe.** This means "consolidation" is mostly about deleting an unused overlay, not reconciling two independent schemas.

---

## 1. Inventory by area

Classification key: **live-and-valuable** (called from a live path AND does something Fleet lacks) · **live-but-duplicative** (called from a live path, but Fleet already does the same thing) · **dead** (no live caller anywhere, verified by grep, not by trusting docstrings/comments) · **dead-in-practice** (technically reachable if its mounted route were hit, but nothing ever hits that route).

### 1.1 `deployed_agent_service.py` (6,037 lines) — the core service

| Piece | Lines | Live caller? | Classification |
|---|---|---|---|
| RBAC + Rust-kernel decision gate (`_enforce_deployed_agent_service_decision`, `_enforce_deployed_agent_data_decision`, `require_deployed_agent_admin_access`, etc.) | 312-475, 3353-3367 | Called ~25+ times internally and by sibling services, but only ever reached via the (frontend-orphaned) routes below | **dead-in-practice** |
| `project_deployed_agent` (core serializer) | 3370-3407 | 11 internal callers + `deployed_agent_admin_dashboard_service.py:83`, `gateway_health_service.py:132` (itself unreachable — no frontend caller of its route) | **dead-in-practice** |
| **`get_deployed_agent_detail`** | 3859-3888 | **The one live exception** — called from `frontend/app/(account)/AccountHomeClient.tsx:74` (a "continue where you left off" existence check) via `routes_deployed_agents.py:529` | **live, but vestigial** — frontend only checks HTTP status; none of the rich payload is used |
| `create_draft_deployed_agent` | 3617-3828 | `POST /deployed-agents` — no frontend caller | **dead** (Fleet equivalent: `fleet_create_agent`, `fleet_tools.py:952`, live) |
| `list_deployed_agents` | 3831-3856 | `GET /deployed-agents` + `routes_studio.py:100` — no frontend caller | **dead** (Fleet equivalent: `fleet_list_agents`, `fleet_tools.py:387`, live) |
| `update_deployed_agent` (incl. marketplace fields `is_public`/`quality_stars`/`cost_tier`/`category`, 4531-4565) | 4476-4733 | `PATCH /deployed-agents/{id}` — no frontend caller | **dead** (Fleet's `fleet_configure_agent`, `fleet_tools.py:662`, covers tool/channel/connector config live; marketplace-listing fields have no Fleet equivalent at all) |
| Full lifecycle set: `deploy_deployed_agent`, `pause_deployed_agent`, `kill_deployed_agent`, `recover_deployed_agent`, `archive_deployed_agent`, `apply_deployed_agent_recovery_action`, `kill_deployed_agent_runtime_session`, `emergency_stop_workspace_deployed_agents` | 4736-5504 | Routes at `routes_deployed_agents.py:625-796` — no frontend caller | **dead**. Fleet has no lifecycle state machine (only a bare `enabled` bool) — but Fleet/Sage already has a *different*, more general kill-switch (`kill_switch_gate.py`, live via `sage_agent_runtime_service.py` import), so reviving this would duplicate that, not fill a gap |
| `export_deployed_agent_audit_logs`, `list_deployed_agent_activity` | 5507-5562, 5705-5775 | Routes 798, 366 — no frontend caller | **dead** (Fleet equivalent: `fleet_get_agent_activity`/`fleet_get_project_activity`, `fleet_tools.py:468,534`, live) |
| `list_deployed_agent_conversations`, `get_deployed_agent_conversation_detail` | 5565-5639, 5778-5954 | Routes 823, 849 — no frontend caller (see §1.2) | **dead** — likely duplicative-if-revived, since Fleet/Sage's `agent_transparency_events.py` canonical schema is already live via `sage_transparency_service.py` → `sage_agent_runtime_service.py:78` |
| `list_deployed_agent_memory_entries` | 5642-5702 | Route 353 — no frontend caller | **dead**. Different concept from Fleet memory: this is per-external-customer memory (table `deployed_agent_conversation_memory`), Fleet has no anonymous-customer concept |
| `delete_deployed_agent_external_user_data` (GDPR deletion) | 5957-6037 | Route 892 — no frontend caller | **dead**. **No Fleet equivalent** — novel capability, not duplicative |
| `verify_deployed_agent_knowledge_retrieval`, `upload_deployed_agent_knowledge_file` | 1786-1922, 1994-2094 | Routes 554, 583 — no frontend caller | **dead**. Partial Fleet overlap (`root_folder_uri`/`folder_grants` is a different, filesystem-based model vs. this RAG-reference-list model) |
| `list_deployed_agent_analytics`, `get_deployed_agent_analytics` | 4417-4438, 4441-4473 | Route 280/291 — no frontend caller anywhere, not even in the orphaned Studio tree | **dead** (most dead of all: even the dead UI island doesn't call it) |
| `get_deployed_agent_telegram_readiness` | 4193-4380 | Route 491 — no frontend caller | **dead**. Telegram-specific launch-readiness gate tied to the shop-assistant vertical, no Fleet equivalent |
| `resolve_deployed_agent_for_channel_owner` | 3195-3216 | **Zero callers anywhere** | **dead** (confirms the given channel-routing fact from this side too) |
| `daily_limit_channel_reply` | 3242-3266 | **Zero callers anywhere** | **dead** |
| `paused_channel_reply`, `suspended_channel_reply` | 3219-3239 | Only caller (`channel_blocking_policy_service.check_deployment_pause`) itself has zero callers | **dead** (one-hop) |
| `evaluate_deployed_shop_assistant_customer_question` (contains the file's cost-ledger write path) | 3927-4190 | `routes_deployed_agents.py:911` (`/shop-evaluate`, requires authed user + CSRF, zero frontend callers) and `channel_execution_service.py:47` (imported only by its own test) | **dead**. Fully-built, fully dead — includes real `record_deployed_agent_monthly_cost_ledger_entry` writes (4118, 4154) that never fire |
| `execute_deployed_agent_catalog_action` | 3891-3925 | Not wired to any route at all; only test callers | **dead** |
| Quota/entitlement/runtime-eligibility engine: `_enforce_phase8_quota_controls`, `_enforce_mode_capability_matrix`, `_enforce_runtime_eligibility`, self-hosted runtime binding, privacy/computer-safety contract snapshots | 792-1028, 1124-1256, 1346-1684 | Called only from the dead lifecycle functions above | **dead**. Substantial, fully-built (per-mode agent caps, daily message limits, monthly cost caps, computer-automation budgets, domain allowlists) — **novel relative to Fleet**, not duplicative, just entirely unexercised |
| **Uncertain signal**: `empyralis_get_agent_conversations` MCP tool | `mcp_server.py:258` | Real non-test call to `list_deployed_agent_conversations` from an `@empyralist_mcp.tool()`-decorated function | **Unconfirmed** — no reference to `mcp_server.py` found in `Dockerfile.runtime`, `Dockerfile.sandbox`, or `render.yaml`; could not confirm this process is part of the deployed production stack. **Resolve before deleting `list_deployed_agent_conversations`.** |

**Schema comparison** (`deployed_agents` table, `control_plane_repository.py:780-817` + SQLite mirror `2204-2229`) — columns with no `workspace_agent_installs` equivalent:

| Column/concept | Fleet equivalent | Migration note |
|---|---|---|
| `name` (required) | `label` (nullable, display-only) | Trivial |
| `avatar`, `persona`, `system_prompt` | None — Fleet stores this via satellite `agent_manifests`/`agent_bible_versions` tables | Real remodel: flat column → manifest/bible tables |
| `deployment_state` (draft/private_test/ready_for_review/live/paused/suspended/archived) | Only `status` (free text) + `enabled` (bool) | Fleet has no formal lifecycle state machine at all |
| `channels` (JSONB blob) | Sibling table `agent_channel_bindings` (one row per channel) | Different shape (blob vs. rows) |
| `knowledge_sources` (JSONB array of RAG refs) | None | Net-new concept for Fleet |
| `runtime_target` (enum) | `runtime_profile_id` FK → `runtime_profiles` (richer) | Different representation |
| `billing_plan`, `operational_state`, `last_deployed_at`, `last_paused_at` | None | Net-new |
| `is_public`, `quality_stars`, `cost_tier`, `category` (marketplace listing) | None — Fleet has no public-marketplace concept | Net-new concept |
| `metadata` sub-schema: `commerce_policy`, `customer_policy`, `escalation_policy`, `safety_policy`, privacy/computer-safety contract snapshots, `computer_automation` budgets (`deployed_agent_config_schema.py:251-279`, 11 typed Pydantic sub-objects) | Fleet's `metadata` is a thin catch-all; `tool_toggles`/`connector_bindings`/`memory_scope_overrides`/`policy_context_overrides` are Fleet's own narrower JSONB columns | No equivalent shape |

**Satellite tables keyed on `deployed_agent_id`, zero Fleet equivalent** (`control_plane_repository.py`): `deployed_agent_daily_message_usage` (819), `deployed_agent_monthly_cost_ledger` (839), `deployed_agent_upgrade_click_events` (1057), `deployed_agent_conversation_memory` (1072), `deployed_agent_business_insights` (1089), `external_user_privacy_requests` (1117), `external_user_privacy_delete_audits` (1140). All model **anonymous public customers** (channel_key + external_user_id pairs) — a concept Fleet has never needed, since Fleet's users are internal workspace members. **This is the part that's a genuine product gap, not dead scaffolding, if the business ever wants a customer-facing monetized agent product again.**

### 1.2 Transparency + conversations + Work tab

- `deployed_agent_transparency_service.py` (237 lines) contains **exactly one function**, `emit_deployed_agent_test_turn_events()` (29-237), scoped explicitly (docstring 1-11) to test/playground flows only — it says outright that production channel-turn transparency lives in `agent_turn.py`/Sage's own pipeline, not here.
- Deployed-agent conversation list/detail (`routes_deployed_agents.py:822-870` → `deployed_agent_service.py:5565-5639,5778+` → `control_plane_repository.py:2994-3007,12386-12511`) reads from **`agent_channel_events`**, a shared event-log table (not a deployed-agent-only table — it already has `thread_id` and `responder_install_id` FKs into Fleet's own `agent_threads`/`workspace_agent_installs`). Its only writer, `channel_event_journal_service.py:41,72` → `append_agent_channel_event` (`control_plane_repository.py:11262`), has **zero callers anywhere in `server_modules` outside tests** — confirmed absent from `agent_turn.py`, `fleet_tools.py`, `routes_fleet.py`, `execution_router.py`, `channel_execution_service.py` (the real production turn path). **This table plausibly has no rows in production at all, regardless of frontend.**
- The hypothesized fields from the original brief — `customer_label`, `est_cost_usd`, `message_count`, `started_at`, `turn_session_id` — **do not exist anywhere in the repo under those names** (repo-wide grep, confirmed twice independently). Closest real analogs, all at different grain: `customer`/`customer_actor` (a real structured object, `deployed_agent_service.py:2708-2731`), cost only as a **monthly aggregate** (never per-conversation), message counts only as **day/week/month rollups** (never per-conversation).
- `inbox-view.tsx`/`AgentInboxView` — re-verified: zero importers anywhere, not even from within the dead Studio hub itself (`detail-view.tsx` never renders it — its own conversation reference is just an analytics count tile).
- Effort to port anything real to Fleet's `agent_threads`/`agent_turns`:
  - **Actor/customer label — low effort.** Fleet's own `agent_turns.actor` JSONB column already flows through the read path (confirmed surfaced in `runtime_runs_api.py:395,1121-1160,1320`). *(Note: a prior build already added a version of this to `WorkTab.tsx` — see §7.)*
  - **Per-conversation message_count — not a port, net-new work** (a `COUNT(*) GROUP BY thread_id` query doesn't exist anywhere today).
  - **Per-conversation est_cost_usd — the biggest lift, not a port.** No system tracks cost at per-turn/per-thread grain in either system.
  - **`escalation_state`/`outcome` derivation (`deployed_agent_service.py:3100-3144`) — the most legitimately reusable piece.** Pure functions over `activity_ledger_events`, a table already live and already consumed by Fleet's own `/inbox` page (`frontend/app/(account)/w/[workspaceId]/inbox/page.tsx`). Moderate effort to re-scope from "conversation session" to "thread_id."

### 1.3 Test-turn preview, admin dashboard, analytics, business insights, marketplace

All five have **complete, non-stub backend implementations** (several with dedicated unit tests) — "dead" here means *no reachable caller*, not *unimplemented*.

| Service | Backend | Route | Frontend caller | Verdict |
|---|---|---|---|---|
| Test-turn preview | `deployed_agent_test_turn_service.execute_test_turn` (681-871) — real LLM call (611-678), real billing (456-608), transparency events, security audit | `POST /deployed-agents/{id}/test-turn` (`routes_deployed_agents.py:940-977`) | `workstation-client.ts:3900-3916` → `DeployedAgentTestTurnPane` → `playground-panel.tsx` → **only** `detail-view.tsx` (orphaned) | **dead (unreachable)** — refutes the hypothesis that this was "the only live surface." Most complete and directly portable of everything found, if Fleet ever wants a test-turn playground. |
| Admin dashboard | `DeployedAgentAdminDashboardService.get_dashboard` (27-95) | `GET /deployed-agents/{id}/admin-dashboard` (312-336) | `workstation-client.ts:3956-3961` → `workstation-deployed-agent-analytics-pane.tsx:170` → **only** `detail-view.tsx` | **dead (unreachable)** |
| Analytics | `summarize_deployed_agent_analytics`/`summarize_workspace_deployed_agent_analytics` (64-212) | `GET /deployed-agents/analytics`, `/{id}/analytics` (279-309) | `workstation-client.ts:3945-3955` — **zero call sites anywhere**, not even in the orphaned island | **dead** (most dead of the five) |
| Business insights (a) owner review CRUD | `list_owner_business_insights`, `review_owner_business_insight`, `apply_owner_business_insight` (241-359) | 4 routes (391-487) | `workstation-client.ts:3962-3978` → `workstation-deployed-agent-analytics-pane.tsx:457,477` → **only** `detail-view.tsx` | **dead (unreachable)** |
| Business insights (b) detection engine | `detect_business_insight_candidates`, `record_deployed_agent_turn_insights` (61-193) | No direct route; only caller is `deployed_agent_memory_service.py:520` ← the dead channel-persistence path | **dead** (trigger path is dead, not just the review UI) |
| Marketplace | `list_public_agents`, `record_upgrade_click` (68-168) | `GET /marketplace/agents`, `POST /marketplace/upgrade-click` (`routes_marketplace.py:57-80`) | See §1.4/§3 — one technically-reachable page, no in-product links to it | **dead in practice** |

### 1.4 Config schema, quota, routes — full route tables

`server_modules/routes_deployed_agents.py` — 30 routes, all gated by a live Rust-kernel decision call (`_enforce_deployed_agent_route_decision`/`_enforce_deployed_test_turn_readiness`) — **this gating infrastructure is actively maintained**, even though nothing above it (the frontend) reaches it. Full route table, method/path/handler/purpose, is in the underlying research (30 rows) — every route maps to a function classified dead or dead-in-practice in §1.1/§1.3 above, except `get_deployed_agent_detail` (live/vestigial).

`server_modules/routes_marketplace.py` — 11 routes. Two of note beyond §1.3:
- `GET /workspaces/{workspace_id}/marketplace/packages` and its sibling install/review/submit routes (84-244) model a **broader** package concept (kind ∈ agent_template|app|connector|mini_app|provider|skill) via `marketplace_distribution_service.py` (86KB) — **possibly shared infrastructure**, not deployed-agent-specific. Frontend sweep (§3) found `frontend/lib/marketplace/marketplace-pane.tsx` (1921 lines) is the only UI for this, and it too has **zero importers anywhere**. So: broader in scope than "deployed agents," but equally dead today.

**Cost-cap/quota, the key finding:** `deployed_agent_monthly_cost_ledger` write path (`deployed_agent_cost_cap_service.py:370-715`) is **live code, structurally unreachable** — it's correctly wired into the shared `run_service.py:2128` (gated on `context.metadata.get("deployed_agent_id")` being set), but the only things that ever set that metadata key are the confirmed-dead channel-delivery path and test/drill harnesses. The separate daily-message-quota chain (`quota_policy_service.evaluate_channel_quota` → `deployed_agent_daily_quota_adapter` → `deployed_agent_rate_limit_service`) has **zero callers anywhere** — fully dead, not just unreachable. Fleet has no equivalent enforcement mechanism at all (`/fleet/usage` is pure usage *reporting*, not spend-cap *enforcement* — genuinely different concepts, not the same thing via different tables).

### 1.5 `routes_studio.py` (discovered during research, not in the original file list)

Mounted at `/api/studio/*` (`server.py:256,400`). Has its own deployed-agent-channel-binding CRUD backed by `channel_platform_service.py`, which genuinely loads real `deployed_agent` rows. But `channel_platform_service.py` is imported by exactly one file in the whole backend (itself), and its only frontend caller, `integration-settings.tsx`, is inside the orphaned Studio tree. **Closed dead loop** — no live traffic in or out.

---

## 2. Worth salvaging — ranked by effort

| Capability | Where it lives today | Effort to re-home on Fleet | Priority |
|---|---|---|---|
| Actor/customer identity on conversation rows | `deployed_agent_service._actor_summary` (2708-2731); Fleet's `agent_turns.actor` already carries equivalent data | **Low** — mostly a UI-layer task | Already partially done (see §7) |
| `get_deployed_agent_detail` existence check | `AccountHomeClient.tsx:74` | **Trivial** — swap for a Fleet-equivalent existence check, no schema change | Do during Step 1 cleanup (§5) |
| Escalation/outcome classification for inbox-style views | `deployed_agent_service._derive_escalation_state`/`_derive_outcome` (3100-3144), pure functions over the already-live `activity_ledger_events` | **Moderate** — re-scope from "conversation session" to "thread_id" | Optional, product call |
| Per-conversation message count | Doesn't exist anywhere today | **Small-moderate, net-new** | Optional, product call |
| Per-conversation cost attribution | Doesn't exist anywhere today (only monthly aggregates) | **Largest lift, net-new** — needs new token/cost instrumentation at turn level | Defer |
| Customer-facing monetized agent product (public listing, daily quota, cost cap, GDPR deletion, escalation policy, Telegram shop-assistant vertical) | The bulk of `deployed_agent_service.py` + satellite tables (§1.1 schema comparison) | **Large — this is a product rebuild, not a migration.** Fleet has no anonymous-external-customer concept at all today. | **Product decision required**, not an engineering task — see §5 Step 3 |

---

## 3. Frontend consumers — what breaks if the routes go

**Only 3 real fetch call sites exist across all of `frontend/` for deployed-agent/marketplace endpoints:**

1. `AccountHomeClient.tsx:74` — `GET /api/deployed-agents/{id}` (existence check). **Reachable, fires for real** — but its own success-path destination (`canonicalStudioHref` → `/studio?agent=...`) 307-redirects to Fleet and drops the `?agent=` param, so it's a live call to a dead destination either way. Needs fixing regardless of what happens to the rest of the deployed-agent system.
2. `frontend/app/continue/page.tsx:93` — `POST /api/marketplace/upgrade-click`. Reachable (real route, outside the redirect table), but its only realistic trigger (a Telegram daily-quota-hit deep link) comes from the confirmed-dead channel/quota pipeline.
3. `frontend/app/preview/PublicAgentPreviewClient.tsx:33` — `GET /api/marketplace/agents`. Reachable (real route, outside the redirect table) — but a repo-wide grep for links to `/preview` found **zero internal links to it anywhere** in the product (no nav item, no marketing page, no onboarding step).

**Everything else — confirmed exhaustively orphaned, zero importers from anywhere under `frontend/app/`:**
- `frontend/lib/workspace/deployed-agents/` — **17 files, ~11,000 lines** (`wizard.tsx` 1777 lines = the create/edit UI, duplicates live `FleetCreateAgentWizard.tsx`; `detail-view.tsx` 1074 lines = the hub; `inbox-view.tsx` duplicates live `WorkTab.tsx`; plus `playground-panel.tsx`, `integration-settings.tsx`, `roster-sidebar.tsx`, `ai-settings.tsx`, `action-settings.tsx`, `agent-computer-detail.tsx`, `external-agent-detail.tsx`, `components.tsx`, `constants.ts`, `types.ts`, `utils.ts`, `external-agent-provider-badges.ts`).
- `frontend/lib/marketplace/marketplace-pane.tsx` (1921 lines).
- `frontend/lib/workspace/workstation-deployed-agent-test-turn-pane.tsx`, `workstation-deployed-agent-analytics-pane.tsx` (orphaned transitively, only imported by the above).
- ~35 `workstation-client.ts` functions backing all of the above (`listDeployedAgents`, `createDeployedAgent`, `getDeployedAgent`, `updateDeployedAgent`, `deployDeployedAgent`, `pauseDeployedAgent`, `testTurnDeployedAgent`, `listDeployedAgentAnalytics`, `listDeployedAgentMemory`, `listDeployedAgentConversations`, `getDeployedAgentConversationDetail`, `listStudioAgentSurfaces/ChannelCatalog/ChannelAccounts`, `listAgentChannelBindings`+CRUD, `listConnectedExternalAgents`, `listMarketplacePackages`, `listMarketplaceAppSubmissions`, `registerMarketplaceProvider/App`, `installMarketplacePackage`, etc.) — no caller outside this same cluster.
- `frontend/tests/e2e/deployed-agents.spec.ts` and `non-scaffold-surface-sweep.spec.ts` — both navigate to `/w/:id/studio` and assert on a `data-workstation-surface="deployed-agents"` attribute that is applied nowhere reachable. **These e2e specs are themselves stale artifacts of the pre-redirect world** — they currently test nothing real (whether they "pass" depends on how they handle the redirect, not on exercising the intended UI).
- `frontend/lib/workspace/workspace-setup-form.tsx:36` has a stale `/studio` dropdown value pointing at the same dead route.

**Confirmed duplication (not just absence) vs. Fleet:**
- `wizard.tsx`'s `AgentWizard` ↔ live `FleetCreateAgentWizard.tsx`.
- `inbox-view.tsx`'s conversation list ↔ live `WorkTab.tsx` (which fetches `/api/threads`, not any deployed-agent endpoint).

**Verdict: deleting every route/service classified "dead" in §1 breaks nothing a user can currently reach.** The only things that need a code change, not just a deletion, are the 3 live call sites above.

---

## 4. Channel bindings — is any live traffic bound via `DeployedAgentConfig.channels`?

**No.** Direct, exhaustively-verified answer.

- Every read of `DeployedAgentConfig.channels` (`deployed_agent_config_schema.py:272`) is write-time/audit-time only (`deployed_agent_service.py:697,813,1577,3550`) — never on an inbound-message path.
- Checked against every Fleet-supported channel type's real inbound handler: `sage_telegram_hosted` (`routes_sage_telegram_hosted.py:131`), Fleet BYO Telegram (`:236`, resolves via `agent_bindings_repository.get_channel_binding_by_agent_unscoped`), the entire personal-channel gateway cluster (`channel_gateway_bridge.py`, `personal_channel_sage_bridge_service.py`, `routes_personal_channels.py`, `routes_gateway.py`) — **zero mentions of `deployed_agent` anywhere in any of them.**
- A red herring worth flagging explicitly: `connectors/telegram_ingress_service.py:415` (`_dispatch_public_deployed_agent_envelope`, gated by `_is_public_deployed_agent_traffic:974`) *sounds* deployed-agent-aware, but never actually loads a `deployed_agent` row — both its branches converge on `agent_channel_router.route_inbound_channel_message` (`:2297`), which unconditionally calls `execute_sage_turn` for every message on that lane. Its one per-agent resolution branch (`_resolve_agent_for_inbound`, Stage 4B) is never invoked (its own comment at `:2341-2344` admits specialist dispatch is unbuilt — "Stage 5").
- `ChannelRoutingContext` (carries `deployed_agent`/`deployed_agent_id`/`deployed_agent_state`) is constructed in exactly one place (`channel_turn_request_service.py:386`, inside `build_routing_context()`) — confirmed zero real callers, extending the given fact. Its downstream consumers are **also** dead: `channel_execution_service.execute_prepared_channel_turn` (zero callers), `channel_blocking_policy_service.check_deployment_pause`/`check_incident_state` (zero callers).
- `routes_studio.py`'s channel-binding CRUD (`channel_platform_service.py`, genuinely loads real `deployed_agent` rows) is a closed loop — one caller (itself), one frontend consumer (orphaned `integration-settings.tsx`).

**Fleet's `agent_bindings_repository.py` is the only channel-binding mechanism any real inbound webhook consults today.**

---

## 5. Ordered strangler plan

Blast radius is stated for each step; every deletion target below was independently confirmed to have zero live callers by at least one (usually two) of the five research passes.

### Step 0 — Preconditions (do first, before any deletion)
1. **Resolve the MCP uncertainty.** Confirm whether `mcp_server.py` (which exposes `empyralis_get_agent_conversations` → `list_deployed_agent_conversations`) is part of the deployed production stack. If yes, `list_deployed_agent_conversations` needs to move from "delete" to "keep or reimplement" before Step 2. *Blast radius if skipped: an external MCP client silently breaks.*
2. **Confirm production data safety.** Check whether the `deployed_agents` table has any rows in production, and whether any are `deployment_state = live` with real public traffic history (even though no live route serves them today, a past deployment could still have real historical data worth preserving/exporting before table cleanup). *Blast radius if skipped: silent data loss on a later `DROP TABLE`, though nothing here proposes dropping tables yet.*

### Step 1 — Delete now (zero blast radius, confirmed zero live callers)
- **Frontend:** entire `frontend/lib/workspace/deployed-agents/` directory (17 files), `frontend/lib/marketplace/marketplace-pane.tsx`, `workstation-deployed-agent-test-turn-pane.tsx`, `workstation-deployed-agent-analytics-pane.tsx`. This is exactly what `next.config.ts`'s own comment calls the "7B deletion sweep."
- **Frontend cleanup:** fix or delete `frontend/tests/e2e/deployed-agents.spec.ts` and `non-scaffold-surface-sweep.spec.ts` (currently testing an unreachable surface); remove the stale `/studio` option in `workspace-setup-form.tsx:36`; correct `docs/PLATFORM-MAP.md:600` (still describes the pre-Phase-C Work tab endpoint — see §6).
- **Backend, fully-orphaned (zero callers anywhere):** `resolve_deployed_agent_for_channel_owner`, `daily_limit_channel_reply`, `execute_deployed_agent_catalog_action`, `deployed_agent_daily_quota_adapter`/`deployed_agent_rate_limit_service`, `channel_turn_request_service.build_routing_context()`/`bind_deployed_agent_source_to_context()`, `channel_execution_service.execute_prepared_channel_turn`, `channel_blocking_policy_service.check_deployment_pause`/`check_incident_state`, `agent_channel_router.resolve_agent_install_by_inbound_endpoint`.
- **Backend, one-hop dead (only caller is itself confirmed dead):** `paused_channel_reply`, `suspended_channel_reply`.
- *Blast radius: none. Nothing reachable calls any of this.*

### Step 2 — Migrate (small, contained, easily tested)
1. Replace `AccountHomeClient.tsx:74`'s `GET /api/deployed-agents/{id}` existence check with a Fleet-equivalent (a minimal `workspace_agent_installs` existence lookup — no schema change, this is a pure 200-vs-404 check). Fix `canonicalStudioHref` to point at the Fleet agent detail route instead of the dead `/studio` destination.
2. If Step 0.1 confirms the MCP tool is live: reimplement `empyralis_get_agent_conversations` against Fleet's `/api/threads` (already live, already has the data — see §1.2).
3. *Optional, product-prioritized:* port the actor/customer-label logic and/or escalation/outcome classification per §2, if the team wants richer Work-tab conversation rows beyond what a prior build already added (§7).
- *Blast radius: contained to one existence-check flow and (conditionally) one MCP tool. Both are easy to test in isolation — a single "does this agent id exist" round trip and a single MCP call.*

### Step 3 — Defer for a product decision (do not delete without explicit sign-off)
This tier is **not dead scaffolding** — it's unused-but-complete implementation of a **product Fleet doesn't have**: a customer-facing, monetized, anonymous-external-user agent deployment (public marketplace listing, daily message quotas with upsell CTAs, monthly cost caps, GDPR-style external-user deletion, escalation-to-owner policy, a Telegram shop-assistant vertical with menu RAG and order capture, lifecycle state machine, computer-automation safety budgets).
- Everything in the §1.1 "novel relative to Fleet" rows, plus `routes_studio.py`/`channel_platform_service.py`, plus the 7 satellite tables modeling anonymous customers (§1.1 schema comparison), plus `routes_marketplace.py`'s broader package-distribution system (§1.4) if that's confirmed unrelated to any live Fleet marketplace plans.
- **Recommendation:** get an explicit answer from product/stakeholders — "do we still want a customer-facing monetized agent product, ever?" — before deleting this tier. If no: delete it in one deliberate, separately-reviewed pass (it's already fully unreachable, so there's no rush and no live-traffic risk either way). If yes: this is the starting point for rebuilding that product on top of Fleet installs (the `backing_install_id` overlay pattern already shows how cheaply that CAN be re-attached to a Fleet row if resurrected), not scaffolding to throw away.
- *Blast radius if deleted: none functionally (nothing uses it today) — the real cost is **option value**: losing a fully-built reference implementation of a product surface that would take real design + engineering effort to rebuild from scratch.*

---

## 6. Corrections found along the way

- `docs/PLATFORM-MAP.md:600` still states the Work tab uses `GET /api/deployed-agents/{id}/conversations` — **stale**. The code was switched to `/api/threads` in commit `51c098545` ("fix(fleet): channel reply delivery + Work tab data source (Phase C)"). Needs a doc fix independent of this consolidation.
- The two e2e specs listed in §5 Step 1 currently assert against a UI surface `next.config.ts` has redirected away from — whatever they report as "passing" today is not exercising the Studio UI they claim to test.

## 7. Note on work already done

A prior build (Transparency v1, same session lineage) already added actor/customer-identity rendering to `frontend/lib/workspace/fleet/tabs/WorkTab.tsx` (a `conversationWho()` helper reading the already-live `turn.actor.display_name` field) after discovering — independently, and consistent with this report's §1.2 — that Fleet's live conversation pipeline doesn't have a `customer_label` field but does already carry actor identity per-turn. That specific §2 salvage item is therefore already partially delivered; this report's finding is corroborating evidence, not a new recommendation to act on.
