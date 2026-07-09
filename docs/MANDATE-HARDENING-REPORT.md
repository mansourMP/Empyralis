# Mandate Hardening — Build Report

**Date:** 2026-07-09
**Branch:** `verify`
**Scope:** Backend only. No approval UX added. Archived supervisor untouched.

Closes the 4 gaps in the execution-authority mandate system (`server_modules/authority_mandate_service.py`, gated at `skills_service.py`'s tool-execution choke point). Every claim below is `file:line`-backed, not asserted from memory — see "Verification" for how each was checked.

---

## 1. Wake-up consumption — never blend tiers

**Problem:** `bounded_scheduler_service.build_wakeup_execution_bundle` aggregated every due wake request into one composite turn, discarding each request's own tier, with no `channel_origin` — an audience-scheduled instruction (`fleet_tools.schedule_task`, called mid-conversation with an end customer) could execute alongside owner-configured work, indistinguishable once the resulting turn's tier is read downstream.

**Fix:**
- `build_wakeup_execution_bundle` now groups due requests **by tier** (`_wake_request_tier()`) and returns `groups: [{authority_tier, message, heartbeat_tasks, wake_requests, ...}]` — one bundle per tier, never blended. Heartbeat-checklist tasks (`HEARTBEAT.md`, owner-only config) always attach to the owner-tier group only.
- `runtime_heartbeat_service.py`'s scheduler callback now iterates `groups` and executes **one turn per tier group**, each stamped via `build_heartbeat_turn_request(..., authority_tier=...)` — `authority_tier` is now a required keyword argument (no silent default), so a future caller can't forget it.
- A caller still supplying the legacy flat shape (no `groups`) synthesizes the correct single group itself rather than guessing: tasks-only → owner (HEARTBEAT.md is owner-only config); any wake_requests present → `normalize_tier(execution_bundle.get("authority_tier"))`, which fails to audience, never owner.

**Producers stamped:**
| Producer | file:line | Tier |
|---|---|---|
| Context-engine event triggers (`maybe_schedule_event_trigger`) | `bounded_scheduler_service.py:609-614` | Explicit `audience` — no live sender, no parent turn to inherit from (a "system, no traceable parent" turn per the model's own rule) |
| Agent-initiated `schedule_task` | `fleet_tools.py:1287-1305` | `authority_mandate_service.inherit_tier(authority_tier)` — persists the caller's tier onto the wake-request payload |
| Legacy/pre-existing rows with no tier in payload | `bounded_scheduler_service.py:_wake_request_tier` | `trigger_kind == "event_trigger"` → known-safe audience (by design, not a gap); anything else → audience **and** `unattributed=True` |

**Unattributed observability:** any wake request that reaches consumption with no tier and isn't the recognized `event_trigger` case ledgers `mandate_unattributed` (`_ledger_unattributed_wake_request`, `bounded_scheduler_service.py`) — non-blocking, so the gap stays visible instead of silently defaulting forever.

**Honest gap:** `fleet_tools.schedule_task` correctly calls `inherit_tier()` and is structurally correct — but it is **not currently wired to the live LLM tool dispatcher**. `skills_service.py`'s `fleet` connector branch (~line 4246) has explicit per-`action_id` cases for `create_agent`, `list_agents`, `get_agent_activity`, `get_project_activity`, `configure_agent`, `message_agent` — there is no `schedule_task` case. No agent can call this tool today. The tier-inheritance code is correct and ready, but it's currently unreachable from a live turn. Wiring it in is a separate, small follow-up (add one `elif action_id == "schedule_task":` branch passing `authority_tier=session_ctx.get("authority_tier")`).

---

## 2. Gate the connector/MCP path + ownable mandate

**Problem:** `runs_execution._workflow_execute_connector_action` dispatched Telegram/Discord/custom-API/etc. connector actions with zero mandate awareness — a second, complete bypass of the choke point `skills_service.py` enforces.

**Fix — second choke point** (`runs_execution.py:2350-2463`):
- `_connector_mandate_gate()` mirrors `skills_service._authority_mandate_gate`'s enforcement rule (`authority_mandate_service.is_tool_call_allowed`), inserted at the very top of `_workflow_execute_connector_action` — before the `custom_api` special case and before the general connector dispatch, so every path through the function is covered.
- `_run_authority_tier(context)` reads the run's tier from `context["authority_tier"]`, `context["metadata"]["authority_tier"]`, or the nested `context["metadata"]["agent_turn_request"]["authority_tier"]` — the last covers the one confirmed path an `AgentTurnRequest`'s tier actually survives into a run's context today (`serialize_agent_turn_request` → `bind_agent_turn_metadata` → `build_turn_seed_from_request` → `create_run`'s metadata).
- Blocks ledger `mandate_blocked` (`_connector_mandate_blocked_ledger_kwargs`), same event class as the skills_service choke point, queryable together.

**Fix — ownable mandate** (the owner-declared allowlist):
- `authority_mandate_service.py` gained `connector_tool_key(connector_id, action_id)` (canonical `"{connector}.{action}"` id, matching the format already used for metering/ledger entries) and `is_audience_tool_allowed(audience_tools, tool_key)`.
- `fleet_tools.py`: `"mandate"` added to `_ALLOWED_CONFIGURE_KEYS` (fleet_configure_agent.py:756-775). Validates `mandate.audience_tools` is an array of strings (max 200), dedupes/normalizes, stores under `install_metadata.mandate.audience_tools`. PATCHable today via the existing `PATCH /api/w/{workspace_id}/fleet/agents/{agent_id}` — no new route, no UI, exactly as scoped.
- Both choke points consult it: `skills_service._authority_mandate_gate` ORs the `ToolDescriptor.audience_safe` manifest flag with `is_audience_tool_allowed(session_ctx["mandate_audience_tools"], tool_key)` (`skills_service.py:1476-1509`); `runs_execution._connector_mandate_gate` uses it **exclusively** (connector/MCP actions have no manifest-level `audience_safe` at all) — an unlisted connector/MCP action defaults to **not** audience_safe, fail-safe, exactly as specified.
- `sage_agent_runtime_service.py`'s `_resolve_specialist_toolset` now also returns `mandate_audience_tools` from the calling agent's install metadata (same bundle fetch, no extra round-trip) and `_run_sage_action_loop_v3` threads it onto `session_ctx["mandate_audience_tools"]` alongside `session_ctx["authority_tier"]`.

**Deliberately not flipped to fail-closed:** `_connector_mandate_gate` keeps "absent tier → pass through" (`runs_execution.py:2367-2373`, explicit comment). This surface has no confirmed tier-stamping producer other than the one agent-turn-spawned-run chain traced above — `run_service.py`'s `create_run` has exactly one call site (`run_service.py:6524`, inside a large Rust-kernel-gated approval/routing subsystem), and I could not find where that call site's `req`/`metadata` gets a tier from manual UI-triggered runs, webhook-triggered runs, or other non-agent-turn triggers. Flipping this gate too would block every run today, before any of those other producers stamp a tier. **Named explicitly, not silently left half-safe.**

---

## 3. Drive to fail-closed

**Producers enumerated and stamped** (every constructor of `AgentTurnRequest`, `ChannelRoutingContext`, or `SageTurnContract`):

| Producer | file:line | Behavior |
|---|---|---|
| `AgentTurnRequest` dataclass default | `agent_turn.py:141` | `authority_tier: str = "audience"` — a builder that forgets to set it fails safe |
| `serialize_agent_turn_request` | `agent_turn.py:768` | Normalizes through `authority_mandate_service.normalize_tier` |
| `_promote_turn_request_to_primary_engine_path` | `agent_turn.py:872` | Forwards `request.authority_tier` (reconstruction, not `dataclasses.replace` — every field-by-field rebuild had to be checked and fixed individually) |
| `build_agent_turn_request` | `agent_turn.py:921` | `normalize_tier(payload.get("authority_tier"))` |
| `build_inbound_agent_turn_request` | `agent_turn.py:945,973` | New `authority_tier` kwarg, normalized |
| `build_direct_chat_turn_request` | `agent_turn.py:1021` | `derive_tier_from_owner_flag(_current_user_is_owner(current_user))` — web/API session path |
| `normalize_server_owned_turn_request` | `agent_turn.py:1077` | Forwards `turn_request.authority_tier` |
| `build_run_start_turn_request` | `agent_turn.py:1362-1369,1422` | Explicit `authority_tier` in metadata (inherited from a parent turn) wins over deriving fresh from `current_user` — inheritance must not be clobbered by a re-derive |
| `ChannelRoutingContext` dataclass default | `channel_routing_models.py:41` | `authority_tier: str = "audience"` |
| `SageTurnContract` dataclass default | `sage_agent_runtime_contract.py:46` | `authority_tier: str = "owner"` — provably only constructed for authenticated owner sessions today (SAGE_MODE/`owner_sage`); not currently constructed outside tests |
| **The one live stamping site** — `_run_sage_action_loop_v3` | `sage_agent_runtime_service.py:2011,2030` | `derive_tier_from_sender_class(sender_class)` — the same classification already used for tool-visibility filtering. Covers Sage **and every fleet specialist turn across every channel** (Telegram, WhatsApp, Discord, Slack, personal channels, web/API direct chat) |

**Two genuine, live, unauthenticated gaps found and fixed while tracing these producers** (not hypothetical — both were reachable today):

1. **`personal_channel_sage_bridge_service.py:_personal_channel_no_tools_session_ctx`** (line 79-91). This "no tools" fallback for external contacts on a personal-channel bridge had **no `authority_tier` at all**. Despite the name, hardware/memory-write tools were still genuinely reachable through this path — the "no tools" label doesn't actually filter them out at the tool-assembly layer it precedes. An unauthenticated external contact reaching this fallback was gated only by whatever the (formerly pass-through) gate default happened to allow. **Fixed**: stamped `authority_tier: audience` explicitly — this path never resolves a live sender identity, so it can never be provably owner.
2. **`personal_channels_service.py:handle_cloud_channel_inbound`** (line 2141-2149). `remote_jid` (sender_id) feeds directly into the Sage bridge's sender-classification. A missing/empty `remote_jid` wasn't treated as "unclassifiable" downstream — it read as **no live sender at all**, which resolves to owner tier. A malformed or missing sender_id in a relay payload could have bought owner authority. **Fixed**: reject the inbound message outright (`raise ValueError`) rather than let it fall through to that default.

**The flip:** `skills_service._authority_mandate_gate` no longer returns `(True, None, None)` (unconditional pass) when `authority_tier` is absent from `session_ctx`. It now always normalizes — missing or garbage input fails to `audience`, never `owner` — and returns an `unattributed` flag both tool-execution entry points (`execute_single_direct_tool_call`, `execute_single_direct_tool_call_async`) use to ledger `mandate_unattributed` (non-blocking observability) separately from `mandate_blocked` (an actual denial). **Flipped.** Verified safe: the one live stamping site covers every confirmed-live channel, the two gaps found during the audit are fixed at their source (not papered over by the gate default), and the full backend test suite shows zero net-new regressions (see Verification).

---

## 4. Deployed/studio runtime verdict: **DEAD SCAFFOLDING**

Both integration points `channel_turn_request_service.py` exposes for deployed-agent channel routing have **zero callers**, confirmed by exhaustive repo-wide grep (not just an absence in the files I expected — a search across every `.py` file):

- `build_routing_context()` (`channel_turn_request_service.py:288`) — 0 callers.
- `bind_deployed_agent_source_to_context()` (`channel_turn_request_service.py:428`) — 0 callers. (This is the function a prior research pass believed "overlays provider/model/placement" onto live channel routing for deployed agents — it does not, because nothing calls it.)

The only HTTP-reachable deployed-agent turn-execution surface today is `POST /deployed-agents/{deployed_agent_id}/test-turn` (`routes_deployed_agents.py:940`) → `deployed_agent_test_turn_service.execute_test_turn()`. That service:
- Never imports `skills_service`, `sage_agent_runtime_service`, or `agent_turn.AgentTurnRequest` (confirmed via grep — zero matches).
- Is an explicit **preview/simulation**, not real execution: its own system-prompt copy instructs the model *"Do not claim that external tools, channel sends, purchases, file changes, or computer actions were executed... explain that the private test can only preview the response"* (`deployed_agent_test_turn_service.py:395-396`). `_evaluate_tool_policy` only *considers* which tools *would* fire — nothing actually runs.

**Conclusion:** no live traffic reaches either hardened choke point via a "deployed/studio agent" identity today — there is no real tool-execution surface there to gate. `deployed_agents` is a genuinely separate control-plane table from `workspace_agent_installs` (fleet), not a renamed view of the same rows, so this isn't "deployed agents secretly already run through the fleet path" — the deployed-agent live-channel routing was designed but never wired up. I did not add sender-classification to these dead functions — that would be exactly the "half-wire a spec nothing exercises" the task told me not to do. This is a finding to act on separately: either wire `build_routing_context`/`bind_deployed_agent_source_to_context` into a real live-channel dispatch path (and give it a tier the same way `_run_sage_action_loop_v3` does), or decide deployed/studio agents are being consolidated into the fleet model and remove the dead scaffolding.

---

## Tests

- `server_modules/tests/test_bounded_scheduler_service.py`, `test_runtime_heartbeat_service.py`: mixed-tier wake batch → separate turns, correct tier per group; legacy tierless wake request → audience unless provably `event_trigger`-originated, never owner; owner-only heartbeat tasks never leak into a non-owner group.
- `server_modules/tests/test_skills_service.py` (`AuthorityMandateGateTests`): owner tier reaches real dispatch; audience tier blocked for non-`audience_safe` tools and ledgers `mandate_blocked`; missing tier now blocked (fail-closed) and ledgers `mandate_unattributed`; `mandate_audience_tools` allowlist grants access alongside the manifest flag.
- `server_modules/tests/test_runs_execution_graph.py` (`ConnectorActionMandateGateTests`): audience tier blocked for a connector action not in `mandate.audience_tools`; allowed once listed; owner tier unaffected by an empty/absent mandate; absent tier still passes through (documented fail-open for this specific choke point).
- `server_modules/tests/test_hierarchy.py`: `mandate` PATCH shape validation (non-object rejected, `audience_tools` non-array/non-string-items rejected, valid patch applied, dedupe/normalize verified).
- `server_modules/tests/test_authority_mandate_service.py`: tier derivation/normalization/inheritance primitives, pre-existing from the mandate build this hardens.

All new tests pass. Full backend suite (`pytest server_modules/tests/`, 5000+ tests) diffed test-ID-by-test-ID against the unmodified branch (`git stash` before/after): **zero net-new failures**. Every failure present with my changes was independently confirmed present, byte-for-byte identical, without them — pre-existing infrastructure noise (a Rust-kernel-mock confound affecting ~1300+ unrelated tests across the branch, and a large uncommitted unrelated refactor already in the working tree before this task started, e.g. `routes_workspaces.py`'s in-progress invite/revoke rewrite). Two apparent "new" failures were my own new tests + one unrelated test showing up under full-suite ordering pollution — both confirmed to pass 100% in isolation.

## Honest remaining holes (as of the original build)

1. ~~`fleet_tools.schedule_task` is unreachable.~~ **Resolved** — see Follow-up §1 below.
2. ~~`runs_execution._connector_mandate_gate` is not fail-closed.~~ **Resolved** — see Follow-up §2 below.
3. **Deployed/studio agent live-channel routing doesn't exist yet** (Gap 4) — not a mandate gap specifically, but means this whole agent class currently has no tool-execution surface at all, live or gated. Still open.
4. **`fleet_configure_agent`'s bundle-lookup-before-validation ordering** means a malformed `mandate` patch against a nonexistent `agent_id` reports "Agent not found" rather than the shape error — cosmetic (still `ok: False`, still rejected), not a security issue, but worth knowing if this surfaces in the UI. Still open.

---

# Follow-up: Mandate Completion (2026-07-09)

Two items named as open holes in the original build, closed in this pass.

## 1. Wired `schedule_task` into the live dispatcher

`skills_service.py`'s `fleet` connector branch (`execute_single_direct_tool_call`, ~line 4246) gained a `schedule_task` dispatch case, passing `authority_tier=session_metadata.get("authority_tier")` straight through to `fleet_tools.schedule_task` (which already called `inherit_tier()` correctly — the gap was purely reachability, not logic).

Also added the missing `ToolDescriptor` (`tool_name="fleet__schedule_task"`, `connector_id="fleet"`, `action_id="schedule_task"`) to `_builtin_tool_descriptors()` — without it the tool wasn't just undispatchable, it was invisible to Sage's own turn (fleet tools are injected into Sage's tool list by iterating this exact descriptor list; specialists never see `fleet__*` tools at all, by design). Set `audience_safe=False` explicitly, matching every sibling fleet tool.

Verified empirically (not just by reading): with the descriptor in place, `_authority_mandate_gate` correctly resolves `audience_safe=False` by default, `True` only when the calling agent's `mandate.audience_tools` contains `"fleet.schedule_task"` (the canonical key `authority_mandate_service.connector_tool_key("fleet", "schedule_task")` produces).

**Tests** (`test_skills_service.py::AuthorityMandateGateTests`): owner-tier dispatch reaches `propose_self_wakeup` with `payload.authority_tier == "owner"`; audience tier blocked by default (`MANDATE_BLOCKED_MESSAGE`); audience tier allowed once `mandate.audience_tools` lists it, wake request still carries `audience` (never upgraded). **End-to-end** (`test_bounded_scheduler_service.py`): a real `fleet_tools.schedule_task()` call's actual output (not a hand-written fixture) is fed into `build_wakeup_execution_bundle`, confirming the resulting group is tagged `audience`, contains only that instruction, and never blends with an unrelated owner heartbeat task — extends the existing tier-blending test rather than duplicating it.

## 2. Fail-closed audit for the connector gate — flipped

Traced every trigger reaching `run_service.create_run`'s single call site (`run_service.py:6524`, inside `create_run_from_prepared_request`) back through its dependency-injection layers (`create_legacy_run_result_from_request` → `_create_run_from_request`/`_prepare_run_start_request` in `runs_core.py`/`runs_delegation.py`) to every `RunStartRequest` constructor in the codebase:

| Trigger | Path traced | Tier source |
|---|---|---|
| Agent-turn-spawned | `AgentTurnRequest` → `build_run_start_request_from_turn` → `build_turn_seed_from_request` → `bind_agent_turn_metadata` | `metadata.agent_turn_request.authority_tier` (via `serialize_agent_turn_request`, already covered) |
| Child/subflow/delegated runs | `run_service.py:3329,3419,5482` (`child_req`) | Inherited automatically — `build_workflow_child_metadata` does a **wholesale copy** of the parent's `context["metadata"]` before adding depth-tracking keys, so `authority_tier` survives unless deliberately stripped |
| Weekly/cron schedule fired (`runs_core.py:trigger_schedule_now`) | `_execute_scheduled_run_request` → `execute_built_unowned_system_run_start_request_via_turn_runtime` → `turn_ingress_service.start_system_run_start` → `start_run_start` → `resolve_run_start_turn_request` → `build_run_start_turn_request` | `current_user`-derived (see bug found below) |
| Webhook-ingested (`POST /webhooks/ingest/{workspace_id}`, `dependencies=[require_api_key]`) | `ingest_webhook_response` → `enforce_workspace_access(current_user, ..., minimum_role="member")` → `ingest_webhook_payload` → `execute_run_start_request_via_turn_runtime` → `turn_ingress_service.start_run_start` (the **non-system** path — a real authenticated `current_user`, never the placeholder) | `current_user`-derived, correctly (owner if truly workspace owner, audience for a member) |
| `agent_workspace_api.py`'s local file-op routes (`/files/write`, `/files/delete_request`, device actions) | Route-gated by `Depends(require_admin_api_key)`, but `_execute_workspace_run_request_async` doesn't thread the route's `current_user` through — falls to the same system placeholder as the schedule path | Resolves to `audience` post-fix (conservative: an admin-only-reachable route's runs get the least-privileged tier rather than none at all — safe, not a hole) |
| Demo/onboarding first-run (`demo_workflows.py`) | `start_first_run_demo(current_user=...)` threads a real authenticated user | `current_user`-derived, correctly |

**A real, live gap found and fixed in the process**: `turn_ingress_service._default_system_user(None)` — the placeholder used for "no real caller" system/scheduled runs — returns `{"auth_type": "api_key", "user_id": "", "email": ""}`. `agent_turn._current_user_is_owner()` granted owner on `auth_type == "api_key"` alone, with no identity check — meaning **every schedule-fired run with no explicitly persisted tier silently executed with owner authority**. `auth_type == "api_key"` is checked in 30+ places across the codebase for unrelated purposes (route access, run visibility, etc.), so the fix is scoped precisely to the mandate-tier derivation: `_current_user_is_owner` now requires a genuinely api-key-authenticated request to carry a resolved `user_id` or `email` — a real api-key holder always has one; the empty-identity placeholder never does. `agent_workspace_api.py`'s local-file routes shared the exact same placeholder shape and are fixed by the same change.

**The flip**: `runs_execution._connector_mandate_gate` no longer passes through on a missing tier. It now mirrors `skills_service._authority_mandate_gate` exactly — normalizes absent/invalid to `audience`, returns an `unattributed` flag, and the call site in `_workflow_execute_connector_action` ledgers `mandate_unattributed` (non-blocking) before the `mandate_blocked` check runs.

**Tests**: `test_agent_turn.py` — the empty-identity placeholder now resolves to `audience`; a genuine api-key owner (non-empty identity) still resolves to `owner`; a persisted `metadata.authority_tier` still wins over the placeholder (inheritance not clobbered). `test_runs_execution_graph.py::ConnectorActionMandateGateTests` — no-tier now blocked (was pass-through) and ledgers both `mandate_unattributed` and `mandate_blocked`. The file's other 17 pre-existing `_workflow_execute_connector_action` call sites (dispatch/duplicate-guard tests unrelated to the mandate, written before it existed) were updated to pass `authority_tier: "owner"` — they represent real owner-configured/agent-turn-spawned runs, which is what they'd actually carry in production now that every producer is covered.

**Verification**: full backend suite diffed test-ID-by-test-ID against the unmodified branch both before and after this follow-up (same methodology as the original build) — zero net-new failures both times. `test_runs_execution_graph.py` specifically: identical 52 pre-existing failures before and after the flip, confirmed via `diff` on the sorted failing-test-ID lists.

## Remaining holes after this follow-up

1. **`agent_workspace_api.py`'s admin-gated local routes resolve to `audience`, not `owner`.** Safe (never over-grants), but imprecise — the route's own `require_admin_api_key` dependency already proves the caller is an admin; the run just doesn't know it. Fixing this precisely requires threading `current_user` from the route handler into `_execute_workspace_run_request_async` (currently called with one positional arg, dropping it) — a small, contained change, not done here since it's an accuracy improvement, not a safety gap.
2. **Deployed/studio agent live-channel routing** (original Gap 4) — still dead scaffolding, unchanged.
3. I traced `run_service.create_run`'s producers exhaustively for the **shape** of tier propagation (does a code path preserve/derive a tier correctly), not for a fully exhaustive list of every UI button or integration that might eventually call these routes. New callers of `RunStartRequest`/`create_run` added later must follow the same pattern (thread a real `current_user`, or explicitly stamp `metadata.authority_tier`) — nothing enforces that structurally today.
