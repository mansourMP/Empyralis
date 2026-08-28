/**
 * Runs the REAL empyralis-gateway OpenClawGatewayClient (compiled from
 * src/openclaw/openclaw-gateway-client.ts) against a REAL OpenClaw gateway,
 * and invokes the REAL `message.action` RPC — the OpenClaw channel adoption
 * step 3's live proof.
 *
 * It lives beside the step-1/2 harnesses because this directory is the
 * OpenClaw live-verification family, not because it is plugin code: the
 * plugin does not originate sends, the gateway does.
 *
 * WHAT THIS PROVES / DOES NOT PROVE
 * --------------------------------
 * Proves: our client completes OpenClaw's real connect.challenge -> connect
 * -> hello-ok handshake with token auth at operator.write, that
 * `message.action` is reachable at that scope, and that an unconfigured
 * channel comes back as a clean STRUCTURED rejection which
 * classifyOpenClawSendResponse turns into `rejected/INVALID_REQUEST`.
 *
 * Does NOT prove: actual delivery to a real Feishu/LINE/QQ/Zalo/Teams
 * account. That needs real channel credentials and is step 5.
 *
 * Usage (isolated profile ONLY — never ~/.openclaw or ~/.openclaw-dev):
 *   sed "s|<REPO>|$PWD|g" verify/config.outbound.patch.json5 > /tmp/oc.json5
 *   openclaw --profile empyralis-outbound-verify config patch --file /tmp/oc.json5
 *   openclaw --profile empyralis-outbound-verify gateway run \
 *     --port 19299 --bind loopback --auth token --token verify-loopback-secret
 *   # then, in another shell:
 *   cd empyralis-gateway && npm run build
 *   EMPYRALIS_OPENCLAW_GATEWAY_URL=ws://127.0.0.1:19299 \
 *   EMPYRALIS_OPENCLAW_GATEWAY_TOKEN=verify-loopback-secret \
 *     node openclaw-bridge-plugin/verify/gateway-outbound-harness.mjs
 *
 * Tear down: stop the gateway, then `rm -rf ~/.openclaw-empyralis-outbound-verify`.
 */

import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { OpenClawGatewayClient } = require("../../dist/openclaw/openclaw-gateway-client.js");
const { mapOpenClawOutboundPayload } = require("../../dist/openclaw/outbound-payload.js");

const url = String(process.env.EMPYRALIS_OPENCLAW_GATEWAY_URL || "ws://127.0.0.1:19299").trim();
const token = String(process.env.EMPYRALIS_OPENCLAW_GATEWAY_TOKEN || "").trim();
if (!token) {
  console.error("EMPYRALIS_OPENCLAW_GATEWAY_TOKEN is required (the client refuses to run without it).");
  process.exit(1);
}
const channelKey = String(process.env.EMPYRALIS_VERIFY_CHANNEL_KEY || "openclaw_line").trim();

const journal = [];
const client = new OpenClawGatewayClient({
  url,
  token,
  record: async (messageType, payload) => {
    journal.push({ messageType, payload });
    console.log(`[journal] ${messageType} ${JSON.stringify(payload)}`);
  },
  logger: { info: (m) => console.log(`[client] ${m}`), error: (m) => console.error(`[client] ${m}`) },
});

// The exact payload shape gateway_protocol_service.dispatch_channel_outbound
// puts on the `channel.outbound` frame.
const mapped = mapOpenClawOutboundPayload({
  channel_key: channelKey,
  provider: "openclaw",
  remote_jid: "C-verify-1",
  text: "Empyralis outbound verification probe.",
  idempotency_key: `verify-${Date.now()}`,
});
if (!mapped.ok) {
  console.error(`mapping refused: ${mapped.error}`);
  process.exit(1);
}

// Every timer inside OpenClawGatewayClient is unref'd on purpose — in the
// real gateway the process is held open by the cloud WebSocket, and a
// reconnect backoff must never be the thing keeping it alive. A standalone
// harness has no such anchor, so it supplies its own; without this, a run
// against a gateway that rejects the token exits mid-retry and prints
// nothing.
const keepAlive = setInterval(() => {}, 1_000);

await client.start();
// Give the handshake a moment; sendMessageAction also waits internally.
const outcome = await client.sendMessageAction(mapped.request);
console.log("");
console.log("=== RESULT ===");
console.log(`connected:      ${client.isConnected()}`);
console.log(`state:          ${JSON.stringify(client.getState())}`);
console.log(`outcome.status: ${outcome.status}`);
if (outcome.status !== "delivered") {
  console.log(`outcome.code:   ${outcome.code}`);
  console.log(`outcome.msg:    ${outcome.message}`);
}
await client.stop();
clearInterval(keepAlive);
// A clean structured rejection is the expected result without channel
// credentials; a transport error is a FAILURE of this harness.
process.exit(outcome.status === "transient" ? 1 : 0);
