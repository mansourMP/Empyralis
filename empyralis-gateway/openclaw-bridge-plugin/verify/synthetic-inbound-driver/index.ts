import { appendFileSync } from "node:fs";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
// @ts-ignore - no local ambient types for this subpath; this is throwaway verification code, never shipped.
import { getGlobalHookRunner } from "openclaw/plugin-sdk/plugin-runtime";

const RESULT_LOG = "/Users/mansur/empyralis/.claude/worktrees/feat-openclaw-bridge/.verify/driver-results.jsonl";

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
          // dist/message-hook-mappers-*.js earlier this session).
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
              provider: "telegram",
              surface: "telegram",
              channelName: undefined,
              senderName: "Synthetic Sender",
            },
          };
          const receivedCtx = {
            channelId: "telegram",
            accountId: "synthetic-account",
            conversationId: "synthetic-conversation-1",
            sessionKey: "agent:main:+15555550199",
          };
          await runner.runMessageReceived(receivedEvent, receivedCtx);
          record({ step: "runMessageReceived", ok: true, event: receivedEvent, ctx: receivedCtx });

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
