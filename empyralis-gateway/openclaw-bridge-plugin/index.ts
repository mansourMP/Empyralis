/**
 * Empyralis Bridge — an OpenClaw plugin that turns the OpenClaw gateway
 * running it into a channel transport for Empyralis.
 *
 * See ../../CHANNEL-ADOPTION-PLAN.md for the full design and the reason
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
import type { EmpyralisInboundPayload } from "./src/inbound-mapping.js";

const PLUGIN_ID = "empyralis-bridge";

function log(message: string): void {
  // eslint-disable-next-line no-console -- plugin has no other logging channel; never pass secrets here.
  console.log(`[${PLUGIN_ID}] ${message}`);
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Empyralis Bridge",
  description:
    "Forwards inbound channel messages to Empyralis and suppresses OpenClaw's own (intentionally credential-less) agent replies.",
  register(api) {
    let queue: BoundedRetryQueue<EmpyralisInboundPayload> | null = null;

    api.on(
      "gateway_start",
      async (_event, ctx: PluginHookGatewayContext) => {
        const config = loadBridgeConfig(
          process.env,
          ctx.workspaceDir ? path.join(ctx.workspaceDir, "empyralis-bridge") : path.join(os.tmpdir(), "empyralis-bridge"),
        );
        queue = new BoundedRetryQueue<EmpyralisInboundPayload>({
          filePath: config.queueFilePath,
          maxItems: config.queueMaxItems,
          maxAttempts: config.queueMaxAttempts,
          minBackoffMs: config.queueMinBackoffMs,
          maxBackoffMs: config.queueMaxBackoffMs,
          send: (payload) => forwardInboundEvent(config, payload),
          log,
        });
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
      queue?.stop();
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
        if (!queue) {
          // gateway_start has not completed yet. This should not happen in
          // practice (activation.onStartup runs gateway_start first) but if
          // it ever does, do not silently drop the message — log loudly.
          // There is nowhere durable to put it without a queue, so it is
          // lost; that loss is visible in the gateway's own log, not
          // invisible.
          log(
            `message_received arrived before gateway_start finished; dropping one inbound event (channel=${ctx.channelId ?? "unknown"}). This should never happen — investigate plugin load order if it does.`,
          );
          return;
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
