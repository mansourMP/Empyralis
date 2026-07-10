# Build B — honest CLI detection + machine detail view — UI Contract verification

Verifies Build B against [`docs/UI-CONTRACT.md`](../UI-CONTRACT.md) by live DOM
measurement and by exercising the real app end-to-end (real signup, real
backend, real `gateway_registrations`/`gateway_sessions` rows seeded directly
so all three CLI-readiness states — ready / installed-not-signed-in /
not-installed — had real data to render), not visual inspection alone or
mocked fetches. Viewport: 390×844 for mobile, native desktop otherwise. Every
FAIL found during this pass was fixed before this table was written — none
are open.

## What this build touches

Honest 3-state CLI detection (`gateway-box-picker.tsx`), a new machine detail
route (`hardware/[gatewayId]/page.tsx`), inline guided install+login
(`CliSetupGuidance`), and a verify-poll loop reusing the
`GatewayPairPanel`/`cloud-vps-setup-panel` pattern. No backend changes — the
"agents on this box" join is done client-side, confirmed unnecessary since
`fleet_list_agents` already returns `preferred_gateway_id`/`hardware_access`
per agent.

## Measurement table

| Surface | Element | Contract spec | Measured | Result |
|---|---|---|---|---|
| Shell (unchanged) | `.fleet-shell-topbar` height | ≤48px | 48px (47px content box) | PASS |
| Machine detail | Copy button (`CopyableCommand`) | 32-34px, 13px/500 | height 32px (computed), 13px/500 | PASS |
| Machine detail | Verify button | compact, not full-width | content-sized (`fleet-btn--accent`, same class as GatewayPairPanel's) | PASS |
| Machine detail | Capabilities StatusChip | 12px text | 12px/500 | PASS |
| Machine detail | Every page | Page-level horizontal scroll | `scrollWidth === innerWidth` (390) at both list and detail routes | PASS |
| Machine detail | Both themes | light/dark render | Ready/Degraded/Missing all legible, distinct hues both themes | PASS |
| Machine detail | Breadcrumb | real name, not raw id/slug | Fixed this pass — see Bugs below | PASS (after fix) |

One honest gap, not masked: `.fleet-schip` (StatusChip) itself renders at
**~17px** tall in this app today, under the contract's chip/pill spec of
22-24px. This is a pre-existing characteristic of the shared component —
verified by inspecting the *same* class already in use on the Hardware list's
Online/Offline chip before this build touched anything. Build B added a new
`--degraded` tone (color only, `--warning-text` / `--degraded-dot`, both
already-defined tokens) and did not touch `.fleet-schip`'s sizing rules, so
it neither introduces nor fixes this gap. Flagging rather than silently
claiming a PASS against the letter of the contract.

## Real end-to-end verification (not just DOM measurement)

Signed up a throwaway test account (`buildb-verify-throwaway@example.com`,
workspace `ws_c27566c5d79e` — left in place, clearly named, zero effect on
any real workspace; the two synthetic `gateway_registrations`/
`gateway_sessions` rows used to drive it were deleted after this pass).
Confirmed live, with screenshots, both themes:

1. **Three-state detection, for real.** Seeded one box `claude_code: ready` /
   `codex: installed-not-authenticated`, another `both: not installed`.
   Capabilities grid showed **Ready** (muted green dot), **Installed, not
   signed in** (amber dot — the new tone), **Not installed** (gray dot) —
   three visually distinct states, not the pre-existing binary
   `.detected` collapse.
2. **Guided install+login renders per-state.** Not-installed CLI showed both
   the `npm install` and `claude setup-token`/`codex login --device-auth`
   blocks with working Copy buttons; installed-not-signed-in showed only the
   sign-in block. The "Empyralis never sees or stores this credential" line
   is present on every guidance panel.
3. **Verify loop, live.** Clicked Verify, confirmed "Verifying…" (spinner,
   disabled). While polling, flipped the seeded row's `claude_cli` to `ready`
   directly in the Gateway's local SQLite state store — the UI picked up the
   change on its own next 5s tick, the guidance panel for that CLI
   disappeared, no page reload. Codex's independent Verify state was
   unaffected, confirming per-runtime isolation.
4. **Machine detail route, both boxes.** Health card showed real
   `connection_status` (online → later "Degraded" once the seeded heartbeat
   aged past the 45s freshness window — an accurate, not hardcoded, read),
   `heartbeat_age_seconds`, paired-since date. Capabilities grid rendered
   Docker/Ollama/Postgres/GPU from the raw `service_inventory` array,
   including the "Unknown" fallback for a box whose inventory omitted them
   entirely.
5. **"Agents running here."** Honest empty state confirmed with real data
   (zero agents bound): "No agents are pinned to this computer" + the
   any-paired-box distinction copy.
6. **Cross-surface consistency.** The *same* 3-state signal was also
   exercised inside the real agent-creation wizard's Brain step
   (`GatewayBoxPicker`, "Your subscription" mode, since `cli_subscription` is
   unlocked from BYO-brain Phase 3): the computer dropdown showed
   `· Codex not installed` / `· Codex not signed in` for the two seeded
   boxes, and selecting the not-signed-in box surfaced a warning with a
   working `Get the sign-in command →` link straight to that box's new
   detail page — confirming the picker and the detail page agree on the
   same state for the same box, live.
7. **Hardware list → detail navigation**, both viewports: row click (not
   hitting the rename field or remove button, both correctly
   `stopPropagation`-guarded) opens the detail route; verified on 390px too.

## Bugs found and fixed during this pass

1. **Breadcrumb showed a humanized raw gateway id** ("Gw Test Ready") instead
   of the box's real name. The shell's breadcrumb system requires each
   dynamic-segment page to call `useBreadcrumbLabel(id, name)` (see
   `Breadcrumbs.tsx`) — project detail pages already do this; the new machine
   detail page didn't yet. Fixed: `useBreadcrumbLabel(targetGatewayId,
   gatewayLabel(gateway))`, one line, matching the established pattern.

## Scope note

The Hardware list's own `isOnline()` heuristic (substring match on
`status`/`connection_status` including "active") pre-dates this build and
reads a freshly-registered box as "Online" even before a session exists —
visible in this pass's own seeded data. Out of scope for Build B (not one of
the four listed tasks); the new detail page does not share this bug, since it
reads the real `connection_status` field instead.
