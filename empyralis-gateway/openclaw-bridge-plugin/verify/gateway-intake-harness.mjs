/**
 * Runs the REAL empyralis-gateway OpenClawInboundListener (compiled from
 * src/openclaw/inbound-listener.ts) with a recording publisher in place of
 * GatewayWsClient, so a live OpenClaw gateway can POST into it and every
 * mapped GatewayChannelInboundPayload lands in a file the Python half can
 * then replay through the real cloud gate chain.
 *
 * This replaces verify/mock-empyralis-listener.mjs for step 2: that one was
 * a stub that only proved the plugin could reach *something*. This one is
 * the actual shipping code path.
 *
 * Usage:
 *   EMPYRALIS_BRIDGE_TOKEN=<secret> \
 *   EMPYRALIS_BRIDGE_PORT=8790 \
 *   EMPYRALIS_VERIFY_PUBLISHED_LOG=/tmp/.../published.jsonl \
 *     node verify/gateway-intake-harness.mjs
 */

import { appendFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { OpenClawInboundListener } = require("../../dist/openclaw/inbound-listener.js");

const token = String(process.env.EMPYRALIS_BRIDGE_TOKEN || "").trim();
if (!token) {
  console.error("EMPYRALIS_BRIDGE_TOKEN is required (the listener refuses to run without it).");
  process.exit(1);
}
const port = Number.parseInt(process.env.EMPYRALIS_BRIDGE_PORT || "8790", 10);
const publishedLog = process.env.EMPYRALIS_VERIFY_PUBLISHED_LOG || "/tmp/empyralis-openclaw-verify/published.jsonl";
const journalLog = process.env.EMPYRALIS_VERIFY_JOURNAL_LOG || "/tmp/empyralis-openclaw-verify/journal.jsonl";
mkdirSync(path.dirname(publishedLog), { recursive: true });
mkdirSync(path.dirname(journalLog), { recursive: true });

const listener = new OpenClawInboundListener({
  port,
  token,
  publisher: {
    async publishEvent(type, payload) {
      appendFileSync(publishedLog, JSON.stringify({ type, payload }) + "\n");
      console.log(`[harness] published ${type} channel_key=${payload.channel_key}`);
    },
  },
  record: async (messageType, payload) => {
    appendFileSync(journalLog, JSON.stringify({ messageType, payload }) + "\n");
  },
  logger: { info: (m) => console.log(`[harness] ${m}`), error: (m) => console.error(`[harness] ${m}`) },
});

await listener.start();
console.log(`[harness] ready on 127.0.0.1:${port} (bound host: ${listener.boundHost()})`);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    void listener.stop().finally(() => process.exit(0));
  });
}
