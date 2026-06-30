'use client';

import { useMemo } from 'react';

import type { WorkstationChatMessageRecord } from '@/lib/workspace/chat-message';
import type {
  CodexAssistantCell,
  CodexTraceActivityCell,
  CodexTranscriptCell,
} from '@/lib/workspace/codex-chat/cells';
import type { TimelineProjectionEvent } from '@/lib/workspace/codex-chat/timeline-reducer';
import { projectCodexTimeline } from '@/lib/workspace/codex-chat/timeline-reducer';
import { workstationMessageToCodexCell } from '@/lib/workspace/codex-chat/message-adapter';
import { transcriptProjectionEventsFromMetadata } from '@/lib/workspace/transcript-event-contract';

export type TimelineProjectionOptions = {
  approvals: (Record<string, unknown> & {
    approval_id?: string | null;
    id?: string | null;
    status?: string | null;
    prompt?: string | null;
  })[];
  threadMessages: WorkstationChatMessageRecord[];
  pendingUserMessage: WorkstationChatMessageRecord | null;
  isSending: boolean;
  liveTimelineEvents: TimelineProjectionEvent[];
  legacyTraceEventsByTraceId?: Record<string, TimelineProjectionEvent[]>;
  showProjectedAssistant: boolean;
  isSyntheticTranscriptMessage: (message: WorkstationChatMessageRecord) => boolean;
  canonicalIncludesMessage: (
    canonicalMessages: WorkstationChatMessageRecord[],
    candidate: WorkstationChatMessageRecord,
  ) => boolean;
  isProviderGateTranscriptCell: (cell: CodexTranscriptCell) => boolean;
  isProviderGateSystemCell: (cell: CodexTranscriptCell) => boolean;
  projectedAssistantLooksSynthetic: (
    cell: Extract<CodexTranscriptCell, { kind: 'assistant' }> | null,
  ) => boolean;
  readString: (value: unknown) => string;
};

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

const SAFE_TRACE_DATA_KEYS = new Set([
  'action',
  'action_id',
  'approval_id',
  'approval_status',
  'approvalStatus',
  'artifact_id',
  'artifact_ids',
  'artifactId',
  'caption',
  'capability_id',
  'code',
  'connector_id',
  'command',
  'cwd',
  'decision',
  'delivery_transport',
  'description',
  'detail',
  'delta',
  'duration_ms',
  'durationMs',
  'event',
  'exit_code',
  'exitCode',
  'filename',
  'height',
  'id',
  'kind',
  'label',
  'machine_label',
  'message',
  'mime_type',
  'mimeType',
  'name',
  'normalized_approval',
  'path',
  'query',
  'result_summary',
  'resolution',
  'runtime_access_mode',
  'runtime_session_id',
  'runtime_target',
  'stderr',
  'stderr_preview',
  'stderr_tail',
  'specialist_name',
  'state',
  'status',
  'summary',
  'stdout',
  'stdout_preview',
  'stdout_tail',
  'target_summary',
  'target_kind',
  'task_summary',
  'title',
  'tool_call_id',
  'tool_name',
  'url',
  'width',
  'working_directory',
]);

const SAFE_TRACE_METADATA_KEYS = new Set([
  'action_id',
  'approval_id',
  'artifact_id',
  'code',
  'cwd',
  'label',
  'machine_label',
  'runtime_access_mode',
  'runtime_session_id',
  'runtime_target',
  'status',
  'summary',
  'target_kind',
  'title',
]);

const SAFE_TRACE_EVENT_EXACT = new Set([
  'artifact.created',
  'approval.requested',
  'approval.resolved',
  'browser.action',
  'browser.screenshot',
  'computer.browser.action',
  'delegation.finished',
  'delegation.started',
  'screenshot.captured',
  'search.query',
  'search.results',
  'tool.result',
  'tool.started',
  'trace.completed',
  'trace.failed',
]);

const SAFE_TRACE_EVENT_PREFIXES = [
  'approval.',
  'artifact.',
  'browser.',
  'computer.',
  'delegation.',
  'file.',
  'gateway.',
  'hardware.',
  'runtime.',
  'screenshot.',
  'search.',
  'shell.',
  'tool.',
];

const SAFE_SENDISH_TRACE_TOKENS = ['telegram', 'whatsapp', 'gmail', 'email', 'mail', 'message.sent', 'message.queued'];
const BLOCKED_TRACE_EVENTS = new Set([
  'assistant.message.delta',
  'final',
  'message.delta',
  'trace.started',
]);
const BLOCKED_TRACE_EVENT_TOKENS = [
  '.delta',
  'audit',
  'debug',
  'internal',
  'model',
  'policy',
  'prompt',
  'provider',
  'raw',
  'reasoning',
];

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function looksInternalText(value: string): boolean {
  const normalized = value.trim();
  if (!normalized) {
    return false;
  }
  if (
    (normalized.startsWith('{') && normalized.endsWith('}'))
    || (normalized.startsWith('[') && normalized.endsWith(']'))
  ) {
    return true;
  }
  const lowered = normalized.toLowerCase();
  return lowered.includes('activity_event_id')
    || lowered.includes('chain of thought')
    || lowered.includes('chain_of_thought')
    || lowered.includes('default_limit')
    || lowered.includes('deepseek')
    || lowered.includes('exceeds_default')
    || lowered.includes('model id')
    || lowered.includes('model_id')
    || lowered.includes('policy metadata')
    || lowered.includes('policy_metadata')
    || lowered.includes('provider id')
    || lowered.includes('provider_id')
    || lowered.includes('raw output')
    || lowered.includes('stacktrace')
    || lowered.includes('state_payload')
    || lowered.includes('system prompt')
    || lowered.includes('system_prompt')
    || lowered.includes('request id')
    || lowered.includes('request_id')
    || lowered.includes('trace id')
    || lowered.includes('trace_id')
    || lowered.includes('raw_');
}

function isSafeTraceEventType(eventType: string): boolean {
  if (eventType === 'reasoning.summary.delta') {
    return true;
  }
  if (!eventType || BLOCKED_TRACE_EVENTS.has(eventType)) {
    return false;
  }
  if (BLOCKED_TRACE_EVENT_TOKENS.some((token) => eventType.includes(token))) {
    return false;
  }
  if (SAFE_TRACE_EVENT_EXACT.has(eventType)) {
    return true;
  }
  if (SAFE_TRACE_EVENT_PREFIXES.some((prefix) => eventType.startsWith(prefix))) {
    return true;
  }
  return SAFE_SENDISH_TRACE_TOKENS.some((token) => eventType.includes(token));
}

const OUTPUT_TAIL_KEYS = new Set([
  'stderr',
  'stderr_preview',
  'stderr_tail',
  'stdout',
  'stdout_preview',
  'stdout_tail',
]);

function compactOutputTail(value: string): string {
  const charTail = value.length > 8192 ? value.slice(-8192) : value;
  const lines = charTail.replace(/\r/g, '').split('\n');
  return lines.length > 30 ? lines.slice(-30).join('\n') : charTail;
}

function safeScalar(value: unknown, key = ''): string | number | boolean | null {
  if (value === null || typeof value === 'number' || typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'string') {
    const cleaned = value.replace(/\0/g, '').trim();
    if (OUTPUT_TAIL_KEYS.has(key)) {
      return compactOutputTail(cleaned);
    }
    if (
      ['caption', 'description', 'detail', 'label', 'message', 'summary', 'title'].includes(key)
      && looksInternalText(cleaned)
    ) {
      return null;
    }
    return cleaned.slice(0, 500);
  }
  return null;
}

function sanitizeSafeRecord(value: unknown, safeKeys: Set<string>, depth = 0): Record<string, unknown> {
  const record = readRecord(value);
  if (Object.keys(record).length === 0 || depth > 2) {
    return {};
  }
  const sanitized: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(record)) {
    if (!safeKeys.has(key)) {
      continue;
    }
    if (key === 'metadata') {
      const metadata = sanitizeSafeRecord(item, SAFE_TRACE_METADATA_KEYS, depth + 1);
      if (Object.keys(metadata).length > 0) {
        sanitized[key] = metadata;
      }
      continue;
    }
    const scalar = safeScalar(item, key);
    if (scalar !== null) {
      sanitized[key] = scalar;
      continue;
    }
    if (Array.isArray(item)) {
      const values = item
        .slice(0, 20)
        .map((entry) => safeScalar(entry, key))
        .filter((entry): entry is string | number | boolean => entry !== null);
      if (values.length > 0) {
        sanitized[key] = values;
      }
      continue;
    }
    const nested = sanitizeSafeRecord(item, key === 'normalized_approval' ? SAFE_TRACE_DATA_KEYS : safeKeys, depth + 1);
    if (Object.keys(nested).length > 0) {
      sanitized[key] = nested;
    }
  }
  return sanitized;
}

function safeTraceProjectionEvent(record: Record<string, unknown>, index: number): TimelineProjectionEvent | null {
  const eventType = readString(record.event_type).toLowerCase();
  if (!isSafeTraceEventType(eventType)) {
    return null;
  }
  const data = sanitizeSafeRecord(record.data ?? record.payload, SAFE_TRACE_DATA_KEYS);
  const metadata = sanitizeSafeRecord(record.metadata, SAFE_TRACE_METADATA_KEYS);
  const payload: Record<string, unknown> = {
    event_type: eventType,
    item_id: readString(record.item_id) || undefined,
    tool_call_id: readString(record.tool_call_id) || undefined,
    approval_id: readString(record.approval_id) || undefined,
    artifact_id: readString(record.artifact_id) || undefined,
    seq: typeof record.seq === 'number' ? record.seq : index,
    ts: readString(record.ts) || undefined,
  };
  if (Object.keys(data).length > 0) {
    payload.data = data;
  }
  if (Object.keys(metadata).length > 0) {
    payload.metadata = metadata;
  }
  return { type: 'trace', payload };
}

export function safeTimelineEventsFromTraceReplay(replay: unknown): TimelineProjectionEvent[] {
  const record = readRecord(replay);
  const rawEvents = Array.isArray(record.events) ? record.events : [];
  return rawEvents
    .map((item, index) => safeTraceProjectionEvent(readRecord(item), index))
    .filter((event): event is TimelineProjectionEvent => event !== null)
    .slice(-200);
}

function traceIdFromMetadata(metadata: Record<string, unknown>): string {
  const contextUsed = readRecord(metadata.context_used);
  return readString(metadata.trace_id) || readString(contextUsed.trace_id);
}

function isProofCell(cell: CodexTranscriptCell): boolean {
  return (
    cell.kind === 'reasoning_summary'
    || cell.kind === 'exec'
    || cell.kind === 'tool'
    || cell.kind === 'web_search'
    || cell.kind === 'file_change'
    || cell.kind === 'screenshot'
    || cell.kind === 'artifact'
    || cell.kind === 'approval_request'
    || cell.kind === 'status'
    || cell.kind === 'agent_activity'
    || cell.kind === 'error'
  );
}

function isReplayProofCell(cell: CodexTranscriptCell): boolean {
  return isProofCell(cell) && cell.kind !== 'reasoning_summary';
}

function replayProofCellsForMessage(
  message: WorkstationChatMessageRecord,
  options: Pick<TimelineProjectionOptions, 'isProviderGateSystemCell' | 'legacyTraceEventsByTraceId'>,
): CodexTranscriptCell[] {
  const events = transcriptProjectionEventsFromMetadata(message.metadata);
  const replayEvents = events.length > 0
    ? events
    : options.legacyTraceEventsByTraceId?.[traceIdFromMetadata(message.metadata)] ?? [];
  if (replayEvents.length === 0) {
    return [];
  }
  return projectCodexTimeline(replayEvents).cells
    .filter((cell) => isReplayProofCell(cell) && !options.isProviderGateSystemCell(cell))
    .map((cell) => ({
      ...cell,
      id: `${message.id}:transcript:${cell.id}`,
      createdAt: cell.createdAt || message.createdAt,
      dimmed: cell.kind === 'approval_request' ? cell.dimmed : true,
    }));
}

function isTraceActivityCell(cell: CodexTranscriptCell): cell is CodexTraceActivityCell {
  return (
    cell.kind === 'reasoning_summary'
    || cell.kind === 'exec'
    || cell.kind === 'tool'
    || cell.kind === 'web_search'
    || cell.kind === 'file_change'
    || cell.kind === 'screenshot'
    || cell.kind === 'artifact'
    || cell.kind === 'status'
  );
}

function traceActivityIsRunning(cell: CodexTraceActivityCell): boolean {
  if (cell.kind === 'reasoning_summary') {
    return cell.isStreaming;
  }
  if (cell.kind === 'web_search') {
    return cell.status === 'searching';
  }
  return 'status' in cell && cell.status === 'running';
}

function groupExecutionTraceCells(cells: CodexTranscriptCell[]): CodexTranscriptCell[] {
  const grouped: CodexTranscriptCell[] = [];
  let activities: CodexTraceActivityCell[] = [];

  const flushTrace = (response: CodexAssistantCell | null = null) => {
    if (activities.length === 0) {
      if (response) {
        grouped.push(response);
      }
      return;
    }
    const isStreaming = Boolean(response?.isStreaming) || activities.some(traceActivityIsRunning);
    grouped.push({
      id: `execution-trace:${response?.id ?? activities[0]?.id ?? grouped.length}`,
      kind: 'execution_trace',
      activities,
      response,
      isStreaming,
      isIncomplete: response?.isIncomplete === true,
      createdAt: activities[0]?.createdAt ?? response?.createdAt ?? null,
      dimmed: activities.every((activity) => activity.dimmed === true),
    });
    activities = [];
  };

  for (const cell of cells) {
    if (isTraceActivityCell(cell)) {
      activities.push(cell);
      continue;
    }
    if (cell.kind === 'assistant') {
      flushTrace(cell);
      continue;
    }
    flushTrace();
    grouped.push(cell);
  }
  flushTrace();
  return grouped;
}

export function useWorkstationTimelineProjection(options: TimelineProjectionOptions) {
  const projectedTimelineProjection = useMemo(
    () => projectCodexTimeline(options.liveTimelineEvents),
    [options.liveTimelineEvents],
  );

  const projectedTimelineCells = projectedTimelineProjection.cells;

  const projectedSystemCells = useMemo(
    () => projectedTimelineCells.filter((cell) => isProofCell(cell) && !options.isProviderGateSystemCell(cell)),
    [options, projectedTimelineCells],
  );

  const projectedAssistantCell = useMemo(
    () => {
      const candidate = projectedTimelineCells.find(
        (cell): cell is Extract<CodexTranscriptCell, { kind: 'assistant' }> => cell.kind === 'assistant',
      ) ?? null;
      return options.projectedAssistantLooksSynthetic(candidate) ? null : candidate;
    },
    [options, projectedTimelineCells],
  );

  const pinnedTimelineCells: CodexTranscriptCell[] = [];

  const pendingApprovalCells = useMemo<CodexTranscriptCell[]>(() => (
    options.approvals.map((approval, index) => {
      const approvalId = options.readString(approval.approval_id || approval.id) || `approval-${index}`;
      const approvalRecord = approval as Record<string, unknown>;
      return {
        id: approvalId,
        kind: 'approval_request',
        prompt: options.readString(approval.prompt) || `Approval ${index + 1}`,
        actions: ['allow_once', 'allow_session', 'deny'],
        status: 'waiting',
        createdAt: options.readString(approvalRecord.created_at) || null,
        metadata: {
          ...approval,
          approval_id: approvalId,
        },
      };
    })
  ), [options]);

  const visibleTranscriptCells = useMemo(() => {
    const canonicalMessages = options.threadMessages.filter((message) => !options.isSyntheticTranscriptMessage(message));
    const nextCells: CodexTranscriptCell[] = [];
    let latestAssistantMessageId: string | null = null;
    for (let index = canonicalMessages.length - 1; index >= 0; index -= 1) {
      const candidate = canonicalMessages[index];
      if (candidate?.role === 'assistant') {
        latestAssistantMessageId = candidate.id;
        break;
      }
    }
    let insertedReplayProofForLatestAssistant = false;
    for (const message of canonicalMessages) {
      const cell = workstationMessageToCodexCell(message);
      if (options.isProviderGateTranscriptCell(cell)) {
        continue;
      }
      if (!options.isSending && cell.kind === 'assistant') {
        const replayCells = replayProofCellsForMessage(message, options);
        if (replayCells.length > 0) {
          if (message.id === latestAssistantMessageId) {
            insertedReplayProofForLatestAssistant = true;
          }
          nextCells.push(...replayCells);
        }
      }
      nextCells.push(cell);
    }
    if (options.pendingUserMessage && !options.canonicalIncludesMessage(canonicalMessages, options.pendingUserMessage)) {
      nextCells.push(workstationMessageToCodexCell(options.pendingUserMessage));
    }
    const trailingCell = nextCells[nextCells.length - 1] ?? null;
    const scrollableSystemCells = insertedReplayProofForLatestAssistant ? [] : projectedSystemCells;
    const shouldInsertStepsBeforeFinalAssistant = scrollableSystemCells.length > 0
      && trailingCell?.kind === 'assistant';
    if (shouldInsertStepsBeforeFinalAssistant && trailingCell) {
      nextCells.pop();
      nextCells.push(...scrollableSystemCells, trailingCell);
    } else if (scrollableSystemCells.length > 0) {
      nextCells.push(...scrollableSystemCells);
    }
    if (options.showProjectedAssistant && projectedAssistantCell) {
      nextCells.push(projectedAssistantCell);
    }
    if (pendingApprovalCells.length > 0) {
      nextCells.push(...pendingApprovalCells);
    }
    return groupExecutionTraceCells(nextCells);
  }, [
    options,
    pendingApprovalCells,
    projectedAssistantCell,
    projectedSystemCells,
  ]);

  return {
    projectedTimelineProjection,
    projectedTimelineCells,
    projectedSystemCells,
    projectedAssistantCell,
    pinnedTimelineCells,
    pendingApprovalCells,
    visibleTranscriptCells,
  };
}
