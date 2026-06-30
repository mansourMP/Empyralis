'use client';

import type { WorkstationChatMessageRecord } from '@/lib/workspace/chat-message';

import type { CodexTranscriptCell } from './cells';

function readObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function normalizeToolStatus(value: unknown): 'running' | 'done' | 'error' {
  const normalized = readString(value).toLowerCase();
  if (normalized === 'done' || normalized === 'completed' || normalized === 'success' || normalized === 'succeeded') {
    return 'done';
  }
  if (normalized === 'error' || normalized === 'failed' || normalized === 'failure') {
    return 'error';
  }
  return 'running';
}

function providerFromMetadata(metadata: Record<string, unknown>): string | null {
  const contextUsed = readObject(metadata.context_used);
  return readString(metadata.effective_provider)
    || readString(metadata.provider)
    || readString(contextUsed.effective_provider)
    || null;
}

function modelFromMetadata(metadata: Record<string, unknown>): string | null {
  const contextUsed = readObject(metadata.context_used);
  return readString(metadata.effective_model)
    || readString(metadata.model)
    || readString(contextUsed.effective_model)
    || null;
}

export function workstationMessageToCodexCell(message: WorkstationChatMessageRecord): CodexTranscriptCell {
  const metadata = readObject(message.metadata);
  const displayKind = readString(metadata.display_kind);

  if (displayKind === 'thinking_row') {
    return {
      id: message.id,
      kind: 'reasoning_summary',
      text: readString(metadata.thinking_text) || message.content,
      activityLine: readString(metadata.activity_line) || null,
      isStreaming: metadata.step_streaming === true || message.status === 'streaming',
      dimmed: metadata.step_dimmed === true,
      createdAt: message.createdAt,
      metadata,
    };
  }

  if (displayKind === 'tool_row' || displayKind === 'activity_step') {
    return {
      id: message.id,
      kind: 'tool',
      name: readString(metadata.tool_name) || message.content || 'Tool',
      input: readString(metadata.tool_input) || readString(metadata.input) || null,
      status: normalizeToolStatus(metadata.step_status ?? message.status),
      result: readString(metadata.tool_result) || null,
      dimmed: metadata.step_dimmed === true,
      createdAt: message.createdAt,
      metadata,
    };
  }

  if (displayKind === 'file_row') {
    return {
      id: message.id,
      kind: 'file_change',
      filename: readString(metadata.filename) || message.content || 'File',
      action: readString(metadata.file_action) || 'Read',
      status: 'done',
      dimmed: metadata.step_dimmed === true,
      createdAt: message.createdAt,
      metadata,
    };
  }

  if (displayKind === 'search_row') {
    const status = normalizeToolStatus(metadata.step_status ?? message.status);
    return {
      id: message.id,
      kind: 'web_search',
      query: readString(metadata.query) || message.content || 'Search',
      status: status === 'running' ? 'searching' : status,
      result: readString(metadata.search_result) || null,
      dimmed: metadata.step_dimmed === true,
      createdAt: message.createdAt,
      metadata,
    };
  }

  if (displayKind === 'provider_error') {
    return {
      id: message.id,
      kind: 'error',
      message: message.content || 'The request could not finish. Retry when ready.',
      retryable: metadata.retryable !== false,
      createdAt: message.createdAt,
      metadata,
    };
  }

  if (displayKind === 'approval_request') {
    return {
      id: message.id,
      kind: 'approval_request',
      prompt: message.content || readString(metadata.prompt) || 'Approval required',
      actions: ['allow_once', 'allow_session', 'deny'],
      status: 'waiting',
      createdAt: message.createdAt,
      metadata,
    };
  }

  if (displayKind === 'exec_row') {
    return {
      id: message.id,
      kind: 'exec',
      command: readString(metadata.command) || message.content || 'Shell',
      status: normalizeToolStatus(metadata.step_status ?? message.status),
      output: readString(metadata.output) || null,
      exitCode: readNumber(metadata.exit_code),
      dimmed: metadata.step_dimmed === true,
      createdAt: message.createdAt,
      metadata,
    };
  }

  if (message.role === 'user') {
    return {
      id: message.id,
      kind: 'user',
      content: message.content,
      attachments: Array.isArray(message.attachments) ? message.attachments : [],
      createdAt: message.createdAt,
      metadata,
    };
  }

  return {
    id: message.id,
    kind: 'assistant',
    content: message.content,
    isStreaming: message.status === 'streaming',
    isIncomplete: metadata.incomplete === true || message.status === 'incomplete',
    effectiveProvider: providerFromMetadata(metadata),
    effectiveModel: modelFromMetadata(metadata),
    createdAt: message.createdAt,
    metadata,
  };
}
