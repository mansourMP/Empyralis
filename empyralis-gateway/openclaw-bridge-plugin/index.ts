/**
 * Empyralis Bridge — an OpenClaw plugin that turns the OpenClaw gateway
 * running it into a channel transport for Empyralis.
 *
 * See the OpenClaw channel adoption for the full design and the reason
 * this exists at all (do not relitigate it here). Short version:
 *
 *   inbound   OpenClaw receives a channel message
 *                -> message_received fires unconditionally
 *                -> mapped and queued for delivery to Empyralis
 *   outbound  Empyralis calls OpenClaw's own `message.action` RPC directly
 *                (not through this plugin — see the outbound section of
 *                the task report; this plugin does not originate sends)
 *   suppress  the model turn fails on purpose (no provider credentials are
 *             ever configured on this gateway, by design)
 *                -> message_sending fires with OpenClaw's own formatted
 *                   FailoverError text
 *                -> this plugin cancels only that specific reply
 *
 * Every field this file touches on `event`/`ctx` is transcribed from
 * OpenClaw's own shipped type declarations — see
 * types/openclaw-plugin-sdk.d.ts's header comment for the exact audit
 * trail and its staleness caveat.
 */

import * as os from "node:os";
import * as path from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import type {
  PluginHookGatewayContext,
  PluginHookMessageContext,
  PluginHookMessageReceivedEvent,
  PluginHookMessageSendingEvent,
  PluginHookMessageSendingResult,
} from "openclaw/plugin-sdk/plugin-entry";

import { loadBridgeConfig } from "./src/config.js";
import { mapInboundEvent } from "./src/inbound-mapping.js";
import { isSuppressedCredentiallessTurnReply, CANCEL_REASON } from "./src/cancel-predicate.js";
import { BoundedRetryQueue } from "./src/queue.js";
import { forwardInboundEvent } from "./src/forward.js";
import { registerChannelLoginRoute } from "./src/channel-login.js";
import type { EmpyralisInboundPayload } from "./src/inbound-mapping.js";

const PLUGIN_ID = "empyralis-bridge";

function log(message: string): void {
  // eslint-disable-next-line no-console -- plugin has no other logging channel; never pass secrets here.
  console.log(`[${PLUGIN_ID}] ${message}`);
}

/**
 * MODULE scope, not `register()` scope, and that distinction cost a real
 * customer message.
 *
 * The queue used to be a `let` inside `register(api)`, so every handler read
 * the closure of the registration that installed it. Observed live on
 * 2026-08-15 with a real Telegram message from the owner's own account:
 *
 *   [telegram] Inbound message telegram:1932934047 -> @…Bot (direct, 62 chars)
 *   [empyralis-bridge] message_received arrived before gateway_start finished;
 *                      dropping one inbound event (channel=telegram)
 *
 * — seven minutes AFTER `gateway_start` had run and logged. A single
 * registration cannot produce that ordering, so the `message_received`
 * handler was reading a different closure than the `gateway_start` that
 * built the queue.
 *
 * Module scope makes the queue shared by construction, so a second
 * registration (whatever produces it — load path plus an entry, a reload, a
 * future host change) can no longer see a null one.
 */
let sharedQueue: BoundedRetryQueue<EmpyralisInboundPayload> | null = null;

/** Build the queue if it does not exist yet, and return it.
 *
 *  Idempotent and safe to call from either hook: the point is that an
 *  inbound message must NEVER be dropped because of plugin load order. The
 *  old code logged "this should never happen" and threw the message away —
 *  a customer's message lost to an ordering assumption, with the loss
 *  visible only in a log nobody reads. There is a durable file-backed queue
 *  here; the correct response to "the queue is not up yet" is to bring it
 *  up, not to discard the thing it exists to protect. */
function ensureQueue(ctx: { workspaceDir?: string }): BoundedRetryQueue<EmpyralisInboundPayload> {
  if (sharedQueue) return sharedQueue;
  const config = loadBridgeConfig(
    process.env,
    ctx.workspaceDir
      ? path.join(ctx.workspaceDir, "empyralis-bridge")
      : path.join(os.tmpdir(), "empyralis-bridge"),
  );
  sharedQueue = new BoundedRetryQueue<EmpyralisInboundPayload>({
    filePath: config.queueFilePath,
    maxItems: config.queueMaxItems,
    maxAttempts: config.queueMaxAttempts,
    minBackoffMs: config.queueMinBackoffMs,
    maxBackoffMs: config.queueMaxBackoffMs,
    send: (payload) => forwardInboundEvent(config, payload),
    log,
  });
  return sharedQueue;
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Empyralis Bridge",
  description:
    "Forwards inbound channel messages to Empyralis and suppresses OpenClaw's own (intentionally credential-less) agent replies.",
  register(api) {
    // Linking a channel by QR, from the browser. See src/channel-login.ts for
    // why this is a gateway-authenticated HTTP route (OpenClaw's own required
    // shape for in-process method dispatch) rather than a WS method, and for
    // exactly what it does and does not change about the trust boundary.
    registerChannelLoginRoute(api, log);

    api.on(
      "gateway_start",
      async (_event, ctx: PluginHookGatewayContext) => {
        const config = loadBridgeConfig(
          process.env,
          ctx.workspaceDir ? path.join(ctx.workspaceDir, "empyralis-bridge") : path.join(os.tmpdir(), "empyralis-bridge"),
        );
        const queue = ensureQueue(ctx);
        await queue.load();
        queue.start(config.queueFlushIntervalMs);
        log(
          `gateway_start: forwarding inbound events to ${config.endpointUrl} ` +
            `(queue=${config.queueFilePath}, max=${config.queueMaxItems} items)`,
        );
      },
      { priority: 0 },
    );

    api.on("gateway_stop", async () => {
      sharedQueue?.stop();
      log("gateway_stop: queue flush loop stopped");
    });

    // The tap.
    //
    // CORRECTED 2026-08-08 (step 2), replacing an earlier claim here that
    // this "fires unconditionally on every inbound message". It does not.
    // The hook call site in `dispatch-B2e1grFo.js:1240` is unconditional,
    // but reaching that call site is not: OpenClaw's channel ingress runs
    // its OWN gates first and drops or skips before dispatch is ever
    // invoked. Evidence, from openclaw@2026.6.10's shipped bundle:
    //
    //   message-access-CeqV-XzC.js  `decideChannelIngress` returns
    //     admission "drop" | "skip" | "pairing-required" with gate effects
    //     literally named `block-dispatch`.
    //   message-handler.preflight-DjLxkMPn.js:1009  Discord's preflight
    //     `return null` on a mention miss; message-handler-BlOnGv8G.js:367
    //     then never enqueues the job that would dispatch.
    //   bot-Dxj27QDQ.js:4132  Telegram's mention miss `return null`s too,
    //     and on that path fires ONLY the internal hook, never the plugin
    //     `message_received` one.
    //   docs/plugins/sdk-channel-ingress.md  "A mention miss returns
    //     admission: 'skip' so the turn kernel does not process an
    //     observe-only turn."
    //
    // So this tap sees post-gate traffic, and it sees it WITHOUT the gate
    // facts (`toPluginMessageReceivedEvent` forwards neither `isGroup` nor
    // `wasMentioned`, unlike its sibling `toPluginInboundClaimEvent`).
    // Empyralis therefore re-decides everything cloud-side and treats
    // unknown group-ness as a group — see
    // personal_channels_service.normalize_openclaw_gate_facts.
    //
    // Also NOT universal: WhatsApp suppresses this hook entirely unless
    // `channels.whatsapp.pluginHooks.messageReceived: true` is set
    // (docs/channels/whatsapp.md, "Plugin hooks and privacy"). Provisioning
    // must set it; nothing here can compensate for its absence.
    api.on(
      "message_received",
      async (event: PluginHookMessageReceivedEvent, ctx: PluginHookMessageContext) => {
        const payload = mapInboundEvent(event, ctx);
        // Bring the queue up rather than discarding the message. The old
        // code logged "this should never happen" and dropped it — and then
        // it happened, on a real message, seven minutes after gateway_start
        // had already run (see the sharedQueue comment above). A message the
        // customer sent is the one thing here that cannot be recreated; the
        // queue can. Recovering costs one lazy construction, and the durable
        // file-backed queue then carries the message exactly as it would
        // have if the ordering had held.
        let queue = sharedQueue;
        if (!queue) {
          log(
            `message_received arrived before gateway_start finished (channel=${ctx.channelId ?? "unknown"}); starting the queue now rather than dropping the message.`,
          );
          queue = ensureQueue(ctx as unknown as { workspaceDir?: string });
          await queue.load();
        }
        await queue.enqueue(payload);
      },
      { priority: 0 },
    );

    // The suppression. See src/cancel-predicate.ts for exactly why this is
    // a content match and what guards it.
    api.on(
      "message_sending",
      (event: PluginHookMessageSendingEvent): PluginHookMessageSendingResult | undefined => {
        if (isSuppressedCredentiallessTurnReply(event)) {
          return { cancel: true, cancelReason: CANCEL_REASON };
        }
        return undefined;
      },
      { priority: 100 },
    );
  },
});
