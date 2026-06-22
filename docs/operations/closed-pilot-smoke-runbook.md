# Closed Pilot Smoke Runbook

This runbook is the launch proof for the smallest Empyralis pilot wedge. It is not a demo script and must be run against a real local stack before claiming closed-pilot readiness.

## Scope

Primary smoke path:

- Web Chat to Sage.
- Real paired local Gateway.
- One approval-required local browser action.
- Activity/audit lookup by `trace_id`.
- Kill switch blocks the next local action.

Follow-up channel smoke:

- Telegram inbound message through the real channel connector (hosted bot or personal MTProto).
- Run only when live channel credentials and test numbers are available.
- WhatsApp is not a supported channel. Do not include it in smoke testing.

## Prerequisites

- Backend, frontend, and Gateway are running against the same workspace.
- A local Gateway is paired, verified, online, and visible as healthy for the workspace.
- Sage has profile files available where configured: `USER.md`, `IDENTITY.md`, `SOUL.md`, `HEARTBEAT.md`, `MEMORY.md`.
- An LLM provider is configured for Sage.
- The operator has owner/admin access to the workspace.
- No production/staging runtime points at localhost unless this is a local-only smoke environment.

## Smoke Steps

1. Open the Sage web chat for the pilot workspace.
2. Send a message that requires Sage to load profile, memory, and heartbeat context.
3. Confirm the response returns a `trace_id`.
4. Ask Sage to perform one local browser action through My Computer/Gateway that is risky enough to require approval, for example clicking a submit/send button in a controlled test page.
5. Confirm Sage returns an approval-required response and does not execute the action yet.
6. Approve the action through the approval surface or API.
7. Confirm Gateway executes the approved action and returns the result to Sage.
8. Confirm Sage sends the final user-facing response.
9. Search Activity/Safety for the `trace_id`.
10. Confirm the event trail shows the user message, context load, approval requested, approval approved, Gateway execution, Sage response, and audit records.
11. Enable the per-agent or Gateway kill switch.
12. Attempt the same local action again.
13. Confirm execution is blocked and the blocked decision appears in audit/activity.

## Required Evidence

Record these values before marking the smoke passed:

- Workspace ID.
- Actor/user ID.
- Gateway ID.
- Sage chat `trace_id`.
- Approval token or approval record ID.
- Gateway request/run ID.
- Activity event IDs for approval and execution.
- Audit event IDs for approval, execution, and kill-switch block.
- Final status: passed or failed.

## Pass Criteria

- Sage responds with a real provider-backed answer.
- Sage context includes profile, memory, and heartbeat inputs where available.
- Restricted memory is not visible in the prompt/response evidence.
- Risky local action does not execute before approval.
- Approved action executes through the paired Gateway, not cloud fallback.
- Denied or disabled approval blocks execution.
- The `trace_id` is searchable in Activity/Safety.
- Kill switch blocks the next local action.
- No raw secrets appear in events, audit payloads, or user-visible errors.

## Fail Criteria

- Cloud fallback is used for My Computer/Gateway execution.
- A risky browser/local action executes with `interactive_approvals=false`.
- A Gateway-offline path returns cloud fallback by default.
- Activity or audit is missing for approval, execution, failure, or kill-switch denial.
- A production/staging Cloud Computer session uses `InMemoryVirtualComputerRuntime`.
- Sage returns a raw stack trace or leaks restricted memory.

## Telegram/WhatsApp Follow-Up

When live channel credentials are available, rerun the same flow with one Telegram inbound message
(via the hosted bot or Telegram personal MTProto if available):

- Confirm the channel resolves to the correct owner workspace.
- Confirm Sage returns a response with `trace_id`.
- Confirm the same Activity/Safety trace links the inbound channel event to the Sage response.
- Do not mark this follow-up as passed using mocked inbound messages.

Note: WhatsApp is not a supported channel. Do not include WhatsApp in pilot smoke testing.
