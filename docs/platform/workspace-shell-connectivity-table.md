# Workspace Shell Connectivity Table

Date: 2026-06-01

Branch: `codex/ui-workspace-linking`

## Purpose

This table is the UI-linking guardrail before changing workspace interface behavior. It maps each workspace shell route to the component, frontend client call, backend endpoint, live state source, missing link, and physical smoke step.

The rule for the next UI phase is simple: route and pane wiring can proceed only where the live state source and smoke step are explicit. Visual polish waits until the wiring is honest.

## Current route shell

The workspace navigation manifest exposes these destinations:

| Destination | Route ids | Default route | User label |
|---|---|---|---|
| `sage` | `chat`, `memory`, `integrations`, `channels`, `tasks`, `artifacts`, `approvals`, `notifications` | `chat` | Sage |
| `studio` | `studio`, `inbox`, `deploy`, `studioIntegrations` | `studio` | Agents |
| `marketplace` | `marketplace` | `marketplace` | Discover |
| `applications` | `applications` | `applications` | Applications |
| `gateway` | `gateway`, `gatewayApprovals`, `gatewayActivity` | `gateway` | Agent Computer |
| `settings` | `settings` | `settings` | Settings |

## Connectivity table

| Route | Component | Frontend client call | Backend endpoint | Live state source | Missing link | Physical smoke step |
|---|---|---|---|---|---|---|
| `/w/:workspaceId/sage` | `WorkstationChatPane` | `getThread`, `listRuns`, `listApprovals`, `listActivityTimeline`, provider/profile calls, direct gateway readiness requests | Runtime run/thread/activity endpoints, `/api/gateway/registrations`, `/api/gateway/registrations/{gateway_id}/doctor`, billing/provider endpoints | Runtime run records, approval records, activity timeline, gateway registration/doctor state, billing state | Needs end-to-end smoke from chat action to run/activity/approval panels; gateway readiness is direct-request based rather than a named workstation-client method | Open Sage, send a message that starts a run, verify run row, activity event, approval badge if gated, and gateway readiness indicator |
| `/w/:workspaceId/memory` | `WorkstationActivityPane` used as memory surface | `listSageMemory`, `getSageMemoryStoragePolicy`, `listSageContextFiles`, `createSageMemoryEntry`, `updateSageMemoryEntry`, `exportSageMemory` | Sage memory/profile/context-file routes via workstation client | Sage memory entries, storage policy, context files, profile bootstrap snapshot | Route label is `memory`, but component naming still says activity; ensure titlebar and route copy do not confuse memory vs activity | Open Memory, add a safe memory, pin or edit it, refresh, export memory, verify it persists |
| `/w/:workspaceId/approvals` | `WorkstationApprovalsPane` | `listApprovals`, `resolveApproval` through `resolveWorkstationApproval` | Approval listing/resolution endpoints through workstation client and runtime/gateway approval services | Pending approval records plus workstation approval-resolved browser event | Needs one shared approval event contract across Chat, Sage approvals, and Gateway approvals | Trigger an approval from Sage or Gateway, open Approvals, approve once, verify blocked work resumes and approval disappears |
| `/w/:workspaceId/artifacts` | `WorkstationArtifactsPane` | `listArtifacts`, `artifactDownloadUrl` | Artifact list/download endpoints via workstation client | Runtime artifact records and downloadable artifact content | Need verify artifact download URL uses current workspace auth/session correctly | Generate or use an existing artifact, open Library, download it, verify browser receives file |
| `/w/:workspaceId/notifications` | `WorkstationNotificationsPane` | Activity/notification client calls through workstation services | Activity timeline/notification endpoints | Activity ledger, notifications, stream state | Needs explicit cross-panel refresh when runs, approvals, gateway events, and deployed-agent events update | Open Activity, trigger a run/gateway event, verify new item appears without full page reload if stream is connected |
| `/w/:workspaceId/integrations` | `WorkstationSageConnectorsPane` | Connector/vault calls through workstation client | Connector/vault routes | Connector registry and vault state | Need classify which integrations belong to Sage vs Studio vs connected apps | Open Connections, connect or view a connector, verify status and vault metadata |
| `/w/:workspaceId/channels` | `WorkstationSageConnectorsPane` Channels group | Connection catalog/status/actions plus personal-channel/gateway calls through workstation client | `/api/connections/catalog`, `/api/connections/status`, `/api/connections/{id}/setup/start`, `/api/personal-channels/gateways/{gateway_id}/channels`, Telegram setup routes, generic personal-channel message routes | Canonical connection catalog plus selected Agent Computer gateway personal-channel state for Telegram; Signal, iMessage, and WeChat are planned/private bridge records until certified. **WhatsApp is not a supported channel; remove WhatsApp setup UI.** | Needs clearer deep links from Agent Computer status into the relevant channel card and live-account certification evidence before planned private bridges can unlock | Pair a gateway, open Connectors > Channels, verify Telegram (hosted bot) live state, verify Signal/iMessage/WeChat remain locked/planned, and send test messages only through the approval path |
| `/w/:workspaceId/tasks` | Task/runs-oriented surface | `listRuns` and runtime run client calls | Runtime run routes | Runtime run queue and run state | Need confirm whether Tasks is distinct from Runs or a label alias | Open Tasks, start a run, verify pending/running/completed grouping |
| `/w/:workspaceId/studio` | `WorkstationDeployedAgentsPane` | Deployed-agent list/detail/mutation calls | `/api/deployed-agents`, `/api/deployed-agents/{id}`, deploy/pause/kill/recover/archive routes | Deployed-agent records, readiness, runtime binding status | Need ensure route and pane language says Agents, not internal deployed-agent jargon where user-facing | Open Agents, create or select an agent, deploy/pause/recover, verify status updates |
| `/w/:workspaceId/inbox` | Deployed-agent inbox/conversation surface | Deployed-agent conversation list/detail calls | `/api/deployed-agents/{id}/conversations`, `/api/deployed-agents/{id}/conversations/{session_id}` | External user conversation records and deployed-agent channel events | Needs direct route-to-selected-agent behavior; empty state should explain selecting an agent | Open Messages for an agent with conversation data, select a session, verify messages render |
| `/w/:workspaceId/deploy` | Deployed-agent deploy/go-live surface | Deployed-agent readiness and deploy calls | `/api/deployed-agents/{id}/deploy`, `/api/deployed-agents/telegram-readiness`, knowledge verification/upload routes | Deployed-agent readiness, privacy/computer safety contracts, Telegram readiness, knowledge records | Need smoke for deploy blockers and owner approval path | Open Go Live, resolve readiness blockers, deploy, verify live status and route readiness |
| `/w/:workspaceId/studio-integrations` | `WorkstationStudioIntegrationsPane` | Studio integration client calls | Marketplace/package/provider/connector routes depending selected integration | Studio integration registry and provider package state | Needs source-of-truth separation from Sage Connections and Applications | Open Studio Integrations, install or inspect one integration, verify it appears in agent configuration |
| `/w/:workspaceId/marketplace` | `DiscoveryPane` | `listDiscoveryFeed`, `adoptDiscoveryItem` | `/api/workspaces/{workspace_id}/discovery/feed`, `/api/workspaces/{workspace_id}/discovery/items/{feed_item_id}/adopt` | Discovery feed service, marketplace app/template/package sources | Backend route registration is now isolated on this branch; need frontend typecheck and smoke after Discovery pane is committed | Open Discover, filter Apps/Agents/Tools, adopt one item, verify open URL leads to the copied app or Studio target |
| `/w/:workspaceId/applications` | `HostedMiniAppsPane` / Applications surface | App and mini-app list/create/clone/settings/open calls | `/api/workspaces/{workspace_id}/apps`, `/api/workspaces/{workspace_id}/apps/clone`, `/api/workspaces/{workspace_id}/apps/{app_id}/settings`, mini-app hosted/bridge/invoke routes | Mini-app registry, app settings, hosted mini-app sessions, app bridge events | Current dirty work changes Applications feed and creation flow; must separate installed apps, user-owned apps, and public clone flow clearly | Open Applications, create a private app, clone a public app, open app, change permissions, refresh and verify state persists |
| `/w/:workspaceId/gateway` | `WorkstationGatewayOperatorPane` | Gateway registration/session/doctor/browser/tool/personal-channel calls through workstation client or direct requests | `/api/gateway/registrations`, sessions, doctor, browser sessions/actions/resume/interrupt, tool execute/interrupt, personal-channel routes | Gateway registration, live websocket session, doctor/readiness, browser sessions, personal-channel state, gateway events | Highest priority UI link: status, browser session controls, approvals, and personal-channel controls need one visible state model | Start local stack and Agent Computer, open Agent Computer, verify online status, start browser session, resume/interrupt, trigger an approval, resolve it |
| `/w/:workspaceId/gateway-approvals` | Gateway approval subpane or `WorkstationApprovalsPane` variant | Gateway approval listing/resolution calls | `/api/gateway/registrations/{gateway_id}/approvals`, `/api/gateway/registrations/{gateway_id}/approvals/{approval_id}/resolve` | Gateway action approval records | Missing explicit route/component distinction from global Sage approvals | Trigger gateway owner approval, open Gateway Approvals, approve/reject, verify gateway operation resumes or stops |
| `/w/:workspaceId/gateway-activity` | Gateway activity subpane or activity variant | Gateway event listing calls | `/api/gateway/registrations/{gateway_id}/events` | Gateway protocol and action event log | Needs link from Agent Computer status card to raw activity stream | Open Computer Activity, perform gateway connect/tool/browser action, verify event log sequence |
| `/w/:workspaceId/settings` | `WorkstationSettingsPane` | Workspace settings/profile/routing/billing/provider calls | Settings, provider, billing, workspace routing endpoints | Workspace profile, preferences, provider profiles, routing policy, billing summary | Need avoid mixing billing/provider/admin controls with general user preferences without clear grouping | Open Settings, change a harmless preference/provider route if available, refresh, verify persistence |

## Highest-risk missing links

| Risk | Why it matters | Recommended handling |
|---|---|---|
| Agent Computer state is split across registration, doctor, browser sessions, approvals, events, and personal channels | This is the core live UI path and most likely to look connected while not actually being connected | Build one gateway status model in the UI and drive status, controls, approvals, and activity from it |
| Discover depends on route registration plus new frontend client methods | The UI already calls Discovery endpoints, but the route mount was not on `main` before this branch | Keep `server.py` route registration as its own branch commit, then commit Discovery UI separately |
| Applications and Discover overlap | Public apps, private apps, installed apps, mini-apps, hosted apps, and marketplace apps can confuse users | Use Discover for finding/adopting; Applications for owned/installed/operating |
| Approvals appear in Sage, Gateway, and chat composer flows | Duplicate approval surfaces can diverge | Use one resolution helper and one event emission path for all approval panels |
| Hidden routes lack obvious navigation affordances | `approvals`, `artifacts`, `notifications`, `channels`, `gatewayApprovals`, and `gatewayActivity` are hidden from normal nav | Add contextual links from the visible parent panes rather than exposing everything in the rail |

## First UI implementation order

1. Commit the Discovery route mount and Discovery UI as separate reviewable changes.
2. Stabilize `marketplace` route as Discover using `DiscoveryPane`.
3. Stabilize `applications` as the owned/installed apps surface.
4. Build a unified Agent Computer status model for `gateway`, `gatewayApprovals`, and `gatewayActivity`.
5. Link Sage approval and activity events across Chat, Approvals, Notifications, and Runs.
6. Link deployed-agent routes after Agent Computer status and approvals are honest.
7. Do visual polish only after physical smoke passes.

## Physical smoke checklist

Use this checklist when the next UI wiring commit is ready:

| Step | Expected result |
|---|---|
| Start local backend and frontend | Workspace shell loads without route or hydration errors |
| Open `/w/default/marketplace` | Discover feed loads from `/api/workspaces/default/discovery/feed` |
| Adopt one Discover item | User lands on the returned app or Studio URL |
| Open `/w/default/applications` | Installed/owned apps render from live app endpoints |
| Create a private app | App persists after refresh |
| Open `/w/default/gateway` | Agent Computer shows live registration/doctor state |
| Connect local Agent Computer | Status changes from offline/stale to online/healthy |
| Start browser session | Browser session appears and controls become active |
| Trigger an approval | Approval appears in the relevant approval surface |
| Resolve approval | Blocked action resumes or stops and activity panels update |
| Open deployed-agent route | Agent status, inbox, analytics, and test-turn surfaces read live backend state |
