# Phase 7A — UI Foundation (Part A research + Part B design tokens)

Staged delivery. **Part A (research) and Part B (design tokens) are done and
verified in-browser.** Part C (single shell + routing) is NOT started — awaiting
your go-ahead per the staged plan.

---

## Part A — Linear research → decisions

Two independent research passes (both sourced primarily from Linear's own
`linear.app/now` engineering posts, plus corroborating teardowns) converged.
Full sourcing is in the raw reports; the operative decisions:

| # | Principle (what Linear does) | Our decision (this stack: Next.js + pure CSS vars, no Tailwind) |
|---|---|---|
| 1 | **Generated, not hand-painted color.** They collapsed 98 vars/theme → **3 primitives** (base, accent, contrast) in **LCH** for perceptual uniformity. | One base accent hex; derive every step with `color-mix(in oklch,…)`. OKLCH (not LCH) because our accent sits in the blue→purple zone where sRGB/LCH drift hue. Done in `theme-tokens.css`. |
| 2 | **Two-tier Inter** (Inter Display headings / Inter text). Narrow weight band (400/500). | Keep DM Sans (a deliberate prior choice); **one font token, no serif**. Weights 400/500; headings 600, `-0.01em`. Promoted a type scale (`--text-2xs…lg`) into tokens. |
| 3 | **4px grid; density = consistency, not smallness.** 14px body is correct for a dense tool. | Promoted the 4px spacing scale (`--space-1…16`) + radii + control heights into tokens. Kept 14px body (see open question). Tightened line-height + subtle `-0.006em` tracking. |
| 4 | **Optimistic UI; asymmetric motion** — appear instant (`0s`), dismiss brief (`.15s`). Only animate `transform`/`opacity`/`bg`. | Added `--motion-fast/base/slow` (100/150/250ms). Asymmetric popover timing + the local-mutation pattern are Part-C/later work (named, not done here). |
| 5 | **Keyboard-first**: ⌘K palette, single-key verbs, `g`-then-letter nav, shortcuts surfaced inline + a searchable `?` panel. | Fleet already has a ⌘K palette. `g`-chords + inline hint badges land in Part C (left rail / nav). |
| 6 | **Borders over shadows**; motion is corrective not decorative. | Already the house rule in `fleet-theme.css` (zero `box-shadow` except fixed overlays). Written down as a constraint, not just a habit. |

**Flag — `#5E6AD2` is Linear's *literal* product accent**, confirmed across
4+ independent extractions (not just "Linear-adjacent indigo"). You picked it as
"restrained indigo like Linear"; it is in fact their exact hex. Since the brief
says *not cloning Linear*, this is a real call. It's a **one-line change** (every
step derives from the single base), so keeping it now costs nothing later. Open
question below.

---

## Part B — token diff (before → after)

Single source of truth is now real: **`theme-tokens.css`** owns color + scales;
`chrome.css`, `fleet-theme.css`, and the JS theme layer all resolve from it.

**Accent — the headline change**
| token | before | after |
|---|---|---|
| `--accent` (theme-tokens) | ink `#f4f4f5` dark / `#18181b` light | **indigo `#5e6ad2`** both themes; hover/strong/soft/muted/ring derived via `color-mix(in oklch,…)` |
| `chrome.css --app-accent*` (dark) | mint `#00D992` / `#00BF7A` | `var(--accent*)` |
| `chrome.css --app-accent*` (light) | ink `#202020` / `#2F2F2F` | `var(--accent*)` |
| `.app-auth-page --app-accent` | ink `#2f2f2f` | `var(--accent)` |
| JS `resolveWebCssVariables` `--app-accent*` (light+dark) | ink from `DESIGN_SYSTEM_BRAND_COLORS` | `var(--accent*)` pass-through |

**Fonts** — Fraunces serif removed entirely (loader in `layout.tsx`, `--app-font-heading`
in `chrome.css`, h1–h6 fallback in `globals.css`). Headings now render the body sans.
Grep confirms **0** `Fraunces` / `#00D992` references frontend-wide.

**Surfaces** — `--app-surface-2` / `--app-surface-inset` hardcodes (`#1A1A1A`/`#101010`
dark, `#F0F0F0`/`#EEEEEE` light) now resolve from new `--bg-surface-2` / `--bg-inset` tokens.

**New scale tokens (theme-tokens.css)** — `--text-2xs…lg`, `--weight-regular/medium`,
`--tracking-tight/wide`, `--leading-tight/normal`, `--space-1…16`, `--radius-control/card/pill`,
`--h-control-sm/md`, `--motion-fast/base/slow`.

**Files touched:** `frontend/lib/ui/theme-tokens.css`, `frontend/lib/ui/chrome.css`,
`frontend/lib/workspace/fleet/fleet-theme.css`, `frontend/app/globals.css`,
`frontend/app/layout.tsx`, `shared/design-system/tokens.ts`.

---

## What the brief assumed vs. what was already done (named, not silently cut)

A prior restyle ("Phases U/S") had already done several Part-B items the brief
listed as open. I did not redo these; I verified them:

- **`theme-tokens.css` already existed** as the color source — Part B was "finish
  wiring the last hold-outs (accent/surfaces/font) to it," not "create it."
- **The two competing theme switches were already unified.** `fleet-preferences.ts`
  delegates theme to the account-wide `useAppTheme`/`setGlobalTheme`; localStorage
  only holds rail collapse/sections now (documented in `docs/UI-MODEL.md`). Nothing
  to unify.
- **`fleet-theme.css` was already 4px-grid, DM-Sans, Linear-dense.** The typography
  pass tokenized it + tightened, rather than rebuilding density.

**Newly surfaced (was not in the brief):** a **JS token layer** (`shared/design-system/tokens.ts`
→ `resolveWebCssVariables`, inlined onto `<html>` by `AppThemeProvider`) was
overriding the CSS `--app-accent` with ink. Verification caught it (CSS `--accent`
was indigo but `--app-accent` computed to `#202020`). Fixed by making those inlined
values `var(--accent*)` pass-throughs so **CSS is the single accent source**. This
dual JS/CSS token layer is the deeper "one source of truth" issue; the accent now
single-sources, but the JS layer still owns bg/border/text/status vars — worth a
future consolidation decision (named for you, out of 7A scope).

---

## Verification (in-browser, :3000)

- Computed `--accent` = `rgb(94,106,210)` = `#5E6AD2` in **both** `data-theme` states.
- `--app-accent` and `--interactive-accent` resolve to indigo in both themes (JS
  override no longer forces ink).
- Heading `font-family` = DM Sans stack (no serif).
- Scale tokens load (`--space-4`=1rem, `--text-base`=.875rem, `--motion-base`=.15s).
- Landing renders indigo on CTAs + eyebrow, sans headings, clean neutral grays.
- **Zero** console errors. Grep: 0 `Fraunces`, 0 `#00D992` frontend-wide.

Remaining neutral grays in `chrome.css` (shadows, graphite gradients, dark
surfaces) are intentionally gray — not the accent — and were left as-is.

---

## Accent decision (resolved)

`#5E6AD2` was confirmed as Linear's *literal* product accent, so — per "not
cloning Linear" — it was nudged ~+11° toward violet, holding lightness/chroma:
`--accent: oklch(56.7% 0.158 286)` (a distinct violet-indigo, same crispness).
Body base kept at 14px (the research-backed dense baseline).

---

# Part C — single shell + routing

FleetShell is now THE shell for the whole workspace surface. Every new route
renders inside it (rail + breadcrumb frame); the agent detail is a routed page,
not a modal; legacy routes 302 to their new home.

## Route map (before → after)

| Before | After |
|---|---|
| `/w/{ws}` → Fleet grid (modal detail) | `/w/{ws}` → 307 `/w/{ws}/agents` |
| `/w/{ws}/fleet` | 307 `/w/{ws}/agents` |
| `/w/{ws}/chat`, `/sage`, `/deploy`, `/studio`, `/marketplace`, `/artifacts`, `/applications*`, `/channels`, `/integrations`, `/memory` | 307 `/w/{ws}/agents` |
| `/w/{ws}/activity`, `/tasks`, `/notifications` | 307 `/w/{ws}/inbox` |
| `/w/{ws}/gateway`, `/gateway-activity` | 307 `/w/{ws}/hardware` |
| _(agent detail was a modal in Fleet grid)_ | `/w/{ws}/projects/{id}/agents/{agentId}/{tab}` (routed page, deep-linkable) |
| — | `/w/{ws}/projects` (list), `/w/{ws}/projects/{id}` (project agents) |
| — | `/w/{ws}/agents` (all), `/w/{ws}/billing` (usage rollup) |
| `/w/{ws}/inbox`, `/hardware`, `/settings` | unchanged path, now render inside the shell frame |

Left rail: **Inbox · Projects (expandable, live list) · Agents · Hardware ·
Billing · Settings**. Keyboard: `j`/`k` move a highlight (Enter opens); `g`
then a section key jumps (`g i/p/a/h/b/s`). Chord hints surface on hover.

## New / changed files

New: `lib/workspace/fleet/Breadcrumbs.tsx` (segment-chain + dynamic-label
registry), `FleetContentFrame.tsx`, `app/(account)/w/[workspaceId]/{projects,
projects/[projectId], projects/[projectId]/agents/[agentId], projects/[projectId]/agents/[agentId]/[tab],
agents, billing}/page.tsx`.
Changed: `FleetShellDecider.tsx` (shell frame for the new segment set),
`FleetShell.tsx` (pass workspaceId), `PrimaryRail.tsx` (6 items + projects
subnav + keyboard nav), `FleetHome.tsx` (select → route, modal removed),
`FleetAgentDetail.tsx` (added `variant="page"` + URL-seeded tab, reusing every
tab component), `fleet-data.ts` (`useFleetProjects`, `project_id` on agent),
`fleet-theme.css` (shell/breadcrumb/list/stat/rail-subnav styles),
`next.config.ts` (legacy redirects).

## Verification (authenticated, in-browser)

- **Deep-link hard refresh**: navigating cold to `…/agents/ainstall_test_1/memory`
  renders the **Memory** tab (seeded from the URL), page mode, no modal backdrop. ✓
- **Breadcrumbs at depth**: "Home › Projects › **General** › **Support Bot** ›
  Memory" — real project + agent names (label registry), structural `agents`
  segment folded out. ✓
- **Rail**: all six items, Projects expanded to the live "General" project (count
  2), Projects active with the indigo accent bar under a project route. ✓
- **Redirects**: `curl` confirms 307 → correct new home for chat/sage/fleet/
  gateway/notifications/integrations/tasks and the workspace root. ✓
- **Light + dark** both coherent; indigo shows only on primary actions (Save,
  active nav). **Zero** console errors.

## Named for you (not silently decided)

- **Redirect targets for ambiguous legacy routes** (channels / integrations /
  memory / studio → `/agents`) are a best-guess mapping since those surfaces are
  now agent-detail tabs, not top-level pages. Explicit in `next.config.ts` —
  correct any individually.
- **ws-1 tenant binding**: verification was blocked until ws-1 was bound to the
  `default` tenant (it had no `workspaces` row — the latent Phase 5 issue). I
  inserted that binding (correct data state). The `ensure_workspace_tenant_binding`
  helper has a real bug (ON CONFLICT on `id` while `tenant_id` is also unique) —
  flagged for a backend fix.
- **Legacy workstation shell components** (`WorkstationShellFrame`, kernel shell,
  the redirected pages) are now unreferenced by routing but still on disk — the
  full deletion sweep is 7B, as specified.
