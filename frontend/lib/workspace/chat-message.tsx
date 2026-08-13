'use client';

import Link from 'next/link';
import { type ReactNode, memo, useState } from 'react';
import {
  Brain,
  Check,
  ChevronRight,
  CircleAlert,
  FileText,
  Paperclip,
  Search,
  Wrench,
} from 'lucide-react';

import { isPlatformBillingSource } from '@/lib/workspace/platform-brand';
import { MarkdownLiteText } from '@/lib/workspace/markdown-lite';
import { safeExternalHref } from '@/lib/workspace/safe-render-url';

function formatTimestamp(value: string | null): string | null {
  if (!value) {
    return null;
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }

  return parsed.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}

export type WorkstationChatArtifactReference = {
  id: string;
  label: string;
  kind?: string | null;
  mediaType?: string | null;
};

export type WorkstationChatMessageRecord = {
  id: string;
  role: string;
  content: string;
  status: string | null;
  createdAt: string | null;
  runId: string | null;
  approvals: Record<string, unknown>[];
  interventions: Record<string, unknown>[];
  artifacts: WorkstationChatArtifactReference[];
  attachments?: any[];
  metadata: Record<string, unknown>;
};

function humanizeToken(value: string | null | undefined): string {
  const normalized = String(value ?? '').trim();
  if (!normalized) {
    return '';
  }
  return normalized
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function effectiveProviderLabel(metadata: Record<string, unknown>): string {
  const contextUsed = metadata.context_used && typeof metadata.context_used === 'object'
    ? metadata.context_used as Record<string, unknown>
    : null;
  const billingSource = String(metadata.billing_source ?? contextUsed?.billing_source ?? '').trim();
  const aiLabel = String(metadata.ai_label ?? contextUsed?.ai_label ?? '').trim();
  if (isPlatformBillingSource(billingSource)) {
    // Platform path: NEVER leak the underlying provider.
    // Use the backend-supplied ai_label when available, otherwise default to "Platform AI".
    return aiLabel ? `${aiLabel} · Workspace AI` : 'Platform AI · Workspace AI';
  }
  const provider = String(
    metadata.effective_provider
      ?? metadata.provider
      ?? contextUsed?.effective_provider
      ?? '',
  ).trim();
  const model = String(
    metadata.effective_model
      ?? metadata.model
      ?? contextUsed?.effective_model
      ?? '',
  ).trim();
  const parts: string[] = [];
  if (provider) {
    parts.push(humanizeToken(provider));
  }
  if (model) {
    parts.push(model);
  }
  return parts.join(' · ');
}

/** AgentChat's send() stamps `metadata.attachments` on the optimistic local
 *  user turn (SageChatAttachment[] shape); a reloaded turn carries the same
 *  array back off the server, since upsert_agent_turn persists whatever
 *  `attachments` /api/turn was sent under this same metadata key. Read
 *  defensively — a turn from before attachments existed, or one with a
 *  malformed value, just renders no chips rather than throwing. */
function messageAttachments(metadata: Record<string, unknown>): { name: string; url: string }[] {
  const raw = metadata.attachments;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const rec = item as Record<string, unknown>;
      const name = String(rec.filename ?? rec.name ?? '').trim();
      const url = String(rec.url ?? rec.uri ?? '').trim();
      if (!name && !url) return null;
      return { name: name || 'Attachment', url };
    })
    .filter((a): a is { name: string; url: string } => a !== null);
}

function routeLabel(metadata: Record<string, unknown>): string {
  const routeDecision = metadata.route_decision && typeof metadata.route_decision === 'object' && !Array.isArray(metadata.route_decision)
    ? metadata.route_decision as Record<string, unknown>
    : {};
  return String(metadata.route_label ?? routeDecision.user_label ?? '').trim();
}

function stepIcon(kind: string) {
  switch (kind) {
    case 'thinking':
      return Brain;
    case 'file':
      return FileText;
    case 'search':
      return Search;
    default:
      return Wrench;
  }
}

const SYNTHETIC_THINKING_TEXT = new Set([
  'planning the response',
  'planning the next step',
  'prepared the next action',
  'answer ready',
]);

const INTERNAL_THINKING_MARKERS = [
  'state_payload',
  'payload_exceeds',
  'exceeds_default',
  'default_limit',
  'state payload exceeds',
] as const;

function normalizedThinkingContent(rawText: string): string {
  const trimmed = rawText.trim();
  if (!trimmed) {
    return '';
  }
  if (trimmed.toLowerCase().startsWith('thinking...')) {
    return trimmed.slice('thinking...'.length).trim();
  }
  if (trimmed.toLowerCase() === 'thinking') {
    return '';
  }
  return trimmed;
}

function containsInternalThinkingMarker(value: string): boolean {
  const lowered = value.toLowerCase();
  return INTERNAL_THINKING_MARKERS.some((marker) => lowered.includes(marker));
}

function thinkingSyntheticKey(value: string): string {
  return value.toLowerCase().replace(/[.!]+$/g, '').trim();
}

function visibleThinkingContent(rawText: string): string {
  const text = normalizedThinkingContent(rawText);
  if (!text) {
    return '';
  }
  if (containsInternalThinkingMarker(text)) {
    return '';
  }
  return text
    .split('\n')
    .filter((line) => !SYNTHETIC_THINKING_TEXT.has(thinkingSyntheticKey(line)))
    .join('\n')
    .trim();
}

const ThinkingRow = memo(({
  message,
}: {
  message: WorkstationChatMessageRecord;
}) => {
  const [expanded, setExpanded] = useState(false);
  const text = visibleThinkingContent(String(message.metadata.thinking_text ?? message.content ?? ''));
  const isStreaming = message.metadata.step_streaming === true;
  const isDimmed = message.metadata.step_dimmed === true;

  if (!text) {
    return null;
  }

  return (
    <article className={`app-chat-thinking-row${isStreaming ? ' app-chat-thinking-row--streaming' : ''}${isDimmed ? ' app-chat-thinking-row--dimmed' : ''}`}>
      <button
        type="button"
        className="app-chat-thinking-row__toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        <ChevronRight
          size={12}
          strokeWidth={2}
          className={`app-chat-thinking-row__chevron${expanded ? ' app-chat-thinking-row__chevron--expanded' : ''}`}
          aria-hidden="true"
        />
        <span className="app-chat-thinking-row__label">Thought through response</span>
      </button>
      {expanded ? (
        <div className="app-chat-thinking-row__detail">
          <pre className="app-chat-thinking-row__detail-text">{text}</pre>
        </div>
      ) : null}
    </article>
  );
});

const SystemInlineRow = memo(({
  icon,
  primary,
  secondary,
  state,
  dimmed,
}: {
  icon: ReactNode;
  primary: string;
  secondary: string;
  state: 'running' | 'done' | 'error';
  dimmed: boolean;
}) => {
  return (
    <article className={`app-chat-system-row app-chat-system-row--${state}${dimmed ? ' app-chat-system-row--dimmed' : ''}`}>
      <span className="app-chat-system-row__icon" aria-hidden="true">{icon}</span>
      <span className="app-chat-system-row__primary">{primary}</span>
      <span className="app-chat-system-row__secondary">{secondary}</span>
    </article>
  );
});

export const ChatMessage = memo(({
  message,
}: {
  message: WorkstationChatMessageRecord;
  resolvingApprovalId?: string | null;
  onResolveApproval?: (approvalId: string, resolution: 'approved' | 'rejected') => void;
}) => {
  const isUser = message.role === 'user';
  const text = message.content.trim();
  const timestamp = formatTimestamp(message.createdAt);
  const displayKind = typeof message.metadata.display_kind === 'string' ? message.metadata.display_kind : '';
  const actionHref = typeof message.metadata.action_href === 'string' ? message.metadata.action_href : '';
  const actionLabel = typeof message.metadata.action_label === 'string' ? message.metadata.action_label : '';
  const providerLabel = effectiveProviderLabel(message.metadata);
  const taskRouteLabel = routeLabel(message.metadata);
  const isIncomplete = message.metadata.incomplete === true || message.status === 'incomplete';
  const attachments = messageAttachments(message.metadata);

  if (displayKind === 'thinking_row') {
    return <ThinkingRow message={message} />;
  }

  if (displayKind === 'tool_row') {
    const stepStatus = typeof message.metadata.step_status === 'string' ? message.metadata.step_status : 'running';
    const dimmed = message.metadata.step_dimmed === true;
    return (
      <SystemInlineRow
        icon={stepStatus === 'done'
          ? <Check size={14} strokeWidth={2} />
          : stepStatus === 'error'
            ? <CircleAlert size={14} strokeWidth={2} />
            : <Wrench size={14} strokeWidth={1.9} />}
        primary={stepStatus === 'done'
          ? `Used ${String(message.metadata.tool_name ?? message.content ?? 'tool')}`
          : String(message.metadata.tool_name ?? message.content ?? 'Tool')}
        secondary={stepStatus === 'done' ? 'Done' : stepStatus === 'error' ? 'Failed' : 'Running'}
        state={stepStatus === 'done' ? 'done' : stepStatus === 'error' ? 'error' : 'running'}
        dimmed={dimmed}
      />
    );
  }

  if (displayKind === 'file_row') {
    return (
      <SystemInlineRow
        icon={<FileText size={14} strokeWidth={1.9} />}
        primary="Read file"
        secondary={String(message.metadata.file_action ?? 'Read')}
        state="done"
        dimmed={message.metadata.step_dimmed === true}
      />
    );
  }

  if (displayKind === 'search_row') {
    const stepStatus = typeof message.metadata.step_status === 'string' ? message.metadata.step_status : 'running';
    return (
      <SystemInlineRow
        icon={<Search size={14} strokeWidth={1.9} />}
        primary={stepStatus === 'done' ? 'Searched web' : 'Searching web'}
        secondary={stepStatus === 'done' ? 'Done' : 'Searching'}
        state={stepStatus === 'done' ? 'done' : 'running'}
        dimmed={message.metadata.step_dimmed === true}
      />
    );
  }

  if (displayKind === 'activity_step') {
    const stepKind = typeof message.metadata.step_kind === 'string' ? message.metadata.step_kind : 'tool';
    const stepStatus = typeof message.metadata.step_status === 'string' ? message.metadata.step_status : 'active';
    const Icon = stepIcon(stepKind);
    return (
      <article
        data-chat-role="system"
        className={`app-chat-activity-row app-chat-activity-row--${stepStatus}`}
      >
        <span className="app-chat-activity-row__icon" aria-hidden="true">
          <Icon size={14} strokeWidth={1.9} />
        </span>
        <span className="app-chat-activity-row__label">{message.content.trim()}</span>
      </article>
    );
  }

  if (displayKind === 'provider_error') {
    const lowerText = text.toLowerCase();
    const providerNoticeText = lowerText.includes('ollama')
      || lowerText.includes('selected provider')
      || lowerText.includes('selected for chat')
      || lowerText.includes('local-only')
      ? 'Choose the default AI route, connect a model account, or connect Agent Computer.'
      : text;
    // safeExternalHref: action_href lives in a turn's persisted metadata --
    // no producer sets it today (grepped server_modules/), but the render
    // seam has no way to know that, and message.metadata is data this
    // component never authored, same reasoning as the attachment chip's
    // safeExternalHref(a.url) below. An unguarded Link reading the raw
    // field would execute javascript:/data: the moment anything starts
    // setting it -- see safe-render-url.test.ts's structural sweep.
    const safeActionHref = safeExternalHref(actionHref);
    return (
      <article
        data-chat-role="system"
        className="app-chat-transcript-error"
      >
        <span className="app-chat-transcript-error__icon" aria-hidden="true">
          <CircleAlert size={14} strokeWidth={1.9} />
        </span>
        <div className="app-chat-transcript-error__copy">
          <strong>AI route needs attention</strong>
          <span>{providerNoticeText}</span>
        </div>
        {safeActionHref && actionLabel ? (
          <Link href={safeActionHref} className="app-chat-transcript-error__link">
            {actionLabel}
          </Link>
        ) : null}
      </article>
    );
  }

  return (
    <article
      data-chat-role={isUser ? 'user' : 'assistant'}
      className="app-chat-message"
    >
      <div className="app-chat-message__content">
        <MarkdownLiteText text={text} />
      </div>
      {attachments.length > 0 && (
        <div className="app-chat-message__attachments">
          {attachments.map((a, i) => {
            // safeExternalHref: an attachment's `url` is server-generated by
            // our own upload endpoint on every path this UI drives today,
            // but the render seam does not know that — a turn's persisted
            // metadata is data, not a value this component minted, so it
            // gets the same javascript:/data: refusal every other
            // data-to-href seam in this file's sibling renderers already
            // has (see lib/workspace/markdown-lite.tsx's safeMarkdownLiteHref
            // and lib/workspace/fleet/markdown-lite.tsx's safeDocumentHref).
            const href = safeExternalHref(a.url);
            return href ? (
              <a key={`${a.name}-${i}`} href={href} target="_blank" rel="noreferrer" className="app-chat-message__attachment-chip">
                <Paperclip size={11} strokeWidth={1.9} aria-hidden="true" />
                <span>{a.name}</span>
              </a>
            ) : (
              <span key={`${a.name}-${i}`} className="app-chat-message__attachment-chip">
                <Paperclip size={11} strokeWidth={1.9} aria-hidden="true" />
                <span>{a.name}</span>
              </span>
            );
          })}
        </div>
      )}
      {(timestamp || providerLabel || taskRouteLabel || isIncomplete) ? (
        <div className={`app-chat-message__meta${providerLabel || taskRouteLabel || isIncomplete ? ' app-chat-message__meta--visible' : ''}`}>
          {providerLabel ? (
            <span className="app-chat-message__provider">
              {providerLabel}
            </span>
          ) : null}
          {taskRouteLabel ? (
            <span className="app-chat-message__provider">
              {taskRouteLabel}
            </span>
          ) : null}
          {isIncomplete ? (
            <span className="app-chat-message__status">
              Incomplete
            </span>
          ) : null}
          <time className="app-chat-message__timestamp" dateTime={message.createdAt ?? undefined}>
            {timestamp}
          </time>
        </div>
      ) : null}
    </article>
  );
});
