/**
 * Decides whether an outbound `message_sending` event is the synthetic
 * "no model credentials configured" failure OpenClaw's own embedded agent
 * loop produces on every inbound turn (the deliberate hack this bridge
 * relies on — see CHANNEL-ADOPTION-PLAN.md's "THE HACK" section) versus a
 * real reply that must never be touched.
 *
 * WHY THIS IS A STRING MATCH, NOT A STRUCTURAL CHECK (read before editing):
 *
 * `message_sending`'s real, shipped event shape is exactly
 * `{ to, content, replyToId, threadId, metadata }` — confirmed against
 * `dist/plugin-sdk/hook-types-*.d.ts`'s `PluginHookMessageSendingEvent` and
 * against `dist/delivery-*.js`'s Telegram delivery path, which populates
 * `metadata` with only `{ channel, mediaUrls, threadId }`. There is no
 * `error`, `outcome`, `origin`, or `stopReason` field on this event — that
 * richer shape exists on `message_sent` (`PluginHookMessageSentEvent.error`)
 * but that hook fires *after* delivery and cannot cancel anything. So the
 * one and only signal available at the one and only point we can cancel is
 * the free-text `content` OpenClaw's own error formatter produced. This is
 * exactly the "stale string matching" failure mode CLAUDE.md warns about —
 * called out explicitly rather than hidden, per this task's instructions.
 *
 * Mitigations against that risk:
 *   1. Match on `FailoverError`, the internal *exception class name* baked
 *      into every one of these messages by `dist/embedded-agent-*.js`'s
 *      `resolveAssistantFailoverErrorMessage` / `formatAssistantErrorText`
 *      path, not a rewording-prone human sentence.
 *   2. Require it together with the fixed prefix OpenClaw's own
 *      `MissingProviderAuthError` formatter emits ("No API key found for
 *      provider"), so a *different* FailoverError (rate limit, context
 *      overflow, etc. — see `dist/errors-*.js`'s `formatAssistantErrorText`)
 *      is never swallowed. Only the exact failure this deployment
 *      deliberately induces (no provider credentials configured, ever, by
 *      design) matches both.
 *   3. Both markers are OpenClaw-internal identifiers that would never
 *      plausibly appear verbatim in a real Empyralis-authored reply, so the
 *      false-positive risk on a *legitimate* message is effectively zero —
 *      the false-negative risk (OpenClaw rewords the message and we stop
 *      suppressing) is the real one, and is exactly why this comment is
 *      loud and why scratchpad/openclaw-issue-draft.md asks OpenClaw for a
 *      structural discriminator instead.
 *
 * If OpenClaw ever adds a structural field to `message_sending` (see the
 * feature request draft), replace this with that field and delete the
 * string match entirely.
 */

const FAILOVER_ERROR_MARKER = "FailoverError";
const MISSING_API_KEY_MARKER = "No API key found for provider";

/**
 * THE FALSE NEGATIVE THE COMMENT ABOVE PREDICTED, CAUGHT IN PRODUCTION.
 *
 * Observed 2026-08-15 on a real Telegram DM from the workspace owner. What
 * OpenClaw's embedded agent actually sent, verbatim:
 *
 *   "⚠️ Something went wrong while processing your request. Please try
 *    again, or use /new to start a fresh session."
 *
 * It contains NEITHER marker above. So the credential-less reply sailed
 * past the cancel predicate and was delivered to the owner's phone, while
 * Empyralis's own real reply — which had been generated correctly and
 * persisted — never appeared. The customer saw an error from a component
 * that is supposed to be a radio, and nothing from the agent.
 *
 * This is the generic wrapper OpenClaw applies when its agent loop fails
 * for a reason its formatter did not special-case. Our instance has no
 * model provider and no brain BY DESIGN, so its agent loop can only ever
 * fail — every assistant-authored outbound from it is illegitimate here.
 *
 * Matching two independent halves (the apology and the /new remediation)
 * rather than one sentence, for the same reason the pair above exists: a
 * reworded apology alone, or a legitimate Empyralis reply that happens to
 * mention /new, must not flip the decision on its own.
 *
 * THIS IS STILL A STRING MATCH AND STILL THE WRONG SHAPE. The right fix is
 * a structural discriminator on `message_sending` — see the feature request
 * in scratchpad/openclaw-issue-draft.md. Until they ship one, every new
 * error wording OpenClaw introduces is a fresh silent regression that
 * reaches a customer's phone before it reaches a log, which is why this
 * block is loud and why the drift test below enumerates both pairs.
 */
const GENERIC_FAILURE_APOLOGY_MARKER = "Something went wrong while processing your request";
const GENERIC_FAILURE_REMEDIATION_MARKER = "/new to start a fresh session";

export interface SendingContentEvent {
  content: string;
}

export function isSuppressedCredentiallessTurnReply(event: SendingContentEvent): boolean {
  const content = event.content ?? "";
  if (content.length === 0) return false;
  if (content.includes(FAILOVER_ERROR_MARKER) && content.includes(MISSING_API_KEY_MARKER)) {
    return true;
  }
  return (
    content.includes(GENERIC_FAILURE_APOLOGY_MARKER) &&
    content.includes(GENERIC_FAILURE_REMEDIATION_MARKER)
  );
}

export const CANCEL_REASON = "empyralis_bridge_suppressed_credentialless_agent_reply";
