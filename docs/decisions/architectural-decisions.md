# Architectural Decisions

Last updated: 2026-04-14
Status: Active living document

This file is the active decision log for the current Empyralis product shell.
Future sessions should read this before changing navigation, shells, tokens, billing surfaces, or backend ingress semantics.

When code disagrees with a decision below, treat the mismatch as migration debt.
Do not silently assume the implementation has replaced the decision.
Document the gap first, then change code deliberately.

## How To Use This File

- Read this file together with [docs/platform/context.md](/Users/mansur/empyralis/docs/platform/context.md), [docs/platform/canonical-architecture-contract.md](/Users/mansur/empyralis/docs/platform/canonical-architecture-contract.md), and [deployment/cloud-runtime-baseline.md](/Users/mansur/empyralis/deployment/cloud-runtime-baseline.md).
- Treat this file as the architectural why, not as a file-by-file implementation guide.
- If a future change reverses one of these decisions, update this file in the same change.

## ADR-001: Product IA Is Frozen As Five Destinations

Decision:
Empyralis has one canonical information architecture across web, Tauri desktop, and mobile.
That IA is:

- `Home`
- `Chat`
- `Work`
- `Build`
- `Control`

Why:

- The product is one workspace operating system, not separate product brains for chat, admin, agent deployment, and app management.
- The current route tree still contains historical labels such as `workstation`, `runs`, `applications`, and `admin/*`, but those are implementation-era routes, not the long-term user-facing IA.
- Every future shell should group surfaces under the same top-level mental model even when the underlying route ids remain temporarily unchanged.

Canonical grouping:

| Canonical destination | Current route families that belong there |
| --- | --- |
| `Home` | `workstation` |
| `Chat` | `chat` |
| `Work` | `runs`, `approvals`, `artifacts`, `notifications`, `activity` |
| `Build` | `agents`, `applications`, `deployed-agents`, `integrations` |
| `Control` | `settings`, `admin`, `admin/platform`, `admin/billing`, `admin/routing`, `admin/members`, `admin/policies`, account settings |

Boundary:

- New top-level destinations must not be introduced without updating this document.
- Legacy route ids may remain temporarily, but they are mapping debt, not competing IA.

## ADR-002: The Shared Token Contract Is Canonical

Decision:
The single source of truth for design tokens is [shared/design-system/tokens.ts](/Users/mansur/empyralis/shared/design-system/tokens.ts).

Why:

- The project currently carries token values in three places: [frontend/lib/ui/chrome.css](/Users/mansur/empyralis/frontend/lib/ui/chrome.css), [frontend/lib/ui/tokens.ts](/Users/mansur/empyralis/frontend/lib/ui/tokens.ts), and [mobile/src/ui/tokens.ts](/Users/mansur/empyralis/mobile/src/ui/tokens.ts).
- That duplication makes cross-surface consistency impossible to enforce.
- The shared token file consolidates the values already in use instead of inventing a new palette or spacing scale.

Contract:

- Web and Tauri consume token output as CSS variables.
- Mobile consumes token output as TypeScript constants.
- The existing `--app-*` CSS variable namespace remains the compatibility bridge until the web shell is rewired to import directly from the shared token source.
- No new raw colors, spacing values, radii, motion timings, or shadow values should be introduced outside the shared token source.

Implementation note:

- Web currently uses paired light and dark themes.
- Mobile currently uses a native dark palette with its own spacing and radius aliases.
- Both remain preserved in one canonical source until platform components are migrated.

## ADR-003: Mobile Uses One Tab Shell, Not A Separate Product Contract

Decision:
The active mobile shell is the Expo route group under [mobile/app/(tabs)](/Users/mansur/empyralis/mobile/app/(tabs)).
The shared route manifest still describes the cross-shell destination contract, but the currently mounted mobile shell is the `(tabs)` app route group, not a deleted or future `(workspace)` route group.

Current implementation truth:

- Visible mobile tabs today are `Chat`, `Agents`, `Applications`, `Profile`, `Home`, and `Notifications`.
- `Workspaces` exists as a switcher surface, not a primary visible tab.
- `today` and `spaces` remain hidden tab routes.

Why:

- Mobile is allowed to expose a smaller operational subset than web, but it must remain on the same backend contracts and route-manifest logic.
- The current tab set is implementation debt relative to the 5-destination IA, not a separate IA.
- Any future migration to a `(workspace)` route group must be explicit and tested. Until then, docs and manifests must not claim `(workspace)` is mounted.

Boundary:

- Mobile may simplify presentation depth.
- Mobile must not invent a different backend model, permissions model, or shell identity model.

## ADR-004: Tauri Desktop Uses A Frameless Window With A Web Shell And Native Controls

Decision:
The supported desktop shell is the repo-local Tauri app in [src-tauri](/Users/mansur/empyralis/src-tauri), backed by the same web shell and workspace contracts as the browser product.

Current window contract:

- Main window label is `main`.
- Main window title is `Empyralis`.
- Main window size is `1280x800`.
- The main window is created hidden, shown after readiness, and is frameless via `decorations(false)`.
- Window drag and native controls are exposed through the custom titlebar and Tauri bridge commands in [src-tauri/src/lib.rs](/Users/mansur/empyralis/src-tauri/src/lib.rs) and [frontend/lib/workspace/workstation-titlebar.tsx](/Users/mansur/empyralis/frontend/lib/workspace/workstation-titlebar.tsx).
- macOS windows are marked non-restorable.
- A separate transparent overlay window exists for computer-control overlay work.

Why:

- Desktop should feel like one Empyralis shell, not a browser tab inside a generic host frame.
- Native window controls remain the desktop-specific affordance.
- The rest of the workstation chrome stays shared with the web shell.

Launch boundary:

- The supported desktop target is the repo-local Tauri shell described in [deployment/cloud-runtime-baseline.md](/Users/mansur/empyralis/deployment/cloud-runtime-baseline.md).
- The supported contract expects built frontend/runtime artifacts, not an alternate host-dev fallback product.

## ADR-005: Billing State Is Provider-Agnostic Even Though Stripe Is The Only Implemented Provider

Decision:
Billing storage, bootstrap state, and workspace-level billing summaries are provider-agnostic contracts.
Stripe is the current concrete implementation, but Stripe is not the architectural abstraction.

Current proof points:

- [server_modules/control_plane_repository.py](/Users/mansur/empyralis/server_modules/control_plane_repository.py) stores billing account and subscription rows with explicit `provider` fields.
- [server_modules/runtime_config.py](/Users/mansur/empyralis/server_modules/runtime_config.py) resolves `EMPYRALIS_BILLING_PROVIDER`.
- [server_modules/workspace_bootstrap_service.py](/Users/mansur/empyralis/server_modules/workspace_bootstrap_service.py) publishes canonical entitlement and billing plan state into workspace bootstrap payloads.
- [frontend/lib/workspace/workstation-billing-pane.tsx](/Users/mansur/empyralis/frontend/lib/workspace/workstation-billing-pane.tsx) consumes a normalized summary instead of embedding provider SDK logic.

Why:

- Plans, entitlements, quotas, and billing-backed capability flags are platform concerns.
- Provider-specific checkout or portal URLs are implementation details layered on top of one workspace billing model.

Boundary:

- Billing UI must consume normalized account, subscription, plan, and entitlement state.
- Provider-specific identifiers may appear as metadata.
- Frontend code must not make Stripe-specific assumptions the backend contract has not normalized.

## ADR-006: The Canonical Backend Contract Is Frozen Around One Turn Engine

Decision:
Empyralis has one canonical backend execution contract.
All shells and ingress paths converge on the same turn model and runtime switchboard.

Current ownership model:

- [server.py](/Users/mansur/empyralis/server.py) is the composition root.
- [server_modules/agent_turn.py](/Users/mansur/empyralis/server_modules/agent_turn.py) owns the canonical `AgentTurnRequest` contract and request normalization.
- [server_modules/turn_runtime.py](/Users/mansur/empyralis/server_modules/turn_runtime.py) is the execution switchboard between direct chat and durable execution.
- [server_modules/agent_channel_router.py](/Users/mansur/empyralis/server_modules/agent_channel_router.py) is the canonical external channel ingress adapter.

Why:

- Web, mobile, desktop, and channel shells must not create separate run engines.
- Memory assembly, quota enforcement, usage accounting, and policy enforcement must happen inside canonical backend paths, not in the client.

Frozen rules:

- No user-facing route or connector may invent a parallel turn contract.
- `server.py` must not become a product-logic file.
- Channels normalize ingress and egress only; they do not become separate brains.
- Frontend shells render backend truth and collect intent, but they do not own policy, memory routing, or entitlement decisions.

Source documents:

- [docs/platform/context.md](/Users/mansur/empyralis/docs/platform/context.md)
- [docs/platform/canonical-architecture-contract.md](/Users/mansur/empyralis/docs/platform/canonical-architecture-contract.md)
- [deployment/cloud-runtime-baseline.md](/Users/mansur/empyralis/deployment/cloud-runtime-baseline.md)

## ADR-007: Supported Runtime Shapes Stay Singular

Decision:
The supported deploy shapes are still:

- Render cloud runtime
- Repo-local Tauri desktop shell

Why:

- A singular runtime contract matters for shell consistency, supportability, and debugging.
- Unsupported legacy paths are allowed to exist in the repository as markers, but they are not product truth.

Boundary:

- Future sessions should not treat stale compose flows, dead mobile tab shells, or preview harnesses as canonical product architecture.
