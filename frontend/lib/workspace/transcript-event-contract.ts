'use client';

import type { TimelineProjectionEvent } from '@/lib/workspace/codex-chat/cells';

export const TRANSCRIPT_EVENT_SCHEMA_VERSION = 1;
export const TRANSCRIPT_EVENT_LIMIT = 200;
export const TRANSCRIPT_EVENT_TYPES = ['trace', 'step'] as const;
export const TRANSCRIPT_TRACE_EVENT_EXACT = [
  'artifact.created',
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
] as const;
export const TRANSCRIPT_TRACE_EVENT_PREFIXES = [
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
] as const;
export const TRANSCRIPT_SENDISH_TRACE_TOKENS = [
  'telegram',
  'whatsapp',
  'gmail',
  'email',
  'mail',
  'message.sent',
  'message.queued',
] as const;

export type TranscriptEventType = typeof TRANSCRIPT_EVENT_TYPES[number];

export type TranscriptEventRecord = {
  event?: unknown;
  type?: unknown;
  schema_version?: unknown;
  payload?: unknown;
};

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function isTranscriptEventType(value: unknown): value is TranscriptEventType {
  return value === 'trace' || value === 'step';
}

const BLOCKED_KEYS = new Set([
  'args',
  'args_preview',
  'arguments',
  'audit',
  'audit_event',
  'audit_event_id',
  'audit_event_type',
  'chain_of_thought',
  'context',
  'debug',
  'filters',
  'input',
  'messages',
  'model',
  'model_id',
  'modelId',
  'output',
  'parameters',
  'policy',
  'policy_metadata',
  'prompt',
  'provider',
  'provider_id',
  'providerId',
  'raw',
  'raw_input',
  'raw_output',
  'reasoning',
  'result',
  'request_id',
  'requestId',
  'specialist_id',
  'system_prompt',
  'text',
  'trace_id',
  'traceId',
  'tool_args',
]);

const SAFE_TRACE_TOP_LEVEL_KEYS = new Set([
  'approval_id',
  'artifact_id',
  'data',
  'event_type',
  'item_id',
  'metadata',
  'seq',
  'tool_call_id',
  'ts',
]);

const SAFE_TRACE_DATA_KEYS = new Set([
  'action',
  'action_id',
  'actor',
  'approval_id',
  'approval_status',
  'approvalStatus',
  'artifact_id',
  'artifact_ids',
  'artifactId',
  'caption',
  'capability_id',
  'code',
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
  'metadata',
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

const SAFE_STEP_KEYS = new Set([
  'detail',
  'id',
  'kind',
  'label',
  'metadata',
  'status',
]);

const BLOCKED_STEP_KINDS = new Set(['thinking', 'reasoning', 'assistant', 'message', 'final', 'chunk']);
const BLOCKED_TRACE_EVENTS = new Set(['assistant.message.delta', 'final', 'message.delta', 'trace.started']);
const SAFE_STEP_KINDS = new Set([
  'approval',
  'artifact',
  'browser',
  'browser-observation',
  'computer',
  'connector',
  'delegation',
  'error',
  'file',
  'interrupted',
  'runtime',
  'screenshot',
  'search',
  'shell',
  'status',
  'tool',
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
const INTERNAL_TEXT_TOKENS = [
  'activity_event_id',
  'chain of thought',
  'chain_of_thought',
  'deepseek',
  'model id',
  'model_id',
  'policy metadata',
  'policy_metadata',
  'provider id',
  'provider_id',
  'raw output',
  'raw_output',
  'request id',
  'request_id',
  'stacktrace',
  'system prompt',
  'system_prompt',
  'trace id',
  'trace_id',
];

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

function looksInternalText(value: string): boolean {
  const normalized = value.trim();
  if (!normalized) {
    return false;
  }
  if (normalized.startsWith('{') || normalized.startsWith('[')) {
    return true;
  }
  const lowered = normalized.toLowerCase();
  return INTERNAL_TEXT_TOKENS.some((token) => lowered.includes(token));
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

function sanitizeValue(value: unknown, key: string, safeKeys: Set<string>, depth: number): unknown {
  if (BLOCKED_KEYS.has(key)) {
    return null;
  }
  const scalar = safeScalar(value, key);
  if (scalar !== null || value === null) {
    return scalar;
  }
  if (Array.isArray(value)) {
    if (depth > 2) {
      return [];
    }
    return value
      .slice(0, 20)
      .map((entry) => sanitizeValue(entry, '', safeKeys, depth + 1))
      .filter((entry) => entry !== null && !(Array.isArray(entry) && entry.length === 0));
  }
  const record = readRecord(value);
  if (Object.keys(record).length === 0 || depth > 2) {
    return null;
  }
  const nestedSafeKeys = key === 'metadata'
    ? SAFE_TRACE_METADATA_KEYS
    : key === 'data'
      ? SAFE_TRACE_DATA_KEYS
      : safeKeys;
  const sanitized: Record<string, unknown> = {};
  for (const [nestedKey, nestedValue] of Object.entries(record)) {
    const keyToken = nestedKey.trim();
    if (!keyToken || BLOCKED_KEYS.has(keyToken) || (nestedSafeKeys.size > 0 && !nestedSafeKeys.has(keyToken))) {
      continue;
    }
    const nextValue = sanitizeValue(nestedValue, keyToken, nestedSafeKeys, depth + 1);
    if (nextValue === null || (Array.isArray(nextValue) && nextValue.length === 0)) {
      continue;
    }
    if (typeof nextValue === 'object' && nextValue && !Array.isArray(nextValue) && Object.keys(nextValue).length === 0) {
      continue;
    }
    sanitized[keyToken] = nextValue;
  }
  return sanitized;
}

function sanitizeRecord(value: unknown, safeKeys: Set<string>): Record<string, unknown> {
  const record = readRecord(value);
  const sanitized: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(record)) {
    const keyToken = key.trim();
    if (!keyToken || BLOCKED_KEYS.has(keyToken) || (safeKeys.size > 0 && !safeKeys.has(keyToken))) {
      continue;
    }
    const nextValue = sanitizeValue(item, keyToken, safeKeys, 0);
    if (nextValue === null || (Array.isArray(nextValue) && nextValue.length === 0)) {
      continue;
    }
    if (typeof nextValue === 'object' && nextValue && !Array.isArray(nextValue) && Object.keys(nextValue).length === 0) {
      continue;
    }
    sanitized[keyToken] = nextValue;
  }
  return sanitized;
}

function isTranscriptTracePayload(payload: Record<string, unknown>): boolean {
  const eventType = readString(payload.event_type).toLowerCase();
  if (eventType === 'reasoning.summary.delta') {
    return true;
  }
  if (!eventType || BLOCKED_TRACE_EVENTS.has(eventType)) {
    return false;
  }
  if (BLOCKED_TRACE_EVENT_TOKENS.some((token) => eventType.includes(token))) {
    return false;
  }
  if ((TRANSCRIPT_TRACE_EVENT_EXACT as readonly string[]).includes(eventType)) {
    return true;
  }
  if (TRANSCRIPT_TRACE_EVENT_PREFIXES.some((prefix) => eventType.startsWith(prefix))) {
    return true;
  }
  return TRANSCRIPT_SENDISH_TRACE_TOKENS.some((token) => eventType.includes(token));
}

function isTranscriptStepPayload(payload: Record<string, unknown>): boolean {
  const kind = readString(payload.kind).toLowerCase();
  if (BLOCKED_STEP_KINDS.has(kind)) {
    return false;
  }
  return kind ? SAFE_STEP_KINDS.has(kind) : false;
}

function sanitizeTranscriptPayload(type: TranscriptEventType, payload: Record<string, unknown>): Record<string, unknown> {
  if (type === 'trace') {
    if (!isTranscriptTracePayload(payload)) {
      return {};
    }
    return sanitizeRecord(payload, SAFE_TRACE_TOP_LEVEL_KEYS);
  }
  if (!isTranscriptStepPayload(payload)) {
    return {};
  }
  return sanitizeRecord(payload, SAFE_STEP_KEYS);
}

export function transcriptProjectionEventsFromMetadata(
  metadata: Record<string, unknown>,
): TimelineProjectionEvent[] {
  const rawEvents = Array.isArray(metadata.transcript_events) ? metadata.transcript_events : [];
  return rawEvents
    .map((item): TimelineProjectionEvent | null => {
      const record = readRecord(item) as TranscriptEventRecord;
      const type = String(record.event || record.type || '').trim();
      if (!isTranscriptEventType(type)) {
        return null;
      }
      const payload = sanitizeTranscriptPayload(type, readRecord(record.payload));
      if (Object.keys(payload).length === 0) {
        return null;
      }
      return { type, payload };
    })
    .filter((item): item is TimelineProjectionEvent => item !== null)
    .slice(-TRANSCRIPT_EVENT_LIMIT);
}
