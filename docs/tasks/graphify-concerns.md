# Graphify Concerns — Auto-Generated Issues from Knowledge Graph

**Source:** `docs/graphify-report.md` (generated 2026-06-30)
**Graph:** 27,358 nodes · 72,124 edges · 1,140 communities
**Status:** pending review

---

## 1. Import Cycles (19 total)

These are circular imports that make the code fragile — change one file, the cycle breaks.

### 4-file cycles (2)

| # | Cycle | Severity |
|---|-------|----------|
| 1 | `hybrid_policy_service.py → unified_memory_service.py → session_transcript_store.py → memory_service.py → hybrid_policy_service.py` | **high** |
| 2 | `memory_service.py → workspace_context_memory_adapter.py → unified_memory_service.py → session_transcript_store.py → memory_service.py` | **high** |

### 3-file cycles (15)

| # | Cycle |
|---|-------|
| 3 | `hybrid_policy_service.py → unified_memory_service.py → memory_service.py` |
| 4 | `conversation_memory_policy.py → memory_service.py → memory_summary_service.py` |
| 5 | `conversation_memory_policy.py → memory_service.py → workspace_context_memory_adapter.py` |
| 6 | `memory_service.py → workspace_context_memory_adapter.py → unified_memory_service.py` |
| 7 | `direct_chat_tool_catalog_service.py → skills_service.py → no_provider_service.py` |
| 8 | `policy_service.py → skills_service.py → runs_execution.py` |
| 9 | `policy_service.py → skills_service.py → runtime_config.py` |
| 10 | `local_queue.py → run_service.py → runtime_attachment_service.py` |
| 11 | `local_queue.py → run_service.py → runtime_policy.py` |
| 12 | `local_queue.py → runtime_runs_api.py → runtime_policy.py` |
| 13 | `connector_validators.py → connectors/discord_connector.py → runtime_config.py` |
| 14 | `runtime_common.py → runtime_config.py → setup_sessions.py` |
| 15 | `runtime_config.py → setup_sessions.py → shared.py` |
| 16 | `local_queue.py → runtime_runs_api.py → runtime_route_registration_service.py` |
| 17 | `runtime_route_registration_service.py → runtime_run_approval_service.py → runtime_runs_api.py` |
| 18 | `gateway_execution_service.py → gateway_protocol_service.py → personal_channels_service.py` |

### 1-file self-cycle (1)

| # | Cycle |
|---|-------|
| 19 | `empyralis-supervisor/src/capabilities/clipboard.rs → self` |

### Pattern

**Memory system** has the most cycles (cycles 1–6 all involve `memory_service.py`).  
**`local_queue.py`** appears in 5 separate cycles (10, 11, 12, 16).  
**`runtime_config.py`** appears in 4 cycles (9, 13, 14, 15).

---

## 2. God Objects (risk if changed)

These nodes have the highest edge count — changing them breaks the most things.

| # | Node | Edges | Communities bridged |
|---|------|-------|---------------------|
| 1 | `PATH` | 657 | 80+ |
| 2 | `RunStartRequest` | 174 | 20+ |
| 3 | `InMemoryVirtualComputerRuntime` | 132 | 5 |
| 4 | `AgentManifest` | 127 | — |
| 5 | `enforce_workspace_access()` | 126 | — |
| 6 | `_scoped_connection()` | 123 | — |
| 7 | `AgentTurnRequest` | 98 | — |

**Action:** `PATH` needs an architecture decision entry. Any refactor touching filesystem operations must be reviewed against all 80+ dependent communities.

---

## 3. Isolated Nodes (1,079)

Functions and classes with ≤1 connection. Possible explanations:

- **Dead code** — imported by nothing, called by nothing → candidate for deletion
- **Undocumented entry points** — called dynamically or via string-based dispatch → needs a comment
- **Test-only utilities** — only referenced from test files → fine, but should be marked

**Action:** Audit the isolated nodes list. Each one is either: delete, document, or mark as test-only.

---

## 4. Thin Communities (321)

Communities with fewer than 3 nodes — too small to be meaningful modules.

**Action:** These may be fragments of larger modules that should be merged, or genuinely small utilities. Review the top 20 by cohesion score.

---

## 5. Low Cohesion Communities (2)

| Community | Cohesion | Nodes | Issue |
|-----------|----------|-------|-------|
| Agent Settings UI | 0.02 | 245 | Nodes are weakly interconnected — may need splitting |
| Run Service Management | 0.02 | 175 | Same — too many loosely-related functions in one bucket |

**Action:** These communities may represent files that should be split into focused modules.

---

## 6. INFERRED Edges (1,617 at 0.62 avg confidence)

The LLM proposed 1,617 connections it believes exist but couldn't confirm from AST data alone. 0.62 average confidence means many are speculative.

**Action:** Review high-impact inferred edges (those involving god objects). A wrong inferred edge in the graph could mislead an agent.

---

## 7. Suggested Questions (from the graph)

Questions the graph can answer that a human would struggle with:

- Why does `PATH` connect to 80 communities? What would break if it changed?
- Are the 86 inferred relationships involving `RunStartRequest` actually correct?
- What connects the 1,079 isolated nodes to the rest of the system?
- Should Agent Settings UI and Run Service Management be split?

---

## Fix Priority

| Priority | Concern | Effort |
|----------|---------|--------|
| **P0** | Memory system import cycles (cycles 1–6) | High — requires architecture change |
| **P1** | `local_queue.py` cycles (cycles 10–12, 16) | Medium |
| **P2** | Audit top 50 isolated nodes for dead code | Low — grep and verify |
| **P3** | Review INFERRED edges touching god objects | Low — manual verification |
| **P4** | Split low-cohesion communities | Medium |
| **P5** | Thin communities — merge or document | Low |
