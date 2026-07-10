# Build C — hardware list honesty + chip-size reconciliation — proof

Follow-up to Build B, fixing the two gaps that build's own proof doc flagged
rather than masked. Verified live by DOM measurement and by exercising the
real app (real backend, a real seeded `gateway_registrations` row), not
visual inspection alone. Every FAIL found was fixed before this was written.

## 1. Kill the "online-that-isn't" list facade

**Root cause confirmed:** `isOnline()` (Hardware list) did
`` `${connection_status} ${status}`.toLowerCase().includes("active") ``,
so ANY validly-paired registration — `status: "active"` is a registration's
*pairing* lifecycle, set the moment it's created, independent of whether a
live WSS session has ever connected — read as "Online".

**Fix:** extracted `connectionPresentation(g: FleetGateway)` into
`gateway-box-picker.tsx` (previously a private, unshared function inside the
detail page only). Both the Hardware list and the machine detail page now
call the same function, which reads `connection_status` with exact-match
comparisons only (`online`/`degraded`/`reconnecting`/`revoked`, default
`offline`) — never a substring match, never a fallback to a lifecycle
`status` string.

**Driven live:** seeded a registration with `status: "active"` and
deliberately **zero** `gateway_sessions` rows (the exact "just paired, no
session yet" scenario). Screenshotted both themes, both the list row and its
detail page:

| Surface | Theme | Result |
|---|---|---|
| Hardware list row | light | **Offline** (previously would have read Online) |
| Hardware list row | dark | **Offline** |
| Machine detail — Connection | light | **Offline** |
| Machine detail — Connection | dark | **Offline** |

List and detail agree, for the same box, in both themes — one truth.

## 2. Chip-size reconciliation

**Ruling applied to `docs/UI-CONTRACT.md` §3:** split the single "Chip / pill:
22-24px" row into two — non-interactive status chips (~17-20px) and
interactive pills/tags/filters (unchanged, 22-24px) — with a note that
`.fleet-schip` sits in the status band on purpose, and that 20px is a ceiling
only reachable if a real phone reads it as cramped, never 24px.

**Confirmed, not assumed:** `.fleet-schip` text measured at exactly 12px
(≥12px per the ruling) in Build B's own pass. This pass re-viewed it live at
390×844, both themes, on the same seeded box above — Offline/Ready/Not
installed chips all read compact and clearly legible, not cramped. No CSS
change made to `.fleet-schip` — the ruling's own instruction was to amend the
document, not the component, unless mobile reading proved otherwise, and it
didn't.

## Verification

- tsc: diffed byte-identical against the clean baseline (0 net-new failures).
- No backend files touched — same as Build B, this stayed frontend-only.
- Test fixture (`gw_test_neversession`) deleted after verification; the
  throwaway account from Build B's pass is unchanged.
