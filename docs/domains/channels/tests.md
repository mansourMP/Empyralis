# Channels Tests

Status: Active
Owner: Platform
Last verified: 2026-06-06
Source of truth: channel test files

## Focused Command

Run the focused channel docs scope:

```bash
python3 -m pytest \
  server_modules/tests/test_agent_channel_router.py \
  server_modules/tests/test_channel_platform_service.py \
  server_modules/tests/test_personal_channels_service_rust_gate.py \
  server_modules/tests/test_routes_personal_channels_rust_gate.py
```

## Covered Behavior

`server_modules/tests/test_agent_channel_router.py` covers:

- shell surface contract split between full app shells and lightweight channel
  shells
- successful deployed-agent channel routing and audit ids
- thread-busy/runtime-cap graceful replies
- degraded operation reporting
- unbound endpoint, disabled channel, disabled agent, draft deployment, and
  duplicate inbound handling
- draining, paused, suspended, quota-limited, memory overlay, and health safety
  channel outcomes

`server_modules/tests/test_channel_platform_service.py` covers:

- catalog shape and reserved private runtime items
- secret-ref projection without raw secret leakage
- channel account creation through vault credentials
- per-agent endpoint and secret ref persistence
- rejection of personal Agent Computer channels from Studio binding
- rejection of not-launch-ready bindings
- dry-run-only Studio test sends

`server_modules/tests/test_personal_channels_service_rust_gate.py` covers:

- personal gateway configure decisions requiring `dispatch_gateway_operation`
- Telegram/local-bridge sends blocking before dispatch on wrong Rust next actions
  (WhatsApp tests are legacy — WhatsApp personal is not a supported channel)
- automatic replies blocking before dispatch on wrong Rust next actions

`server_modules/tests/test_routes_personal_channels_rust_gate.py` covers:

- approval request accepts only `request_gateway_owner_approval`
- wrong Rust next action blocks before creating a gateway approval request

## Missing Coverage

Not implemented or not covered in the required focused tests:

- real provider webhook signature tests for every Studio channel in one
  command; they exist in connector-specific tests such as Discord, Slack, and
  routes connector security tests
- end-to-end live personal account certification for Telegram personal (MTProto), Signal,
  iMessage, or WeChat (WhatsApp personal is not a supported channel)
- personal-channel media/attachment/voice/image ingestion, because the inspected
  handlers require text only
- frontend channel pairing surface tests for
  `frontend/lib/workspace/workspace-channel-pairing-surface.tsx`
