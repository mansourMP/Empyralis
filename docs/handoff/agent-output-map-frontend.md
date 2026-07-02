# Agent output: map-frontend

I now have comprehensive data across the entire frontend. Here is the complete file map.

---

## `/Users/mansur/empyralis/frontend/` -- COMPLETE FILE MAP

---

### `frontend/v2/` VERDICT

**Not source code. Purely build artifacts and node_modules.** Top-level files are `.env.production` (45 bytes) and `next-env.d.ts` (247 bytes). Contains `.next/` (Next.js build cache), `out/` (static export with HTML/SVG/txt files for index, chat, setup, settings, oauth, 404), and `node_modules/`. Zero `.tsx`, `.ts`, or `.js` source files. This is a stale Next.js static export from a prior build -- no source code lives here.

---

### `frontend/shared/` VERDICT

**Not a symlink.** A real directory at `/Users/mansur/empyralis/frontend/shared/` containing the same files as `/Users/mansur/empyralis/shared/`. Contains two subdirectories (`api-contract/`, `design-system/`) and three standalone files (`mini-app-sdk.js`, `nav-manifest.js`, `nav-manifest.ts`). These are duplicates/copies of `../shared/`, not symlinks.

---

## `frontend/app/` -- ALL PAGES, LAYOUTS, ROUTES, CLIENT COMPONENTS

### Root-level files

| Path | Purpose | Usage Status |
|---|---|---|
| `app/layout.tsx` | Root layout -- imports globals.css, custom fonts, sets metadata, wraps children in `AppTheme`. The HTML shell for the entire Next.js app. | Active |
| `app/page.tsx` | Root page -- renders `LandingClient` with platform branding. The public marketing/landing page. | Active |
| `app/landing-client.tsx` | Client component for landing page -- hero, feature sections, signup CTAs. | Active |
| `app/landing.css` | Styles for the landing page. | Active |
| `app/globals.css` | Global CSS -- design tokens, reset, layout primitives, all component class names (`app-chat-*`, `app-shell-*`, `app-agent-*`, etc.). Massive stylesheet. | Active |
| `app/not-found.tsx` | Custom 404 page. | Active |
| `app/next-env.d.ts` | Next.js TypeScript environment type declarations. | Auto-generated |

### Auth

| Path | Purpose | Usage Status |
|---|---|---|
| `app/login/page.tsx` | Login page route. | Active |
| `app/signup/page.tsx` | Signup page route. | Active |
| `app/continue/page.tsx` | Session continuation/restore page with custom CSS. | Active |
| `app/auth/complete/page.tsx` | OAuth callback completion page. | Active |
| `app/invite/[code]/page.tsx` | Invite-code based signup/join route. | Active |
| `app/onboarding/page.tsx` | Onboarding page -- renders `OnboardingClient`. | Active |
| `app/onboarding/OnboardingClient.tsx` | Client-side onboarding wizard component. | Active |

### Account shell (route group `(account)`)

| Path | Purpose | Usage Status |
|---|---|---|
| `app/(account)/layout.tsx` | Account shell layout -- loads account bootstrap, sets up `WorkspaceBoundary` context, renders sidebar navigation, workspace switcher, recovery actions. The authenticated shell for all workspace pages. | Active |
| `app/(account)/page.tsx` | Account home -- redirects to first workspace or shows `AccountHomeClient`. | Active |
| `app/(account)/AccountHomeClient.tsx` | Client component for account home when user has no workspaces. | Active |
| `app/(account)/AccountTenantSwitcher.tsx` | Tenant/workspace switcher component for multi-tenant accounts. | Active |
| `app/(account)/ShellRecoveryActions.tsx` | Recovery action buttons when shell fails to load (retry, sign out, etc.). | Active |
| `app/(account)/settings/account/page.tsx` | Account-wide settings page. | Active |
| `app/(account)/settings/resolve-settings-route.ts` | Utility to resolve settings route from account context. | Active |
| `app/(account)/workspaces/new/page.tsx` | Create new workspace page. | Active |
| `app/(account)/workspaces/new/NewWorkspacePageClient.tsx` | Client component for new workspace creation form. | Active |

### Workspace routes (`w/[workspaceId]/`)

| Path | Purpose | Usage Status |
|---|---|---|
| `w/[workspaceId]/layout.tsx` | Workspace shell layout -- renders `WorkspaceSurfacePage` as the inner shell (titlebar, sidebar, split workbench). | Active |
| `w/[workspaceId]/page.tsx` | Workspace home -- renders `WorkspaceHomeRedirect`. | Active |
| `w/[workspaceId]/WorkspaceHomeRedirect.tsx` | Redirects based on shell profile's home route (typically `chat`). | Active |
| `w/[workspaceId]/WorkspaceSurfacePage.tsx` | **Core shell page** -- `'use client'`. Renders `WorkstationShellFrame` with `WorkstationSplitWorkbench` (sidebar nav + content viewport). Orchestrates the entire workspace UI: titlebar, navigation sidebar, kernel content pane. Contains the route-matching logic that maps URL segments to kernel panes. Uses `WorkstationSurfaceViewport`, `WorkstationKernelShell`, and all workstation-* pane components. | **Active, recently modified** |
| `w/[workspaceId]/chat/page.tsx` | Chat route -- thin page that delegates to `WorkstationChatPane`. | Active |
| `w/[workspaceId]/memory/page.tsx` | Memory management route. | Active |
| `w/[workspaceId]/activity/page.tsx` | Activity/audit timeline route. | Active |
| `w/[workspaceId]/applications/page.tsx` | Deployed applications list route. | Active |
| `w/[workspaceId]/applications/[appId]/page.tsx` | Single deployed application detail route. | Active |
| `w/[workspaceId]/artifacts/page.tsx` | Artifacts gallery route. | Active |
| `w/[workspaceId]/channels/page.tsx` | Channel pairing management route. | Active |
| `w/[workspaceId]/deploy/page.tsx` | Agent deployment route. | Active |
| `w/[workspaceId]/gateway/page.tsx` | Gateway/computer connection route. | Active |
| `w/[workspaceId]/gateway-activity/page.tsx` | Gateway activity monitoring route. | Active |
| `w/[workspaceId]/hardware/page.tsx` | Hardware/AI runtime management route. | Active |
| `w/[workspaceId]/inbox/page.tsx` | Agent inbox route. | Active |
| `w/[workspaceId]/integrations/page.tsx` | Integrations management route. | Active |
| `w/[workspaceId]/marketplace/page.tsx` | App marketplace route. | Active |
| `w/[workspaceId]/notifications/page.tsx` | Notifications route. | Active |
| `w/[workspaceId]/sage/page.tsx` | Sage profile/setup route. | Active |
| `w/[workspaceId]/settings/page.tsx` | Workspace settings route. | Active |
| `w/[workspaceId]/studio/page.tsx` | Studio route (legacy name, may redirect). | Active (deprecated route) |
| `w/[workspaceId]/studio-integrations/page.tsx` | Studio integrations route. | Active (deprecated route) |
| `w/[workspaceId]/tasks/page.tsx` | Tasks route. | Active |

### API routes (Next.js Route Handlers)

All are `route.ts` files using Next.js App Router API routes. They proxy or implement backend APIs.

| Path | Purpose | Usage Status |
|---|---|---|
| `app/api/[...path]/route.ts` | Catch-all API proxy -- forwards requests to the control plane backend. | Active |
| `app/api/auth/google/route.ts` | Google OAuth initiation. | Active |
| `app/api/auth/google/callback/route.ts` | Google OAuth callback handler. | Active |
| `app/api/auth/login/route.ts` | Login endpoint. | Active |
| `app/api/auth/logout/route.ts` | Logout endpoint. | Active |
| `app/api/auth/me/route.ts` | Current user info. | Active |
| `app/api/auth/providers/route.ts` | List available auth providers. | Active |
| `app/api/auth/refresh/route.ts` | Token refresh. | Active |
| `app/api/auth/register/route.ts` | Registration endpoint. | Active |
| `app/api/auth/signup/route.ts` | Signup endpoint. | Active |
| `app/api/auth/account-shell/route.ts` | Account shell bootstrap data. | Active |
| `app/api/activity/timeline/route.ts` | Activity timeline endpoint. | Active |
| `app/api/channel-pairing/intents/route.ts` | Create channel pairing intents. | Active |
| `app/api/channel-pairing/links/route.ts` | List channel links. | Active |
| `app/api/channel-pairing/links/[linkId]/revoke/route.ts` | Revoke a channel link. | Active |
| `app/api/hardware/bootstrap/install.sh/route.ts` | Serves the Agent Computer install script as text. | Active |
| `app/api/workspaces/[workspaceId]/channel-operations/route.ts` | Workspace-specific channel operations. | Active |
| `app/agents/[...path]/route.ts` | Agent registry API proxy. | Active |
| `app/agent-registry/[...path]/route.ts` | Agent registry API proxy (alternate path). | Active |
| `app/apps/[...path]/route.ts` | Apps registry API proxy. | Active |
| `app/healthz/route.ts` | Health check endpoint. | Active |
| `app/install/agent-computer.sh/route.ts` | Serves or redirects for Agent Computer installer. | Active |

### Public pages

| Path | Purpose | Usage Status |
|---|---|---|
| `app/preview/page.tsx` | Public agent preview page. | Active |
| `app/preview/PublicAgentPreviewClient.tsx` | Client component for public agent preview. | Active |
| `app/privacy/page.tsx` | Privacy policy page. | Active |
| `app/terms/page.tsx` | Terms of service page. | Active |

### Static assets

| Path | Purpose |
|---|---|
| `app/fonts/DMSans-{Bold,Medium,Regular}.ttf` | DM Sans font files (3 weights). |
| `app/fonts/Fraunces-{Bold,Regular}.ttf` | Fraunces font files (2 weights) -- used for headings. |

---

## `frontend/lib/workspace/` -- ALL FILES

### codex-chat/ (Chat projection pipeline -- 5 files)

This is the **core chat event projection engine** that transforms raw transport events (SSE stream, trace replay, metadata) into typed UI cells for rendering.

| Path | Purpose | Usage Status |
|---|---|---|
| `codex-chat/cells.ts` | **Type definitions** for the entire chat projection system. Defines `TimelineProjectionEvent` (the 6 raw input event shapes: user/step/trace/typed/chunk/final), `CodexChatEvent` (the 19 normalized intermediate event types: user, reasoning_delta, assistant_delta, assistant_final, tool_started, tool_result, exec_started, exec_delta, exec_result, web_search_started, web_search_result, file_change, screenshot_captured, artifact_created, approval_request, status, agent_activity, error), `CodexTranscriptCell` (the 14 cell types rendered in UI: user/assistant/reasoning_summary/exec/tool/web_search/file_change/screenshot/artifact/approval_request/status/agent_activity/error/execution_trace), and legacy `TimelineRow`. Pure types, zero logic. | Active |
| `codex-chat/event-projector.ts` | **`projectRawEventToCodexEvents()`** -- 1189 lines. The biggest projection function. Takes a single raw `TimelineProjectionEvent` (user/step/trace/typed/chunk/final) and converts it into one or more `CodexChatEvent[]`. Handles: step events (thinking, shell, file, search, browser, agent_activity), trace events (tool.started/result, shell.*, file.*, runtime.*, gateway.*, hardware.*, browser.*, delegation.*, search.*, screenshot.*, artifact.*, approval.*, telegram, whatsapp), typed stream events (thinking, tool_call, tool_result, bash_output, response), and chunk/final/user events. Contains the entire event-type-to-cell mapping logic including tool name display normalization, shell command extraction, file path resolution, output tail truncation, agent-computer hardware state labeling, and approval status normalization. Also exports `codexChatEventInternals` (readObject/readNumber/readString helpers) for testing. | **Active, recently modified** |
| `codex-chat/timeline-reducer.ts` | **`projectCodexTimeline(events)`** -- 848 lines. Takes an array of `TimelineProjectionEvent[]`, calls `projectRawEventToCodexEvents` on each, and applies the resulting `CodexChatEvent[]` through `applyCodexEvent()` to build a `CodexTimelineProjection`. This is the reducer/state machine that accumulates cells, tracks streaming state, merges delta events into running cells, dims completed cells, and computes `agentActivityState`. Also exports `projectTimeline()` for the legacy row format. Exports `TimelineProjectionEvent` and `TimelineRow` types. | Active |
| `codex-chat/message-adapter.ts` | **`workstationMessageToCodexCell(message)`** -- 165 lines. Converts a persisted `WorkstationChatMessageRecord` (from thread history) into a `CodexTranscriptCell` based on its `metadata.display_kind` field. Maps: `thinking_row` -> reasoning_summary, `tool_row`/`activity_step` -> tool, `file_row` -> file_change, `search_row` -> web_search, `provider_error` -> error, `approval_request` -> approval_request, `exec_row` -> exec, user role -> user cell, default -> assistant cell. Also extracts effective provider/model from metadata. | Active |
| `codex-chat/cell-components.tsx` | **All UI render components for `CodexTranscriptCell` types** -- 1460 lines. `'use client'`. The visual rendering layer. Contains `CodexChatCell` (the main dispatcher that renders any cell type), plus individual components: `UserCell`, `AssistantCell` (with animated text streaming), `ExecutionTraceCell` (groups trace activities with a collapsible summary), `ExecCell` (expandable terminal card with stdout/stderr/exit code/duration), `ToolCallCell`, `WebSearchCell`, `FileChangeCell`, `ScreenshotCell`, `ArtifactCell`, `ApprovalCell` (with allow-once/allow-session/deny buttons), `StatusCell`, `AgentActivityCell`, `ErrorCell`, `ReasoningSummaryCell`, and helper components (`MarkdownMessage`, `CodeBlock`, `SystemInlineRow`, `ToolTraceCard`, `TerminalOutputBlock`, `ThoughtProcessPanel`). Also contains text sanitization logic (`isLeakedMachineResultJson`, `visibleThinkingContent`). | Active |

### Chat surface (the main Sage chat pane)

| Path | Purpose | Usage Status |
|---|---|---|
| `workstation-chat-pane.tsx` | **The main Sage chat UI** -- ~3854 lines. `'use client'`. The single largest component in the codebase. Orchestrates: thread management (load, switch, create), message sending (streaming SSE turns with abort), provider catalog/model selection (Sage model picker canvas with Empyralis/Anthropic/OpenAI/Codex/Google/DeepSeek/Ollama provider panels), reasoning effort toggle, Agent Computer hardware runtime selection, Sage memory editor (CRUD for memory entries), Sage profile bootstrap (first-run setup questions), billing/credit summary, tool policy refresh, gateway readiness doctor, pre-run cost estimates, slash command palette (`/status`, `/usage`, `/tools`, `/runtime`, `/doctor`), workspace command palette (Cmd+K), composer with file attachments and voice transcription, transcript scrolling with auto-stick, chat transcript rendering via `useWorkstationTimelineProjection`, and intervention handling (connector setup, provider gate). Uses ~190 imports from its model/hooks sub-files. | Active |
| `chat-message.tsx` | **Legacy chat message renderer** -- 376 lines. `'use client'`. Renders individual `WorkstationChatMessageRecord` items. Handles display kinds: `thinking_row` (collapsible thought process), `tool_row`, `file_row`, `search_row`, `activity_step`, `provider_error`. Also renders user/assistant messages with metadata (provider label, route label, timestamp). The legacy path; `codex-chat/cell-components.tsx` is the newer Codex cell renderer. | Active (legacy+current dual path) |
| `chat-composer.tsx` | **Chat input composer** -- 972 lines. `'use client'`. Full-featured textarea with: file drag-and-drop, voice recording (MediaRecorder API), slash command palette with keyboard navigation, action/capability menu with submenus, model control slot, reasoning effort toggle, attachment chips. Handles submit on Enter, stop on Escape. The primary input mechanism for the chat pane. | Active |
| `workstation-chat-pane-hooks.ts` | **State hooks for the chat pane** -- defines all TypeScript interfaces and React hook factories for: `CanonicalChatThreadState`, `CanonicalRunSummary`, `LiveTraceState`, `LiveActivityStepState`, `SageMemorySnapshot`, `SageProfileSnapshot`, `RecentThreadSummary`, `SendFailureNotice`, `ChatModelOption`, `ChatReasoningEffort`, `ChatAutonomyMode`, `ChatMachineTrust`, etc. Plus hooks: `useChatThreadState`, `useChatComposerState`, `useChatRunState`, `useChatMemoryProfileState`, `useChatStreamRunState`, `useChatProviderModelState`, `useChatUiPanelsState`, `useChatMemoryEditorState`, and state management helpers. Very large file -- handles all chat state. | Active |
| `workstation-chat-pane-model.ts` | **Pure functions and constants for chat logic** -- massive utility file. Contains: constants (`PRIMARY_THREAD_ID`, `CHAT_READ_TIMEOUT_MS`, `SAGE_SETUP_TIMEOUT_MS`, `CHAT_THINKING_RECOVERY_MS`, query keys, `EMPYRALIS_TIER_SET`, `VALID_REASONING_LEVELS`), and hundreds of exported pure functions: `readString`, `readNumber`, `readObject`, message normalization, provider matching, model selection logic, tool policy normalization, thread summarization, intervention detection, provider gate checks, step event normalization, timeline item normalization, runtime card summarization, trust zone resolution, permission policy building, pre-run cost estimation, context window formatting, reasoning label conversion, Sage setup failure humanization, and many more. The "business logic" layer for the entire chat pane. | Active |
| `workstation-chat-memory-loaders.ts` | **Async loaders for memory/profile snapshots** -- wraps API calls with caching, timeout, and fallback logic. `loadChatMemorySnapshot()` and `loadChatProfileSnapshot()`. | Active |
| `workstation-chat-thread-events.ts` | **Custom DOM events for cross-component chat communication** -- emits and subscribes to `chat-thread-selected`, `chat-new-thread-requested`, `chat-history-invalidated` events. Allows titlebar/notifications to trigger chat thread navigation. | Active |
| `workstation-provider-events.ts` | **Custom DOM events for provider changes** -- emits/subscribes to `workspace-provider-changed` events so the chat pane refreshes when providers are updated elsewhere. | Active |

### Chat timeline projection (bridge layer)

| Path | Purpose | Usage Status |
|---|---|---|
| `workstation-chat-timeline-projection.ts` | **`useWorkstationTimelineProjection()`** -- 552 lines. `'use client'`. The React hook that bridges the raw event pipeline to the UI. Combines: thread messages (via `workstationMessageToCodexCell`), live timeline events (via `projectCodexTimeline`), legacy trace replay events (via `safeTimelineEventsFromTraceReplay`), and approval/pending/synthetic/projected messages into a unified `visibleTranscriptCells` array. Contains: `safeTimelineEventsFromTraceReplay()` (whitelist-based event filtering with `SAFE_TRACE_DATA_KEYS`, `SAFE_TRACE_EVENT_EXACT`, `SAFE_TRACE_EVENT_PREFIXES`), `replayProofCellsForMessage()` (reconstructs proof cells from stored transcript_events metadata for historical assistant messages), `groupExecutionTraceCells()` (groups trace activities into `execution_trace` cells), and `isProofCell()` / `isReplayProofCell()` guards. The sanitization functions (`sanitizeSafeRecord`, `safeScalar`, `compactOutputTail`, `looksInternalText`) filter out internal/debug data before it reaches the UI. Contains the full whitelist of safe trace event types and data keys. | **Active, recently modified** |
| `transcript-event-contract.ts` | **Sanitization contract for stored transcript events** -- 438 lines. `'use client'`. Defines `TRANSCRIPT_EVENT_SCHEMA_VERSION` (1), `TRANSCRIPT_EVENT_LIMIT` (200), and the whitelists for what trace/step event types and data keys are safe to persist in message metadata. `transcriptProjectionEventsFromMetadata()` extracts and sanitizes `TimelineProjectionEvent[]` from `metadata.transcript_events`. Contains extensive `BLOCKED_KEYS` (args, input, output, parameters, prompt, provider, model, trace_id, system_prompt, etc.) and `SAFE_TRACE_DATA_KEYS` / `SAFE_TRACE_METADATA_KEYS`. The "privacy filter" for event persistence. | Active |

### Workspace infrastructure

| Path | Purpose | Usage Status |
|---|---|---|
| `workspace-bootstrap.ts` | **Type definitions for workspace bootstrap payload** -- `WorkspaceBootstrapAccount`, `WorkspaceBootstrapWorkspace`, `WorkspaceBootstrapMembership`, `WorkspaceBootstrapEntitlements`, `WorkspaceBootstrapRuntimeTarget`, `WorkspaceBootstrapExecutionMode`, `WorkspaceBootstrapPayload`, `WorkspaceBootstrapRuntimeSummary`. Also exports `createWorkstationKernelKey()`. | Active |
| `workspace-shell.ts` | **Route manifest and shell profile logic** -- imports from `../../shared/nav-manifest`. Defines `SHELL_PROFILE_DEFINITIONS`, `deriveShellProfile()`, `buildRouteManifest()`, `hasWorkspaceCapability()`, `isWorkspaceRouteId()`. The navigation/routing configuration layer. | Active |
| `workspace-boundary.tsx` | **React context provider for workspace scope** -- creates `WorkspaceBoundaryContext` with workspace ID, kernel key, shell profile, route manifest, bootstrap data, and capability checks. Wraps every workspace page. | Active |
| `workspace-services.tsx` | **Dependency injection container** -- creates and provides `workstationClient` (API client), `WorkstationStreamManager` (SSE stream management), and a `queryClient` (in-memory cache with invalidation). Also provides React hooks: `useWorkspaceServices()`, `useWorkstationStreamSelector()`, `useWorkstationActivityVersion()`, `useWorkstationNotifications()`. The "service locator" for the entire workstation UI. | Active |
| `workstation-client.ts` | **API client factory** -- `createWorkstationClient()`. Provides all API methods called by the UI: `sendTurn()`, `streamTurn()`, `getThread()`, `listThreads()`, `listRuns()`, `listActivityTimeline()`, `listProviderCatalog()`, `listProviderProfiles()`, `upsertProviderProfile()`, `getSageProfile()`, `answerSageProfileBootstrap()`, `getSageMemory()`, `createSageMemoryEntry()`, `updateSageMemoryEntry()`, `deleteSageMemoryEntry()`, `getSageToolPolicy()`, `getWorkspaceAiRoute()`, `updateWorkspaceAiRouteDefault()`, `getBillingSummary()`, `getGatewayRegistrations()`, `getGatewayDoctor()`, `listApprovals()`, `getTraceReplay()`, `openSageTurnStream()`, `openNotificationsStream()`, `openChannelEventsStream()`, `artifactDownloadUrl()`, `artifactFileUrl()`, `killDeployedAgentRuntimeSession()`, and more. Constructs fetch requests with auth headers and the control plane base URL. | Active |
| `workstation-stream-manager.ts` | **SSE stream manager** -- manages two persistent EventSource connections (notifications + activity/channel events) with reconnect logic, cursor tracking, and state subscription. Exposes `useWorkstationStreamSelector` hook. | Active |
| `workspace-json-request.ts` | **Generic JSON request helper** -- `requestWorkspaceJson()` with auth headers, error normalization, and workspace-scoped URL construction. | Active |
| `server-workspace-bootstrap.ts` | **Server-side bootstrap loader** -- `'server-only'`. Loads workspace bootstrap payload on the server (for SSR/initial page load). | Active |

### Workstation UI shell components

| Path | Purpose | Usage Status |
|---|---|---|
| `workstation-shell-frame.tsx` | **Shell frame** -- renders `WorkstationShellFrame` (the outer frame with kernel host), `WorkstationSurfaceViewport` (scrollable content area, locked for chat/memory), and `WorkstationRouteFallback` (unavailable route display with navigation link). | Active |
| `workstation-split-workbench.tsx` | **Split workbench layout** -- resizable sidebar + main content area with drag handle, localStorage persistence, and min/max width clamping. Used as the main workspace layout container. | Active |
| `workstation-titlebar.tsx` | **Workspace titlebar** with branding, navigation breadcrumbs, account menu, notifications bell, and action slots. | Active |
| `workstation-surface-primitives.tsx` | **Thin wrapper components** around `@/lib/ui/primitives` -- `WorkstationSurfaceRoot`, `WorkstationSurfaceCard`, `WorkstationSurfaceNotice`, `WorkstationSurfaceList`, `WorkstationSurfaceListItem`, `WorkstationSurfaceStat`, `WorkstationSurfaceStatGrid`. Adds `data-workstation-surface` attributes. | Active |
| `workstation-kernel-shell.tsx` | **Kernel shell** -- the inner content pane that renders based on the current route. Maps `surface` (route slug) to the appropriate kernel pane component (ChatPane, RunsPane, SettingsPane, etc.). | Active |

### Feature panes (workstation-*.tsx)

| Path | Purpose | Usage Status |
|---|---|---|
| `workstation-runs-pane.tsx` | **Activity/Audit pane** -- 1056 lines. `'use client'`. Owner/admin audit surface. Aggregates proof items from 4 sources: threads (chat history), activity timeline, runs, and approvals. Renders filterable list with type categories (all/chat/tools/approvals/channels/computers/providers/files/outcomes), trace ID search, admin audit detail toggle (provider/model/tokens/duration/ledger), computer proof inspection (modal with screenshot preview and runtime session details), and "stop computer" action. Uses in-memory caching (`threadsPaneCache`, `activityPaneCache`), localStorage thread persistence, and relative time formatting. **Designed as the "heavier" owner audit surface -- inline chat proof is the "normal" transparency path.** | **Active, recently modified** |
| `workstation-chat-pane.tsx` | The main chat pane (described above). | Active |
| `workstation-deployed-agents-pane.tsx` | Deployed agents management -- list/create/configure deployed agents. | Active |
| `workstation-deployed-agent-analytics-pane.tsx` | Per-agent analytics view. | Active |
| `workstation-deployed-agent-test-turn-pane.tsx` | Test turn panel for deployed agents (send test messages). | Active |
| `workstation-hardware-pane.tsx` | Hardware/AI runtime management pane -- Agent Computer status, gateway registrations. | Active |
| `workstation-hardware-status.tsx` | Hardware status display component. | Active |
| `workstation-settings-pane.tsx` | Workspace settings pane. | Active |
| `workstation-billing-pane.tsx` | Billing/credits/usage pane. | Active |
| `workstation-notifications-pane.tsx` | Notifications pane. | Active |
| `workstation-activity-pane.tsx` | Activity feed (simpler than runs-pane, more timeline-oriented). | Active |
| `workstation-artifacts-pane.tsx` | Artifacts gallery pane. | Active |
| `workstation-gateway-operator-pane.tsx` | Gateway/computer operator controls. | Active |
| `workstation-platform-analytics-pane.tsx` | Platform-wide analytics (admin only). | Active |
| `workstation-sage-heartbeat-pane.tsx` | Sage health/heartbeat monitoring pane. | Active |
| `workstation-sage-tools-pane.tsx` | Sage tool policy management pane. | Active |
| `workstation-sage-connectors-pane.tsx` | Sage connector management pane. | Active |
| `workstation-sage-profile-pane.tsx` | Sage profile/identity settings pane. | Active |
| `workstation-studio-integrations-pane.tsx` | Studio integrations pane (legacy naming). | Active |
| `workspace-channel-pairing-surface.tsx` | Channel pairing management surface -- Telegram/WhatsApp link creation, revocation, intent code display. | Active |
| `workspace-setup-form.tsx` | Workspace creation/setup form component. | Active |
| `hosted-mini-app-surface.tsx` | Iframe-based mini-app container that renders hosted apps inside the workspace. | Active |
| `hosted-mini-apps-pane.tsx` | Mini-apps management pane. | Active |
| `hosted-mini-apps.module.css` | CSS module for mini-apps pane. | Active |
| `cloud-vps-setup-panel.tsx` | Cloud VPS setup wizard panel. | Active |
| `connector-setup-modal-shell.tsx` | Modal shell for connector setup flows. | Active |
| `desktop-startup-screen.tsx` | Desktop app startup/loading screen. | Active |
| `desktop-window-controls.tsx` | Desktop window control buttons (close/minimize/maximize). | Active |
| `data-pane-error.tsx` | Error display component for data panes with retry button. | Active |
| `transparency-timeline.tsx` | Transparency timeline component for chat -- shows proof/audit trail inline. | Active |
| `workstation-desktop-bridge.ts` | Bridge to Electron/desktop APIs (platform detection, local companion status). | Active |
| `workstation-desktop-status.tsx` | Desktop companion status indicator. | Active |
| `workstation-app-update-action.tsx` | App update notification/action component. | Active |

### Deployed agents sub-module

| Path | Purpose | Usage Status |
|---|---|---|
| `deployed-agents/components.tsx` | Shared UI components for deployed agent views. | Active |
| `deployed-agents/detail-view.tsx` | Deployed agent detail view (main). | Active |
| `deployed-agents/roster-sidebar.tsx` | Agent roster sidebar for navigation between agents. | Active |
| `deployed-agents/wizard.tsx` | Agent creation/deployment wizard. | Active |
| `deployed-agents/inbox-view.tsx` | Agent inbox view. | Active |
| `deployed-agents/playground-panel.tsx` | Agent playground/test panel. | Active |
| `deployed-agents/ai-settings.tsx` | AI model/routing settings for an agent. | Active |
| `deployed-agents/action-settings.tsx` | Agent action/capability settings. | Active |
| `deployed-agents/integration-settings.tsx` | Agent integration/connector settings. | Active |
| `deployed-agents/agent-computer-detail.tsx` | Agent Computer detail view. | Active |
| `deployed-agents/external-agent-detail.tsx` | External agent detail view. | Active |
| `deployed-agents/external-agent-provider-badges.ts` | Provider badge icons for external agents. | Active |
| `deployed-agents/constants.ts` | Constants for deployed agent types and defaults. | Active |
| `deployed-agents/types.ts` | TypeScript types for deployed agents. | Active |
| `deployed-agents/utils.ts` | Utility functions for deployed agents. | Active |

### Sage chat sub-module

| Path | Purpose | Usage Status |
|---|---|---|
| `sage-chat/types.ts` | Re-exports all chat pane types (from hooks + model). | Active |
| `sage-chat/constants.ts` | Re-exports all constants (query keys, timeouts, tier IDs, labels). | Active |
| `sage-chat/composer.tsx` | Sage-specific composer wrapper (may wrap ChatComposer with Sage-specific config). | Active |
| `sage-chat/transcript.tsx` | Sage-specific transcript renderer. | Active |
| `sage-chat/hooks.ts` | Sage-specific hooks. | Active |
| `sage-chat/utils.ts` | Sage-specific utility functions. | Active |

### Standalone workspace utilities

| Path | Purpose | Usage Status |
|---|---|---|
| `sage-command-catalog.ts` | **Slash command definitions** -- `SAGE_COMMAND_CATALOG` (5 commands: status, usage, tools, runtime, doctor with Lucide icons) and `SAGE_WORKSPACE_COMMAND_CATALOG` (workspace navigation commands). `resolveSageCommandBySlash()` maps a slash-prefixed text to its command. | Active |
| `model-capabilities.ts` | **Hardcoded model capability database** -- reasoning levels and context windows for: gpt-4o, gpt-4o-mini, o3, claude-opus-4-5, claude-sonnet-4-5, claude-haiku-4-5. Also provides `MODEL_CAPABILITY_ALIASES` for legacy model IDs. `getModelCapabilities()` and `resolveModelContextWindow()`. | Active |
| `platform-brand.ts` | Platform brand utilities -- `isPlatformBillingSource()` identifies Empyralis-managed billing. | Active |
| `platform-brand.test.ts` | Unit tests for platform brand utilities. | Active |
| `application-surface-tabs.ts` | Tab definitions for the application surface. | Active |

---

## `frontend/lib/ui/` -- DESIGN SYSTEM PRIMITIVES

| Path | Purpose | Usage Status |
|---|---|---|
| `primitives.tsx` | **Core design system components** -- `AppButton`, `AppNotice`, `AppShinyText`, `AppSurfaceRoot`, `AppSurfaceCard`, `AppSurfaceList`, `AppSurfaceListItem`, `AppSurfaceStat`, `AppSurfaceStatGrid`, `joinClassNames`. The building blocks for all workstation panes. | Active |
| `form-controls.tsx` | Form components -- `FormField`, `FormGrid`, `FormInput`, `FormTextarea`, `FormSection`, `FormReadout`. | Active |
| `command-sheet.tsx` | Modal sheet component used for computer proof inspection and other slide-over panels. | Active |
| `confirm-dialog.tsx` | Confirmation dialog component. | Active |
| `modal.tsx` | Generic modal components -- `ModalSection`. | Active |
| `data-table.tsx` | Table components -- `DataTable`, `DataTableRow`, `DataTableCell`, `DataTableHeader`, `DataTableHeaderCell`, `DataBadge`. | Active |
| `list-detail.tsx` | Master-detail layout components -- `ListDetailShell`, `ListDetailPanel`, `ListDetailColumns`. | Active |
| `empty-panel.tsx` | Empty state panel with title/body. | Active |
| `skeleton-block.tsx` | Loading skeleton placeholder. | Active |
| `state-banner.tsx` | Status banner component. | Active |
| `scroll-region.tsx` | Scrollable region wrapper with optional custom scrollbar styling. | Active |
| `platform-notification.tsx` | Toast/notification component (`PlatformNotification`). | Active |
| `motion.tsx` | Animation utility components (likely wraps framer-motion). | Active |
| `icons.tsx` | Custom icon components or icon utilities. | Active |
| `app-theme.tsx` | Theme provider -- likely sets CSS custom properties for light/dark mode. | Active |
| `tokens.ts` | Design tokens -- colors, spacing, typography, etc. as JS/TS constants. | Active |
| `chrome.css` | Chrome/browser-specific CSS resets or styles. | Active |
| `use-animated-text.ts` | Custom hook for word-by-word typewriter text animation, used by `AssistantCell` for streaming responses. | Active |

---

## `frontend/lib/` -- OTHER MODULES

### server/

| Path | Purpose | Usage Status |
|---|---|---|
| `server/control-plane-base-url.ts` | **`'server-only'`** -- resolves the backend API base URL from environment variables (`EMPYRALIS_API_URL`, `ORION_API_URL`, `NEXT_PUBLIC_ORION_API_URL`, `NEXT_PUBLIC_API_URL`). Defaults to `http://127.0.0.1:8001` in development. Validates HTTPS in staging/production. Exports `controlPlaneBaseUrl()` and `resolveControlPlaneBaseUrl()`. The single source of truth for backend URL resolution on the server side. | **Active, recently modified** |
| `server/control-plane-proxy.ts` | Proxies requests from Next.js API routes to the control plane backend. | Active |
| `server/google-oauth.ts` | Server-side Google OAuth configuration/helpers. | Active |
| `server/load-account-shell-session.ts` | Server-side account shell session loader -- resolves user session from cookies/tokens for SSR. | Active |

### auth/

| Path | Purpose | Usage Status |
|---|---|---|
| `auth/auth-client.ts` | Client-side auth utilities -- login, logout, token management, session checks. | Active |
| `auth/auth-provider-icons.tsx` | Provider icon components (Google, etc.). | Active |
| `auth/auth-timeouts.ts` | Auth-related timeout constants. | Active |
| `auth/csrf.ts` | CSRF token generation and cookie management, `buildCookieAuthHeaders()`. | Active |
| `auth/first-launch-panel.tsx` | First-launch onboarding panel component. | Active |

### shell/

| Path | Purpose | Usage Status |
|---|---|---|
| `shell/account-shell-context.tsx` | React context for account shell (user, tenants, workspaces). | Active |
| `shell/account-shell-payload.ts` | Type definitions for account shell API payload. | Active |
| `shell/account-shell-storage.ts` | Client-side storage for account shell state (localStorage/sessionStorage). | Active |
| `shell/account-shell-store.ts` | State management store for account shell. | Active |
| `shell/workspace-membership-model.ts` | Workspace membership/permission model and checks. | Active |

### Other lib modules

| Path | Purpose | Usage Status |
|---|---|---|
| `account/account-workspaces-client.ts` | Client for fetching/managing workspace list at account level. | Active |
| `discovery/discovery-pane.tsx` | Discovery/app marketplace pane. | Active |
| `discovery/discovery-pane.module.css` | Styles for discovery pane. | Active |
| `marketplace/marketplace-pane.tsx` | App marketplace pane (alternative to discovery). | Active |

---

## `frontend/shared/` -- SHARED MODULES (copy of `../shared/`)

Not a symlink. Contains:

| Path | Purpose | Usage Status |
|---|---|---|
| `shared/mini-app-sdk.js` | Mini-app SDK for iframe-hosted apps -- provides the API that mini-apps call (workspace context, theme, navigation). | Active |
| `shared/nav-manifest.ts` | **Navigation manifest** -- TypeScript source. Defines `WORKSPACE_ROUTE_DEFINITIONS`, `WORKSPACE_NAV_DESTINATIONS`, route ID types, `WorkspaceRouteId`, `WorkspaceShellProfileId`. Used by `workspace-shell.ts` to build route manifests. | Active |
| `shared/nav-manifest.js` | Compiled JS version of nav-manifest (likely for the mini-app SDK). | Active (generated) |
| `shared/api-contract/index.ts` | API contract types -- shared between frontend and backend for type-safe API communication. | Active |
| `shared/api-contract/client.ts` | API client contract -- defines the typed API surface. | Active |
| `shared/api-contract/model-tier-contract.ts` | Model tier contract -- defines tier structures (light/pro/max). | Active |
| `shared/design-system/tokens.ts` | Shared design tokens -- colors, spacing, typography usable by both frontend and mini-apps. | Active |

---

## `frontend/` -- TOP-LEVEL CONFIG FILES

| Path | Purpose | Usage Status |
|---|---|---|
| `next.config.ts` | Next.js 16 configuration -- serverExternalPackages, experimental features, rewrites, headers. | **Active, recently modified** |
| `tsconfig.json` | TypeScript configuration with path aliases (`@/lib/*` maps to `./lib/*`). | Active |
| `package.json` | Dependencies and scripts -- Next.js 16, React 19, lucide-react, etc. | Active |
| `proxy.ts` | Custom dev proxy/middleware -- likely processes API requests before forwarding. | Active |
| `Dockerfile` | Docker build configuration for the frontend. | Active |
| `vercel.json` | Vercel deployment configuration. | Active |
| `playwright.config.ts` | Playwright E2E test configuration. | Active |
| `.env.example` / `.env.local` / `.env.vercel` / `.env.vercel-prod` | Environment variable files. | Active |

---

## `frontend/tests/e2e/` -- E2E TESTS (19 test files)

All are Playwright spec files (`*.spec.ts`). Tests cover: auth web sessions, account shell bootstrap resilience/hydration, activity proof panes, admin surfaces, app marketplace identity, approvals, artifacts, billing entitlements, chat transparency timeline, deployed agents, gateway operator state, Sage launch first-run, non-scaffold surface sweep, Sage heartbeat, visual capture, workspace setup, workstation data reconciliation. Plus `support/auth.ts` for shared auth helpers.

---

## `frontend/scripts/` -- BUILD SCRIPTS

| Path | Purpose |
|---|---|
| `api-wall-network-smoke.mjs` | API smoke test script. |
| `prepare-vercel-next-output.mjs` | Vercel deployment preparation script. |
| `start-e2e-backend.sh` | E2E test backend launcher. |

---

## SUMMARY STATS

- **Total source files mapped**: ~150+
- **Largest file**: `workstation-chat-pane.tsx` (~3854 lines)
- **Recently modified** (per git status): `event-projector.ts`, `workstation-chat-timeline-projection.ts`, `workstation-runs-pane.tsx`, `control-plane-base-url.ts`, `WorkspaceSurfacePage.tsx`
- **Architecture pattern**: The chat system follows a pipeline: raw SSE/trace events -> `TimelineProjectionEvent[]` -> `projectRawEventToCodexEvents()` (`event-projector.ts`) -> `CodexChatEvent[]` -> `applyCodexEvent()` in `timeline-reducer.ts` -> `CodexTranscriptCell[]` -> `groupExecutionTraceCells()` in `workstation-chat-timeline-projection.ts` -> `CodexChatCell` components in `cell-components.tsx`. A parallel legacy path renders `WorkstationChatMessageRecord` via `chat-message.tsx`.
- **Sanitization is dual-layered**: `transcript-event-contract.ts` filters what gets persisted to metadata, and `workstation-chat-timeline-projection.ts` filters what gets projected to UI.
- **frontend/v2/**: No source code -- only build artifacts (`.next/`, `out/`, `node_modules/`).