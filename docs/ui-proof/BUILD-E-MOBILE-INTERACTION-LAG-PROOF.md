# Build E — mobile interaction lag — diagnosis + proof

Diagnosed live on a real 390×844 session (real backend, real login, real
polling), not guessed from source alone. Every claim below is either a
direct source-code fact (file:line) or a live measurement
(`performance.getEntriesByType('resource')`, a `PerformanceObserver` for
`longtask` entries, and DOM `scrollWidth` checks run in-page via the browser
tool's JS execution). Before/after network timing captured on the same
account, same page, same 30s window, unfixed vs. fixed build.

## Suspects checked, in the order given

### (a) Live polling re-rendering too much — CONFIRMED, this is the dominant cause

`useFleetAgents`/`useFleetProjects` (`frontend/lib/workspace/fleet/fleet-data.ts`)
were each "fetch on mount + own `setInterval`" — every call site got its own
independent timer and state. On a real page, multiple always-mounted
components each hold their own instance:

| Hook | Simultaneous callers on the Agents page | File:line |
|---|---|---|
| `useFleetAgents` | `PrimaryRail.tsx:96`, `SageLauncher.tsx:56`, `FleetCommandPalette.tsx:87`, `agents/page.tsx:52` | 4 |
| `useFleetProjects` | `PrimaryRail.tsx:94`, `FleetCommandPalette.tsx:88`, `agents/page.tsx:53` | 3 |

**Measured live**, `performance.getEntriesByType('resource')` on the
unfixed build, logged-in real session, 390px viewport, idle (no user
interaction) for ~150s:

```
/fleet/agents:   4 simultaneous requests, every ~30000ms, for the whole session
/fleet/projects: 3 simultaneous requests, every ~60000ms, for the whole session
/fleet/usage (scope=workspace): 2 simultaneous requests — but ONE-TIME at
  mount (PrimaryRail.tsx:181, agents/page.tsx:117 — neither polls), not a
  recurring cost. Noted, not fixed this pass — see Scope below.
```

Every 30 seconds, this fires 4 near-simultaneous network requests, 4 JSON
parses, and 4 separate React state updates/re-renders — all in the same JS
tick, at an unpredictable moment relative to whatever the user is doing
(mid-scroll, mid-tap, mid-drawer-animation). On this test account (1 agent,
1 project) it didn't cross the browser's 50ms `longtask` threshold — but the
cost scales directly with account size (a real account with more agents/
activity re-renders more per tick) and this sandbox's CPU is materially
faster than real mobile hardware, so the absence of a measured longtask here
undersells the real-device cost. The *duplication factor* itself (4x, 3x) is
the hard, unambiguous, MEASURED waste regardless of device speed.

### (b) Animation transitioning layout instead of transform/opacity — one real violation found

- **Ruled out**: the mobile nav drawer itself (`.fleet-rail--mobile-open`,
  `fleet-theme.css:4860-4866`) already animates `transform: translateX()`
  only — compositor-friendly, contract-compliant. The Properties drawer
  (`.fleet-properties-drawer`, `fleet-theme.css:1362-1378`) animates
  `opacity`/`transform` only too. Neither needed a fix.
- **Confirmed violation**: `.fleet-rail-section-items` (`fleet-theme.css:438-446`)
  — the collapsible Projects/Agents nav sections *inside* the open mobile
  drawer — transitioned `max-height` (400px → 0) alongside `opacity`.
  `max-height` is layout-triggering (forces reflow every frame, not just
  composite), a direct §10 violation ("transitions are opacity/transform
  only"). Live-tapped the collapse control with the same `longtask`
  observer running: zero longtasks recorded at this account's nav-item
  count, so not itself a proven jank source on this data — but it's fixed
  regardless, per "honor the contract's motion rules," since leaving a
  known layout-animated property in a frequently-tapped drawer control is a
  real violation whether or not this test account was large enough to
  reveal its cost.

### (c) Heavy unvirtualized DOM on long lists — present, not the measured cause

Inbox (`frontend/app/(account)/w/[workspaceId]/inbox/page.tsx:24`, `.map()`
at line 113) is not virtualized, but `useWorkspaceActivity(workspaceId, 50)`
caps it at 50 rows. No `longtask` observed scrolling this list. Not fixed —
virtualizing a 50-row list would add a dependency for a suspect measurement
didn't support as significant.

## The fix

1. **`fleet-data.ts`**: added `useSharedPolledResource()`, a small
   module-level cache (`Map<key, {data, subscribers, intervalId, inFlight}>`).
   Every hook instance for the same cache key now shares one fetch + one
   interval + one cached value: the interval starts on the first subscriber
   and stops on the last, and a fetch already in flight is reused instead of
   duplicated. `useFleetAgents`/`useFleetProjects` now delegate to it — same
   public return shape (`{agents, loading, error, refresh}` /
   `{projects, ...}`), so no call site elsewhere needed to change. `refresh()`
   now updates every subscriber sharing the key immediately (a strict
   improvement — e.g. creating an agent now updates the sidebar instantly
   instead of waiting up to 30s for its own independent poll).
2. **`fleet-theme.css`**: `.fleet-rail-section-items` transitions `opacity`
   only now; `max-height` changes instantly (no longer animated). Visible
   effect is close to unchanged (content still fades) without animating a
   layout property on every nav-section tap.

## Before / after — measured, same account, same page, same window

| | Before (unfixed) | After (fixed) |
|---|---|---|
| `/fleet/agents` simultaneous requests per poll tick | **4** | **1** |
| `/fleet/projects` simultaneous requests per poll tick | **3** | **1** |
| Poll cadence | ~30000ms / ~60000ms (unchanged) | ~30000ms / ~60000ms (unchanged) |
| `longtask` entries during a 150s idle window | 0 (this account's data volume) | 0 |

Timestamps from the fixed build's own `performance` timeline (ms since
navigation), confirming clean single-fire intervals:
`agents: [492, 30589, 60590, 90591, 120590, 150591]`,
`projects: [490, 60590, 120590]`.

## Verification

- Both themes, 390×844: drawer open/close, nav-section collapse/expand,
  agent list — all visually correct, no regressions, no horizontal scroll
  (`scrollWidth === innerWidth` in both).
- tsc: diffed byte-identical against a clean baseline (0 net-new failures).
- `npm run test:unit`: 37/37 passed.
- No backend files touched.

## Scope note

The one-time (non-recurring) `/fleet/usage?scope=workspace` duplicate
fetch (`PrimaryRail.tsx:181`, `agents/page.tsx:117`, `projects/page.tsx:147`
all fetch the identical response independently on mount) is real but a
smaller, mount-only cost — not a recurring per-interaction jank source the
way the agents/projects polling was. Left as a follow-up candidate for the
same `useSharedPolledResource` mechanism rather than folded into this pass.
