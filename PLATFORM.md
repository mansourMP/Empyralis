# Empyralis Platform

## Overview

Empyralis is an enterprise-grade operating system for building, running, and governing AI workers. The platform provides six integrated workspace surfaces — Sage, Agents, Discover, Applications, Agent Computer, and Settings — each backed by a service-oriented Python backend and a Next.js 16 frontend with React 19.

The platform supports multi-tenant workspaces, multi-channel agent deployment, a governed marketplace, hosted mini-applications, and local hardware execution through a secure gateway protocol.

---

## Workspace Surfaces

The workspace shell presents six navigation destinations, each with defined child routes.

### 1. Sage

**Destination ID:** `sage`
**Icon:** `message-square`
**Default route:** `chat`

Sage is the primary AI intelligence bound to each workspace. It maintains a persistent identity, layered memory, and governs interactions across all channels.

#### Routes

| Route | Segment | Label | Description |
|---|---|---|---|
| `chat` | `sage` | Sage | Primary conversational interface. Real-time turn-based chat with full thread history, tool use, and channel awareness. |
| `memory` | `memory` | Memory | Timeline view of Sage interactions, memory recalls, and agent actions. |
| `integrations` | `integrations` | Connections | MCP server connections, tool contracts, provider configuration, and API service management. |
| `channels` | `channels` | Connections | Per-channel inbound and outbound message routing configuration. |
| `tasks` | `tasks` | Tasks | Scheduled and heartbeat-based agent tasks with recurring run schedules. |
| `artifacts` | `artifacts` | Library | Persistent documents, knowledge sources, and generated content managed by Sage. |
| `approvals` | `approvals` | Approvals | Human-in-the-loop approval gates for actions Sage or agents request. Hidden from web navigation; accessible via mobile bottom tabs. |
| `notifications` | `notifications` | Activity | Real-time activity notifications across all workspace surfaces. Hidden from web navigation; primary mobile tab. |
| `activity` | `activity` | Activity | Execution history of Sage tasks, run traces, and event logs. Hidden from web navigation. |

#### Core Capabilities

- **Persistent Identity**: Sage maintains a stable identity bound to the workspace via Soul.md, Identity.md, and Heartbeat.md memory files.
- **Layered Memory Architecture**: Four-tier memory system — Profile Memory (long-term), Episodic Memory (interactions), Local Private (on-device), Cloud Synced (encrypted). Memory is permissioned and private by design.
- **Safety Control Plane**: Granular governance for every turn. The agent internalizes rules and judgment; consumers do not see approval buttons. Quotas, entitlements, and policy-bound runtime placement.
- **Provider Routing**: Model selection and provider routing with tier-based contracts. Empyralis credits always route through the Platform AI engine. Users can bring their own API key for other providers via "My API Key" tier.
- **Skills System**: Installable callable tools that extend Sage's capabilities. Skills are registered in a workspace-scoped registry.
- **Transparency Timeline**: Every interaction produces a verifiable transparency record showing tool calls, memory loads, approvals, and provider decisions.
- **Context Files**: Knowledge retrieval augmented generation with file upload support (.md, .txt, .csv, .json).

---

### 2. Agents

**Destination ID:** `studio`
**Icon:** `boxes`
**Label in navigation:** Agents
**Default route:** `studio`
**Requires capability:** `workspace_admin_enabled`

The Agents surface is the studio for creating, configuring, deploying, and managing AI agents. It supports native agents built from templates as well as externally connected agents.

#### Routes

| Route | Segment | Label | Description |
|---|---|---|---|
| `studio` | `studio` | Agents | Agent roster with list and detail views. Create agents from templates or from scratch. View deployed agent status (live, draft, blocked). |
| `inbox` | `inbox` | Messages | Message inbox for deployed agents showing inbound messages across all connected channels. |
| `deploy` | `deploy` | Go Live | Deployment and rollout controls. Bind agents to production channels with configuration validation. |
| `studioIntegrations` | `studio-integrations` | Integrations | Channel account linking, credential management, and agent-to-channel binding. Hidden from web navigation. |

#### Agent Detail Tabs

Each agent in the roster exposes a detail view with the following tabs:

- **Overview (Command Center)**: Agent identity, description, status badges, launch readiness checklist, recent test message preview, deploy blockers, and cost/usage signals.
- **Chat (Playground)**: Private test conversation surface with full chat transcript, transparency timeline, and the same tool/model controls as production.
- **Knowledge**: Agent purpose (system prompt / instructions) and trusted data sources. File upload with retrieval health metrics and search test capability.
- **Model (AI Settings)**: Provider connection, model selection, provider catalog, model tier and routing configuration.
- **Actions**: Tool and action configuration for the agent.
- **Memory**: Conversation memory policy — how the agent retains and forgets conversation history.
- **Integrations**: Channel bindings and credential management.
- **Results**: Analytics dashboard with message volume, cost tracking, and quality metrics.

#### Agent Types

- **Template-based Agents**: Built from marketplace agent templates with pre-configured behaviors, channels, data needs, and safety policies. Templates include business-specific configurations (restaurant orders, auto parts sales, real estate leads, support FAQ, appointment booking, and more).
- **Custom Agents**: Built from scratch with full control over all configuration dimensions.
- **Connected External Agents**: API-based external agents registered in the workspace and integrated into the chat routing system.

#### Agent Lifecycle

1. **Draft**: Agent created but not yet deployed. Editable in all dimensions.
2. **Test**: Private test conversations available. Behavior validated before going live.
3. **Live**: Deployed to one or more channels. Production traffic routed per channel bindings.
4. **Blocked**: Deployment blocked by policy, missing configuration, or approval requirements.

#### Studio Capabilities

- **Agent Templates**: Pre-built agent configurations installable from the marketplace. Templates include proof-of-concept contracts with defined business behavior, channel requirements, data source needs, monetization models, and safety policies.
- **Test Turns**: Sandbox environment for testing agent behavior before production deployment.
- **Channel Bindings**: Bind agents to Telegram (hosted bot), Slack, Discord, and custom API channels. WhatsApp is not a supported channel. Web Chat and Email are planned but not yet launch-ready.
- **Business Insights**: Per-agent analytics including message volume, cost tracking, and quality ratings.
- **Conversation Memory Policy**: Configurable retention and forgetting behavior per agent.
- **Cost Caps**: Spending limits per agent with automatic enforcement.
- **Rate Limiting**: Configurable rate limits per agent and per channel.

---

### 3. Discover

**Destination ID:** `marketplace`
**Icon:** `compass`
**Label in navigation:** Discover
**Default route:** `marketplace`

Discover is the governed marketplace where workspace members browse, evaluate, and install capabilities. It supports agent templates, skills, MCP connectors, bundles, and community applications.

#### Package Types

| Kind | Label | Description |
|---|---|---|
| `agent_template` | Agent Template | Pre-built agent configurations. Creates a draft agent in the studio on install. |
| `skill` | Skill | Callable tools registered in the workspace tool catalog. Available to Sage and eligible agents. |
| `connector` | MCP | MCP server connections. Opens setup in the integrations surface on install. |
| `bundle` | Bundle | Composable groups containing templates, tools, connectors, setup steps, and required access in a single installable package. |
| `app` | App | Community or platform applications installable as workspace apps. |
| `mini_app` | Mini-app | Lightweight hosted applications with scoped permissions. |

#### Trust and Verification

Every marketplace package carries:

- **Verification Status**: `unverified`, `partner`, or `verified`
- **Review State**: `pending`, `approved`, or `restricted`
- **Health State**: `setup_required`, `healthy`, or `degraded`
- **Policy Posture**: `governed` or `restricted`
- **Lifecycle**: `Active`, `Preview`, or `Deprecated`

#### Install Eligibility

Packages are evaluated against install blockers before installation is permitted:

- `preview_only` — Preview only; installation is not enabled
- `review_not_approved` — Waiting for marketplace review
- `verification_required` — Publisher verification is required
- `policy_restricted` — Restricted by marketplace policy
- `manual_approval_required` — Owner approval required before install
- `excessive_permissions_requested` — Requests too much access for automatic install
- `excessive_domain_scope` — Requests a broad domain scope
- `unsafe_local_runtime_permission_combo` — Requests unsafe local-computer permissions
- `owner_resource_boundary_violation` — Requests owner resources that require review

#### Developer Registration

Workspace admins with the `workspace_admin_enabled` capability can:

- **Submit community apps** for review with publisher metadata, hosted URLs, icon URLs, permissions, allowed origins, and bridge contracts.
- **Register provider packages** with model rosters, auth modes, capability labels, jurisdictions, privacy postures, and monetization configurations.
- **Review pending submissions** — approve or reject community app submissions before they appear in Discover.

#### Monetization

Packages support four monetization models:
- **Free**: No charge; uses workspace credits for messages and actions.
- **Metered**: Usage-based billing.
- **Subscription**: Recurring subscription.
- **Revenue Share**: Revenue-share basis points configuration.

---

### 4. Applications

**Destination ID:** `applications`
**Icon:** `package`
**Label in navigation:** Applications
**Default route:** `applications`
**Requires capability:** `workspace_admin_enabled`

The Applications surface hosts mini-applications within the workspace shell. It supports official first-party apps, community apps from the marketplace, and private workspace apps published directly.

#### Official Mini-Apps

| App | ID | Description |
|---|---|---|
| Flashcards | `flashcards` | Spaced-repetition flashcard study tool with AI-powered card generation from source text. |
| Calorie Tracker | `calorie_tracking` | Food and meal logging with daily totals, protein tracking, and goal setting. |

#### Private App Publishing

Workspace members can publish private applications directly without review:

- **App Name**: Required label for the application.
- **App URL**: HTTPS URL for the hosted application (localhost allowed in development).
- **Icon URL**: Optional HTTPS icon for the app tile.
- **Instant Deployment**: Apps go live immediately with no review process.
- **Scoped Permissions**: Permission model configurable after publishing.

#### App Bridge

Hosted applications communicate bidirectionally with Sage through a bridge contract system:

- **Bridge Contracts**: Define allowed communication patterns between apps and Sage (e.g., `app_to_sage: summary_request, search`).
- **Permission Scopes**: `app_bridge.read`, `app_bridge.write`, `app.ai.invoke`.
- **AI Invoke**: Apps can invoke Sage's AI capabilities with credit caps (monthly and per-invocation limits), consent management, and accounting via the credit ledger.
- **Trust Tiers**: `user_private`, `first_party`, `public_untrusted_url` — each with different capability levels.
- **Active Sessions**: Timed, signed tokens for app active sessions with scope verification.

#### App Store

A browsable app store surface allows workspace members to discover and install applications, distinct from the Discover marketplace which focuses on agent templates, skills, and connectors.

---

### 5. Agent Computer

**Destination ID:** `gateway`
**Icon:** `waypoints`
**Label in navigation:** Agent Computer
**Default route:** `gateway`

Agent Computer is the hardware execution surface. It enables agents to run on local or cloud machines with full desktop control — browser, terminal, and filesystem access — governed by policy, quotas, and approval gates.

#### Routes

| Route | Segment | Label | Description |
|---|---|---|---|
| `gateway` | `gateway` | Agent Computer | Primary operator pane for gateway management, device registration, and policy configuration. |
| `gatewayApprovals` | `gateway-approvals` | Approvals | Human approval workflows for gateway and computer agent actions. Hidden from web navigation. |
| `gatewayActivity` | `gateway-activity` | Computer Activity | Detailed activity feed of all gateway actions with transparency records. Hidden from web navigation. |

#### Runtime Profiles

Three execution profiles determine where and how agents run:

- **Cloud Computer Agent**: Secure cloud sandbox with browser, terminal, and filesystem access.
- **My Computer Agent**: Local machine execution through the gateway — Mac mini, workstation, or laptop under the user's desk.
- **Self-Hosted Agent**: Self-managed node connected through the gateway protocol.

#### Gateway Infrastructure

- **Gateway Registration**: Register devices (Mac mini, cloud VMs, local machines) as gateway endpoints with pairing and trust establishment.
- **Gateway Protocol**: Wire protocol for encrypted gateway-to-cloud communication.
- **Device Trust**: Trust state management for paired gateways with certificate-based verification.
- **Gateway Health**: Real-time health monitoring of connected gateways.
- **Gateway Inventory**: Inventory management of registered gateway devices.

#### Security and Policy

- **Agent Computer Policy**: Fine-grained policy per gateway — autonomy mode, allowed and blocked capabilities, filesystem scope, domain allowlist, network policy, terminal policy, browser policy, app access policy, approval TTL, maximum runtime, and emergency stop configuration.
- **Capability Risk Classification**: Risk-based classification for browser actions and tool execution producing decisions: `DECISION_APPROVAL_REQUIRED` or `DECISION_BLOCK`.
- **Gateway Quotas**: Per-minute and per-session quota enforcement for tool execution, browser sessions, approvals, and WebSocket connections.
- **Kill Switch**: Emergency stop for all gateway and computer agent operations. Operates at both the gateway and channel level.
- **Safe Mode**: Restricted operations mode for gateways, limiting capabilities to read-only and low-risk actions.
- **Security Auditing**: Full audit trail of all gateway actions with transparency emission.
- **Hardware Access Policy**: Access control for mouse, keyboard, screen, and clipboard interactions.

#### Hardware Execution

- **Hardware Action Broker**: Brokers local hardware interactions — mouse movement, keyboard input, screen capture, clipboard access.
- **Hardware Runtime Adapters**: Adapter layer supporting Cloud Computer, Gateway (local machine), and Self-Hosted Node execution targets.
- **Result Correlation**: Correlates hardware action requests with their results for transparency and audit.
- **Runtime Session Management**: Session lifecycle management for hardware execution contexts.

#### Browser Automation

- **Browser Engine**: Cloud and local browser session management with risk-classified action execution.
- **Browser Approval Service**: Per-action approval decisions for browser interactions based on risk classification.
- **Browser Checkpoint Service**: State checkpointing for browser sessions enabling pause, resume, and rollback.

---

### 6. Settings

**Destination ID:** `settings`
**Icon:** `sliders-horizontal`
**Label in navigation:** Settings
**Default route:** `settings`

Workspace configuration surface with the following sections:

- **Account**: Profile management, identity, and plan information.
- **Appearance**: Theme configuration (System, Light, Dark).
- **Agent Computer / Devices**: Gateway device management and trust configuration.
- **Usage**: Workspace usage analytics and consumption metrics.
- **Billing**: Plan management, checkout, customer portal, and credit purchases.
- **Privacy & Safety**: Trust boundaries, data handling, and safety configuration.
- **Transparency**: Visibility levels — Quiet, Basic, Normal, Detailed, Admin.

---

## Workflow Builder

The platform includes an AI-powered visual workflow builder that generates workflow graphs from plain-language descriptions.

### Node Types

| Category | Node Types |
|---|---|
| **Triggers** | Manual, Connector Event, Schedule, Webhook, File Watch |
| **Agents** | Deployed Agent (any agent from the studio) |
| **Tools** | Connector Action, HTTP Request, Browser, File Operation, Shell Command, Code Execution |
| **Decisions** | If/Else, Classifier, Field Router |
| **Human** | Approval, Review, Wait |
| **Data** | Transform, Compose, Validate |
| **Flow Control** | Subflow, Loop |

### Workflow Lifecycle

- **Draft**: Workflow in design/editing phase.
- **Published**: Production-ready with non-manual triggers. Executable by the runtime engine.
- **Execution**: Runtime execution with state persistence, delegation, and output capture.

---

## Channel Infrastructure

The platform supports messaging channels that bring outside messages into the platform and send replies back. Channels are thin transports; channel-reliability logic lives in the shared core ingress layer.

### Supported Channels

| Channel | Status | Notes |
|---|---|---|
| **Telegram (Hosted Bot)** | Live | The reliable Telegram path. Shared platform bot; customers never need BotFather. |
| **Discord** | Live when configured | Discord bot connector. |
| **Slack** | Live when configured | Slack app connector. |
| **Web Chat** | Planned | Not launch-ready; widget, ingress, and delivery proof still required. |
| **API** | Live | Programmatic access via REST and streaming endpoints. |

> **WhatsApp:** Not a supported channel. Personal WhatsApp (Baileys/QR) is banned by the provider. WhatsApp Business Cloud API bars third-party general-purpose AI assistants as of Jan 15, 2026. Do not present WhatsApp as working or coming soon.
>
> **iMessage, WeChat, Signal:** Require the user's own hardware bridge. These are not cloud features and are not offered on the base tier.
>
> **Telegram Personal (MTProto/phone-number login):** Fragile and advanced. It is not the recommended Telegram path. Use the hosted bot instead.

### Channel Services

Each channel is backed by a common service layer:

- **Channel Execution Service**: Orchestrates turn execution across channels with quota enforcement.
- **Channel Routing**: Routes inbound messages to the correct agent or Sage based on channel bindings and routing rules.
- **Channel Safety Overlay**: Applies safety policies at the channel boundary before messages reach agents.
- **Channel Memory Overlay**: Attaches channel-specific memory context to conversations.
- **Channel Preflight Service**: Validates channel configuration before deployment.
- **Channel Identity Service**: Manages channel-level identity and sender verification.
- **Channel Pairing Service**: Links personal channels (Telegram hosted bot) to workspaces via pairing codes.
- **Channel Blocking Policy**: Blocks or allows specific channels per workspace policy.
- **Channel Concurrency**: Manages concurrent channel sessions and turn execution ordering.
- **Channel Quota Policy**: Enforces per-channel usage quotas.
- **Channel Activity Service**: Tracks and reports channel-level activity metrics.

### Personal Channels

Users can link personal messaging accounts to workspaces through a pairing flow, enabling personal device access to workspace agents.

---

## Authentication and Account Management

### Authentication Methods

- **Email/Password**: Standard credential-based authentication with registration toggle.
- **Google OAuth**: Social login with Google identity provider.
- **External Identity Tokens**: Enterprise SSO integration with verified external identity tokens.
- **Mobile Beta Login**: Separate authentication flow for mobile beta users.

### Session and Security

- **Cookie-based Sessions**: HttpOnly, secure, same-site cookies with refresh token rotation.
- **Device Management**: Per-user device tracking with revocation capability.
- **CSRF Protection**: Double-submit cookie pattern for state-changing operations.
- **Role-based Access**: Workspace membership with role boundaries.

### Account Shell

- **Multi-Tenant Workspace Switching**: Users belong to multiple workspaces with seamless switching.
- **Workspace Membership Index**: Aggregated view of all workspace memberships for a user.
- **Onboarding Flow**: Guided workspace setup for new members.

---

## Billing and Credits

### Plans

Workspaces are provisioned under tiered plans: Personal, Professional, Team, and Pilot.

### Credit System

- **Credit Ledger**: Unified ledger contract tracking all workspace credit consumption — messages, tool calls, AI invocations, and mini-app usage.
- **Credit Purchase**: USD-based credit purchasing.
- **Checkout**: Stripe-hosted checkout for plan selection.
- **Customer Portal**: Self-service subscription management, payment methods, and invoice access.

### Cost Controls

- **Agent Cost Caps**: Per-agent spending limits with automatic enforcement.
- **Mini-App AI Invoke Caps**: Monthly and per-invocation credit limits for apps invoking Sage's AI.
- **Usage Analytics**: Consumption tracking per workspace, per agent, and per surface.

---

## Security and Governance

### Runtime Safety

- **Approval Gates**: Human-in-the-loop approval for high-risk actions. Configurable per agent, per action type, and per channel.
- **Egress Policy**: Controls what data leaves the workspace boundary.
- **External Content Guard**: Evaluates external URLs and content for safety before ingestion.
- **External Write Safety**: Controls and validates write operations to external systems.
- **Response Leak Guard**: Prevents sensitive data from leaking in agent responses.
- **Secret Redaction**: Automatic redaction of secrets and credentials from logs and transcripts.

### Execution Policy

- **Execution Sandbox**: Isolated execution environments for agent code and tool use.
- **Execution Mode Policy**: Controls which execution modes are permitted (hosted secure, local, self-hosted).
- **File Mount Security**: Validates filesystem access scope and permissions.
- **Browser URL Safety**: Validates URLs before browser navigation in computer agents.

### Observability

- **Transparency Timeline**: Every turn produces a verifiable record showing reasoning steps, tool calls, memory operations, approvals, and provider decisions.
- **Agent Traces**: Full execution traces for debugging and audit.
- **Activity Ledger**: Immutable record of all workspace activity.
- **Security Audit Service**: Audit trail for all security-relevant events.
- **Runtime Events**: Event stream for all runtime operations.

### Data Control

- **Data Retention**: Configurable retention policies with automated enforcement jobs.
- **Memory Isolation**: Cross-agent memory isolation preventing data leakage between agents.
- **External User Privacy**: Privacy controls for external users interacting with workspace agents.

---

## Pilot Program

The platform includes a pilot program infrastructure for controlled early access:

- **Pilot Invites**: Create and manage invites with role assignment, plan selection, maximum uses, and expiration.
- **Pilot Feedback**: Collect usefulness scores and free-text comments from pilot users.
- **Pilot Issue Reporting**: Report issues with workflows including severity levels and fix tracking.
- **Operations Contract**: SLA and operational parameters for pilot deployments.

---

## Platform Administration

- **Platform Analytics**: Admin-level analytics across all workspaces including usage, growth, and health metrics.
- **Workspace Admin Service**: Workspace-level administration capabilities.
- **Enterprise Tenant Settings**: Per-tenant enterprise configuration — SSO, data residency, and policy overrides.
- **Health Diagnostics**: Platform health checking with database, endpoint, and service-level probes.
- **Doctor Gate**: Startup health verification ensuring all required services are operational.

---

## Technical Architecture

### Frontend

- **Framework**: Next.js 16 (App Router) with React 19
- **Language**: TypeScript 5.9
- **Styling**: CSS Modules with CSS custom properties for theming
- **Animation**: GSAP (GreenSock) with ScrollTrigger plugin; Lenis for smooth scrolling
- **Icons**: Lucide React
- **Fonts**: DM Sans (UI), Fraunces (headings) — local font loading
- **Testing**: Playwright for end-to-end tests

#### Key Frontend Modules

| Module | Path | Purpose |
|---|---|---|
| Workspace Shell Frame | `frontend/lib/workspace/workstation-shell-frame.tsx` | Chrome layout: titlebar, sidebar, viewport |
| Workspace Kernel Shell | `frontend/lib/workspace/workstation-kernel-shell.tsx` | Navigation routing and destination rendering |
| Workspace Boundary | `frontend/lib/workspace/workspace-boundary.tsx` | Capability gates and route manifest |
| Workspace Services | `frontend/lib/workspace/workspace-services.tsx` | API client injection via React context |
| Marketplace Pane | `frontend/lib/marketplace/marketplace-pane.tsx` | Discover catalog, detail views, installation, developer registration |
| Hosted Mini-Apps Pane | `frontend/lib/workspace/hosted-mini-apps-pane.tsx` | Application listing, private app publishing |
| Deployed Agents Pane | `frontend/lib/workspace/workstation-deployed-agents-pane.tsx` | Agent roster, detail views, wizard |
| Chat Pane | `frontend/lib/workspace/workstation-chat-pane.tsx` | Sage conversational interface |
| Gateway Operator Pane | `frontend/lib/workspace/workstation-gateway-operator-pane.tsx` | Agent Computer management |
| Transparency Timeline | `frontend/lib/workspace/transparency-timeline.tsx` | Turn-by-turn reasoning visibility |

### Backend

- **Language**: Python 3
- **Framework**: FastAPI (Starlette-based)
- **Database**: SQLite with helpers for connection management
- **Session Management**: Actor-based session manager with runtime caching and observability

#### Backend Service Modules (Representative)

**Sage Domain:**
`sage_chat_api`, `sage_memory_api`, `sage_memory_service`, `sage_profile_api`, `sage_profile_service`, `sage_services_api`, `sage_services_service`, `sage_skills_api`, `sage_heartbeat_api`, `sage_heartbeat_service`, `sage_approval_service`, `sage_context_files_api`, `sage_agent_runtime_contract`, `sage_agent_runtime_service`, `sage_instruction_compiler_service`, `sage_transparency_service`, `sage_turn_adapter`

**Agent Studio Domain:**
`deployed_agent_service`, `deployed_agent_config_schema`, `deployed_agent_runtime_contract_service`, `deployed_agent_test_turn_service`, `deployed_agent_memory_service`, `deployed_agent_analytics_service`, `deployed_agent_business_insights_service`, `deployed_agent_cost_cap_service`, `deployed_agent_rate_limit_service`, `deployed_agent_transparency_service`, `deployed_agent_virtual_runtime_service`, `deployed_agent_marketplace_service`, `deployed_agent_daily_quota_adapter`, `studio_proof_agent_seed_service`, `studio_app_boundary_service`, `connected_external_agent_service`

**Marketplace Domain:**
`marketplace_distribution_service`

**Applications Domain:**
`mini_apps_service`, `mini_app_host_service`, `mini_app_invoke_service`, `mini_app_token_exchange_service`, `app_bridge_service`, `app_registry_api`, `flashcards_tracking_service`, `calorie_tracking_service`

**Gateway/Agent Computer Domain:**
`gateway_registry_service`, `gateway_protocol_service`, `gateway_execution_service`, `gateway_approval_service`, `gateway_activity_service`, `gateway_browser_service`, `gateway_browser_runtime`, `gateway_health_service`, `gateway_pairing_service`, `gateway_quota_enforcement`, `gateway_inventory_service`, `gateway_transparency_service`, `agent_computer_policy_service`, `agent_computer_approval_decision_service`, `agent_computer_profile_service`, `agent_computer_permission_secret_model`, `capability_risk_classifier_service`, `hardware_action_broker_service`, `hardware_access_policy_service`, `hardware_runtime_session_service`, `hardware_runtime_target_resolver`, `hardware_result_correlator_service`, `hardware_runtime_adapters/` (cloud, gateway, self-hosted), `safe_mode_service`, `kill_switch_gate`, `security_audit_service`, `browser_engine`, `browser_approval_service`, `browser_checkpoint_service`, `computer_control`, `computer_action_safety`

**Channel Domain:**
`channel_execution_service`, `channel_routing_models`, `channel_lane_contract_service`, `channel_safety_overlay_service`, `channel_memory_overlay_service`, `channel_preflight_service`, `channel_identity_service`, `channel_pairing_service`, `channel_platform_service`, `channel_activity_service`, `channel_blocking_policy_service`, `channel_concurrency_service`, `channel_quota_policy_service`, `channel_turn_request_service`, `channel_surface_contract_service`, `channel_user_acquisition_service`, `channel_event_journal_service`, `channel_owner_resolution_service`, `channel_execution_quota_adapter`, `connectors/` (Telegram, WhatsApp, Slack, Discord, Email, GitHub, Notion, Linear, Dropbox, S3)

**Runtime Domain:**
`run_service`, `runs_core`, `runs_engine`, `runs_execution`, `runs_delegation`, `runs_history`, `runs_output`, `runtime_models`, `runtime_config`, `runtime_policy`, `runtime_events`, `runtime_events_api`, `runtime_status`, `runtime_common`, `runtime_heartbeat_service`, `runtime_history_service`, `runtime_request_service`, `runtime_usage_service`, `runtime_attachment_service`, `runtime_workspace_service`, `runtime_webhook_trigger_service`, `runtime_local_execution_approval_service`, `session_manager/` (actor queue, manager, observability, runtime cache, types), `execution_router`, `execution_mode_policy`, `execution_sandbox_service`

**Billing Domain:**
`billing_service`, `billing_credit_config`, `credit_ledger_contract`, `pricing_registry_service`

**Auth Domain:**
`auth`, `account_shell_service`, `jwt_secret`, `session_service`

**Platform Operations:**
`platform_analytics_service`, `pilot_invite_service`, `pilot_operations_service`, `pilot_proof_service`, `health_core`, `health_diagnostics`, `doctor_gate`, `doctor_report`, `telemetry`, `config_loader`, `config_defaults_service`, `data_retention_service`, `retention_enforcement_job`, `product_catalog_live_data_service`

**AI/Model Infrastructure:**
`model_router`, `provider_catalog_service`, `provider_profiles`, `multimodal_provider_service`, `no_provider_service`, `empyralis_model_tier_contract`, `empyralis_model_tier_routing_service`, `direct_chat_service`, `direct_chat_runtime_service`, `direct_chat_stream_runtime_service`, `direct_chat_generation_service`, `direct_chat_prompt_service`, `direct_chat_provider_service`, `direct_chat_routing_service`, `direct_chat_composition_service`, `direct_chat_response_service`, `direct_chat_transport_service`, `direct_chat_context_service`, `direct_chat_memory_facade_service`, `direct_chat_handoff_service`, `direct_chat_tool_catalog_service`, `llm_task`

**General Infrastructure:**
`memory_service`, `memory_summary_service`, `conversation_memory_facade_service`, `conversation_memory_policy`, `conversation_compaction`, `agent_memory`, `agent_turn`, `agent_transparency_events`, `agent_trace_service`, `agent_manifest`, `agent_action_metering_service`, `activity_ledger_service`, `notification_service`, `artifact_service`, `approval_contracts`, `policy_service`, `quota_policy_service`, `quota_response_service`, `entitlements_service`, `egress_policy`, `error_contracts`, `error_response_service`, `secrets_broker`, `secret_redaction_service`, `response_leak_guard_service`, `external_content_guard`, `external_write_safety`, `file_bridge_service`, `file_mount_security`, `knowledge_rag_service`, `skills_registry`, `skills_service`, `skill_registry`, `skill_scanner`, `installed_skills`, `installed_solutions`, `capability_registry`, `mcp_registry_service`, `connector_manifests`, `connector_metadata`, `connector_validators`, `connectors_core`, `connectors_actions`, `template_compiler_service`, `automation_intents`, `autopilot_connectors`, `outbox_service`, `idempotency`, `downstream_resilience_service`, `machine_lease_service`, `machine_capability_check`, `bounded_scheduler_service`, `local_queue`, `hosted_secure_worker`, `blackbox_runtime_support`, `agent_channel_router`, `supervisor_client`, `customer_ops_pack`, `outcome_packs`, `demo_workflows`, `setup_sessions`

---

## Reference Materials

Platform screenshots are maintained in `docs/references/visuals/` (11 PNG files at 1254×1254 resolution). These capture the primary workspace surfaces and should be updated when the UI changes materially.

---

*Document last updated: 2026-06-23. Reflects platform state and ground-truth decisions current as of that date.*
