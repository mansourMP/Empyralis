# Sage Runtime

Status: Active
Owner: Platform
Last verified: 2026-06-07
Source of truth: Sage runtime and Agent Computer code

## Cloud Default

The inspected `/api/sage/chat` path is cloud-provider backed by default.
`server_modules/sage_agent_runtime_service.py` resolves a provider from
workspace credentials in priority order `anthropic`, `deepseek`, `openai`,
`gemini`, then calls `generate_chat_reply_with_provider_fallback()` with
`disable_provider_fallback=True` in the context.

For action-shaped prompts, `handle_sage_chat()` enters the Sage operator loop
v3 before the plain text-generation path. That loop routes through the
provider-backed direct-chat tool stream so Sage can plan a tool, receive the
tool result, and finish with the result in context. It covers web search, web
fetch, guarded direct tools, approval handoff, and compatible MCP skill
execution. It returns real `tool_calls`, `blocked_tools`,
`approvals_required`, `action_execution_mode`, and loop budget metadata.

## Daily Operator Recipes

Sage has a dedicated Daily Operator recipe path before the generic provider
loop. Source: `server_modules/sage_daily_operator_service.py`, called from
`server_modules/sage_agent_runtime_service.py`.

Certified recipe v1 lanes:

- Morning brief: uses `google_workspace__fetch_emails` and
  `google_workspace__list_calendar_events`.
- Email triage: uses `google_workspace__fetch_emails`; drafting/sending remains
  approval-gated through `google_workspace__draft_email`.
- Meeting prep: uses `google_workspace__list_calendar_events` by default.
  `google_workspace__list_drive_files` is used only when Drive scope is
  explicitly enabled and verified; calendar writes remain approval-gated.

These recipes run only when the required read tools are available. If Gmail or
Calendar access is missing, Sage returns `daily_operator_blocked` with concrete
`blocked_tools`; it must not fabricate brief, email, or calendar context. If
Drive access is missing, Drive context is skipped unless the selected recipe
explicitly requires it. Read tool outputs are sanitized and included in direct-tool
result text so the operator can reason over real connector data. Every recipe
result also returns a structured `proof_log` with what Sage checked, what
changed, what could not run, and why approval is needed. External writes and
recurring recipe scheduling return `approvals_required` metadata instead of
dispatching immediately, and the `proof_log.changes` list remains empty until a
write is actually approved and dispatched.

Sage action results with a `proof_log` are also persisted through
`server_modules/sage_proof_log_service.py` after Rust runtime state-store
authorization. Proof records are idempotent by Sage trace id, sanitized before
write, and queryable through:

- `GET /api/sage/proof-logs?workspace_id=...`
- `GET /api/sage/proof-logs/summary?workspace_id=...`
- `GET /api/sage/proof-logs/{proof_id}?workspace_id=...`

The chat response includes `proof_log_id` when persistence succeeds, so web,
mobile, and channel surfaces can link a conversation turn to its durable proof
record instead of scraping assistant text.

## Task Routing Contract

Sage should route work in this order:

- `chat_only`: answer directly in chat for writing, reasoning, uploaded-file
  work, and memory-backed answers.
- `connector_api`: use OAuth/API connectors, MCP tools, web search/fetch,
  reminders, and recipes before any computer runtime.
- `cloud_browser`: use a hosted browser for websites that do not need local
  browser state.
- `cloud_computer`: use an isolated cloud computer for heavier jobs, code, files,
  terminal, or browser automation that does not need the user's machine.
- `gateway_required`: use Agent Computer only for local files, local apps, local
  browser profiles/cookies, personal Telegram (personal MTProto)/iMessage/Signal/WeChat,
  (Note: WhatsApp personal is not a supported channel)
  local network, SSH keys, secrets on the user's machine, desktop control, or
  dedicated hardware.

This route decision is emitted by `server_modules/sage_agent_runtime_service.py`
as `route_decision` with the customer-facing labels `Basic Assistant`,
`Connected Assistant`, and `Computer Assistant`.

`server_modules/direct_chat_provider_service.py` separates credential planes:
workspace BYOK credentials, platform runtime credentials, and local runtime
credentials. Platform runtime credentials are filtered out when hosted Sage AI
is not allowed for the workspace.

## Agent Computer Requirement

Agent Computer is required for local files, local shell, local browser sessions,
screenshot/computer-control on the user's machine, local network access, and
personal-channel sessions. Normal website automation should use hosted browser
first unless the user explicitly needs the local signed-in browser. Tool intent
detection for local file/shell/screenshot/computer requests lives in
`server_modules/direct_chat_tool_catalog_service.py`; actual gateway execution
is guarded in `server_modules/routes_gateway.py` and personal-channel services.

`/api/sage/chat` can plan and request guarded local actions, but successful
execution still requires the selected gateway/Agent Computer path and the
direct-tool approval layer. When Agent Computer is offline, browser/shell/file
requests are blocked with runtime-unavailable tool records rather than fake
assistant text.

## Offline Behavior

Capability truth marks local tools available only when runtime health and either
local gateway or local worker online state are true. Otherwise the inventory
reply says This Device capabilities require Agent Computer to be online. Source:
`server_modules/direct_chat_provider_service.py` and
`server_modules/direct_chat_tool_catalog_service.py`.

Hosted browser fallback is not a downgrade path from local-private work. It is
the default path for public or cloud-login websites that do not require the
user's local browser profile, local cookies, or machine-local environment.

## Access Modes

Product contract from `docs/domains/agent-computer/runtime.md`:

- Default: guarded Agent Computer access; risky local, send, install, payment,
  deploy, credential, and system actions require approval.
- Custom: guarded access with editable grants; missing custom policy behaves
  like Default.
- Full Access: Sage can use the selected Agent Computer without per-action
  Empyralis approval prompts, including broad filesystem, shell, browser data,
  token, SSH key, secret, delete, and connected-account access.

Do not weaken Full Access in code or docs. The controls are explicit consent,
Sage-only scope, signed requests, replay protection, audit, revocation, kill
switch, OS permission enforcement, and dedicated-hardware recommendation.

## Separation From Studio

Sage runtime is the owner/personal-agent contract. Studio agents are separate
deployed-agent runtime contracts, even when they eventually run work on the same
physical Agent Computer. Sage Agent Computer selection, Sage memory, personal
channel sessions, owner browser state, and Sage Full Access approval must not be
copied into Studio runtime bindings.

Code-level enforcement appears in two layers:

- Gateway dispatch requires Full Access to be Sage scoped. Source:
  `server_modules/gateway_execution_service.py`.
- The Rust supervisor rejects `full_access` unless the request resolves to
  `agent_scope=sage` and carries the Sage setup warning acknowledgement. Source:
  `empyralis-supervisor/src/main.rs`.

## ACP Bridge

`/gateway/acp/turn` accepts authenticated ACP `agent.turn`, checks that any
payload workspace matches the authenticated workspace, enforces the Rust
gateway action decision, then calls `handle_sage_chat(surface="acp")`.
Non-implemented ACP message types return `501`. Source:
`server_modules/routes_gateway.py`.

Migration debt: `normalize_sage_surface()` in
`server_modules/sage_agent_runtime_contract.py` does not list `acp` as an
allowed surface, while `/gateway/acp/turn` passes `surface="acp"` into
`handle_sage_chat()`. The gateway tests cover the Rust gate, but this surface
contract mismatch should be reconciled.
