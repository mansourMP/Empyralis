/**
 * Shared draft-state management for personal channel outbound messages.
 *
 * Tracks in-progress drafts (draft_start / draft_delta) and handles
 * the draft_final path by delegating to the channel's sendFinalOutbound.
 */

export type ChannelOutboundOperation = "draft_start" | "draft_delta" | "draft_final" | "send_final";

export function normalizeChannelOutboundOperation(
  operation: unknown,
): ChannelOutboundOperation {
  const token = String(operation ?? "").trim().toLowerCase();
  if (token === "draft_start" || token === "draft_delta" || token === "draft_final") {
    return token;
  }
  return "send_final";
}

export interface DraftState {
  remoteJid: string;
  idempotencyKey: string;
  replyToExternalMessageId?: string;
  text: string;
  sequence: number;
}

export type SendFinalFn = (payload: Record<string, unknown>) => Promise<Record<string, unknown>>;

export interface HandleDraftOutboundParams {
  draftId: string;
  remoteJid: string;
  idempotencyKey: string;
  sequence: number;
  operation: Exclude<ChannelOutboundOperation, "send_final">;
  text: string;
  delta: string;
  replyToExternalMessageId?: string;
  channelKey: string;
  provider: string;
}

export interface DraftResponse {
  channel_key: string;
  provider: string;
  idempotency_key: string;
  draft_id: string;
  operation: Exclude<ChannelOutboundOperation, "send_final">;
  delivered: false;
  text?: string;
  stale?: true;
}

export class DraftManager {
  private readonly drafts = new Map<string, DraftState>();

  async handleDraftOutbound(
    params: HandleDraftOutboundParams,
    sendFinalFn: SendFinalFn,
  ): Promise<Record<string, unknown>> {
    const {
      draftId,
      remoteJid,
      idempotencyKey,
      sequence,
      operation,
      text,
      delta,
      replyToExternalMessageId,
      channelKey,
      provider,
    } = params;

    if (!draftId || !remoteJid || !idempotencyKey) {
      throw new Error(
        "draft.outbound requires draft_id, remote_jid, and idempotency_key.",
      );
    }

    const previous = this.drafts.get(draftId);
    if (previous && sequence <= previous.sequence) {
      return {
        channel_key: channelKey,
        provider,
        idempotency_key: idempotencyKey,
        draft_id: draftId,
        operation,
        delivered: false,
        stale: true as const,
      } satisfies DraftResponse;
    }

    const nextText =
      operation === "draft_delta" && previous
        ? `${previous.text}${delta}`
        : text;

    const draft: DraftState = {
      remoteJid,
      idempotencyKey,
      replyToExternalMessageId: replyToExternalMessageId || previous?.replyToExternalMessageId,
      text: nextText,
      sequence,
    };
    this.drafts.set(draftId, draft);

    if (operation !== "draft_final") {
      return {
        channel_key: channelKey,
        provider,
        idempotency_key: idempotencyKey,
        draft_id: draftId,
        operation,
        delivered: false,
        text: draft.text,
      } satisfies DraftResponse;
    }

    try {
      return await sendFinalFn({
        channel_key: channelKey,
        provider,
        idempotency_key: idempotencyKey,
        draft_id: draftId,
        remote_jid: remoteJid,
        text: draft.text,
        operation: "draft_final",
        reply_to_external_message_id: draft.replyToExternalMessageId,
      });
    } finally {
      this.drafts.delete(draftId);
    }
  }
}
