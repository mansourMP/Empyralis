# Build H — real install / sign-in buttons on the machine detail page

Replaces the SSH copy-paste guidance (`CliSetupGuidance`, Build B) with a real
`CliSetupControl` component wired to Build F's `cli.install` / `cli.login.*`
routes. Verified live against a real backend (`server.py` on the consolidated
`verify` tree, after Build G's merges) with a seeded gateway registration —
not a UI mockup read from source alone.

## Honest scope: what's real vs. what's mocked

No real paired Gateway exists in this environment (same gap noted in Build F).
What was actually exercised, live:

- **Real, end-to-end, against the real backend**: both buttons' *error* path.
  Seeded a `gateway_registrations` row (`gw-build-h-test`, no live websocket
  session) in a workspace I own, clicked "Install Claude Code" and "Sign in"
  for Codex, and the real network calls fired:
  `POST /api/gateway/registrations/gw-build-h-test/cli/install` → `409`,
  `POST /api/gateway/registrations/gw-build-h-test/cli/login/start` → `409`,
  both returning Build F's real `CLI_SETUP_GATEWAY_OFFLINE` platform-voice
  text verbatim: *"Heads up: the Gateway is offline. Start it on the paired
  machine and retry."* The UI displayed it correctly with a working Retry.
- **Rendering-only, via a temporary `window.fetch` override in the live page**
  (removed on reload, no lasting effect): the *happy* path for both runtimes,
  since that requires a real spawned `claude setup-token` / `codex login
  --device-auth` process on a paired box. Confirmed the Codex device-auth
  layout (URL + code, no input field, Cancel) and the Claude Code paste-back
  layout (URL + code-prompt + input field + Submit + Cancel) both render as
  designed, and that submitting a code clears the field and returns to the
  active-flow view awaiting the next event.
- **Not verified live**: the actual verify-poll transition to "Ready" once a
  real login truly completes (this only differs from the already-proven
  Build B verify-poll mechanism — same `refresh()`/`gatewayRuntimeState()`
  call — by the predicate it waits for, so this is low-risk, but it's still
  an honest gap). Confirms the first time this is run against a real paired
  VPS, per Build F's own honest-scope note.

## What changed

`frontend/app/(account)/w/[workspaceId]/hardware/[gatewayId]/page.tsx`:

- Deleted `CopyableCommand`, `INSTALL_COMMAND`, `LOGIN_COMMAND` — zero SSH
  copy-paste commands remain on the page.
- New `CliSetupControl` replaces `CliSetupGuidance`, reusing the exact same
  verify-poll shape (`refresh()` on an interval, checking
  `gatewayRuntimeState()`) Build B already proved live, plus a second poll
  loop against `GET .../cli/login/{run_id}/events` for the async URL/code
  stream a login run produces.
- Three states, matching `RuntimeState` exactly:
  1. `missing` → `Install {label}` button → `POST .../cli/install` →
     `Installing…` → typed failure message + Retry, or verify-poll until the
     state moves off "missing".
  2. `unauthenticated` → `Sign in` button → `POST .../cli/login/start` →
     polls for the URL/code, shows them plainly with the "Empyralis never
     sees your credential" line; Claude Code additionally gets a paste-back
     input + Submit (`POST .../login/{run_id}/input`); a `Cancel` button
     (`POST .../login/{run_id}/cancel`) is always available while a run is
     active.
  3. `ready` → renders `null` (the status chip above already reads "Ready" —
     matches Build B's original behavior exactly, no redundant text).

## Measured against UI-CONTRACT §2 (Controls)

All measurements taken via `getBoundingClientRect()` / `getComputedStyle()`
in the live page, not eyeballed.

| Element | Desktop (1280px) | Mobile (390px) | Contract | Verdict |
|---|---|---|---|---|
| `Install {CLI}` / `Sign in` / `Retry` / `Submit` / `Cancel` height | 30px | 32px | 28px desktop / 32-34px mobile | Desktop 2px over; pre-existing shared `.fleet-btn` class, unchanged by this build (same class Build B's own Verify button already used) |
| Button padding | `0 12px` | `0 12px` | `0 12px` | Match |
| Button border-radius | 10px | 10px | 6-8px | Over; same pre-existing `.fleet-btn` token, not introduced here |
| Button font | 13px / 500 | 13px / 500 | 13px / 500 | Match |
| Error text (`.fleet-channel-expand-error`) | 12px | 12px | 12px meta/secondary | Match |
| Paste-back input (`.fleet-wizard-input`) | 36px height, 13px font | same | — (no dedicated input row spec; sized like the existing wizard input it reuses) | Consistent with existing usage elsewhere |

The two "over contract" rows are an existing, already-shipped shared class
(`.fleet-btn`) this build reuses verbatim — not a new deviation. Flagging
honestly per the contract's own "unmeasured = not done" rule rather than
silently rounding it to "compliant."

## Consistency fix caught during live testing

Initial pass styled the Install error's Retry button as plain `.fleet-btn`
and the Sign-in error's Retry as `.fleet-btn--accent` — an inconsistency for
the same semantic action, only visible once both were on screen together at
the same time (a seeded box with Claude Code=missing, Codex=unauthenticated).
Fixed: both Retry buttons are `.fleet-btn--accent` now, matching the primary
action they retry.

## Verification

- Both themes: dark confirmed via live `data-theme` toggle — error text,
  chips, and buttons all remain legible; no contrast regressions.
- Desktop (1280px) and mobile (390px, real viewport resize): no horizontal
  overflow, error text wraps cleanly, Retry never overlaps.
- Console: zero errors or warnings across the full signup → agents → hardware
  → detail → install/sign-in/error/cancel flow.
- tsc: diffed against the clean baseline — identical 3 pre-existing errors
  (`workstation-chat-pane-model.ts`, `next.config.ts` eslint key,
  `fleet-restyle-proof.spec.ts` implicit any), 0 net-new.
- `npm run test:unit`: 37/37 passed, unaffected (unrelated test file).
- No backend files touched this build — F's routes consumed as-is.
- Cleaned up: the seeded `gw-build-h-test` registration was deleted after
  verification; the temporary `window.fetch` mock was never persisted
  (cleared on page reload).

## Known follow-up (not fixed this pass)

Discovered while wiring this up: `frontend/shared` is a local, git-untracked
symlink (`-> ../shared`) required for `npx tsc` to resolve
`../../shared/design-system/tokens` and `../../shared/nav-manifest` imports.
Every fresh `git worktree add` is missing it until manually recreated —
flagged separately as a background task rather than folded into this UI
change.
