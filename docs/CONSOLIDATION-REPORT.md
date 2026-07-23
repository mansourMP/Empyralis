# Consolidation Report — one tree

> **OUTDATED (2026-07-23):** this is a point-in-time branch-consolidation
> report from 2026-07-10 — a one-off record of reconciling scattered
> uncommitted work, not living architecture. It also predates the 2026-07-23
> founder ruling that "Sage" is dead product terminology (the platform has
> only agents — owner-facing, customer-facing serving the owner, and AskAI);
> this document uses "Sage" throughout as a live concept. Kept for history;
> do not build from this.

**Date:** 2026-07-10 | **Target:** `verify` (local only — nothing pushed) | **Trigger:** the Truth Map audit ran against a stale snapshot because tonight's work was scattered across three uncommitted locations, and re-flagged a facade already fixed on an unmerged branch.

---

## 1. Inventory

| Location | Branch | Base | State found | Files changed |
|---|---|---|---|---|
| Main tree (`/Users/mansur/empyralis`) | `verify` | — | Uncommitted working-tree edits from a **concurrent Claude Code session**, all tagged `U3-B` … `U3-I` in code comments — one changeset, not separate branches | 30 files, +2447/−782 |
| `.claude/worktrees/build-u3-j-hardware-truth` | `build/u3-j-hardware-truth` | `dfe808048` | Committed on its own branch (my own BUILD U3-J work), unmerged | 15 files, +634/−176 |
| `.claude/worktrees/agent-ab1d5512fdb5ebc72` | `feat/cli-subscription-brain` | `dfe808048` | Committed on its own branch, unmerged, depends on U3-J's `resolveHardwarePlacement`/`GatewayBoxPicker` surface | 13 files, +2256/−54 |
| "wherever U3-I landed" | — | — | **Not a separate location.** `git diff \| grep -oE "U3-[A-Z]" \| sort -u` on the main tree's uncommitted diff returned `A` through `I` — U3-I (the usage dashboard rebuild) was folded into the same concurrent-session changeset as U3-B–H, confirmed by reading `billing/page.tsx`'s own diff and comments | — |

Two more worktrees exist and were **found, inspected, and left untouched** — neither matches the user's bounded four-item list:

| Worktree | Branch | State | Disposition |
|---|---|---|---|
| `.claude/worktrees/elegant-curran-e6951d` | `claude/elegant-curran-e6951d` | One uncommitted change to `server_modules/shared.py` (RLS/tenant-scoping fix in progress, unrelated to Fleet UI/Hardware/CLI-subscription) | **Out of scope — not touched** |
| `.../scratchpad/hotfix-wt` | `telegram-hosted-polling-hotfix` | Its own committed history (`e5312667e`), not an ancestor of `verify` — has commits `verify` doesn't | **Out of scope — not merged** |

---

## 2. Merge — commits, in dependency order

All 8 commits/merges below are on `verify`, HEAD is `df06f7577`. Nothing was pushed.

| # | Commit | What |
|---|---|---|
| 1 | `71fd16966` | Committed the concurrent session's uncommitted U3-B–I changes as-is (the baseline — nothing from this changeset was altered, only committed) |
| 2 | `5d3ecf351` | My own docs from Tasks C/E: `HARDWARE-BRAIN-REALITY-REPORT.md` + `TRUTH-MAP-AUDIT.md` |
| 3 | `f38c76cbf` | BUILD U3-J, committed on its own branch before merging |
| 4 | `3d885c2c9` | **Merge** `build/u3-j-hardware-truth` → `verify` (3 conflicts, see below) |
| 5 | `09200c9d3` | `feat/cli-subscription-brain`, already committed on its own branch |
| 6 | `37466a10c` | **Merge** `feat/cli-subscription-brain` → `verify` (zero conflicts) |
| 7 | `1aa86e3ad` | Fix: `FleetAgentDetail.tsx` still hard-locked `cli_subscription` after the CLI-brain merge unlocked it (found during post-merge verification, not a conflict) |
| 8 | `df06f7577` | Fix: same bug, second location — `FleetCreateAgentWizard.tsx`'s Brain step had its own separate lock (found live-driving the wizard during verification) |

Dependency order was respected: U3-I (folded into #1) and U3-J (#3) don't depend on each other and merged independently; the CLI-subscription branch (#5) was merged *after* U3-J because it needed `resolveHardwarePlacement`/`GatewayBoxPicker`'s `requireRuntime` extension to exist first.

### Conflicts and resolutions

**Merge `3d885c2c9` (U3-J → verify) — 4 conflicting hunks, all hand-reconciled:**

1. **`frontend/lib/ui/theme-tokens.css`** — both sides added new CSS custom properties in the same region (degraded-dot / gateway-note tone from U3-J, unrelated tokens from the concurrent session). Resolved by keeping both sets of variables.
2. **`frontend/app/(account)/w/[workspaceId]/hardware/page.tsx`** — U3-J rewrote this file for the rename field + `accountMethod` badges; the concurrent session had made smaller edits to the same region. Resolved by hand-merging U3-J's structure with the concurrent session's changes preserved.
3. **`frontend/lib/workspace/fleet/FleetHome.tsx`** — both sides touched the card-rendering loop (U3-J to thread `gateways` for placement resolution, concurrent session for other card fields). Resolved by combining both.
4. **`frontend/lib/workspace/fleet/FleetAgentDetail.tsx`** — the big one, three separate hunks:
   - OverviewTab's placement source — conflict-marked, resolved in favor of U3-J's `resolveHardwarePlacement`.
   - The concurrent session's new permanent Properties panel (replacing the old `FleetRightPanel` toggle) — conflict-marked, kept as-is (not U3-J's concern).
   - **A silent, non-conflict-marked wrong-merge**: a parent-scope `const placement = derivePlacement(...)` line was new-to-HEAD text my branch had never touched in that exact spot, so git auto-merged it cleanly — but it was exactly the inert pattern U3-J was built to eliminate. Auto-merge only merges *text*, not *intent*; because my corresponding fix lived in a different function, git had no reason to flag this one. Caught by grepping `resolveHardwarePlacement|derivePlacement|useWorkspaceGateways` across the whole post-merge file rather than trusting "zero conflict markers." Fixed by hand: added `useWorkspaceGateways` + `resolveHardwarePlacement` to that scope and corrected `value={placement}` → `value={placement.label}`.

**Merge `37466a10c` (cli-subscription-brain → verify) — zero conflicts.** The branch touched Gateway-side TS, `sage_agent_runtime_service.py`, `fleet_tools.py`, and `platform_event.py` — none of which U3-J or the concurrent session's changeset had touched.

### Post-merge correctness fixes (not conflicts — found by verification, not by git)

Merging in the CLI-subscription branch flipped `COMING_SOON_MODES` from `{"cli_subscription"}` to `{}` (cli_subscription is now real), but that branch never touched frontend copy. Two places still described it as locked:

- **`FleetAgentDetail.tsx`** (commit `1aa86e3ad`): the Model tab's mode button still showed a Lock icon and `--soon` styling; a hint paragraph literally said *"Not saveable yet on this deployment — the Gateway-side CLI runner isn't wired up"* (now false); and there was no `cliSubscriptionNeedsBox` guard mirroring `local` mode's, so saving with no paired Gateway would silently succeed and only fail at turn time.
- **`FleetCreateAgentWizard.tsx`** (commit `df06f7577`): found live-driving the wizard during this verification pass — a second, independent copy of the identical bug. `submitBrain()` unconditionally rejected `providerMode === "subscription"` with a hard "Coming soon" error (a functional block, not just stale copy — the wizard could not create a cli_subscription agent at all), plus the same Lock icon and a stale "you'll be able to save this once your box can run it" hint.

Both fixed the same way: dropped the lock styling, replaced the block with a paired-Gateway-required guard (same shape as `local` mode already uses), and threaded `requireRuntime` into `GatewayBoxPicker` so it labels Claude Code vs. Codex readiness. Live-verified in the browser (see §3).

---

## 3. Verification

**tsc:** exactly **3** pre-existing errors, matching the baseline you cited — none in any file touched tonight:
- `lib/workspace/workstation-chat-pane-model.ts(22,3)` — last touched `1f6babc41` (predates this session)
- `next.config.ts(37,3)` — last touched `d9386809b` (predates this session)
- `tests/e2e/fleet-restyle-proof.spec.ts(39,24)` — last touched `efda493cb` (predates this session)

**Backend suite:** ran the full suite (`-m "not blackbox_db and not kernel"`, ~5,100 tests) on a worktree at the pre-consolidation commit (`dfe808048`) and on the consolidated tree, both with the same Rust kernel binary and DB. Both runs show ~1,150 failures/errors — all one systemic signature (`unexpected_next_action` from the Rust runtime-kernel client, reproducing even on the exact 20-file CI "demo-critical" subset run locally). Diffing the two failing-test-ID sets: **1,141 of 1,142 unique IDs are byte-identical between baseline and current**; the one-off delta is a single flaky test flipping pass/fail in each direction (`test_vault_passphrase_generates_local_key_when_env_is_local`, untouched by anything tonight) — not a regression. A second, targeted run scoped to exactly the files tonight's 3 merges touched (`activity_ledger_service`, `fleet_tools`, `control_plane_repository`, `gateway_registry/state`, `routes_gateway`, `sage_agent_runtime_service`, `tool_broker`, `vps_provisioning_service`, `hierarchy`) found 54 failures — **all 54 already present in the baseline**. Net-new regressions from tonight's consolidation: **zero**. (The systemic failure signature itself is environmental — a local kernel/DB pairing mismatch outside CI — not something introduced or fixable within this consolidation's scope.)

**Boot + click-through** (fresh signup, `web-verify`/`backend-verify` on the consolidated tree): signup → fleet home → Hardware page (VPS provider cards show `accountMethod` pills, not the old hardcoded price badges) → create-agent wizard (Placement → Brain, selected "Your subscription," confirmed Next correctly disables with no paired box) → existing agent's Model tab (cli_subscription shows the real dynamic hint, no lock) → agent's Hardware tab, clicked "Cloud only" → "Paired computer" live and watched the header **and** the Properties side panel update to the same value in the same instant. Zero console errors, zero failed network requests across the whole pass.

---

## 4. What was NOT merged

Nothing was left stuck. The two out-of-scope worktrees (`elegant-curran-e6951d`, `telegram-hosted-polling-hotfix`) were inspected, confirmed unrelated to this consolidation's four-item scope, and left exactly as found — no changes, no merge attempted.

## 5. Not pushed

Everything above is local to `verify` in `/Users/mansur/empyralis`. No `git push` was run.
