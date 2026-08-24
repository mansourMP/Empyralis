# Empyralis for iOS

Native Swift / SwiftUI. No React Native, no bridge, no third-party packages —
zero SPM dependencies, and it should stay that way.

## Build

The Xcode project is **generated** — `project.yml` is the source of truth and
`Empyralis.xcodeproj` is gitignored.

```bash
brew install xcodegen        # once
cd ios-app
xcodegen generate
xcodebuild -project Empyralis.xcodeproj -scheme Empyralis \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build
```

Run the tests:

```bash
xcodebuild test -project Empyralis.xcodeproj -scheme Empyralis \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

## What this app is — and is not

It is a **setup and observation** surface, exactly like the web platform:
see what your agents are doing, see and file work, react to a notification.

**There is no message composer anywhere, and there must not be one.** Talking
to an agent happens in its channel (Telegram/Slack). This is the same
platform-wide law recorded in the root `CLAUDE.md`; a chat surface here would
contradict it.

## Architecture

**Local-first.** `WorkspaceStore` hydrates from disk *synchronously* at launch,
so the first frame after cold start already holds real content. The network
syncs behind it. Writes apply optimistically and roll back only if the server
refuses. The rule that follows: **a screen never awaits a network call in order
to render.** Anything that adds a fetch-on-appear to a list view is a
regression, not a feature.

Loading states follow the same honesty law as the rest of the product:
`hasLoadedOnce` and `isRefreshing` are separate, and only the first may show a
skeleton. A spinner over content already on screen is a lie about what is known.

**Design tokens are ported verbatim from the web app's
`frontend/lib/ui/theme-tokens.css`** so the two products read as one. When a
token changes there, change it in `DesignSystem/Theme.swift` — never invent an
iOS-only colour.

**The accent law carries over:** the violet appears ONLY on the single
primary-action button in a view. Not on selected rows, tab bars, badges,
links, or status. Selection is weight and shape. Verify with:

```bash
grep -rn "Theme\.accent" Empyralis --include="*.swift" | grep -v DesignSystem/Theme.swift
```

## Setup that needs a real Apple account

None of this is required to build and run in the Simulator.

| Need | Where | Blocks |
| --- | --- | --- |
| Team ID | Xcode ▸ Settings ▸ Accounts | running on a physical device, TestFlight |
| APNs auth key (`.p8`) | developer.apple.com ▸ Keys ▸ **+** ▸ Apple Push Notifications | push delivery |
| `apple-app-site-association` | served from `https://empyralis.ai/.well-known/` | universal links |

The `.p8` is downloadable **once** — Apple never shows it again.

Backend env for push (see `server_modules/apns_service.py`):
`EMPYRALIS_APNS_KEY_P8`, `EMPYRALIS_APNS_KEY_ID`, `EMPYRALIS_APNS_TEAM_ID`,
`EMPYRALIS_APNS_BUNDLE_ID`, `EMPYRALIS_APNS_ENVIRONMENT`.

Until those are set, `apns_service.send_push` reports `not_configured` — which
is a distinct state from `failed`, deliberately, and is the honest state today.

## Traps worth not re-discovering

- **`agent_traces.root_agent_id` is never a bare install id.** It is
  `specialist:{install_id}` — stamped that way by `agent_turn.py` and
  `run_service.py` independently. Querying with the bare id compiles, returns
  HTTP 200, and matches zero rows, so every agent reads "hasn't run yet"
  forever with nothing reporting why. See `AgentDetailView`.
- **A DEBUG build's APNs token comes from the sandbox gateway** and is rejected
  outright by production. `PushManager` reports which environment minted the
  token alongside it; getting this wrong looks exactly like a dead token.
- **The `hidden` HTML-attribute trap has a SwiftUI cousin:** prefer removing a
  view from the hierarchy over hiding it. (The web login hit the literal
  version of this — an explicit CSS `display` silently beat `hidden`.)
- **A simulator's permission state persists across installs.** After testing a
  "denied" notification path, erase the device (`xcrun simctl erase <udid>`)
  or the next run inherits the refusal.
