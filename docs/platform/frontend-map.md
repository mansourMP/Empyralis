# Frontend Map

Last verified: 2026-06-08

This is a strict dumb-UI strategy.

This document reflects the current active frontend structure.
It replaces older maps that still referenced deleted roots such as `frontend/components`, `frontend/app/(shell)`, `mobile/src/screens`, and the old mobile `(tabs)` shell.

Mobile stays the daily-use surface with the current mounted Expo tab contract:
- Chat
- Build
- Activity
- Settings

Internal `/apps/...` routes are hidden tool/runtime routes, not a visible mobile inventory tab.

### Channel Shells

- Live personal Agent Computer channels: Telegram, WhatsApp
- Planned personal Agent Computer bridge channels: Signal, iMessage, WeChat
- Live when configured cloud/business channels: Telegram Bot, Slack, Discord
- Email setup: Gmail through Google Workspace, Microsoft 365, or custom SMTP / IMAP
- Planned/partial channels: Web Chat, WhatsApp Business, Webhook, Teams, Matrix

They may do:
- lightweight approvals where supported

They may not become:
- deep admin surfaces
- separate product brains

They still share the same captain identity and run engine truth as full shells.

Mobile and desktop-power must use the same backend semantics.

## Active Frontend Roots

- `frontend/app`: Next.js App Router entrypoints, public pages, account shell routes, and BFF proxy routes
- `frontend/lib`: client-side auth, shell state, UI primitives, workspace surfaces, and server-facing helpers used by the web shell
- `shared/design-system`: canonical cross-platform token source
- `mobile/app`: Expo Router route groups for auth and the native workspace shell
- `mobile/app/(tabs)/_layout.tsx`: current native bottom-tab contract
- `mobile/src`: native runtime, storage, shell model, surfaces, and UI primitives
- `src-tauri`: desktop window shell and sidecar lifecycle for the repo-local desktop target

Roots that are not current truth:

- `frontend/components` does not exist
- `mobile/app/(tabs)` is no longer an active route contract
- `frontend/lib/account-shell` currently exists as an empty directory and should not be treated as active architecture

## Web App Structure

### App Router Root

Current top-level files and route groups under `frontend/app`:

- `layout.tsx`
- `globals.css`
- `not-found.tsx`
- `login/page.tsx`
- `signup/page.tsx`
- `privacy/page.tsx`
- `onboarding/page.tsx`
- `onboarding/OnboardingClient.tsx`
- `preview/page.tsx`
- `preview/PublicWorkstationPreview.tsx`

### Account Shell Route Group

Current authenticated shell lives under `frontend/app/(account)`:

- `layout.tsx`
- `page.tsx`
- `AccountHomeClient.tsx`
- `AccountTenantSwitcher.tsx`
- `settings/account/page.tsx`
- `settings/devices/page.tsx`
- `workspaces/new/page.tsx`
- `workspaces/new/NewWorkspacePageClient.tsx`

Workspace routes live under `frontend/app/(account)/w/[workspaceId]`:

- `page.tsx`
- `layout.tsx`
- `WorkspaceHomeRedirect.tsx`
- `WorkspaceSurfacePage.tsx`
- `chat/page.tsx`
- `workstation/page.tsx`
- `runs/page.tsx`
- `approvals/page.tsx`
- `artifacts/page.tsx`
- `notifications/page.tsx`
- `activity/page.tsx`
- `applications/page.tsx`
- `agents/page.tsx`
- `deployed-agents/page.tsx`
- `integrations/page.tsx`
- `settings/page.tsx`
- `admin/page.tsx`
- `admin/platform/page.tsx`
- `admin/billing/page.tsx`
- `admin/routing/page.tsx`
- `admin/members/page.tsx`
- `admin/policies/page.tsx`
- `trace-preview/page.tsx`

Notes:

- `trace-preview` is still a preview harness route, not canonical product IA.
- The route tree still uses historical route ids; canonical IA is documented in [docs/decisions/architectural-decisions.md](/Users/mansur/empyralis/docs/decisions/architectural-decisions.md).

### Web BFF And Proxy Routes

Current server-side route handlers under `frontend/app/api` and related proxy routes:

- `frontend/app/api/activity/timeline/route.ts`
- `api/[...path]/route.ts`
- `api/activity/timeline/route.ts`
- `api/auth/account-shell/route.ts`
- `api/auth/login/route.ts`
- `api/auth/logout/route.ts`
- `api/auth/me/route.ts`
- `api/auth/refresh/route.ts`
- `api/auth/register/route.ts`
- `api/auth/signup/route.ts`
- `api/channel-pairing/intents/route.ts`
- `api/channel-pairing/links/route.ts`
- `api/channel-pairing/links/[linkId]/revoke/route.ts`
- `api/workspaces/[workspaceId]/channel-operations/route.ts`
- `agent-registry/[...path]/route.ts`
- `agents/[...path]/route.ts`
- `apps/[...path]/route.ts`

## Web Library Structure

Current active directories under `frontend/lib`:

- `account`
  Workspace listing, creation, and account-shell bootstrap client logic
- `auth`
  Auth client helpers and CSRF header helpers
- `server`
  Server-only proxy and control-plane base URL helpers
- `shell`
  Account shell context, payload parsing, storage, and membership models
- `ui`
  Shared web primitives and token consumers such as `primitives.tsx`, `list-detail.tsx`, `data-table.tsx`, `form-controls.tsx`, and `chrome.css`
- `workspace`
  Workspace bootstrap, route manifest logic, boundary providers, service layer, desktop bridge, and all workstation surfaces

Important workspace files:

- `workspace-shell.ts`
- `workspace-boundary.tsx`
- `workspace-services.tsx`
- `workstation-kernel-shell.tsx`
- `workstation-titlebar.tsx`
- `workstation-command-bar.tsx`
- `workstation-shell-frame.tsx`
- `workstation-*.tsx` surface files

## Shared Design System

Current cross-platform design-token root:

- `shared/design-system/tokens.ts`

Role:

- one canonical token source for web, Tauri, and mobile
- web and Tauri consume CSS-variable output
- mobile consumes TypeScript constants
- Radix primitives for interaction, accessibility, layering, focus, and composition
- Framer Motion for motion orchestration and transitions

## Mobile Structure

### Expo Router

Current route groups under `mobile/app`:

- `_layout.tsx`
- `index.tsx`
- `(auth)/login.tsx`
- `(tabs)/_layout.tsx`
- `(tabs)/chats/index.tsx`
- `(tabs)/chats/[id].tsx`
- `(tabs)/kin/index.tsx`
- `(tabs)/kin/[id].tsx`
- `(tabs)/apps/index.tsx`
- `(tabs)/profile/index.tsx`
- `(tabs)/home/index.tsx`
- `(tabs)/inbox/index.tsx`
- top-level operational screens such as `approvals.tsx`, `artifacts.tsx`, `gateway.tsx`, `integrations.tsx`, `memory.tsx`, `notifications.tsx`, and `settings.tsx`

Notes:

- `(tabs)` is the active native shell today.
- The shared route manifest is the cross-shell destination contract, but it must not claim routes that are not mounted.
- The current mobile shell exposes more visible tab labels than the canonical 5-destination IA; this is implementation debt, not a separate product model.

### Mobile Runtime And Surfaces

Current active directories under `mobile/src`:

- `lib`
  Native runtime state, workspace foundation, storage adapters, shell models, and surface factories
- `ui`
  Native token consumers, primitives, state screens, chat UI, and list-detail primitives

Important mobile files:

- `lib/mobile-runtime.js`
- `lib/mobile-workspace-surfaces.js`
- `lib/workspace/workspace-shell.js`
- `lib/workspace/workspace-services.js`
- `ui/tokens.ts`
- `ui/primitives.tsx`
- `ui/list-detail.tsx`

## Desktop / Tauri Structure

Current desktop shell lives under `src-tauri`:

- `src/main.rs`
- `src/lib.rs`

Responsibilities:

- create the frameless desktop window
- expose native window commands
- boot runtime, Next, and worker sidecars for the supported repo-local desktop target
- preserve the same web shell instead of inventing a separate desktop product

## Current Fragmentation To Remember

These paths are real and active even if they are not the final shape:

- `frontend/app/preview`
- `frontend/app/(account)/w/[workspaceId]/trace-preview`
- legacy route ids such as `workstation`, `applications`, and `admin/*`
- `mobile/app/(tabs)`

These are implementation facts, not architectural truth.
Future cleanup should remove or consolidate them without changing the backend contract.

## Backend And BFF Contract Alignment

Backend and BFF contract alignment is proven for:
- `/runs`
- `/approvals`

Current rendered truth:
- the current web shell can render a real cloud-backed assistant answer
- the current web shell now routes serious first-send task requests into the durable run path
- lightweight question-and-answer chat is still allowed to stay on the direct chat path
