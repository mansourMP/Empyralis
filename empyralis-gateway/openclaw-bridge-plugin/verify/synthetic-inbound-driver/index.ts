import { appendFileSync } from "node:fs";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
// @ts-ignore - no local ambient types for this subpath; this is throwaway verification code, never shipped.
import { getGlobalHookRunner } from "openclaw/plugin-sdk/plugin-runtime";

// Was a hardcoded path into a since-deleted worktree. Env-driven now so this
// harness is runnable from any checkout (step 2 re-ran it).
const RESULT_LOG =
  process.env.EMPYRALIS_VERIFY_DRIVER_LOG ?? "/tmp/empyralis-openclaw-verify/driver-results.jsonl";

function record(entry: unknown) {
  appendFileSync(RESULT_LOG, JSON.stringify(entry) + "\n");
}

const REAL_CAPTURED_FAILOVER_TEXT =
  'FailoverError: No API key found for provider "openai". Auth store: /Users/mansur/.openclaw-empyralis-bridge-verify/agents/main/agent/openclaw-agent.sqlite (agentDir: /Users/mansur/.openclaw-empyralis-bridge-verify/agents/main/agent). Configure auth for this agent (openclaw --profile empyralis-bridge-verify agents add <id>) or copy only portable static auth profiles from the main agentDir.';

export default definePluginEntry({
  id: "synthetic-inbound-driver",
  name: "Synthetic Inbound Driver",
  register(api) {
    api.on("gateway_start", async () => {
      // Let every plugin (including empyralis-bridge) finish registering first.
      setTimeout(async () => {
        try {
          const runner = getGlobalHookRunner();
          if (!runner) {
            record({ step: "getGlobalHookRunner", ok: false, error: "no global hook runner" });
            return;
          }

          // 1) A realistic message_received event, shaped exactly like
          // toPluginMessageReceivedEvent's real output (audited from
          // dist/message-hook-mappers-*.js).
          //
          // Channel is `line`: one of the OpenClaw-transported channels
          // Empyralis actually registers (channel_lane_contract_service's
          // OPENCLAW_PERSONAL_CHANNEL_SPECS). `channelName` is absent, which
          // reproduces THE case step 2 is about — OpenClaw's group-ness fact
          // (GroupSubject) never reaches this hook, so the bridge can only
          // report isGroup: undefined and the cloud must fail closed.
          const receivedEvent = {
            from: "+15555550199",
            content: "hello from the synthetic inbound driver — verification probe",
            timestamp: Date.now(),
            threadId: undefined,
            messageId: "synthetic-msg-1",
            senderId: "synthetic-sender-1",
            sessionKey: "agent:main:+15555550199",
            runId: undefined,
            metadata: {
              to: "main-bot",
              provider: "line",
              surface: "line",
              channelName: undefined,
              senderName: "Synthetic Sender",
            },
          };
          const receivedCtx = {
            channelId: "line",
            accountId: "synthetic-account",
            conversationId: "synthetic-conversation-1",
            sessionKey: "agent:main:+15555550199",
          };
          await runner.runMessageReceived(receivedEvent, receivedCtx);
          record({ step: "runMessageReceived", ok: true, event: receivedEvent, ctx: receivedCtx });

          // 1b) The same hook, but with the one group signal that DOES
          // survive into message_received metadata (`channelName` <-
          // ctx.GroupChannel). Proves the mapper reports isGroup: true when
          // there is positive evidence, and only then.
          const groupEvent = {
            ...receivedEvent,
            messageId: "synthetic-msg-2",
            content: "who is this bot",
            metadata: { ...receivedEvent.metadata, channelName: "#general" },
          };
          const groupCtx = { ...receivedCtx, conversationId: "synthetic-group-1" };
          await runner.runMessageReceived(groupEvent, groupCtx);
          record({ step: "runMessageReceived(group)", ok: true, event: groupEvent, ctx: groupCtx });

          // 2) message_sending fed the REAL captured synthetic FailoverError
          // text (copied verbatim from this profile's own gateway.log
          // earlier this session) - empyralis-bridge's handler must cancel.
          const sendingEventShouldCancel = {
            to: "synthetic-conversation-1",
            content: REAL_CAPTURED_FAILOVER_TEXT,
            replyToId: undefined,
            threadId: undefined,
            metadata: { channel: "telegram" },
          };
          const cancelResult = await runner.runMessageSending(sendingEventShouldCancel, receivedCtx);
          record({ step: "runMessageSending(shouldCancel)", ok: true, result: cancelResult });

          // 3) message_sending fed a normal, real-looking reply - must NOT
          // be cancelled.
          const sendingEventShouldPass = {
            to: "synthetic-conversation-1",
            content: "Sure, here's the answer you asked for.",
            replyToId: undefined,
            threadId: undefined,
            metadata: { channel: "telegram" },
          };
          const passResult = await runner.runMessageSending(sendingEventShouldPass, receivedCtx);
          record({ step: "runMessageSending(shouldPass)", ok: true, result: passResult });

          record({ step: "done", ok: true });
        } catch (err) {
          record({ step: "error", ok: false, error: err instanceof Error ? `${err.name}: ${err.message}` : String(err) });
        }
      }, 1500);
    });
  },
});
